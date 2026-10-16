from collections import Counter
from threading import Lock


class BoundedMetrics:
    """In-process counters with fixed label vocabularies; no user-controlled labels."""
    _stages={"accepted","queue_wait","analysis","result_commit","http"}
    _outcomes={"success","retry","failure"}
    _queue={"accepted","claimed","completed","retry_scheduled","terminal_failure"}
    _storage={"stored","resolve_failed","deleted","retention_deleted","deletion_failed","compensation_failed","directus_deletion_failed"}
    def __init__(self): self._lock=Lock(); self._counts=Counter()
    def increment(self, metric: str, label: str) -> None:
        allowed=self._stages if metric=="stage" else self._outcomes if metric=="outcome" else self._queue if metric=="queue" else self._storage if metric=="storage" else set()
        if label not in allowed: return
        with self._lock: self._counts[(metric,label)]+=1
    def snapshot(self) -> dict[str,int]:
        with self._lock: return {f"{k[0]}_{k[1]}":v for k,v in self._counts.items()}


worker_metrics=BoundedMetrics()
