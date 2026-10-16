from __future__ import annotations

import threading
import time
from collections import deque


class TenantRateLimiter:
    """Process-local sliding-window throttle. Distributed deployments need shared storage."""
    def __init__(self, limit: int, window_seconds: float = 60.0, max_tenants: int = 10000):
        if limit < 1 or window_seconds <= 0 or max_tenants < 1:
            raise ValueError("rate limit settings must be positive")
        self.limit, self.window_seconds, self.max_tenants = limit, window_seconds, max_tenants
        self._lock = threading.Lock()
        self._events: dict[str, deque[float]] = {}

    def consume(self, tenant_id: str, now: float | None = None) -> tuple[bool, int]:
        instant = time.monotonic() if now is None else now
        with self._lock:
            bucket = self._events.get(tenant_id)
            if bucket is None:
                if len(self._events) >= self.max_tenants:
                    oldest = min(self._events, key=lambda key: self._events[key][-1] if self._events[key] else 0)
                    self._events.pop(oldest, None)
                bucket = self._events.setdefault(tenant_id, deque())
            cutoff = instant - self.window_seconds
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.limit:
                return False, max(1, int(bucket[0] + self.window_seconds - instant + 0.999))
            bucket.append(instant)
            return True, 0
