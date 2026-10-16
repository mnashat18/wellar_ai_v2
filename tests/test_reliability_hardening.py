from __future__ import annotations

from app.security.rate_limit import TenantRateLimiter
from app.core.logging import JsonFormatter, request_id_var
import logging
import json


def test_tenant_rate_limiter_isolated_and_returns_retry_delay():
    limiter = TenantRateLimiter(limit=1, window_seconds=10)
    assert limiter.consume("tenant-a", now=100) == (True, 0)
    assert limiter.consume("tenant-a", now=101) == (False, 9)
    assert limiter.consume("tenant-b", now=101) == (True, 0)
    assert limiter.consume("tenant-a", now=111) == (True, 0)


def test_http_rate_limit_uses_429_and_retry_after(client, auth):
    client.app.state.rate_limiter = TenantRateLimiter(limit=1, window_seconds=30)
    first = client.get("/api/v1/scans/does-not-exist", headers=auth)
    second = client.get("/api/v1/scans/does-not-exist", headers=auth)
    assert first.status_code == 404
    assert second.status_code == 429
    assert int(second.headers["Retry-After"]) >= 1
    assert second.json()["error"]["code"] == "tenant_rate_limited"


def test_queue_saturation_error_has_retry_after(client, auth, monkeypatch):
    from app.core.errors import QueueFull
    from app.scans.service import ScanService
    monkeypatch.setattr(ScanService, "submit", lambda *args, **kwargs: (_ for _ in ()).throw(QueueFull()))
    response = client.post("/api/v1/scans", headers={**auth, "Idempotency-Key": "saturated"}, json={"schema_version": "1.0", "media_refs": []})
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"


def test_json_logs_pseudonymize_identifiers_and_redact_canaries():
    token = request_id_var.set("worker@example.test")
    try:
        record = logging.LogRecord("test", logging.ERROR, "C:/Users/canary-name/app.py", 5,
            "failed for pilot.user@example.test token=canary-token", (), None)
        record.event_fields = {"scan_id": "scan-canary-123", "job_id": "job-canary-456", "worker_id": "worker-canary"}
        rendered = JsonFormatter().format(record)
    finally:
        request_id_var.reset(token)
    assert "canary" not in rendered and "pilot.user@example.test" not in rendered
    assert "worker@example.test" not in rendered and "canary-token" not in rendered
    assert json.loads(rendered)["scan_id"] != "scan-canary-123"


def test_metrics_endpoint_uses_bounded_metric_names(client):
    result = client.get("/metrics")
    assert result.status_code == 200
    assert result.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert "wellar_" in result.text
    assert "tenant-a" not in result.text and "user-a" not in result.text
