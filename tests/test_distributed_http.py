import json
from collections import Counter

import pytest

from benchmarks import distributed_http


@pytest.mark.parametrize("value,expected", [("1", (1,)), ("1,5,10", (1, 5, 10))])
def test_parse_cohorts_supports_bounded_selection(value, expected):
    assert distributed_http.parse_cohorts(value) == expected


@pytest.mark.parametrize("value", ["", "0", "51", "1,1", "1,2,3,4,5,6", "abc"])
def test_parse_cohorts_rejects_unbounded_or_invalid_selection(value):
    with pytest.raises(Exception):
        distributed_http.parse_cohorts(value)


def _run_one_cohort(monkeypatch, tmp_path, upload_response):
    (tmp_path / "data").mkdir()
    (tmp_path / ".env.sprint08").write_text("", encoding="utf-8")
    monkeypatch.setattr(distributed_http, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(distributed_http, "dotenv_values", lambda _: {"WELLAR_TEST_AUTH_TOKEN": "test-token-never-written"})

    def media(root):
        result = {}
        for modality, filename in (("image", "image.jpg"), ("video", "video.mp4"), ("audio", "audio.wav"), ("png", "skip.png")):
            path = root / filename
            path.write_bytes(b"synthetic")
            result[modality] = (path, "application/octet-stream")
        return result

    monkeypatch.setattr(distributed_http, "make_media", media)
    monkeypatch.setattr(distributed_http, "load_worker_events", lambda _: {"available": False, "reason": "container_logs_unavailable"})
    calls = []

    def fake_request(url, method, token, **kwargs):
        calls.append((url, method))
        if method == "POST" and "/api/v1/media/" in url:
            modality = url.rsplit("/", 1)[-1]
            if modality in upload_response:
                response = upload_response[modality]
                if isinstance(response, list):
                    count = sum(1 for called_url, called_method in calls if called_method == "POST" and called_url.endswith("/" + modality))
                    return response[min(count - 1, len(response) - 1)]
                return response
            return 429, {"error": {"code": "arbitrary-untrusted-code-test-token"}}, 2.0
        if method == "DELETE":
            return 204, None, 0.5
        if url.endswith("/ready"):
            return 200, {"queue": {"queued": 0, "running": 0, "terminal_failures": 0}}, 0.1
        pytest.fail("one-scan upload failure must stop before scan submission")

    monkeypatch.setattr(distributed_http, "request", fake_request)
    output = "one-scan.json"
    code = distributed_http.main(["--cohorts", "1", "--skip-idempotency-replay", "--output", output])
    return code, json.loads((tmp_path / output).read_text(encoding="utf-8")), calls, (tmp_path / output).read_text(encoding="utf-8")


def test_429_exhaustion_writes_safe_failure_report_and_cleans_partial_upload(monkeypatch, tmp_path):
    code, report, calls, serialized = _run_one_cohort(
        monkeypatch,
        tmp_path,
        {"image": (201, {"media_ref": "media://synthetic/image-ref"}, 1.0)},
    )
    assert code == 1
    assert report["failed"] == 1 and report["accepted"] == 0
    assert report["failure_types"]["upload:http_429"] == 1
    assert report["http_429_responses_total"] == 5
    assert report["http_429_responses_by_endpoint_reason"] == {"media_upload:video:unclassified": 5}
    assert report["retry_429_count"] == 4
    assert report["cohorts"][0]["failed"] == 1
    assert report["latency_ms"]["failed_upload_request"]["sample_count"] == 1
    assert report["concurrent_idempotency"]["skipped"] is True
    assert any(method == "DELETE" for _, method in calls)
    assert "arbitrary-untrusted-code-test-token" not in serialized
    assert "test-token-never-written" not in serialized
    assert "Authorization" not in serialized


def test_media_quota_code_is_allowlisted_and_classified():
    assert distributed_http.safe_failure_code({"error": {"code": "media_storage_quota"}}, 429) == "http_429:media_storage_quota"
    assert distributed_http.safe_failure_code({"error": {"code": "secret-bearing-value"}}, 429) == "http_429"
    assert distributed_http.safe_429_reason({"error": {"code": "secret-bearing-value"}}) == "unclassified"


def test_retry_429_is_counted_by_endpoint_reason_and_kept_separate_from_completed_scans(monkeypatch, tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / ".env.sprint08").write_text("", encoding="utf-8")
    monkeypatch.setattr(distributed_http, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(distributed_http, "dotenv_values", lambda _: {"WELLAR_TEST_AUTH_TOKEN": "synthetic-secret-never-written"})
    monkeypatch.setattr(distributed_http, "load_worker_events", lambda _: {
        "available": True,
        "stage_latency_ms": {"queue_wait": {"sample_count": 1, "p50": 3.0, "p95": 3.0, "p99": 3.0}},
        "worker_event_counts": {},
    })
    monkeypatch.setattr(distributed_http.time, "sleep", lambda _: None)

    def media(root):
        result = {}
        for modality, filename in (("image", "image.jpg"), ("video", "video.mp4"), ("audio", "audio.wav")):
            path = root / filename
            path.write_bytes(b"synthetic")
            result[modality] = (path, "application/octet-stream")
        return result

    monkeypatch.setattr(distributed_http, "make_media", media)
    calls = Counter()

    def fake_request(url, method, token, **kwargs):
        if method == "POST" and "/api/v1/media/" in url:
            modality = url.rsplit("/", 1)[-1]
            calls[modality] += 1
            if modality == "image" and calls[modality] == 1:
                return 429, {"error": {"code": "upload_capacity_exceeded"}}, 2.0
            return 201, {"media_ref": f"media://synthetic/{modality}"}, 1.0
        if method == "POST" and url.endswith("/api/v1/scans"):
            return 202, {"scan_id": "synthetic-internal-id", "status": "queued", "created": True}, 1.0
        if method == "GET" and url.endswith("/result"):
            return 200, {"status": "insufficient_evidence", "targets": {
                "reference_sleepiness": {"value": None}, "vigilance_impairment": {"value": None},
            }}, 0.5
        if method == "GET" and "/api/v1/scans/" in url:
            return 200, {"status": "completed"}, 0.5
        if method == "DELETE":
            return 204, None, 0.5
        if url.endswith("/ready"):
            return 200, {"queue": {"queued": 0, "running": 0, "terminal_failures": 0}}, 0.1
        pytest.fail("unexpected synthetic benchmark request")

    monkeypatch.setattr(distributed_http, "request", fake_request)
    output = "retry-accounting.json"
    exit_code = distributed_http.main(["--cohorts", "1", "--skip-idempotency-replay", "--output", output])
    serialized = (tmp_path / output).read_text(encoding="utf-8")
    report = json.loads(serialized)

    assert exit_code == 0
    assert report["http_429_responses_total"] == 1
    assert report["http_429_responses_by_endpoint_reason"] == {"media_upload:image:upload_capacity_exceeded": 1}
    assert report["retry_429_count"] == 1
    assert report["scan_attempts"] == 1
    assert report["scan_submission_requests"] == 1
    assert report["accepted"] == report["completed"] == 1
    assert report["cohorts"][0]["scan_attempts"] == 1
    assert report["cohorts"][0]["accepted_scans_per_second"] > 0
    assert report["cohorts"][0]["throughput_scans_per_second"] > 0
    assert report["configuration"]["synthetic_fixture_bytes"] == {"image": 9, "video": 9, "audio": 9}
    assert report["http_requests_by_endpoint"]["media_upload:image"] == 2
    assert "synthetic-internal-id" not in serialized
    assert "synthetic-secret-never-written" not in serialized
    assert "upload_capacity_exceeded" in serialized
