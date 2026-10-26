from __future__ import annotations

import logging
import threading
import hashlib
import time

from app.core.logging import event
from app.media.contracts import AnalysisResult,FeatureEnvelope,ModalityQuality,PredictionExtension,TargetOutput,VersionedTargetPrediction
from app.media.extractors import InvalidMediaError
from app.measurements.dual_output import dual_measurement_output
from app.behavioral.engine import build_behavioral_evidence
from app.media.isolated_decode import DecodeChildError,DecodeMemoryLimitError,DecodeTimeoutError,_decode_media,_warm_media_runtime,run_killable
from app.ml.runtime import TARGETS,PredictionRequest
from app.ml.feature_mapping import map_features
from app.ml.research_proxy_adapter import stage1_model_abstentions
from app.rule_based_indicators import evaluate as evaluate_rule_indicators

logger=logging.getLogger("wellar.media.analysis")
MODALITIES=("video","image","audio")


class MultimodalFeatureProcessor:
    """Worker-only extractors and fail-closed invocation of approved predictors."""
    def __init__(self,media_store,*,max_video_seconds:int,max_audio_seconds:int,max_image_pixels:int,max_video_frames:int,video_sample_hz:float,decode_timeout_seconds:float=12.0,decode_memory_limit_mb:int=768,analysis_timeout_seconds:float=40.0,predictors=None,prediction_enabled:bool=False,research_image_classifier=None):
        self.media_store=media_store
        self.limits={"max_video_seconds":max_video_seconds,"max_audio_seconds":max_audio_seconds,"max_image_pixels":max_image_pixels,"max_video_frames":max_video_frames,"video_sample_hz":video_sample_hz}
        self.decode_timeout_seconds=decode_timeout_seconds;self.decode_memory_limit_mb=decode_memory_limit_mb
        self.analysis_timeout_seconds=analysis_timeout_seconds
        # Model objects must be supplied by an explicit, guarded loader. Stage 1
        # never loads them and keeps this opt-in boundary closed by default.
        self.predictors=dict(predictors or {})
        self.prediction_enabled=bool(prediction_enabled)
        self._face_meshes={}; self._model_lock=threading.RLock()

    def _predict(self, _envelopes):
        if not self.prediction_enabled:
            return PredictionExtension(inference_status="disabled",targets={
                target:VersionedTargetPrediction(status="unavailable",quality_status="not_evaluated",
                    abstention_reason="prediction_not_enabled") for target in sorted(TARGETS)
            })
        target_results={}
        for target in sorted(TARGETS):
            predictor=self.predictors.get(target)
            if predictor is None:
                target_results[target]=VersionedTargetPrediction(status="unavailable",quality_status="not_evaluated",abstention_reason="model_unavailable")
                continue
            try:
                feature_names=tuple(getattr(predictor,"feature_names",()))
                if not feature_names:
                    response=predictor.predict(PredictionRequest(target,{}, {},1.0))
                else:
                    mapped,statuses,missingness=map_features(_envelopes,feature_names)
                    response=predictor.predict(PredictionRequest(target,mapped,statuses,missingness))
                result_status=response.status; reason=response.reason
                quality_status="passed" if result_status=="available" else ("failed" if reason=="quality_failure" else "not_evaluated" if result_status=="unavailable" else "passed")
                target_results[target]=VersionedTargetPrediction(status=result_status,value=response.value if result_status=="available" else None,
                    confidence=response.probability if result_status=="available" else None,confidence_calibrated=response.calibrated if result_status=="available" else False,
                    model_version=response.model_version,quality_status=quality_status,abstention_reason=reason if result_status!="available" else None)
            except Exception:
                target_results[target]=VersionedTargetPrediction(status="abstained",quality_status="not_evaluated",abstention_reason="inference_failed")
        statuses={item.status for item in target_results.values()}
        invocation_status="disabled" if statuses=={"unavailable"} else "available" if statuses=={"available"} else "partial" if "available" in statuses else "abstained"
        return PredictionExtension(inference_status=invocation_status,targets=target_results)

    def _face_mesh(self,static_image_mode:bool):
        key="image" if static_image_mode else "video"
        with self._model_lock:
            if key not in self._face_meshes:
                from app.media.extractors import _face_mesh_factory
                self._face_meshes[key]=_face_mesh_factory(static_image_mode=static_image_mode)
            return self._face_meshes[key]

    def close(self):
        with self._model_lock:
            for mesh in self._face_meshes.values(): mesh.close()
            self._face_meshes.clear()

    def warmup(self):
        """Check the real decoder runtime in a disposable, resource-limited child."""
        started=time.perf_counter()
        try:
            runtime=run_killable(_warm_media_runtime,(),timeout_seconds=self.decode_timeout_seconds,
                memory_limit_mb=self.decode_memory_limit_mb)
        except Exception as exc:
            event(logger,"worker.warmup.failed",logging.ERROR,processing_stage="worker_warmup",
                error_code="worker_warmup_failed",exception_type=type(exc).__name__)
            raise RuntimeError("worker media runtime warmup failed") from None
        event(logger,"worker.warmup.completed",processing_stage="worker_warmup",
            duration_ms=round((time.perf_counter()-started)*1000,3),
            opencv_version=runtime.get("opencv_version"),mediapipe_version=runtime.get("mediapipe_version"))
        return runtime

    def __call__(self,payload:dict)->dict:
        context=payload.get("_worker_context",{})
        scan_id=context.get("scan_id","unknown")
        tenant_ref=hashlib.sha256(context.get("tenant_id","").encode()).hexdigest()[:16]
        features={}; timings={}; qualities={m:"not_provided" for m in (*MODALITIES,"reaction_task")}
        refs=payload.get("media_refs",[])
        analysis_deadline=time.monotonic()+self.analysis_timeout_seconds
        manifest={item["media_ref"]:item["modality"] for item in payload.get("_media_manifest",[]) if isinstance(item,dict) and "media_ref" in item and "modality" in item}
        for ref in refs:
            stage=time.perf_counter()
            try:
                asset=self.media_store.resolve(ref,context.get("tenant_id",""),context.get("subject_id",""),verify_integrity=True)
            except Exception as exc:
                # Preserve the input for a worker retry. Terminal failures are
                # cleaned after the queue transition and remain reconciliation-visible.
                event(logger,"media.resolve.failed",logging.ERROR,scan_id=scan_id,tenant_ref=tenant_ref,processing_stage="media_resolve",error_code="media_resolve_failed",exception_type=type(exc).__name__)
                raise
            if asset is None:
                event(logger,"media.resolve.failed",logging.WARNING,scan_id=scan_id,tenant_ref=tenant_ref,processing_stage="media_resolve",error_code="media_reference_unavailable")
                lost_modality=manifest.get(ref)
                if lost_modality in MODALITIES:
                    qualities[lost_modality]="failed"
                    features[lost_modality]=FeatureEnvelope(modality=lost_modality,presence="present",extraction_status="failed",quality=ModalityQuality(modality=lost_modality,status="failed",reason="media_reference_unavailable"),features={"error_code":"media_reference_unavailable"},provenance={"extractor":"wellar-v2-media-storage","version":"1.0"}).model_dump(mode="json")
                else: qualities["media_reference"]="failed"
                # No registered object is available to read. Keep the reference
                # in the durable scan payload; terminal reconciliation can remove
                # stale metadata idempotently after result commit.
                continue
            modality=asset["modality"]
            event(logger,"media.resolve.completed",processing_stage="media_resolve",scan_id=scan_id,tenant_ref=tenant_ref,duration_ms=round((time.perf_counter()-stage)*1000,3),modality=modality)
            event(logger,"media.modality.started",processing_stage=f"{modality}_processing",scan_id=scan_id,tenant_ref=tenant_ref,modality=modality)
            try:
                if not asset.get("integrity_ok"):
                    qualities[modality]="failed"
                    features[modality]=FeatureEnvelope(modality=modality,presence="present",extraction_status="failed",quality=ModalityQuality(modality=modality,status="failed",reason="media_integrity_failed",provenance="wellar-v2-media-storage"),features={"error_code":"media_integrity_failed"},provenance={"extractor":"wellar-v2-media-storage","version":"1.0"}).model_dump(mode="json")
                    event(logger,"media.integrity.failed",logging.ERROR,processing_stage="media_integrity",scan_id=scan_id,tenant_ref=tenant_ref,error_code="media_integrity_failed",modality=modality)
                    continue
                if not asset.get("available"):
                    qualities[modality]="failed"
                    features[modality]=FeatureEnvelope(modality=modality,presence="present",extraction_status="failed",quality=ModalityQuality(modality=modality,status="failed",provenance="wellar-v2-media-storage"),features={"error_code":"stored_media_unavailable"},provenance={"extractor":"wellar-v2-media-storage","version":"1.0"}).model_dump(mode="json")
                    continue
                if modality not in MODALITIES: raise InvalidMediaError("unsupported_media_modality")
                stage_remaining=analysis_deadline-time.monotonic()
                if stage_remaining<=0:raise DecodeTimeoutError("scan analysis deadline exceeded")
                extracted=run_killable(_decode_media,(modality,str(asset["path"]),self.limits),
                    timeout_seconds=min(self.decode_timeout_seconds,stage_remaining),memory_limit_mb=self.decode_memory_limit_mb)
                status=extracted["status"]; timings[modality]=extracted.get("timing_ms",{}); qualities[modality]=status
                envelope=FeatureEnvelope(modality=modality,presence="present",extraction_status=status,quality=ModalityQuality(modality=modality,status=status,coverage=extracted.get("quality",{}).get("overall_coverage"),missingness=extracted.get("quality",{}).get("missingness"),provenance=extracted["provenance"]["extractor"],reason=extracted.get("quality",{}).get("reason")),features=extracted.get("features",{}),provenance=extracted["provenance"])
                features[modality]=envelope.model_dump(mode="json")
                timing=extracted.get("timing_ms",{}); duration_ms=timing.get("total",timing.get("decode_and_features",0))
                event(logger,"media.decode.completed",processing_stage=f"{modality}_decode",scan_id=scan_id,duration_ms=timing.get("decode",timing.get("decode_and_features",duration_ms)),modality=modality,tenant_ref=tenant_ref)
                event(logger,"media.modality.completed",processing_stage=f"{modality}_processing",scan_id=scan_id,duration_ms=duration_ms,modality=modality,tenant_ref=tenant_ref)
            except InvalidMediaError as exc:
                qualities[modality]="invalid_media"
                features[modality]=FeatureEnvelope(modality=modality,presence="present",extraction_status="invalid_media",quality=ModalityQuality(modality=modality,status="invalid_media",provenance="wellar-v2-media-validator-1.0"),features={"error_code":str(exc)},provenance={"extractor":"wellar-v2-media-validator","version":"1.0"}).model_dump(mode="json")
                event(logger,"media.modality.invalid",logging.WARNING,processing_stage=f"{modality}_processing",scan_id=scan_id,error_code=str(exc),duration_ms=round((time.perf_counter()-stage)*1000,3),modality=modality,tenant_ref=tenant_ref)
            except DecodeTimeoutError:
                qualities[modality]="failed"
                features[modality]=FeatureEnvelope(modality=modality,presence="present",extraction_status="failed",quality=ModalityQuality(modality=modality,status="failed",reason="decode_timeout"),features={"error_code":"decode_timeout"},provenance={"extractor":"wellar-v2-isolated-decoder","version":"1.0"}).model_dump(mode="json")
                event(logger,"media.modality.failed",logging.WARNING,processing_stage=f"{modality}_processing",scan_id=scan_id,error_code="decode_timeout",modality=modality,tenant_ref=tenant_ref)
            except DecodeMemoryLimitError:
                qualities[modality]="failed"
                features[modality]=FeatureEnvelope(modality=modality,presence="present",extraction_status="failed",quality=ModalityQuality(modality=modality,status="failed",reason="decode_memory_limit"),features={"error_code":"decode_memory_limit"},provenance={"extractor":"wellar-v2-isolated-decoder","version":"1.0"}).model_dump(mode="json")
                event(logger,"media.modality.failed",logging.WARNING,processing_stage=f"{modality}_processing",scan_id=scan_id,error_code="decode_memory_limit",modality=modality,tenant_ref=tenant_ref)
            except DecodeChildError:
                raise
            except Exception as exc:
                qualities[modality]="failed"
                features[modality]=FeatureEnvelope(modality=modality,presence="present",extraction_status="failed",quality=ModalityQuality(modality=modality,status="failed",provenance="wellar-v2-media-extractor-1.0"),features={"error_code":"extraction_failed"},provenance={"extractor":"wellar-v2-media-extractor","version":"1.0"}).model_dump(mode="json")
                event(logger,"media.modality.failed",logging.ERROR,processing_stage=f"{modality}_processing",scan_id=scan_id,error_code="extraction_failed",exception_type=type(exc).__name__,duration_ms=round((time.perf_counter()-stage)*1000,3),modality=modality,tenant_ref=tenant_ref)
            finally:
                release=getattr(self.media_store,"release",None)
                if release: release(asset)
        for modality in MODALITIES:
            if modality not in features:
                features[modality]=FeatureEnvelope(modality=modality,presence="missing",extraction_status="not_provided",quality=ModalityQuality(modality=modality,status="not_provided"),features={},provenance={}).model_dump(mode="json")
        subjective_reference=None
        if payload.get("subjective_fatigue") and payload["subjective_fatigue"].get("collection_consent") is True:
            response=payload["subjective_fatigue"]
            # Deliberately described as a custom self-report, not a validated scale.
            subjective_reference={"schema_version":response["schema_version"],"instrument":response["instrument"],"score":response["score"],"question_version":response.get("question_version","legacy_prompt_unverified"),"collection_consent":True,"status":"self_reported_unvalidated_reference","model_target":False,"training_consent":False}
        reaction_task=payload.get("reaction_task")
        measurements=dual_measurement_output(reaction_task,legacy_reference=subjective_reference)
        if reaction_task:
            task=reaction_task
            vigilance=measurements["vigilance"]
            raw_reaction=dict(vigilance["value"] or {})
            # Retain the valid RT vector in the legacy envelope too: downstream
            # consumers may use it, and the scalar summaries must remain auditable.
            reaction={key:value for key,value in raw_reaction.items()
                if value is None or isinstance(value,(str,int,float,bool)) or key in {"timing_metadata","valid_reaction_times_ms"}}
            valid=reaction.get("valid_reaction_times_ms",[])
            trial_count=reaction.get("submitted_trial_count",0)
            consistency=reaction.get("valid_trial_fraction",0.0)
            responses=trial_count-reaction.get("missed_response_count",0)
            reaction.update({
                "protocol":"brief_timestamped_simple_reaction_task",
                "task_type":task["task_type"],
                "trial_count":trial_count,
                "response_count":responses,
                "omission_count":reaction.get("missed_response_count",0),
                "plausible_response_count":len(valid),
                "median_client_reported_rt_ms":reaction.get("median_valid_rt_ms"),
                "p90_client_reported_rt_ms":reaction.get("p90_valid_rt_ms"),
                "slow_response_count_ge_500ms":sum(ms>=500 for ms in valid),
                "timing_consistency_fraction":consistency,
                "pvt_equivalence":"not_established",
                "fatigue_inference":"not_performed",
            })
            status="available" if len(valid)>=3 and consistency>=.8 else "insufficient_quality"; qualities["reaction_task"]=status
            features["reaction_task"]=FeatureEnvelope(modality="reaction_task",presence="present",extraction_status=status,quality=ModalityQuality(modality="reaction_task",status=status,coverage=consistency,missingness=1-consistency,provenance="client_report_unverified",reason=None if status=="available" else "insufficient_valid_trials_or_timing_integrity"),features=reaction,provenance={"extractor":"wellar-reaction-task-integrity-check","version":"1.1"}).model_dump(mode="json")
            event(logger,"reaction_task.validated",processing_stage="reaction_task",scan_id=scan_id,tenant_ref=tenant_ref,error_code="client_report_unverified",modality="reaction_task")
        else:
            features["reaction_task"]=FeatureEnvelope(modality="reaction_task",presence="missing",extraction_status="not_provided",quality=ModalityQuality(modality="reaction_task",status="not_provided"),features={"missing":True},provenance={}).model_dump(mode="json")
        prediction_extension=self._predict(features)
        video=features.get("video",{}); video_metrics=video.get("features",{}) if isinstance(video,dict) else {}
        video_quality=video.get("quality",{}) if isinstance(video,dict) else {}
        indicator_metrics={
            **video_metrics,
            "video_status":video.get("extraction_status","not_provided"),
            "video_reason":video_quality.get("reason") or video_metrics.get("error_code"),
            "mean_luminance":video_metrics.get("mean_luminance",video_metrics.get("mean_sampled_luminance")),
            "frame_count":video_metrics.get("observation_count",video_metrics.get("frame_count_decoded",0)),
            "temporal_coverage":video_metrics.get("overall_coverage",video_metrics.get("temporal_coverage",0)),
        }
        history=[]
        history_reader=getattr(getattr(self.media_store,"queue_store",None),"get_indicator_history",None)
        if callable(history_reader) and context.get("subject_id"):
            try: history=history_reader(context.get("tenant_id",""),context["subject_id"])
            except Exception: history=[]
        rule_indicator=evaluate_rule_indicators(indicator_metrics,history=history)
        rule_indicator["disclaimer"]="Estimated wellness indicator, not medical advice. This rule-based heuristic has not been validated for individual or occupational decisions."
        behavioral_evidence=build_behavioral_evidence(features,measurements)
        modality_evidence={}
        for name in MODALITIES:
            item=features[name]
            status=item["extraction_status"]
            if status=="invalid_media": status="failed"
            elif status=="not_provided": status="missing"
            modality_evidence[name]={"status":"missing" if item["presence"]=="missing" else status,"quality":item["quality"],"features":item["features"],"provenance":item["provenance"],"timing_ms":timings.get(name,{}),"target_claim":"descriptive_only"}
        reaction_evidence=features["reaction_task"]
        attention_evidence={"status":"missing" if reaction_evidence["presence"]=="missing" else reaction_evidence["extraction_status"],"quality":reaction_evidence["quality"],"features":reaction_evidence["features"],"provenance":reaction_evidence["provenance"],"target_claim":"measured_task_performance"}
        self_report_evidence=None if subjective_reference is None else {"status":"available","quality":{"collection_consent":True},"features":{"score":subjective_reference["score"]},"provenance":{"instrument":subjective_reference["instrument"],"schema_version":subjective_reference["schema_version"]},"target_claim":"self_report_only"}
        model_evidence=[]
        absent=[name for name,item in modality_evidence.items() if item["status"] in {"missing","not_provided"}]
        if attention_evidence["status"]=="missing": absent.append("attention")
        if self_report_evidence is None: absent.append("subjective_reference")
        abstentions=sorted({target.abstention_reason for target in prediction_extension.targets.values() if target.abstention_reason})
        face_model_used=any(item["status"] not in {"missing","not_provided","failed"} and item["provenance"].get("extractor","").startswith(("opencv_image_quality_mediapipe_face_mesh_visibility","opencv_decode_mediapipe_face_mesh_eye_mouth_geometry")) for item in modality_evidence.values())
        if face_model_used:
            model_evidence.append({"name":"MediaPipe Face Mesh","revision":"mediapipe-0.10.21","runtime":"mediapipe.solutions.face_mesh.FaceMesh; CPU XNNPACK","weights":["face_detection_short_range.tflite","face_landmark_with_attention.tflite"],"task":"face landmarks and normalized eye/mouth geometry only","license_id":"Apache-2.0 upstream MediaPipe repository","license_scope_note":"Installed distribution bundles these assets; no asset-specific license metadata was present in the wheel."})
        # Stage 1 ends at descriptive extraction. Do not turn eye/audio features
        # into fatigue states, risk labels, or an indicator score here.
        drowsiness_indicator=rule_indicator
        evidence={"schema_version":"1.2","scan_id":scan_id,"modalities":modality_evidence,"attention":attention_evidence,"subjective_reference":self_report_evidence,"missing_modalities":absent,"models":model_evidence,"research_model_compatibility":stage1_model_abstentions(features),"fusion":{"status":"inactive_no_validated_release","model_revision":None,"calibration_status":"not_calibrated"},"abstention_reasons":abstentions,"drowsiness_indicator":drowsiness_indicator}
        # Legacy V2 fields remain null-only; the versioned extension is additive.
        # Scientific result status remains insufficient_evidence until a guarded,
        # independently validated model is active and its result contract evolves.
        analysis_status=rule_indicator["analysis_status"]
        event(logger,"model.invocation.completed",processing_stage="model_invocation",scan_id=scan_id,tenant_ref=tenant_ref,error_code=prediction_extension.inference_status)
        messages={
            "disabled":"Media processing completed; model inference is disabled, so no prediction was performed.",
            "available":"Media processing completed; target predictions are available in prediction_extension, but this contract does not provide a readiness category.",
            "partial":"Media processing completed; predictions are available for only some targets, while other targets are unavailable or abstained.",
            "abstained":"Media processing completed; inference did not produce usable target predictions.",
        }
        return AnalysisResult(status="insufficient_evidence",scan_id=scan_id,targets={"reference_sleepiness":TargetOutput(),"vigilance_impairment":TargetOutput()},measurements=measurements,behavioral_evidence=behavioral_evidence,prediction_extension=prediction_extension,subjective_reference=subjective_reference,evidence=evidence,drowsiness_indicator=drowsiness_indicator,analysis_status=rule_indicator["analysis_status"],prediction=rule_indicator["prediction"],confidence_level=rule_indicator["confidence_level"],risk_level=rule_indicator["risk_level"],engine_type=rule_indicator["engine_type"],contributing_indicators=rule_indicator["contributing_indicators"],insufficient_evidence_reason=rule_indicator["insufficient_evidence_reason"],features=features,quality=qualities,message=messages[prediction_extension.inference_status]).model_dump(mode="json")
