from __future__ import annotations

import logging
import math
import statistics
import threading
import hashlib
import time

from app.core.logging import event
from app.drowsiness_indicator import classify as classify_drowsiness, load_thresholds, personal_closure_deviation, fresh_baseline_values
from app.media.contracts import AnalysisResult,FeatureEnvelope,ModalityQuality,PredictionExtension,TargetOutput,VersionedTargetPrediction
from app.media.extractors import InvalidMediaError
from app.media.isolated_decode import DecodeChildError,DecodeMemoryLimitError,DecodeTimeoutError,_decode_media,run_killable
from app.media.reconciliation import delete_with_retry
from app.ml.feature_mapping import map_features
from app.ml.registry import load_predictors
from app.ml.runtime import TARGETS, DisabledPredictor, PredictionRequest

logger=logging.getLogger("wellar.media.analysis")
MODALITIES=("video","image","audio")


class MultimodalFeatureProcessor:
    """Worker-only extractors and fail-closed invocation of approved predictors."""
    def __init__(self,media_store,*,max_video_seconds:int,max_audio_seconds:int,max_image_pixels:int,max_video_frames:int,video_sample_hz:float,decode_timeout_seconds:float=12.0,decode_memory_limit_mb:int=768,analysis_timeout_seconds:float=40.0,predictors=None,research_image_classifier=None):
        self.media_store=media_store
        self.limits={"max_video_seconds":max_video_seconds,"max_audio_seconds":max_audio_seconds,"max_image_pixels":max_image_pixels,"max_video_frames":max_video_frames,"video_sample_hz":video_sample_hz}
        self.decode_timeout_seconds=decode_timeout_seconds;self.decode_memory_limit_mb=decode_memory_limit_mb
        self.analysis_timeout_seconds=analysis_timeout_seconds
        self.predictors=predictors if predictors is not None else load_predictors(enabled=False)
        self.research_image_classifier=research_image_classifier
        self._face_meshes={}; self._model_lock=threading.RLock()

    def _predict(self, envelopes):
        target_results={}
        for target in sorted(TARGETS):
            predictor=self.predictors.get(target,DisabledPredictor())
            try:
                feature_names=tuple(getattr(predictor,"feature_names",()))
                if not feature_names:
                    response=predictor.predict(PredictionRequest(target,{}, {},1.0))
                else:
                    mapped,statuses,missingness=map_features(envelopes,feature_names)
                    response=predictor.predict(PredictionRequest(target,mapped,statuses,missingness))
                result_status=response.status
                reason=response.reason
                quality_status="passed" if result_status=="available" else ("failed" if reason=="quality_failure" else "not_evaluated" if result_status=="unavailable" else "passed")
                target_results[target]=VersionedTargetPrediction(
                    status=result_status,
                    value=response.value if result_status=="available" else None,
                    confidence=response.probability if result_status=="available" else None,
                    confidence_calibrated=response.calibrated if result_status=="available" else False,
                    model_version=response.model_version,
                    quality_status=quality_status,
                    abstention_reason=reason if result_status!="available" else None,
                )
            except Exception:
                # Never surface feature values, artifact contents, paths, or backend errors.
                target_results[target]=VersionedTargetPrediction(
                    status="abstained",quality_status="not_evaluated",abstention_reason="inference_failed")
        statuses={item.status for item in target_results.values()}
        if statuses=={"unavailable"}:
            invocation_status="disabled"
        elif statuses=={"available"}:
            invocation_status="available"
        elif "available" in statuses:
            invocation_status="partial"
        else:
            invocation_status="abstained"
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
        close=getattr(self.research_image_classifier,"close",None)
        if close: close()

    def __call__(self,payload:dict)->dict:
        context=payload.get("_worker_context",{})
        scan_id=context.get("scan_id","unknown")
        tenant_ref=hashlib.sha256(context.get("tenant_id","").encode()).hexdigest()[:16]
        features={}; timings={}; research_image_results=[]; qualities={m:"not_provided" for m in (*MODALITIES,"reaction_task")}
        refs=payload.get("media_refs",[])
        analysis_deadline=time.monotonic()+self.analysis_timeout_seconds
        manifest={item["media_ref"]:item["modality"] for item in payload.get("_media_manifest",[]) if isinstance(item,dict) and "media_ref" in item and "modality" in item}
        for ref in refs:
            stage=time.perf_counter()
            try:
                asset=self.media_store.resolve(ref,context.get("tenant_id",""),context.get("subject_id",""),verify_integrity=True)
            except Exception as exc:
                # Resolve may fail after a temporary object download or metadata read.
                # Attempt deletion even when no asset descriptor was returned.
                try:
                    delete_media=getattr(self.media_store,"delete",None)
                    if callable(delete_media) and not delete_with_retry(self.media_store,ref,context.get("tenant_id",""),context.get("subject_id","")):
                        raise RuntimeError("media deletion did not confirm removal")
                except Exception as delete_exc:
                    from app.observability.metrics import worker_metrics
                    worker_metrics.increment("storage","deletion_failed")
                    event(logger,"media.delete.failed",logging.ERROR,scan_id=scan_id,tenant_ref=tenant_ref,processing_stage="media_deletion",error_code="media_delete_failed",exception_type=type(delete_exc).__name__)
                    raise RuntimeError("scan media deletion failed") from None
                event(logger,"media.resolve.failed",logging.ERROR,scan_id=scan_id,tenant_ref=tenant_ref,processing_stage="media_resolve",error_code="media_resolve_failed",exception_type=type(exc).__name__)
                raise
            if asset is None:
                event(logger,"media.resolve.failed",logging.WARNING,scan_id=scan_id,tenant_ref=tenant_ref,processing_stage="media_resolve",error_code="media_reference_unavailable")
                lost_modality=manifest.get(ref)
                if lost_modality in MODALITIES:
                    qualities[lost_modality]="failed"
                    features[lost_modality]=FeatureEnvelope(modality=lost_modality,presence="present",extraction_status="failed",quality=ModalityQuality(modality=lost_modality,status="failed",reason="media_reference_unavailable"),features={"error_code":"media_reference_unavailable"},provenance={"extractor":"wellar-v2-media-storage","version":"1.0"}).model_dump(mode="json")
                else: qualities["media_reference"]="failed"
                try:
                    delete_media=getattr(self.media_store,"delete",None)
                    if callable(delete_media) and not delete_with_retry(self.media_store,ref,context.get("tenant_id",""),context.get("subject_id","")):
                        raise RuntimeError("media deletion did not confirm removal")
                except Exception as exc:
                    from app.observability.metrics import worker_metrics
                    worker_metrics.increment("storage","deletion_failed")
                    event(logger,"media.delete.failed",logging.ERROR,scan_id=scan_id,tenant_ref=tenant_ref,processing_stage="media_deletion",error_code="media_delete_failed",exception_type=type(exc).__name__)
                    raise RuntimeError("scan media deletion failed") from None
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
                if modality=="image" and self.research_image_classifier is not None:
                    if status=="available" and extracted.get("features",{}).get("face_visible") is True:
                        try:
                            research_image_results.append(self.research_image_classifier.predict(asset["path"]))
                        except Exception:
                            research_image_results.append({"schema_version":"wellar-research-image-drowsiness/1.0","status":"abstained","reason":"inference_failed","production_prediction":False})
                    else:
                        research_image_results.append({"schema_version":"wellar-research-image-drowsiness/1.0","status":"abstained","reason":"image_quality_failure","production_prediction":False})
                envelope=FeatureEnvelope(modality=modality,presence="present",extraction_status=status,quality=ModalityQuality(modality=modality,status=status,coverage=extracted.get("quality",{}).get("overall_coverage"),missingness=extracted.get("quality",{}).get("missingness"),provenance=extracted["provenance"]["extractor"],reason=extracted.get("quality",{}).get("reason")),features=extracted.get("features",{}),provenance=extracted["provenance"])
                features[modality]=envelope.model_dump(mode="json")
                timing=extracted.get("timing_ms",{}); duration_ms=timing.get("total",timing.get("decode_and_features",0))
                event(logger,"media.decode.completed",processing_stage=f"{modality}_decode",scan_id=scan_id,duration_ms=timing.get("decode",timing.get("decode_and_features",duration_ms)),modality=modality,tenant_ref=tenant_ref)
                event(logger,"media.modality.completed",processing_stage=f"{modality}_processing",scan_id=scan_id,duration_ms=duration_ms,modality=modality,tenant_ref=tenant_ref)
            except InvalidMediaError as exc:
                qualities[modality]="invalid_media"
                features[modality]=FeatureEnvelope(modality=modality,presence="present",extraction_status="invalid_media",quality=ModalityQuality(modality=modality,status="invalid_media",provenance="wellar-v2-media-validator-1.0"),features={"error_code":str(exc)},provenance={"extractor":"wellar-v2-media-validator","version":"1.0"}).model_dump(mode="json")
                event(logger,"media.modality.invalid",logging.WARNING,processing_stage=f"{modality}_processing",scan_id=scan_id,error_code=str(exc),duration_ms=round((time.perf_counter()-stage)*1000,3),modality=modality,tenant_ref=tenant_ref)
            except (DecodeTimeoutError,DecodeMemoryLimitError,DecodeChildError):
                raise
            except Exception as exc:
                qualities[modality]="failed"
                features[modality]=FeatureEnvelope(modality=modality,presence="present",extraction_status="failed",quality=ModalityQuality(modality=modality,status="failed",provenance="wellar-v2-media-extractor-1.0"),features={"error_code":"extraction_failed"},provenance={"extractor":"wellar-v2-media-extractor","version":"1.0"}).model_dump(mode="json")
                event(logger,"media.modality.failed",logging.ERROR,processing_stage=f"{modality}_processing",scan_id=scan_id,error_code="extraction_failed",exception_type=type(exc).__name__,duration_ms=round((time.perf_counter()-stage)*1000,3),modality=modality,tenant_ref=tenant_ref)
            finally:
                release=getattr(self.media_store,"release",None)
                if release: release(asset)
                try:
                    delete_media=getattr(self.media_store,"delete",None)
                    if callable(delete_media) and not delete_with_retry(self.media_store,ref,context.get("tenant_id",""),context.get("subject_id","")):
                        raise RuntimeError("media deletion did not confirm removal")
                except Exception as exc:
                    from app.observability.metrics import worker_metrics
                    worker_metrics.increment("storage","deletion_failed")
                    event(logger,"media.delete.failed",logging.ERROR,scan_id=scan_id,tenant_ref=tenant_ref,processing_stage="media_deletion",error_code="media_delete_failed",exception_type=type(exc).__name__,modality=modality)
                    raise RuntimeError("scan media deletion failed") from None
        for modality in MODALITIES:
            if modality not in features:
                features[modality]=FeatureEnvelope(modality=modality,presence="missing",extraction_status="not_provided",quality=ModalityQuality(modality=modality,status="not_provided"),features={},provenance={}).model_dump(mode="json")
        subjective_reference=None
        if payload.get("subjective_fatigue") and payload["subjective_fatigue"].get("collection_consent") is True:
            response=payload["subjective_fatigue"]
            # Deliberately described as a custom self-report, not a validated scale.
            subjective_reference={"schema_version":response["schema_version"],"instrument":response["instrument"],"score":response["score"],"status":"self_reported_unvalidated_reference","collection_consent":True,"model_target":False,"training_consent":False}
        if payload.get("reaction_task"):
            task=payload["reaction_task"]; trials=task["trials"]
            responses=[t for t in trials if t.get("response_timestamp_ms") is not None]
            false_starts=[t for t in responses if t["response_timestamp_ms"]<=t["stimulus_timestamp_ms"]]
            latencies=[t["response_timestamp_ms"]-t["stimulus_timestamp_ms"] for t in responses if t["response_timestamp_ms"]>t["stimulus_timestamp_ms"]]
            plausible=[ms for ms in latencies if 80<=ms<=5000]
            omissions=len(trials)-len(responses); consistency=len(plausible)/len(trials) if trials else 0.0
            ordered=sorted(plausible)
            p90=ordered[min(len(ordered)-1,math.ceil(.9*len(ordered))-1)] if ordered else None
            reaction={"protocol":"brief_timestamped_simple_reaction_task","task_type":task["task_type"],"trial_count":len(trials),"response_count":len(responses),"omission_count":omissions,"false_start_count":len(false_starts),"plausible_response_count":len(plausible),"median_client_reported_rt_ms":statistics.median(plausible) if plausible else None,"p90_client_reported_rt_ms":p90,"slow_response_count_ge_500ms":sum(ms>=500 for ms in plausible),"timing_consistency_fraction":consistency,"timing_metadata":task.get("timing_metadata",{}),"integrity_status":"unverified_client_report","server_verified":False,"pvt_equivalence":"not_established","fatigue_inference":"not_performed"}
            status="available" if len(plausible)>=3 and consistency>=.8 else "insufficient_quality"; qualities["reaction_task"]=status
            features["reaction_task"]=FeatureEnvelope(modality="reaction_task",presence="present",extraction_status=status,quality=ModalityQuality(modality="reaction_task",status=status,coverage=consistency,missingness=1-consistency,provenance="client_report_unverified"),features=reaction,provenance={"extractor":"wellar-reaction-task-integrity-check","version":"1.0"}).model_dump(mode="json")
            event(logger,"reaction_task.validated",processing_stage="reaction_task",scan_id=scan_id,tenant_ref=tenant_ref,error_code="client_report_unverified",modality="reaction_task")
        else:
            features["reaction_task"]=FeatureEnvelope(modality="reaction_task",presence="missing",extraction_status="not_provided",quality=ModalityQuality(modality="reaction_task",status="not_provided"),features={"missing":True},provenance={}).model_dump(mode="json")
        prediction_extension=self._predict(features)
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
        for predictor in self.predictors.values():
            manifest=getattr(predictor,"manifest",None)
            if manifest:
                labels=manifest.get("label_mapping")
                if not isinstance(labels,list) or any(not isinstance(label,str) for label in labels): labels=None
                model_evidence.append({"name":manifest.get("target","unknown"),"revision":manifest.get("model_version"),"sha256":manifest.get("artifact_sha256"),"license":manifest.get("license_id"),"preprocessing":manifest.get("preprocessing_id"),"labels":labels})
        if research_image_results:
            model_evidence.append(self.research_image_classifier.provenance())
        absent=[name for name,item in modality_evidence.items() if item["status"] in {"missing","not_provided"}]
        if attention_evidence["status"]=="missing": absent.append("attention")
        if self_report_evidence is None: absent.append("subjective_reference")
        abstentions=sorted({target.abstention_reason for target in prediction_extension.targets.values() if target.abstention_reason})
        face_model_used=any(item["status"] not in {"missing","not_provided","failed"} and item["provenance"].get("extractor","").startswith(("opencv_image_quality_mediapipe_face_mesh_visibility","opencv_decode_mediapipe_face_mesh_eye_mouth_geometry")) for item in modality_evidence.values())
        if face_model_used:
            model_evidence.append({"name":"MediaPipe Face Mesh","revision":"mediapipe-0.10.21","runtime":"mediapipe.solutions.face_mesh.FaceMesh; CPU XNNPACK","weights":["face_detection_short_range.tflite","face_landmark_with_attention.tflite"],"task":"face landmarks and normalized eye/mouth geometry only","license_id":"Apache-2.0 upstream MediaPipe repository","license_scope_note":"Installed distribution bundles these assets; no asset-specific license metadata was present in the wheel."})
        video_features=features.get("video",{}).get("features",{})
        image_features=features.get("image",{}).get("features",{})
        face_features=video_features if video_features else image_features
        indicator_measurements={
            "short_clip_eye_closure_fraction":video_features.get("short_clip_eye_closure_fraction"),
            "blink_rate_per_minute":video_features.get("blink_rate_per_minute"),
            "mean_eye_closure_seconds":video_features.get("closure_duration_mean_seconds"),
            "max_eye_closure_seconds":video_features.get("closure_duration_max_seconds"),
            "face_detection_ratio":video_features.get("face_coverage",face_features.get("face_detection_ratio")),
            "mean_luminance":video_features.get("mean_sampled_luminance",face_features.get("mean_luminance")),
            "mean_blur_variance":video_features.get("mean_blur_variance",face_features.get("mean_blur_variance")),
            "temporal_coverage":video_features.get("short_eye_time_coverage"),
            "yaw_proxy":video_features.get("yaw_proxy"),
            "pitch_proxy":video_features.get("pitch_proxy"),
        }
        audio_status=qualities.get("audio") if features.get("audio",{}).get("presence")=="present" else None
        indicator_thresholds=load_thresholds()
        history=[]
        history_reader=getattr(getattr(self.media_store,"queue_store",None),"get_indicator_history",None)
        if callable(history_reader) and context.get("tenant_id") and context.get("subject_id"):
            try: history=history_reader(context["tenant_id"],context["subject_id"])
            except Exception: history=[]
        min_baseline_scans=indicator_thresholds.get("baseline_minimum_prior_scans",5)
        fresh_values,baseline_reason=fresh_baseline_values(history,int(indicator_thresholds["baseline_freshness_window_days"]))
        baseline_z,prior_scan_count=personal_closure_deviation(indicator_measurements["short_clip_eye_closure_fraction"],fresh_values,min_baseline_scans)
        drowsiness_indicator=classify_drowsiness(indicator_measurements,indicator_thresholds,audio_quality_status=audio_status,baseline_z_score=baseline_z,minimum_prior_scans=min_baseline_scans,prior_scans=prior_scan_count)
        if "personal_baseline" not in drowsiness_indicator:
            drowsiness_indicator["baseline_reason_code"]=baseline_reason or "baseline_not_ready"
        evidence={"schema_version":"1.2","scan_id":scan_id,"modalities":modality_evidence,"attention":attention_evidence,"subjective_reference":self_report_evidence,"missing_modalities":absent,"models":model_evidence,"fusion":{"status":"inactive_no_validated_release","model_revision":None,"calibration_status":"not_calibrated"},"abstention_reasons":abstentions,"drowsiness_indicator":drowsiness_indicator}
        if research_image_results:
            evidence["research_model_inference"]={"scope":"non_production_research_only","results":research_image_results,"target_claim":"unvalidated_still_image_driver_drowsiness_not_a_wellar_employee_fatigue_focus_or_risk_prediction"}
        # Legacy V2 fields remain null-only; the versioned extension is additive.
        # Scientific result status remains insufficient_evidence until a guarded,
        # independently validated model is active and its result contract evolves.
        analysis_status="insufficient_evidence"
        event(logger,"model.invocation.completed",processing_stage="model_invocation",scan_id=scan_id,tenant_ref=tenant_ref,error_code=prediction_extension.inference_status)
        return AnalysisResult(status=analysis_status,scan_id=scan_id,targets={"reference_sleepiness":TargetOutput(),"vigilance_impairment":TargetOutput()},prediction_extension=prediction_extension,subjective_reference=subjective_reference,evidence=evidence,drowsiness_indicator=drowsiness_indicator,features=features,quality=qualities,message="Feature extraction completed; drowsiness indicator is indicative and non-diagnostic.").model_dump(mode="json")
