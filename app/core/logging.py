from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import re
import traceback
from pathlib import Path
from datetime import datetime, timezone
from typing import Any
from opentelemetry import trace

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="-")
_environment = "unknown"
_SECRET = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/-]+=*|((?:token|password|secret|api[_-]?key)\s*[=:]\s*)[^\s,;]+")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")


def _pseudonym(value: str) -> str:
    if value in {"", "-"}:
        return value or "-"
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]


def sanitize(value: str) -> str:
    value = _SECRET.sub(lambda m: (m.group(1) or m.group(2) or "") + "[REDACTED]", value)
    return _EMAIL.sub("[REDACTED_EMAIL]", value)[:1200]


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = getattr(record, "event_fields", {})
        active_context=trace.get_current_span().get_span_context()
        base: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "severity": record.levelname,
            "event_name": sanitize(record.getMessage()),
            "service": "wellar-ai-v2",
            "environment": getattr(record, "environment", _environment),
            "request_id": _pseudonym(request_id_var.get()),
            "trace_id": format(active_context.trace_id,"032x") if active_context.is_valid else trace_id_var.get(),
        }
        allowed = {"scan_id", "job_id", "worker_id", "processing_stage", "duration_ms", "duration_clock", "error_code", "exception_type", "model_version", "status_code", "attempt", "attempt_count", "modality", "size_bytes", "tenant_ref", "item_count", "operation", "sqlite_error_code", "sqlite_error_name", "retry_attempt_count", "transaction_duration_ms", "busy_lock_count", "endpoint_name", "directus_error_code", "directus_error_message", "field_names"}
        for key, value in fields.items():
            if key in allowed and value is not None:
                if isinstance(value, str):
                    base[key] = _pseudonym(value) if key in {"scan_id", "job_id", "worker_id"} else sanitize(value)
                else:
                    base[key] = value
        if record.exc_info:
            exc = record.exc_info[1]
            base["exception_type"] = type(exc).__name__ if exc else "Exception"
            # Frames only: exception messages can contain credentials or personal data.
            base["sanitized_stack_trace"] = [sanitize(f"{Path(f.filename).name}:{f.lineno} in {f.name}") for f in traceback.extract_tb(record.exc_info[2])[-8:]]
        return json.dumps(base, ensure_ascii=True, separators=(",", ":"))


def configure_logging(level: str, environment: str = "unknown") -> None:
    global _environment
    _environment = environment
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


def event(logger: logging.Logger, name: str, level: int = logging.INFO, **fields: Any) -> None:
    logger.log(level, name, extra={"event_fields": fields})
