"""Bounded HTTP benchmark for the explicitly isolated Sprint 08 Compose stack.

This harness never starts containers. It accepts only the loopback V2 port
18080 and writes aggregate, credential-free JSON beneath the V2 project root.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import os
import platform
import random
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values

from benchmarks.e2e_scan import make_media


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = PROJECT_ROOT / "deployment" / "sprint08" / "compose.yaml"
EXPECTED_ENDPOINT = "http://127.0.0.1:18080"
COHORTS = (1, 10, 25, 50)
MODALITIES = {"image": "image/jpeg", "video": "video/mp4", "audio": "audio/wav"}
SAFE_ERROR_CODES = {
    "upload_capacity_exceeded", "media_storage_quota", "media_too_large",
    "media_not_found", "object_storage_unavailable", "media_metadata_unavailable",
    "scan_queue_full", "idempotency_conflict", "idempotency_key_reused",
}


def percentile(values: list[float], fraction: float):
    if not values:
        return None
    values = sorted(values)
    return round(values[max(0, math.ceil(len(values) * fraction) - 1)], 3)


def summarize(values: list[float]) -> dict:
    return {
        "sample_count": len(values),
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
    }


def parse_cohorts(value: str) -> tuple[int, ...]:
    """Parse at most five unique positive cohorts, each capped at 50."""
    try:
        values = tuple(int(part.strip()) for part in value.split(","))
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("cohorts must be comma-separated integers") from None
    if not values or len(values) > 5 or len(set(values)) != len(values) or any(v < 1 or v > 50 for v in values):
        raise argparse.ArgumentTypeError("cohorts must contain 1..5 unique values in the range 1..50")
    return values


def safe_failure_code(payload, status):
    """Return a bounded allowlisted category; never echo server error text."""
    safe_code = None
    if isinstance(payload, dict):
        error = payload.get("error") or payload.get("detail")
        if isinstance(error, dict):
            code = error.get("code")
            if isinstance(code, str) and code in SAFE_ERROR_CODES:
                safe_code = code
    if int(status) == 429:
        return f"http_429:{safe_code}" if safe_code else "http_429"
    if safe_code:
        return safe_code
    return f"http_{int(status)}"


def safe_429_reason(payload) -> str:
    """Return only an allow-listed 429 reason, never arbitrary server text."""
    classified = safe_failure_code(payload, 429)
    return classified.partition(":")[2] or "unclassified"


def request(url: str, method: str, token: str, *, body=None, content_type=None, idem=None, timeout=10):
    data = body if isinstance(body, bytes) else json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {token}"}
    if content_type:
        headers["Content-Type"] = content_type
    if idem:
        headers["Idempotency-Key"] = idem
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
            try:
                payload = json.loads(raw) if raw else None
            except (ValueError, TypeError):
                payload = None
            return response.status, payload, (time.perf_counter() - start) * 1000
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read()
            payload = json.loads(raw) if raw else None
        except Exception:
            payload = None
        return exc.code, payload, (time.perf_counter() - start) * 1000


def load_worker_events(scan_ids: set[str]) -> dict:
    """Return only allow-listed timing events; never persist raw service logs."""
    try:
        result = subprocess.run(
            ["docker", "compose", "--project-name", "wellar-v2-sprint08", "--file", str(COMPOSE_FILE),
             "--env-file", str(PROJECT_ROOT / ".env.sprint08"), "logs", "--no-color", "worker"],
            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"available": False, "reason": "container_logs_unavailable"}
    if result.returncode:
        return {"available": False, "reason": "container_logs_unavailable"}
    timing = defaultdict(list)
    counts = Counter()
    modalities = ("image", "video", "audio")
    for line in result.stdout.splitlines():
        try:
            event = json.loads(line[line.find("{"):])
        except (ValueError, TypeError):
            continue
        if event.get("scan_id") not in scan_ids:
            continue
        name = event.get("event_name")
        stage = event.get("processing_stage")
        duration = event.get("duration_ms")
        if isinstance(duration, (int, float)) and 0 <= duration < 86_400_000:
            if stage in {"queue_wait", "analysis", "result_commit"} or (isinstance(stage, str) and stage.endswith(("_extraction", "_decode", "_processing"))):
                timing[str(stage)].append(float(duration))
        if name in {"scan.processing.completed", "scan.processing.retry_scheduled", "scan.processing.terminal_failure", "scan.processing.lease_lost"}:
            counts[name] += 1
        if isinstance(name, str) and name.startswith("media.") and stage:
            for modality in modalities:
                if stage.startswith(modality + "_"):
                    counts[f"{modality}_media_events"] += 1
    return {"available": True, "stage_latency_ms": {k: summarize(v) for k, v in timing.items()}, "worker_event_counts": dict(counts)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run bounded real-HTTP PostgreSQL/MinIO scan cohorts against the local Sprint 08 stack")
    parser.add_argument("--endpoint", default=EXPECTED_ENDPOINT)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--deadline-seconds", type=float, default=90)
    parser.add_argument("--workers", type=int, choices=(1, 2, 4), default=1, help="operator-reported worker count; this harness does not scale containers")
    parser.add_argument("--cohorts", type=parse_cohorts, default=COHORTS, help="comma-separated cohort sizes (default: 1,10,25,50; max 50 each)")
    parser.add_argument("--skip-idempotency-replay", action="store_true", help="omit the separate 10-request replay exercise for a one-scan smoke test")
    parser.add_argument("--output", default="SPRINT_08_DISTRIBUTED_BENCHMARK.json")
    args = parser.parse_args(argv)
    parsed = urllib.parse.urlparse(args.endpoint)
    if args.endpoint != EXPECTED_ENDPOINT or parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.port != 18080:
        parser.error("endpoint is fixed to the Sprint 08 loopback API at http://127.0.0.1:18080")
    if not 1 <= args.repetitions <= 3 or not 10 <= args.deadline_seconds <= 180:
        parser.error("repetitions must be 1..3 and deadline 10..180 seconds")
    output = (PROJECT_ROOT / args.output).resolve()
    if output.parent != PROJECT_ROOT or output == PROJECT_ROOT:
        parser.error("output must be a new file directly beneath the V2 project root")

    local_credentials = dotenv_values(PROJECT_ROOT / ".env.sprint08")
    token = local_credentials.get("WELLAR_TEST_AUTH_TOKEN")
    if not token:
        parser.error("generate the gitignored .env.sprint08 file first")
    try:
        configured_upload_slots = int(local_credentials.get("WELLAR_MAX_CONCURRENT_UPLOADS", "4"))
    except (TypeError, ValueError):
        configured_upload_slots = None

    started_at = datetime.now(timezone.utc).isoformat()
    run_start = time.perf_counter()
    scan_upload_ms, scan_terminal_ms, result_visible_ms = [], [], []
    failed_upload_ms, failed_submit_ms, failed_scan_ms = [], [], []
    request_latencies = defaultdict(list)
    failure_types = Counter()
    http_requests_by_endpoint = Counter()
    http_429_by_endpoint_reason = Counter()
    response_counts_lock = threading.Lock()
    retries_429 = 0
    cleanup_failures = 0
    assets_retained_for_unresolved_scans = 0
    accepted_total = completed_total = failed_total = timeout_total = 0
    all_scan_ids: set[str] = set()
    cohorts = []
    idempotency_result = {"skipped": bool(args.skip_idempotency_replay), "requests": 0, "accepted": 0, "created": 0, "distinct_scan_ids": 0}

    def tracked_request(endpoint_label: str, url: str, method: str, **kwargs):
        """Count endpoint attempts and safe 429 categories without identifiers."""
        with response_counts_lock:
            http_requests_by_endpoint[endpoint_label] += 1
        response = request(url, method, token, **kwargs)
        status, payload, _ = response
        if status == 429:
            reason = safe_429_reason(payload)
            with response_counts_lock:
                http_429_by_endpoint_reason[f"{endpoint_label}:{reason}"] += 1
        return response

    with tempfile.TemporaryDirectory(prefix="wellar-sprint08-fixtures-", dir=PROJECT_ROOT / "data") as temp:
        media = make_media(Path(temp))
        fixture_bytes = {
            modality: path.stat().st_size
            for modality, (path, _) in media.items()
            if modality in MODALITIES
        }

        def delete_assets(refs):
            nonlocal cleanup_failures
            for ref in tuple(refs.values()):
                try:
                    query = urllib.parse.urlencode({"media_ref": ref})
                    status, payload, elapsed = tracked_request("media_delete", args.endpoint + "/api/v1/media?" + query, "DELETE", timeout=5)
                    request_latencies["cleanup"].append(elapsed)
                    if status != 204:
                        failure_types[f"cleanup:{safe_failure_code(payload, status)}"] += 1
                        cleanup_failures += 1
                except Exception:
                    failure_types["cleanup:request_error"] += 1
                    cleanup_failures += 1

        def upload_all(deadline, refs):
            nonlocal retries_429
            for modality, (path, content_type) in media.items():
                if modality not in MODALITIES:
                    continue
                blob = path.read_bytes()
                for attempt in range(5):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return {"stage": "upload", "code": "deadline_exceeded", "latency_ms": None}
                    try:
                        status, payload, elapsed = tracked_request(
                            f"media_upload:{modality}", args.endpoint + "/api/v1/media/" + modality, "POST",
                            body=blob, content_type=content_type, timeout=min(10, remaining),
                        )
                    except (TimeoutError, urllib.error.URLError, OSError):
                        return {"stage": "upload", "code": "request_error", "latency_ms": None}
                    request_latencies[f"upload_{modality}"].append(elapsed)
                    if status == 429 and attempt < 4:
                        retries_429 += 1
                        time.sleep(min(1.0, 0.1 * (2 ** attempt)) * random.uniform(0.8, 1.2))
                        continue
                    if status != 201 or not isinstance(payload, dict) or not isinstance(payload.get("media_ref"), str):
                        return {"stage": "upload", "code": safe_failure_code(payload, status), "latency_ms": elapsed}
                    refs[modality] = payload["media_ref"]
                    break
            return None

        def await_result(scan_id, deadline):
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                try:
                    status, payload, elapsed = tracked_request("scan_status_poll", args.endpoint + f"/api/v1/scans/{scan_id}", "GET", timeout=min(5, remaining))
                except (TimeoutError, urllib.error.URLError, OSError):
                    return "request_error", None, None
                request_latencies["poll"].append(elapsed)
                if status != 200 or not isinstance(payload, dict):
                    failure_types[f"poll:{safe_failure_code(payload, status)}"] += 1
                    return "request_error", None, None
                if payload.get("status") in {"completed", "failed"}:
                    terminal_at = time.perf_counter()
                    if payload["status"] == "failed":
                        return "failed", None, terminal_at
                    try:
                        status, result, elapsed = tracked_request("result_retrieval", args.endpoint + f"/api/v1/scans/{scan_id}/result", "GET", timeout=5)
                    except (TimeoutError, urllib.error.URLError, OSError):
                        return "request_error", None, terminal_at
                    request_latencies["result_get"].append(elapsed)
                    if status != 200 or not isinstance(result, dict):
                        failure_types[f"result:{safe_failure_code(result, status)}"] += 1
                        return "request_error", None, terminal_at
                    if result.get("status") != "insufficient_evidence" or result.get("targets", {}).get("reference_sleepiness", {}).get("value") is not None or result.get("targets", {}).get("vigilance_impairment", {}).get("value") is not None:
                        return "safety_invariant_failed", None, terminal_at
                    return "completed", result, terminal_at
                time.sleep(min(0.1, max(0, remaining)))
            return "timeout", None, None

        for repetition in range(1, args.repetitions + 1):
            for concurrency in args.cohorts:
                def run_scan(index):
                    uploaded_start = time.perf_counter()
                    deadline = time.monotonic() + args.deadline_seconds
                    refs = {}
                    cleaned = False
                    retain_refs = False
                    accepted_scan = False
                    scan_id = None

                    def cleanup_owned_refs():
                        nonlocal cleaned
                        if not cleaned:
                            delete_assets(refs)
                            cleaned = True

                    try:
                        upload_error = upload_all(deadline, refs)
                        if upload_error:
                            failure_types[f"upload:{upload_error['code']}"] += 1
                            if upload_error["latency_ms"] is not None:
                                failed_upload_ms.append(upload_error["latency_ms"])
                            cleanup_owned_refs()
                            return {"accepted": False, "completed": False, "timeout": False, "failure_stage": "upload", "failure_code": upload_error["code"], "failure_latency_ms": upload_error["latency_ms"]}
                        submitted_at = time.perf_counter()
                        payload = {"schema_version": "1.0", "consent": {"media_processing": True, "research_use": True}, "media_refs": list(refs.values())}
                        try:
                            status, accepted, elapsed = tracked_request(
                                "scan_submission", args.endpoint + "/api/v1/scans", "POST", body=payload,
                                content_type="application/json", idem=f"s08-{repetition}-{concurrency}-{index}-{uuid4().hex}",
                                timeout=max(0.1, min(10, deadline - time.monotonic())),
                            )
                        except (TimeoutError, urllib.error.URLError, OSError):
                            retain_refs = True  # submission may have committed before the response was lost
                            failure_types["submit:response_unknown"] += 1
                            return {"accepted": False, "completed": False, "timeout": False, "failure_stage": "submit", "failure_code": "response_unknown", "failure_latency_ms": (time.perf_counter() - submitted_at) * 1000, "refs": refs}
                        request_latencies["submit"].append(elapsed)
                        if status != 202 or not isinstance(accepted, dict) or not isinstance(accepted.get("scan_id"), str):
                            code = safe_failure_code(accepted, status)
                            failure_types[f"submit:{code}"] += 1
                            failed_submit_ms.append(elapsed)
                            cleanup_owned_refs()
                            return {"accepted": False, "completed": False, "timeout": False, "failure_stage": "submit", "failure_code": code, "failure_latency_ms": elapsed}
                        scan_id = accepted["scan_id"]
                        accepted_scan = True
                        retain_refs = True
                        outcome, _, terminal_at = await_result(scan_id, deadline)
                        if outcome == "timeout":
                            return {"accepted": True, "completed": False, "timeout": True, "scan_id": scan_id, "refs": refs}
                        if outcome == "failed":
                            retain_refs = False
                            cleanup_owned_refs()
                            failure_latency = (terminal_at - uploaded_start) * 1000 if terminal_at else None
                            failure_types["scan:terminal_failure"] += 1
                            return {"accepted": True, "completed": False, "timeout": False, "scan_id": scan_id, "failure_stage": "worker", "failure_code": "terminal_failure", "failure_latency_ms": failure_latency}
                        if outcome == "request_error":
                            retain_refs = True
                            failure_types["scan:result_poll_unavailable"] += 1
                            return {"accepted": True, "completed": False, "timeout": False, "scan_id": scan_id, "failure_stage": "result", "failure_code": "request_error", "failure_latency_ms": (time.perf_counter() - uploaded_start) * 1000, "refs": refs}
                        if outcome != "completed":
                            failure_types[f"scan:{outcome}"] += 1
                            retain_refs = False
                            cleanup_owned_refs()
                            return {"accepted": True, "completed": False, "timeout": False, "scan_id": scan_id, "failure_stage": "result", "failure_code": outcome, "failure_latency_ms": (time.perf_counter() - uploaded_start) * 1000}
                        visible_at = time.perf_counter()
                        retain_refs = False
                        cleanup_owned_refs()
                        return {
                            "accepted": True, "completed": True, "timeout": False, "scan_id": scan_id,
                            "upload_start_to_visible_ms": (visible_at - uploaded_start) * 1000,
                            "submit_to_terminal_ms": (terminal_at - submitted_at) * 1000,
                            "submit_to_result_visible_ms": (visible_at - submitted_at) * 1000,
                        }
                    except Exception:
                        failure_types["scan_attempt:unexpected_error"] += 1
                        if not retain_refs:
                            cleanup_owned_refs()
                        return {"accepted": accepted_scan, "completed": False, "timeout": False, "scan_id": scan_id, "failure_stage": "harness", "failure_code": "unexpected_error", "failure_latency_ms": (time.perf_counter() - uploaded_start) * 1000, "refs": refs if retain_refs else {}}

                cohort_start = time.perf_counter()
                submission_requests_before = http_requests_by_endpoint["scan_submission"]
                cohort_failed_upload_start = len(failed_upload_ms)
                queue_samples = []
                stop_sampling = threading.Event()

                def sample_queue():
                    while not stop_sampling.is_set():
                        try:
                            status, payload, elapsed = tracked_request("readiness_queue_sample", args.endpoint + "/ready", "GET", timeout=1)
                        except (TimeoutError, urllib.error.URLError, OSError):
                            continue
                        request_latencies["queue_sampler"].append(elapsed)
                        if status == 200 and isinstance(payload, dict) and isinstance(payload.get("queue"), dict):
                            queue_samples.append(payload["queue"])
                        stop_sampling.wait(0.25)

                sampler = threading.Thread(target=sample_queue, name="sprint08-queue-sampler", daemon=True)
                sampler.start()
                try:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
                        outcomes = list(pool.map(run_scan, range(concurrency)))
                except Exception:
                    # A truly unexpected exception is categorized without serializing exception text.
                    outcomes = [{"accepted": False, "completed": False, "timeout": False, "failure_stage": "harness", "failure_code": "unexpected_worker_exception"} for _ in range(concurrency)]
                    failure_types["harness:unexpected_worker_exception"] += 1
                finally:
                    stop_sampling.set()
                    sampler.join(timeout=2)
                    if sampler.is_alive():
                        failure_types["harness:queue_sampler_shutdown_timeout"] += 1
                elapsed = time.perf_counter() - cohort_start
                accepted = sum(bool(item.get("accepted")) for item in outcomes)
                completed = sum(bool(item.get("completed")) for item in outcomes)
                failed = sum(not item.get("completed") and not item.get("timeout") for item in outcomes)
                timeouts = sum(bool(item.get("timeout")) for item in outcomes)
                failed_scan_ms.extend(item["failure_latency_ms"] for item in outcomes if item.get("failure_latency_ms") is not None and item.get("failure_stage") in {"worker", "result"})
                assets_retained_for_unresolved_scans += sum(len(item.get("refs", {})) for item in outcomes)
                ids = {item["scan_id"] for item in outcomes if item.get("scan_id")}
                all_scan_ids.update(ids)
                successful = [item for item in outcomes if item.get("completed")]
                upload_ms = [item["upload_start_to_visible_ms"] for item in successful]
                terminal_ms = [item["submit_to_terminal_ms"] for item in successful]
                visible_ms = [item["submit_to_result_visible_ms"] for item in successful]
                scan_upload_ms.extend(upload_ms)
                scan_terminal_ms.extend(terminal_ms)
                result_visible_ms.extend(visible_ms)
                accepted_total += accepted
                completed_total += completed
                failed_total += failed
                timeout_total += timeouts
                try:
                    queue_status, queue_data, _ = tracked_request("readiness_queue_snapshot", args.endpoint + "/ready", "GET")
                except Exception:
                    queue_status, queue_data = 0, None
                    failure_types["queue_snapshot:request_error"] += 1
                queue = queue_data.get("queue") if queue_status == 200 and isinstance(queue_data, dict) else None
                depth_trace = {
                    "sample_count": len(queue_samples),
                    "max_queued": max((int(item.get("queued", 0)) for item in queue_samples), default=None),
                    "max_running": max((int(item.get("running", 0)) for item in queue_samples), default=None),
                    "max_terminal_failures": max((int(item.get("terminal_failures", 0)) for item in queue_samples), default=None),
                    "max_oldest_queued_age_seconds": max((float(item.get("oldest_queued_age_seconds") or 0) for item in queue_samples), default=None),
                    "sample_interval_ms": 250,
                }
                cohorts.append({
                    "repetition": repetition, "concurrency": concurrency,
                    "scan_attempts": len(outcomes),
                    "scan_submission_requests": http_requests_by_endpoint["scan_submission"] - submission_requests_before,
                    "accepted": accepted, "completed": completed, "failed": failed, "timeouts": timeouts,
                    "failure_codes": dict(Counter(item.get("failure_code") for item in outcomes if item.get("failure_code"))),
                    "failed_upload_request_latency_ms": summarize(failed_upload_ms[cohort_failed_upload_start:]),
                    "elapsed_seconds": round(elapsed, 3),
                    "accepted_scans_per_second": round(accepted / elapsed, 3) if elapsed else 0,
                    "throughput_scans_per_second": round(completed / elapsed, 3) if elapsed else 0,
                    "upload_start_to_result_visible_ms": summarize(upload_ms),
                    "submit_to_terminal_ms": summarize(terminal_ms),
                    "submit_to_result_visible_ms": summarize(visible_ms),
                    "failed_scan_end_to_end_ms": summarize([item["failure_latency_ms"] for item in outcomes if item.get("failure_stage") in {"worker", "result"} and item.get("failure_latency_ms") is not None]),
                    "queue_snapshot_after_cohort": queue,
                    "queue_depth_trace": depth_trace,
                })

        if not args.skip_idempotency_replay:
            idem_refs = {}
            idem_refs_cleaned = False
            idem_media_may_be_in_use = False

            def cleanup_idempotency_refs():
                nonlocal idem_refs_cleaned
                if not idem_refs_cleaned:
                    delete_assets(idem_refs)
                    idem_refs_cleaned = True

            try:
                with tempfile.TemporaryDirectory(prefix="wellar-sprint08-replay-", dir=PROJECT_ROOT / "data") as temp:
                    media = make_media(Path(temp))
                    idem_deadline = time.monotonic() + args.deadline_seconds
                    upload_error = upload_all(idem_deadline, idem_refs)
                    if upload_error:
                        failure_types[f"idempotency_upload:{upload_error['code']}"] += 1
                        cleanup_idempotency_refs()
                    else:
                        idem_payload = {"schema_version": "1.0", "consent": {"media_processing": True, "research_use": True}, "media_refs": list(idem_refs.values())}
                        idem_key = "s08-replay-" + uuid4().hex
                        idem_media_may_be_in_use = True
                        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
                            replay_responses = list(pool.map(lambda _: tracked_request("idempotency_replay_submission", args.endpoint + "/api/v1/scans", "POST", body=idem_payload, content_type="application/json", idem=idem_key, timeout=max(0.1, min(10, idem_deadline - time.monotonic()))), range(10)))
                        replay_ok = [item for item in replay_responses if item[0] == 202 and isinstance(item[1], dict) and isinstance(item[1].get("scan_id"), str)]
                        replay_ids = {item[1]["scan_id"] for item in replay_ok}
                        created_count = sum(bool(item[1].get("created")) for item in replay_ok)
                        idempotency_result = {"skipped": False, "requests": 10, "accepted": len(replay_ok), "created": created_count, "distinct_scan_ids": len(replay_ids)}
                        if len(replay_ok) != 10 or len(replay_ids) != 1 or created_count != 1:
                            failure_types["concurrent_idempotency_replay"] += 1
                            idem_media_may_be_in_use = any(item[0] == 202 or item[0] >= 500 for item in replay_responses)
                            if idem_media_may_be_in_use:
                                assets_retained_for_unresolved_scans += len(idem_refs)
                            else:
                                cleanup_idempotency_refs()
                        else:
                            accepted_total += 1
                            replay_id = next(iter(replay_ids))
                            all_scan_ids.add(replay_id)
                            outcome, _, _ = await_result(replay_id, idem_deadline)
                            if outcome == "completed":
                                completed_total += 1
                                idem_media_may_be_in_use = False
                                cleanup_idempotency_refs()
                            elif outcome in {"timeout", "request_error"}:
                                timeout_total += 1
                                assets_retained_for_unresolved_scans += len(idem_refs)
                            else:
                                failed_total += 1
                                failure_types[f"idempotency_scan:{outcome}"] += 1
                                idem_media_may_be_in_use = False
                                cleanup_idempotency_refs()
            except Exception:
                failure_types["idempotency:harness_error"] += 1
                if idem_media_may_be_in_use:
                    assets_retained_for_unresolved_scans += len(idem_refs)
                else:
                    cleanup_idempotency_refs()

    try:
        worker_events = load_worker_events(all_scan_ids)
    except Exception:
        worker_events = {"available": False, "reason": "container_logs_unavailable"}
    result = {
        "benchmark": "wellar_v2_postgres_minio_real_http",
        "synthetic_only": True,
        "internet_used": False,
        "endpoint_class": "fixed_loopback_sprint08_compose",
        "run_started_at_utc": started_at,
        "run_finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": round(time.perf_counter() - run_start, 3),
        "machine": {"os": platform.platform(), "architecture": platform.machine(), "cpu": platform.processor() or None, "logical_processors": os.cpu_count()},
        "configuration": {"cohorts": args.cohorts, "repetitions": args.repetitions, "deadline_seconds": args.deadline_seconds, "worker_count_reported_by_operator": args.workers, "worker_scaling_performed_by_harness": False, "configured_upload_slots_from_env": configured_upload_slots, "media": list(MODALITIES.values()), "synthetic_fixture_bytes": fixture_bytes, "idempotency_replay_skipped": args.skip_idempotency_replay},
        "scan_attempts": sum(item["scan_attempts"] for item in cohorts) + (1 if not args.skip_idempotency_replay else 0),
        "scan_submission_requests": http_requests_by_endpoint["scan_submission"],
        "http_requests_by_endpoint": dict(http_requests_by_endpoint),
        "http_429_responses_total": sum(http_429_by_endpoint_reason.values()),
        "http_429_responses_by_endpoint_reason": dict(http_429_by_endpoint_reason),
        "accepted": accepted_total,
        "completed": completed_total,
        "failed": failed_total,
        "timeouts": timeout_total,
        "retry_429_count": retries_429,
        "media_cleanup": {"delete_failures": cleanup_failures, "assets_retained_for_unresolved_scans": assets_retained_for_unresolved_scans, "unknown_refs_after_ambiguous_upload_failure": True},
        "failure_types": dict(failure_types),
        "cohorts": cohorts,
        "latency_ms": {
            "successful_upload_start_to_result_visible": summarize(scan_upload_ms),
            "successful_submit_to_terminal": summarize(scan_terminal_ms),
            "successful_submit_to_result_visible": summarize(result_visible_ms),
            "failed_upload_request": summarize(failed_upload_ms),
            "failed_submit_request": summarize(failed_submit_ms),
            "failed_scan_end_to_end": summarize(failed_scan_ms),
            "requests": {name: summarize(values) for name, values in request_latencies.items()},
        },
        "concurrent_idempotency": idempotency_result,
        "worker_events": worker_events,
        "credential_free": True,
        "limitations": ["single host and local Compose stack only", "no inference or scientific validity claim", "queue snapshots are sampled after each cohort", "container resource telemetry is not collected by this HTTP harness", "a server-side object whose upload response was lost has no safe client-visible media_ref and is left to retention/operator reconciliation"],
    }
    try:
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(result, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except FileExistsError:
        parser.error("output already exists; choose a new output path")
    print(f"Wrote synthetic distributed HTTP measurements to {output.name}; no credentials or media were saved.")
    success = completed_total == accepted_total and failed_total == 0 and timeout_total == 0 and not failure_types and cleanup_failures == 0
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
