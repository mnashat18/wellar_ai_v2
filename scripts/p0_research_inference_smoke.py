"""Run opt-in research inference on consent-authorized local media only.

Outputs only modality statuses and timings; never prints labels, feature values,
identifiers, media paths, or copies the analyzed media.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.media.analysis import MultimodalFeatureProcessor
from app.ml.research_image_drowsiness import ResearchImageDrowsinessClassifier
from app.workers.worker import Worker


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--consent-file", required=True, type=Path)
    parser.add_argument("--session-file", required=True, type=Path)
    parser.add_argument("--media-dir", required=True, type=Path)
    parser.add_argument("--artifact-dir", required=True, type=Path)
    args = parser.parse_args()
    consent = json.loads(args.consent_file.read_text(encoding="utf-8"))
    permissions = consent.get("permissions", {})
    if consent.get("scope_confirmed_before_local_acknowledgement") is not True:
        raise SystemExit("consent scope is not confirmed")
    if permissions.get("media_analysis") is not True:
        raise SystemExit("local media analysis is not authorized")
    if permissions.get("model_training") is not False or permissions.get("evaluation") is not False:
        raise SystemExit("this smoke requires training and evaluation to remain unauthorized")
    session = json.loads(args.session_file.read_text(encoding="utf-8"))
    if not args.media_dir.is_dir() or args.media_dir.is_symlink():
        raise SystemExit("media directory is unavailable")
    kinds = {".jpg": "image", ".jpeg": "image", ".png": "image", ".mp4": "video", ".wav": "audio"}
    root = args.media_dir.resolve()
    files = []
    for record in session.get("media", []):
        if not isinstance(record, dict) or record.get("modality") not in {"image", "video", "audio"}:
            raise SystemExit("session media manifest is invalid")
        path = (args.session_file.parent / record.get("path", "")).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            raise SystemExit("session media path escapes the authorized media directory") from None
        if path.is_symlink() or not path.is_file() or path.suffix.lower() not in kinds:
            raise SystemExit("session media file is unavailable or unsupported")
        if kinds[path.suffix.lower()] != record["modality"]:
            raise SystemExit("session media type does not match its manifest")
        files.append(path)
    if not files:
        raise SystemExit("no consent-attested supported media found")
    import psutil
    process = psutil.Process()
    rss_before = process.memory_info().rss
    peak = [rss_before]
    stop_monitor = threading.Event()
    def monitor_memory():
        while not stop_monitor.wait(0.02):
            try:
                peak[0] = max(peak[0], process.memory_info().rss)
            except psutil.Error:
                return
    monitor = threading.Thread(target=monitor_memory, daemon=True)
    monitor.start()
    cpu_before = process.cpu_times()
    load_started = time.perf_counter()
    classifier = ResearchImageDrowsinessClassifier.load(
        args.artifact_dir, enabled=True, non_production=True, cpu_threads=1
    )
    cold_load_ms = round((time.perf_counter() - load_started) * 1000, 3)
    assert classifier is not None
    rss_after_load = process.memory_info().rss
    image_files = [path for path in files if kinds[path.suffix.lower()] == "image"]
    if not image_files:
        raise SystemExit("session manifest has no image for the still-image model")
    warm_times = [classifier.predict(image_files[0])["latency_ms"] for _ in range(5)]
    resolve = lambda self, ref, tenant, subject, verify_integrity: {
        "modality": kinds[Path(ref).suffix.lower()], "path": Path(ref),
        "integrity_ok": True, "available": True,
    }
    store = type("LocalConsentOnlyStore", (), {"resolve": resolve, "release": lambda self, asset: None})()
    processor = MultimodalFeatureProcessor(
        store, max_video_seconds=30, max_audio_seconds=30, max_image_pixels=4_000_000,
        max_video_frames=1800, video_sample_hz=4.0, research_image_classifier=classifier,
    )
    refs = [str(path) for path in files]
    queue_state = {"job": {
        "job_id": "local-smoke-job", "scan_id": "local-consented-smoke",
        "tenant_id": "", "subject_id": "", "request_id": "local-smoke",
        "payload_json": json.dumps({
            "media_refs": refs,
            "_media_manifest": [{"media_ref": ref, "modality": kinds[Path(ref).suffix.lower()]} for ref in refs],
        }),
        "created_at": time.time(), "attempts": 1,
    }, "result": None}
    queue_store = type("InMemoryQueue", (), {
        "lease_seconds": 30,
        "max_attempts": 3,
        "shared_monotonic_clock": False,
        "claim": lambda self, worker_id: queue_state.pop("job", None),
        "heartbeat": lambda self, job: True,
        "complete": lambda self, job, value: queue_state.update(result=value),
        "fail": lambda self, job, reason: queue_state.update(failed=reason),
    })()
    started = time.perf_counter()
    worker_processed = Worker(queue_store, "local-smoke-worker", processor).run_once()
    result = queue_state.get("result") or {}
    if not worker_processed or not result:
        raise SystemExit("local worker processing did not persist a result")
    timing = {name: item.get("timing_ms", {}) for name, item in result["evidence"]["modalities"].items()}
    research = result["evidence"].get("research_model_inference", {}).get("results", [])
    stop_monitor.set()
    monitor.join(timeout=1)
    peak[0] = max(peak[0], process.memory_info().rss)
    cpu_after = process.cpu_times()
    cpu_seconds = (cpu_after.user + cpu_after.system) - (cpu_before.user + cpu_before.system)
    print(json.dumps({
        "processor_status": result["status"],
        "prediction_targets": {name: value["status"] for name, value in result["prediction_extension"]["targets"].items()},
        "modality_status": {name: item["status"] for name, item in result["evidence"]["modalities"].items()},
        "research_model_invocations": len(research),
        "research_model_statuses": [item["status"] for item in research],
        "research_model_latencies_ms": [item.get("latency_ms") for item in research],
        "cold_model_load_ms": cold_load_ms,
        "warm_model_inference_ms_n5": warm_times,
        "warm_model_inference_median_ms": round(statistics.median(warm_times), 3),
        "process_rss_before_bytes": rss_before,
        "process_rss_after_load_bytes": rss_after_load,
        "process_peak_rss_bytes": peak[0],
        "process_cpu_seconds_including_load_and_scan": round(cpu_seconds, 3),
        "modality_timing_ms": timing,
        "worker_run_wall_ms": round((time.perf_counter() - started) * 1000, 3),
        "worker_result_persisted_in_memory": bool(queue_state.get("result")),
        "media_exported": False,
        "training_or_evaluation_performed": False,
    }, sort_keys=True))
    processor.close()


if __name__ == "__main__":
    main()
