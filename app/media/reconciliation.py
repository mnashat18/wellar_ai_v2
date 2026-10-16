from __future__ import annotations

import logging
import time

from app.core.logging import event
from app.observability.metrics import worker_metrics

logger=logging.getLogger("wellar.media.reconciliation")


def delete_with_retry(media_store, reference: str, tenant_id: str, subject_id: str, *, attempts: int=3, base_delay: float=0.02) -> bool:
    """Idempotently attempt an owned media deletion; expose exhaustion without PII."""
    for attempt in range(1, attempts+1):
        try:
            deleted=media_store.delete(reference,tenant_id,subject_id)
            if deleted:
                return True
            # A missing row is already erased; storage adapters must make delete idempotent.
            return True
        except Exception as exc:
            worker_metrics.increment("storage","deletion_retry")
            if attempt < attempts:
                time.sleep(base_delay*(2**(attempt-1)))
                continue
            worker_metrics.increment("storage","deletion_failed")
            event(logger,"media.delete.retry_exhausted",logging.ERROR,processing_stage="media_deletion",
                  error_code="media_delete_failed",exception_type=type(exc).__name__,attempt_count=attempt)
            return False
    return False


class MediaReconciler:
    def __init__(self, media_store, queue_store, *, scratch_age_seconds: float=3600):
        self.media_store,self.queue_store=media_store,queue_store
        self.scratch_age_seconds=max(60.0,float(scratch_age_seconds))

    def run_once(self) -> dict[str,int]:
        removed=0; failed=0
        refs=self.queue_store.list_terminal_scan_media_refs()
        for item in refs:
            if delete_with_retry(self.media_store,item["reference"],item["tenant_id"],item["subject_id"]):
                removed+=1
            else:
                failed+=1
        scratch=getattr(self.media_store,"sweep_stale_scratch",None)
        scratch_removed=scratch(self.scratch_age_seconds) if callable(scratch) else 0
        worker_metrics.increment("storage","reconciliation_run")
        event(logger,"media.reconciliation.completed",processing_stage="media_reconciliation",
              terminal_references=removed,failed_references=failed,scratch_files=scratch_removed)
        return {"terminal_media_deleted":removed,"deletion_failed":failed,"scratch_removed":scratch_removed}
