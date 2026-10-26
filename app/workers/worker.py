from __future__ import annotations

import logging
import random
import threading
import time
import hashlib
import re
from typing import Callable
from opentelemetry import context,propagate,trace

from app.core.logging import event, request_id_var,trace_id_var
from app.observability.metrics import worker_metrics
from app.drowsiness_indicator import classify as classify_drowsiness
from app.measurements.dual_output import dual_measurement_output
from app.behavioral.engine import build_behavioral_evidence
from app.media.contracts import PredictionExtension, VersionedTargetPrediction
from app.ml.runtime import TARGETS
from app.media.reconciliation import MediaReconciler,delete_with_retry

logger=logging.getLogger("wellar.worker")


def research_only_processor(payload: dict) -> dict:
    """Default safe stub: no media/model inference and no fatigue score."""
    report = payload.get("subjective_fatigue") if isinstance(payload, dict) else None
    legacy_reference = None
    if isinstance(report, dict) and report.get("collection_consent") is True:
        legacy_reference = {
            "score": report.get("score"),
            "instrument": report.get("instrument", "wellar_custom_fatigue_rating_0_10"),
            "question_version": report.get("question_version", "legacy_prompt_unverified"),
            "collection_consent": True,
        }
    measurements=dual_measurement_output(None, legacy_reference=legacy_reference)
    prediction_extension=PredictionExtension(inference_status="disabled",targets={
        target:VersionedTargetPrediction(status="unavailable",quality_status="not_evaluated",
            abstention_reason="prediction_not_enabled") for target in sorted(TARGETS)
    })
    return {
        "status": "insufficient_evidence",
        "model_version": None,
        "prediction_extension": prediction_extension.model_dump(mode="json"),
        "targets": {
            "reference_sleepiness": {"status":"unavailable","value":None},
            "vigilance_impairment": {"status":"unavailable","value":None},
        },
        "measurements": measurements,
        "behavioral_evidence": build_behavioral_evidence({}, measurements),
        "drowsiness_indicator": classify_drowsiness({}),
        "quality": {"video":"not_processed","image":"not_processed","audio":"not_processed","reaction_task":"not_provided"},
        "message": "Media processing completed; model inference is disabled, so no prediction was performed.",
    }


class Worker:
    def __init__(self, store, worker_id: str, processor: Callable[[dict],dict]=research_only_processor, *, directus_publisher=None, reconciliation_page_size: int=50, reconciliation_max_pages: int=5, reconciliation_max_references: int=500, reconciliation_lease_seconds: int=300, reconciliation_delete_attempts:int=3,reconciliation_delete_retry_deadline_seconds:float=16.0,reconciliation_retry_initial_seconds:float=1.0,reconciliation_retry_multiplier:float=2.0,reconciliation_retry_max_seconds:float=300.0,reconciliation_retry_jitter:float=0.2,reconciliation_stop_event=None):
        self.store,self.worker_id,self.processor=store,worker_id,processor
        self.directus_publisher=directus_publisher
        media_store=getattr(processor,"media_store",None)
        self.reconciler=MediaReconciler(media_store,store,page_size=reconciliation_page_size,max_pages_per_run=reconciliation_max_pages,max_references_per_run=reconciliation_max_references,lease_seconds=reconciliation_lease_seconds,delete_attempts=reconciliation_delete_attempts,delete_retry_deadline_seconds=reconciliation_delete_retry_deadline_seconds) if media_store is not None and callable(getattr(store,"list_terminal_scan_media_refs",None)) else None
        self._last_reconciliation=0.0
        self._reconciliation_interval_seconds=300.0
        self._reconciliation_retry_initial=max(0.1,float(reconciliation_retry_initial_seconds))
        self._reconciliation_retry_multiplier=max(1.1,float(reconciliation_retry_multiplier))
        self._reconciliation_retry_max=max(self._reconciliation_retry_initial,float(reconciliation_retry_max_seconds))
        self._reconciliation_retry_jitter=max(0.0,min(float(reconciliation_retry_jitter),0.5))
        self._reconciliation_failures=0
        self._next_reconciliation_at=0.0
        self._last_directus_reconciliation=0.0
        self._directus_reconciliation_failures=0
        self._next_directus_reconciliation_at=0.0
        if self.reconciler is not None:
            self._reconcile_media(stop_event=reconciliation_stop_event)
        self._reconcile_directus_deletions()

    def _reconcile_media(self,stop_event=None):
        try:
            result=self.reconciler.run_once(stop_event=stop_event) if stop_event is not None else self.reconciler.run_once()
        except Exception as exc:
            self._reconciliation_failures+=1
            delay=min(self._reconciliation_retry_max,self._reconciliation_retry_initial*(self._reconciliation_retry_multiplier**(self._reconciliation_failures-1)))
            delay=min(self._reconciliation_retry_max,delay*random.uniform(1-self._reconciliation_retry_jitter,1+self._reconciliation_retry_jitter))
            self._next_reconciliation_at=time.monotonic()+delay
            worker_metrics.increment("storage","reconciliation_failed")
            event(logger,"media.reconciliation.failed",logging.ERROR,processing_stage="media_reconciliation",error_code="reconciliation_run_failed",exception_type=type(exc).__name__,retry_delay_seconds=round(delay,3))
            return {"run_status":"failed","retry_delay_seconds":delay}
        self._last_reconciliation=time.monotonic()
        status=result.get("run_status","complete") if isinstance(result,dict) else "complete"
        per_item_failures=int(result.get("deletion_failed",0)) if isinstance(result,dict) else 0
        if status=="partial" and per_item_failures>0:
            self._reconciliation_failures+=1
            delay=min(self._reconciliation_retry_max,self._reconciliation_retry_initial*(self._reconciliation_retry_multiplier**(self._reconciliation_failures-1)))
            delay=min(self._reconciliation_retry_max,delay*random.uniform(1-self._reconciliation_retry_jitter,1+self._reconciliation_retry_jitter))
            self._next_reconciliation_at=self._last_reconciliation+delay
        elif status=="partial" or status=="lease_busy":
            # Partial checkpoint work and another owner's lease are distinct from
            # a failed run; retry after a bounded scheduler delay without hot-looping.
            if status=="partial":self._reconciliation_failures=0
            if status=="lease_busy":
                event(logger,"media.reconciliation.lease_busy",logging.WARNING,processing_stage="media_reconciliation",error_code="reconciliation_lease_busy")
            delay=random.uniform(self._reconciliation_retry_initial,
                min(self._reconciliation_retry_max,self._reconciliation_retry_initial*(1+self._reconciliation_retry_jitter)))
            self._next_reconciliation_at=self._last_reconciliation+delay
        else:
            self._reconciliation_failures=0
            self._next_reconciliation_at=self._last_reconciliation+self._reconciliation_interval_seconds
        return result

    def _reconcile_directus_deletions(self):
        reconcile=getattr(self.processor,"reconcile_pending_deletions",None)
        if not callable(reconcile):
            self._next_directus_reconciliation_at=time.monotonic()+self._reconciliation_interval_seconds
            return
        try:
            reconcile()
            self._last_directus_reconciliation=time.monotonic()
            self._directus_reconciliation_failures=0
            self._next_directus_reconciliation_at=self._last_directus_reconciliation+self._reconciliation_interval_seconds
        except Exception as exc:
            self._directus_reconciliation_failures+=1
            delay=min(self._reconciliation_retry_max,self._reconciliation_retry_initial*(self._reconciliation_retry_multiplier**(self._directus_reconciliation_failures-1)))
            delay=min(self._reconciliation_retry_max,delay*random.uniform(1-self._reconciliation_retry_jitter,1+self._reconciliation_retry_jitter))
            self._next_directus_reconciliation_at=time.monotonic()+delay
            worker_metrics.increment("storage","directus_deletion_failed")
            event(logger,"directus.deletion.reconciliation_failed",logging.ERROR,processing_stage="directus_cleanup",error_code="directus_deletion_failed",exception_type=type(exc).__name__,retry_delay_seconds=round(delay,3))

    def _delete_payload_media(self,payload,job):
        media_store=getattr(self.processor,"media_store",None)
        if media_store is None:return
        refs=payload.get("media_refs",[]) if isinstance(payload,dict) else []
        if not isinstance(refs,list):
            refs=[]
        for reference in refs:
            if isinstance(reference,str) and reference.startswith("media://"):
                delete_with_retry(media_store,reference,job.get("tenant_id",""),job.get("subject_id",""))

    def run_once(self) -> bool:
        job=self.store.claim(self.worker_id)
        if not job:
            self._publish_directus_once()
            return False
        request_token=request_id_var.set(job.get("request_id","-"))
        try:
            payload=__import__("json").loads(job["payload_json"]);payload_parse_error=False
        except Exception:
            payload={};payload_parse_error=True
        if not isinstance(payload,dict):
            payload={};payload_parse_error=True
        parent_traceparent=payload.pop("_traceparent","")
        parent=propagate.extract({"traceparent":parent_traceparent})
        # The opaque W3C trace ID is logged across the durable queue even without an SDK exporter.
        trace_match=re.fullmatch(r"[0-9a-fA-F]{2}-([0-9a-fA-F]{32})-[0-9a-fA-F]{16}-[0-9a-fA-F]{2}",parent_traceparent)
        trace_token=trace_id_var.set(trace_match.group(1).lower() if trace_match and int(trace_match.group(1),16)!=0 else "-")
        attached=context.attach(parent)
        span=trace.get_tracer("wellar.worker").start_span("scan.process")
        span.set_attribute("wellar.scan.id",job["scan_id"]);span.set_attribute("wellar.job.id",job["job_id"]);span.set_attribute("wellar.worker.id",self.worker_id)
        span_scope=trace.use_span(span,end_on_exit=True);span_scope.__enter__()
        tenant_ref=hashlib.sha256(job.get("tenant_id","").encode()).hexdigest()[:16]
        start=time.perf_counter()
        wall_queue_age=max(0,time.time()-job["created_at"])
        accepted_monotonic=payload.get("_accepted_monotonic")
        queue_wait_clock="wall_clock"
        queue_wait_seconds=wall_queue_age
        if getattr(self.store,"shared_monotonic_clock",False) and type(accepted_monotonic) in (int,float):
            monotonic_age=time.perf_counter()-float(accepted_monotonic)
            if monotonic_age>=0 and abs(monotonic_age-wall_queue_age)<=max(1.0,wall_queue_age*0.05):
                queue_wait_seconds=monotonic_age
                queue_wait_clock="monotonic"
        event(logger,"scan.processing.stage_completed",processing_stage="queue_wait",scan_id=job["scan_id"],job_id=job["job_id"],worker_id=self.worker_id,tenant_ref=tenant_ref,duration_ms=round(max(0,queue_wait_seconds)*1000,3),duration_clock=queue_wait_clock)
        worker_metrics.increment("stage","queue_wait")
        heartbeat_stop=threading.Event()
        heartbeat_lost=threading.Event()
        heartbeat_interval=max(0.1,self.store.lease_seconds/3)
        def maintain_lease():
            failures=0
            while not heartbeat_stop.wait(heartbeat_interval):
                try:
                    renewed=self.store.heartbeat(job)
                except Exception as exc:
                    failures+=1
                    event(logger,"scan.processing.heartbeat.failed",logging.WARNING,
                        scan_id=job["scan_id"],job_id=job["job_id"],worker_id=self.worker_id,
                        tenant_ref=tenant_ref,processing_stage="lease",error_code="lease_heartbeat_error",
                        exception_type=type(exc).__name__,attempt=failures)
                    continue
                if not renewed:
                    heartbeat_lost.set()
                    event(logger,"scan.processing.heartbeat.rejected",logging.WARNING,
                        scan_id=job["scan_id"],job_id=job["job_id"],worker_id=self.worker_id,
                        tenant_ref=tenant_ref,processing_stage="lease",error_code="lease_heartbeat_rejected")
                    return
                if failures:
                    event(logger,"scan.processing.heartbeat.recovered",logging.INFO,
                        scan_id=job["scan_id"],job_id=job["job_id"],worker_id=self.worker_id,
                        tenant_ref=tenant_ref,processing_stage="lease",recovered_after_failures=failures)
                    failures=0
        heartbeat_thread=threading.Thread(target=maintain_lease,daemon=True)
        heartbeat_thread.start()
        try:
            if payload_parse_error: raise ValueError("invalid queued payload")
            payload["_worker_context"]={"scan_id":job["scan_id"],"tenant_id":job["tenant_id"],"subject_id":job["subject_id"]}
            analysis_started=time.perf_counter()
            result=self.processor(payload)
            event(logger,"scan.processing.stage_completed",processing_stage="analysis",scan_id=job["scan_id"],job_id=job["job_id"],worker_id=self.worker_id,tenant_ref=tenant_ref,duration_ms=round((time.perf_counter()-analysis_started)*1000,3))
            worker_metrics.increment("stage","analysis")
            result["scan_id"]=job["scan_id"]
            # Stop and join the heartbeat before the terminal transition. This
            # prevents a ping racing with completion and mistaking the now-
            # completed row for a lost lease.
            heartbeat_stop.set(); heartbeat_thread.join()
            if heartbeat_lost.is_set():
                raise RuntimeError("stale worker lease")
            persistence_started=time.perf_counter()
            self.store.complete(job,result)
            # Keep native scan media available until the durable result commit;
            # failed analysis or persistence must remain safely retryable.
            if not payload.get("_directus_pull"):
                self._delete_payload_media(payload,job)
            event(logger,"scan.processing.stage_completed",processing_stage="result_commit",scan_id=job["scan_id"],job_id=job["job_id"],worker_id=self.worker_id,tenant_ref=tenant_ref,duration_ms=round((time.perf_counter()-persistence_started)*1000,3))
            event(logger,"scan.processing.completed",scan_id=job["scan_id"],job_id=job["job_id"],worker_id=self.worker_id,tenant_ref=tenant_ref,processing_stage="result_commit",duration_ms=round((time.perf_counter()-start)*1000,3),attempt=job["attempts"])
            worker_metrics.increment("stage","result_commit"); worker_metrics.increment("outcome","success")
            self._publish_directus_once()
        except Exception as exc:
            span.set_attribute("error",True)
            heartbeat_stop.set(); heartbeat_thread.join()
            if heartbeat_lost.is_set():
                # A worker that no longer owns the lease must not mutate queue
                # state or delete media; the current owner/reaper handles it.
                event(logger,"scan.processing.lease_lost",logging.WARNING,scan_id=job["scan_id"],job_id=job["job_id"],worker_id=self.worker_id,tenant_ref=tenant_ref,processing_stage="lease",error_code="lease_lost")
                return True
            retryable=(getattr(exc,"retryable",True) and not isinstance(exc,ValueError)
                       and job["attempts"] < self.store.max_attempts)
            # Native jobs need their uploaded media for a retry. Directus jobs
            # refetch remote files on every attempt, and the facade also cleans
            # any staged V2 copies in its own finally block.
            safe_failure_codes={"stale worker lease":"lease_lost","scan media deletion failed":"media_delete_failed"}
            failure_code=getattr(exc,"error_code",None)
            if not isinstance(failure_code,str) or not re.fullmatch(r"[a-z0-9_.-]{1,80}",failure_code):
                failure_code=safe_failure_codes.get(str(exc),"processing_error")
            try:
                self.store.fail(job,failure_code,force_terminal=not retryable)
            except RuntimeError:
                event(logger,"scan.processing.lease_lost",logging.WARNING,scan_id=job["scan_id"],job_id=job["job_id"],worker_id=self.worker_id,tenant_ref=tenant_ref,processing_stage="lease",error_code="lease_lost")
                return True
            if payload.get("_directus_pull") or not retryable:
                # Only clean media after the queue accepted this worker's
                # failure transition. A stale worker must leave it untouched.
                try:
                    self._delete_payload_media(payload,job)
                except Exception as cleanup_exc:
                    event(logger,"scan.processing.media_cleanup_failed",logging.ERROR,
                        scan_id=job["scan_id"],job_id=job["job_id"],worker_id=self.worker_id,
                        tenant_ref=tenant_ref,processing_stage="media_cleanup",
                        error_code="media_delete_failed",exception_type=type(cleanup_exc).__name__)
            worker_metrics.increment("outcome","retry" if retryable else "failure")
            event(logger,"scan.processing.retry_scheduled" if retryable else "scan.processing.failed",logging.WARNING if retryable else logging.ERROR,scan_id=job["scan_id"],job_id=job["job_id"],worker_id=self.worker_id,tenant_ref=tenant_ref,processing_stage="analysis",error_code=failure_code,exception_type=type(exc).__name__,attempt=job["attempts"])
        finally:
            request_id_var.reset(request_token)
            span_scope.__exit__(None,None,None)
            context.detach(attached)
            trace_id_var.reset(trace_token)
        return True

    def run_forever(self, stop_event, idle_seconds: float=0.05):
        while not stop_event.is_set():
            if self.reconciler is not None and time.monotonic()>=self._next_reconciliation_at:
                self._reconcile_media(stop_event=stop_event)
            if stop_event.is_set():break
            if time.monotonic()>=self._next_directus_reconciliation_at:
                self._reconcile_directus_deletions()
            if stop_event.is_set():break
            if not self.run_once():
                wait_seconds=max(0.0,float(idle_seconds))
                if self.reconciler is not None:
                    wait_seconds=min(wait_seconds,max(0.0,self._next_reconciliation_at-time.monotonic()))
                wait_seconds=min(wait_seconds,max(0.0,self._next_directus_reconciliation_at-time.monotonic()))
                stop_event.wait(wait_seconds)

    def _publish_directus_once(self):
        if self.directus_publisher is None:return
        try:
            outcome=self.directus_publisher.run_once(self.worker_id)
            if outcome not in {"idle","delivered"}:
                event(logger,"directus.result_write.retry",logging.WARNING,processing_stage="directus_writeback",error_code=outcome)
        except Exception as exc:
            event(logger,"directus.result_write.retry",logging.WARNING,processing_stage="directus_writeback",error_code="directus_sync_failed",exception_type=type(exc).__name__)
