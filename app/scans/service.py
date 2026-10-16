from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from app.core.errors import IdempotencyConflict
from app.core.logging import event
from app.core.logging import request_id_var
from app.core.ids import new_id
from app.security.auth import Principal

logger = logging.getLogger("wellar.scans")


class ScanService:
    def __init__(self, store):
        self.store = store

    def submit(self, principal: Principal, key: str, payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        if not key or len(key) > 128:
            raise ValueError("Idempotency-Key is required and must be at most 128 characters")
        semantic_payload={key:value for key,value in payload.items() if key not in {"_traceparent","_accepted_monotonic"}}
        canonical = json.dumps(semantic_payload, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        persisted = json.dumps(payload,sort_keys=True,separators=(",",":"))
        scan_id = new_id()
        record, created = self.store.submit(scan_id, principal, key, digest, persisted, request_id_var.get())
        if not created and record["payload_hash"] != digest:
            raise IdempotencyConflict()
        tenant_ref=hashlib.sha256(principal.tenant_id.encode()).hexdigest()[:16]
        event(logger, "scan.submitted" if created else "scan.idempotent_replay", scan_id=record["scan_id"], job_id=record["job_id"], tenant_ref=tenant_ref,processing_stage="accepted")
        return record, created
