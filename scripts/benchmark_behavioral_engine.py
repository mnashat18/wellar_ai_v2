"""Reproducible, bounded microbenchmark for the behavioral evidence projection."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import statistics
import sys
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.behavioral.engine import build_behavioral_evidence


PAYLOAD_PATH = ROOT / "benchmarks" / "behavioral_feature_payload_v1.json"
ITERATIONS = 500
WARMUP = 20


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, int((len(ordered) * fraction + 0.999999) - 1)))]


def main() -> int:
    raw = PAYLOAD_PATH.read_bytes()
    payload = json.loads(raw)
    if payload.get("schema_version") != "wellar.behavioral-benchmark-input/1.0":
        raise SystemExit("unsupported pinned benchmark payload schema")
    envelopes = payload["feature_envelopes"]
    measurements = payload["measurements"]
    for _ in range(WARMUP):
        build_behavioral_evidence(envelopes, measurements)
    samples: list[float] = []
    cpu_start = time.process_time()
    for _ in range(ITERATIONS):
        started = time.perf_counter_ns()
        output = build_behavioral_evidence(envelopes, measurements)
        samples.append((time.perf_counter_ns() - started) / 1_000_000)
    cpu_seconds = time.process_time() - cpu_start
    tracemalloc.start()
    for _ in range(20):
        output = build_behavioral_evidence(envelopes, measurements)
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    result_bytes = len(json.dumps(output, separators=(",", ":"), allow_nan=False).encode("utf-8"))
    report = {
        "benchmark_schema": "wellar.behavioral-engine-benchmark/1.0",
        "payload_sha256": hashlib.sha256(raw).hexdigest(),
        "payload_bytes": len(raw),
        "output_bytes": result_bytes,
        "iterations": ITERATIONS,
        "warmup_iterations": WARMUP,
        "latency_ms": {
            "median": round(statistics.median(samples), 6),
            "p95": round(percentile(samples, 0.95), 6),
            "max": round(max(samples), 6),
        },
        "cpu_seconds_for_measured_iterations": round(cpu_seconds, 6),
        "tracemalloc_peak_bytes": peak_bytes,
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "processor": platform.processor() or "unreported",
            "logical_cpu_count": os.cpu_count(),
            "measurement_scope": "in_process_projection_only; excludes media decode, model inference, storage, and API network",
        },
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
