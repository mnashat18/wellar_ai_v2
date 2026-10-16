from __future__ import annotations

import logging
import threading
import time
import hashlib
import re
from typing import Callable
from opentelemetry import context,propagate,trace

from app.core.logging import event, request_id_var,trace_id_var
from app.observability.metrics import worker_metrics
from app.drowsiness_indicator import classify as classify_drowsiness
from app.media.reconciliation import MediaReconciler,delete_with_retry

logger=logging.getLogger("wellar.worker")


def research_only_processor(payload: dict) -> dict:
    """Default safe stub: no media/model inference and no fatigue score."""
    return {
        "status": "insufficient_evidence",
        "model_version": None,
        "targets": {
            "reference_sleepiness": {"status":"unavailable","value":None},
            "vigilance_impairment": {"status":"unavailable","value":None},
        },
        "drowsiness_indicator": classify_drowsiness({}),
        "quality": {"video":"not_processed","image":"not_processed","audio":"not_processed","reaction_task":"not_provided"},
        "message": "No authorized, validated analysis pipeline is configured.",
    }


class Worker:
    def __init__(self, store, worker_id: str, processor: Callable[[dict],dict]=research_only_processor):
        self.store,self.worker_id,self.processor=store,worker_id,processor
        media_store=getattr(processor,"media_store",None)
        self.reconciler=MediaReconciler(media_store,store) if media_store is not None and callable(getattr(store,"list_terminal_scan_media_refs",None)) else None
        self._last_reconciliation=0.0
        if self.reconciler is not None:
            self._reconcile_media()

    def _reconcile_media(self):
        try:
            self.reconciler.run_once()
            self._last_reconciliation=time.monotonic()
        except Exception as exc:
            worker_metrics.increment("storage","reconciliation_failed")
            event(logger,"media.reconciliation.failed",logging.ERROR,processing_stage="media_reconciliation",error_code="reconciliation_failed",exception_type=type(exc).__name__)

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
        if not job: return False
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
        heartbeat_interval=max(0.1,self.store.lease_seconds/3)
        def maintain_lease():
            while not heartbeat_stop.wait(heartbeat_interval):
                if not self.store.heartbeat(job): return
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
            persistence_started=time.perf_counter()
            self.store.complete(job,result)
            heartbeat_stop.set(); heartbeat_thread.join(timeout=heartbeat_interval+0.1)
            event(logger,"scan.processing.stage_completed",processing_stage="result_commit",scan_id=job["scan_id"],job_id=job["job_id"],worker_id=self.worker_id,tenant_ref=tenant_ref,duration_ms=round((time.perf_counter()-persistence_started)*1000,3))
            event(logger,"scan.processing.completed",scan_id=job["scan_id"],job_id=job["job_id"],worker_id=self.worker_id,tenant_ref=tenant_ref,processing_stage="result_commit",duration_ms=round((time.perf_counter()-start)*1000,3),attempt=job["attempts"])
            worker_metrics.increment("stage","result_commit"); worker_metrics.increment("outcome","success")
        except Exception as exc:
            span.set_attribute("error",True)
            heartbeat_stop.set(); heartbeat_thread.join(timeout=heartbeat_interval+0.1)
            self._delete_payload_media(payload,job)
            try:
                self.store.fail(job,"processing_error")
            except RuntimeError:
                event(logger,"scan.processing.lease_lost",logging.WARNING,scan_id=job["scan_id"],job_id=job["job_id"],worker_id=self.worker_id,tenant_ref=tenant_ref,processing_stage="lease",error_code="lease_lost")
                return True
            retryable=job["attempts"] < self.store.max_attempts
            safe_failure_codes={"stale worker lease":"lease_lost","scan media deletion failed":"media_delete_failed"}
            failure_code=safe_failure_codes.get(str(exc),"processing_error")
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
            if self.reconciler is not None and time.monotonic()-self._last_reconciliation>=300:
                self._reconcile_media()
            if not self.run_once(): stop_event.wait(idle_seconds)
