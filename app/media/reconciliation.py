from __future__ import annotations

import logging
import time
import uuid

from app.core.logging import event
from app.observability.metrics import worker_metrics

logger=logging.getLogger("wellar.media.reconciliation")


def delete_with_retry(media_store, reference: str, tenant_id: str, subject_id: str, *, attempts: int=3,
                      base_delay: float=0.02, retry_deadline_seconds:float=16.0,
                      for_reconciliation:bool=False,stop_event=None) -> bool:
    """Idempotently attempt an owned media deletion; expose exhaustion without PII."""
    if attempts<1 or retry_deadline_seconds<=0 or base_delay<0:
        raise ValueError("media deletion retry limits must be positive")
    started=time.monotonic()
    for attempt in range(1, attempts+1):
        if stop_event is not None and stop_event.is_set():return False
        remaining=retry_deadline_seconds-(time.monotonic()-started)
        if remaining<=0:break
        try:
            method=getattr(media_store,"delete_for_reconciliation",None) if for_reconciliation else None
            if method is not None:
                deleted=method(reference,tenant_id,subject_id,timeout_seconds=remaining)
            else:
                deleted=media_store.delete(reference,tenant_id,subject_id)
            if deleted:
                return True
            # A missing row is already erased; storage adapters must make delete idempotent.
            return True
        except Exception as exc:
            worker_metrics.increment("storage","deletion_retry")
            if attempt < attempts:
                delay=base_delay*(2**(attempt-1))
                remaining=retry_deadline_seconds-(time.monotonic()-started)
                if remaining<=0:break
                if stop_event is not None:
                    if stop_event.wait(min(delay,remaining)):return False
                else:time.sleep(min(delay,remaining))
                continue
            break
    worker_metrics.increment("storage","deletion_failed")
    event(logger,"media.delete.retry_exhausted",logging.ERROR,processing_stage="media_deletion",
          error_code="media_delete_failed",exception_type=type(exc).__name__ if "exc" in locals() else "RetryDeadlineExceeded",attempt_count=attempt)
    return False


class MediaReconciler:
    def __init__(self, media_store, queue_store, *, scratch_age_seconds: float=3600,
                 page_size: int=50, max_pages_per_run: int=5,
                 max_references_per_run: int=500, lease_seconds: int=300,
                 delete_attempts:int=3,delete_retry_deadline_seconds:float=16.0):
        self.media_store,self.queue_store=media_store,queue_store
        self.scratch_age_seconds=max(60.0,float(scratch_age_seconds))
        self.page_size=max(1,min(int(page_size),5000))
        self.max_pages_per_run=max(1,min(int(max_pages_per_run),100))
        self.max_references_per_run=max(1,min(int(max_references_per_run),100000))
        self.lease_seconds=max(1,min(int(lease_seconds),3600))
        self.delete_attempts=max(1,min(int(delete_attempts),8))
        self.delete_retry_deadline_seconds=max(0.1,min(float(delete_retry_deadline_seconds),300.0))

    def run_once(self,stop_event=None) -> dict[str,int|str]:
        """Process terminal media with an exclusive terminal-time cycle cutoff.

        Records whose terminal timestamp equals the cycle boundary are left for
        the next cycle; this avoids host/database timestamp ties admitting work
        that became terminal after the reconciliation snapshot.
        """
        removed=0; failed=0; pages=0; processed=0; cycle_complete=False;interrupted=False
        owner=uuid.uuid4().hex
        state=self.queue_store.begin_media_reconciliation(owner,self.lease_seconds)
        if state is None:
            return {"terminal_media_deleted":0,"deletion_failed":0,"scratch_removed":0,
                    "pages_processed":0,"references_processed":0,"budget_exhausted":0,
                    "cycle_complete":0,"lease_busy":1,"run_status":"lease_busy"}
        lease_active=True
        after_scan_id=state["after_scan_id"]
        after_reference=state["after_reference"]
        try:
            while pages<self.max_pages_per_run and processed<self.max_references_per_run:
                if stop_event is not None and stop_event.is_set():
                    interrupted=True
                    break
                remaining=self.max_references_per_run-processed
                refs=self.queue_store.list_terminal_scan_media_refs(
                    limit=min(self.page_size,remaining),after_scan_id=after_scan_id,
                    after_reference=after_reference,
                    terminal_before=state["cycle_started_at"])
                if not refs:
                    if not self.queue_store.finish_media_reconciliation_cycle(owner):
                        raise RuntimeError("media reconciliation lease was lost before cycle completion")
                    lease_active=False
                    cycle_complete=True
                    break
                batch=refs
                successful_by_scan={}
                processed_batch=[]
                for item in batch:
                    if stop_event is not None and stop_event.is_set():
                        interrupted=True
                        break
                    if not self.queue_store.renew_media_reconciliation(owner,self.lease_seconds):
                        raise RuntimeError("media reconciliation lease was lost during deletion")
                    if delete_with_retry(self.media_store,item["reference"],item["tenant_id"],item["subject_id"],
                            attempts=self.delete_attempts,retry_deadline_seconds=self.delete_retry_deadline_seconds,
                            for_reconciliation=True,stop_event=stop_event):
                        removed+=1
                        successful_by_scan.setdefault(item["scan_id"],[]).append(item["reference"])
                    else:
                        failed+=1
                    processed_batch.append(item)
                for scan_id,references in successful_by_scan.items():
                    if not self.queue_store.remove_terminal_scan_media_refs(owner,scan_id,references):
                        raise RuntimeError("media reconciliation lease was lost before metadata cleanup")
                if processed_batch:
                    last=processed_batch[-1]
                    after_scan_id,after_reference=last["scan_id"],last["reference"]
                    if not self.queue_store.advance_media_reconciliation(owner,after_scan_id,after_reference,self.lease_seconds):
                        raise RuntimeError("media reconciliation lease was lost before checkpoint")
                    pages+=1
                    processed+=len(processed_batch)
                if interrupted:break
        finally:
            if lease_active:
                self.queue_store.release_media_reconciliation(owner)
        scratch=getattr(self.media_store,"sweep_stale_scratch",None)
        scratch_removed=scratch(self.scratch_age_seconds) if callable(scratch) and not interrupted else 0
        budget_exhausted=not interrupted and not cycle_complete and (pages>=self.max_pages_per_run or processed>=self.max_references_per_run)
        run_status="partial" if failed or budget_exhausted or interrupted else "complete"
        worker_metrics.increment("storage","reconciliation_run")
        event(logger,"media.reconciliation.completed",processing_stage="media_reconciliation",
              terminal_references=removed,failed_references=failed,scratch_files=scratch_removed,
              pages_processed=pages,references_processed=processed,cycle_complete=cycle_complete,
              budget_exhausted=budget_exhausted,run_status=run_status,shutdown_requested=interrupted)
        return {"terminal_media_deleted":removed,"deletion_failed":failed,"scratch_removed":scratch_removed,
                "pages_processed":pages,"references_processed":processed,"budget_exhausted":int(budget_exhausted),
                "cycle_complete":int(cycle_complete),"lease_busy":0,
                "run_status":run_status,"shutdown_requested":int(interrupted)}
