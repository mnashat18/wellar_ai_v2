"""Read-only staging smoke check for a deployed Wellar V2 API.

The script never uploads media or creates labels. It checks service health,
readiness, the published contract, and the measurements-only indicator route.
"""
from __future__ import annotations

import json
import os
import sys
from urllib.parse import urlsplit

import httpx


MEASUREMENTS = {
    "short_clip_eye_closure_fraction": 0.03,
    "blink_rate_per_minute": 10.0,
    "mean_eye_closure_seconds": 0.1,
    "max_eye_closure_seconds": 0.2,
    "face_detection_ratio": 0.9,
    "mean_luminance": 80.0,
    "mean_blur_variance": 40.0,
    "temporal_coverage": 0.8,
    "yaw_proxy": 0.01,
    "pitch_proxy": 0.01,
}


def validate_target(url: str, *, allow_staging: bool, allowed_host: str) -> str:
    parsed = urlsplit(url)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("target must be an origin URL without credentials, query, or fragment")
    host = parsed.hostname.lower().rstrip(".")
    if host in {"localhost", "127.0.0.1", "::1"}:
        if parsed.scheme != "http":
            raise ValueError("loopback target must use plain HTTP")
    elif not allow_staging or parsed.scheme != "https" or host != allowed_host.lower().rstrip("."):
        raise ValueError("non-loopback target requires HTTPS and an exact allowed-host match")
    if parsed.path not in {"", "/"}:
        raise ValueError("target must be an origin, without a path prefix")
    return f"{parsed.scheme}://{parsed.netloc}"


def main() -> int:
    raw_url = os.getenv("V2_VERIFY_BASE_URL", "")
    try:
        base_url = validate_target(raw_url, allow_staging=os.getenv("V2_VERIFY_ALLOW_STAGING") == "true",
                                   allowed_host=os.getenv("V2_VERIFY_ALLOWED_HOST", ""))
    except ValueError as exc:
        print(f"FAIL target configuration: {exc}")
        return 2
    token = os.getenv("V2_VERIFY_BEARER_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    checks: list[tuple[str, bool]] = []
    client = httpx.Client(timeout=8.0, follow_redirects=False, headers=headers)

    def get(path: str):
        response = client.get(base_url + path)
        return response

    def record(name: str, success: bool):
        checks.append((name, success))
        print(f"{'PASS' if success else 'FAIL'} {name}")

    try:
        try:
            response = get("/health")
            record("health endpoint", response.status_code == 200 and response.json().get("status") == "ok")
        except Exception:
            record("health endpoint", False)
        try:
            response = get("/ready")
            record("readiness endpoint", response.status_code == 200 and response.json().get("status") == "ready")
        except Exception:
            record("readiness endpoint", False)
        try:
            response = get("/openapi.json")
            spec = response.json() if response.status_code == 200 else {}
            record("OpenAPI document", response.status_code == 200 and isinstance(spec.get("paths"), dict))
        except Exception:
            spec = {}
            record("OpenAPI document", False)
        try:
            response = client.post(base_url + "/api/v1/scans/measurements", json=MEASUREMENTS)
            body = response.json() if response.status_code == 200 else {}
            indicator = body.get("drowsiness_indicator", {})
            clean = (body.get("source") == "client_measurements"
                     and body.get("measurement_provenance") == "client-supplied, unverified"
                     and body.get("label") == "indicative, non-diagnostic, not a validated classifier"
                     and indicator.get("status") in {"available", "insufficient_quality"}
                     and not any(word in body for word in ("confidence", "accuracy")))
            record("authenticated measurements contract", response.status_code == 200 and clean)
        except Exception:
            record("authenticated measurements contract", False)
    finally:
        client.close()
    failed = sum(not passed for _, passed in checks)
    print(f"Summary: {len(checks) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
