from app.workers.worker import Worker
import io
import logging
from app.core.logging import JsonFormatter


BODY={"schema_version":"1.0","media_refs":[],"reaction_task":None}


def test_health_ready_and_request_id(client):
    assert client.get("/health").json()=={"status":"ok"}
    response=client.get("/ready",headers={"X-Request-ID":"trace:123"})
    assert response.status_code==200 and response.headers["X-Request-ID"]=="trace:123"


def test_submit_worker_result_and_tenant_access(client,auth,store):
    accepted=client.post("/api/v1/scans",headers={**auth,"Idempotency-Key":"key-1","X-Request-ID":"scan-trace-001"},json=BODY)
    assert accepted.status_code==202
    sid=accepted.json()["scan_id"]
    assert store.db.execute("select request_id from scans where scan_id=?",(sid,)).fetchone()[0]=="scan-trace-001"
    assert Worker(store,"worker-test").run_once()
    status=client.get(f"/api/v1/scans/{sid}",headers=auth)
    assert status.status_code==200 and status.json()["status"]=="completed"
    result=client.get(f"/api/v1/scans/{sid}/result",headers=auth)
    assert result.status_code==200
    assert result.json()["status"]=="insufficient_evidence"
    assert result.json()["targets"]["reference_sleepiness"]["value"] is None
    assert result.json()["measurements"]["schema_version"]=="wellar.dual-measurements/1.0"
    assert result.json()["measurements"]["fatigue"]["status"]=="unavailable"
    assert result.json()["measurements"]["fatigue"]["value"] is None
    assert result.json()["measurements"]["vigilance"]["status"]=="not_provided"
    assert client.get(f"/api/v1/scans/{sid}",headers={"Authorization":"Bearer token-other"}).status_code==404


def test_status_and_result_are_indistinguishable_for_cross_tenant_scan_access(client,auth):
    accepted=client.post("/api/v1/scans",headers={**auth,"Idempotency-Key":"tenant-private-scan"},json=BODY)
    scan_id=accepted.json()["scan_id"]
    assert Worker(client.app.state.store,"tenant-private-worker").run_once()
    intruder={"Authorization":"Bearer token-b"}
    status=client.get(f"/api/v1/scans/{scan_id}",headers=intruder)
    result=client.get(f"/api/v1/scans/{scan_id}/result",headers=intruder)
    assert status.status_code==result.status_code==404
    assert status.json()["error"]["code"]=="scan_not_found"
    assert result.json()["error"]["code"]=="result_unavailable"
    assert scan_id not in status.text and scan_id not in result.text


def test_auth_and_idempotency_conflict(client,auth):
    assert client.post("/api/v1/scans",json=BODY).status_code==401
    headers={**auth,"Idempotency-Key":"same"}
    first=client.post("/api/v1/scans",headers=headers,json=BODY)
    conflict={**BODY,"consent":{"media_processing":True,"consent_version":"operational-media-v1","research_use":False},"reaction_task":{"task_type":"pvt","trials":[{"trial_index":0,"stimulus_timestamp_ms":100,"response_timestamp_ms":450}]}}
    second=client.post("/api/v1/scans",headers=headers,json=conflict)
    assert first.status_code==202 and second.status_code==409


def test_sqlite_busy_returns_sanitized_retryable_503_without_accepting(client,auth,store):
    import sqlite3
    blocker=sqlite3.connect(store.path,timeout=1,isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    store.db.execute("PRAGMA busy_timeout=1")
    try:
        response=client.post("/api/v1/scans",headers={**auth,"Idempotency-Key":"busy-test"},json=BODY)
        assert response.status_code==503
        assert response.json()["error"]["code"]=="queue_temporarily_unavailable"
        assert response.json()["error"]["retryable"] is True
        assert store._sqlite_retry_count==2
        assert store.db.execute("SELECT COUNT(*) FROM scans").fetchone()[0]==0
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()
        store.db.execute(f"PRAGMA busy_timeout={store.busy_timeout_ms}")


def test_idempotent_replay_and_opaque_media_validation(client,auth):
    headers={**auth,"Idempotency-Key":"same-payload"}
    first=client.post("/api/v1/scans",headers=headers,json=BODY)
    replay=client.post("/api/v1/scans",headers=headers,json=BODY)
    assert replay.status_code==202 and replay.json()["created"] is False
    assert replay.json()["scan_id"]==first.json()["scan_id"]
    invalid=client.post("/api/v1/scans",headers={**auth,"Idempotency-Key":"bad"},json={**BODY,"media_refs":["https://example.com/private.mp4"]})
    assert invalid.status_code==422


def test_trace_context_does_not_break_idempotent_submission(client,auth):
    headers={**auth,"Idempotency-Key":"trace-replay"}
    first=client.post("/api/v1/scans",headers={**headers,"traceparent":"00-11111111111111111111111111111111-2222222222222222-01"},json=BODY)
    replay=client.post("/api/v1/scans",headers={**headers,"traceparent":"00-33333333333333333333333333333333-4444444444444444-01"},json=BODY)
    assert first.status_code==202 and replay.status_code==202 and replay.json()["created"] is False


def test_w3c_trace_id_is_logged_without_logging_the_parent_header(client):
    trace_id="abcdefabcdefabcdefabcdefabcdefab"
    stream=io.StringIO();handler=logging.StreamHandler(stream);handler.setFormatter(JsonFormatter());root=logging.getLogger();root.addHandler(handler)
    try:client.get("/health",headers={"traceparent":f"00-{trace_id}-1234567890abcdef-01"})
    finally:root.removeHandler(handler)
    assert trace_id in stream.getvalue()
    assert "1234567890abcdef" not in stream.getvalue()
