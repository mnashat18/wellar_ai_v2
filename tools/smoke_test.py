#!/usr/bin/env python3
"""Run a single authenticated scan against a deployed Wellar API.

Required environment: BASE_URL, WELLAR_SMOKE_TOKEN, SAMPLE_VIDEO_PATH.
This script uploads only the caller-provided file and creates one scan.
"""
from __future__ import annotations

import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path


def _request(url: str, *, token: str, method: str = "GET", data: bytes | None = None,
             headers: dict[str, str] | None = None, timeout: float = 20.0):
    request_headers = {"Authorization": f"Bearer {token}", **(headers or {})}
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
        return response.status, json.loads(body) if body else None


def main() -> int:
    base_url = os.environ.get("BASE_URL", "").rstrip("/")
    token = os.environ.get("WELLAR_SMOKE_TOKEN", "")
    sample = Path(os.environ.get("SAMPLE_VIDEO_PATH", ""))
    timeout = int(os.environ.get("SMOKE_TIMEOUT_SECONDS", "180"))
    poll_seconds = float(os.environ.get("SMOKE_POLL_SECONDS", "2"))
    if not base_url or not token or not str(sample) or not sample.is_file():
        print("Set BASE_URL, WELLAR_SMOKE_TOKEN, and SAMPLE_VIDEO_PATH to a local video file.", file=sys.stderr)
        return 2
    if timeout < 1 or poll_seconds <= 0:
        print("SMOKE_TIMEOUT_SECONDS and SMOKE_POLL_SECONDS must be positive.", file=sys.stderr)
        return 2
    try:
        video = sample.read_bytes()
        media_type = mimetypes.guess_type(sample.name)[0]
        if media_type not in {"video/mp4", "video/x-msvideo"}:
            print("Sample must be an MP4 (.mp4) or AVI (.avi) video.", file=sys.stderr)
            return 2
        _, uploaded = _request(
            f"{base_url}/api/v1/media/video", token=token, method="POST", data=video,
            headers={"Content-Type": media_type, "Content-Length": str(len(video))},
            timeout=max(20, timeout),
        )
        media_ref = uploaded["media_ref"]
        _, submitted = _request(
            f"{base_url}/api/v1/scans", token=token, method="POST",
            data=json.dumps({
                "schema_version": "1.0", "media_refs": [media_ref],
                "consent": {"media_processing": True, "consent_version": "operational-media-v1"},
            }).encode(),
            headers={"Content-Type": "application/json", "Idempotency-Key": str(uuid.uuid4())},
        )
        scan_id = submitted["scan_id"]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            _, status = _request(f"{base_url}/api/v1/scans/{scan_id}", token=token)
            if status["status"] in {"failed", "cancelled", "dead_letter"}:
                print(json.dumps({"scan_id": scan_id, "status": status["status"], "job_error": status}, indent=2))
                return 1
            if status["status"] == "completed":
                _, result = _request(f"{base_url}/api/v1/scans/{scan_id}/result", token=token)
                fields = {key: result.get(key) for key in (
                    "analysis_status", "prediction", "confidence_level", "insufficient_evidence_reason",
                    "engine_type", "model_version",
                )}
                fields["scan_id"] = scan_id
                fields["job_status"] = status["status"]
                print(json.dumps(fields, indent=2))
                return 0 if fields["analysis_status"] in {"prediction_available", "retake_required"} else 1
            time.sleep(poll_seconds)
        print(f"Timed out waiting for scan {scan_id} after {timeout}s.", file=sys.stderr)
        return 1
    except (urllib.error.URLError, TimeoutError, OSError, KeyError, ValueError) as exc:
        print(f"Smoke test failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
