"""Synthetic queue-only load measurement; it does not process media or labels."""
from __future__ import annotations

import concurrent.futures
import json
import statistics
import tempfile
import time
from pathlib import Path

from app.queue.sqlite_store import SQLiteQueueStore
from app.security.auth import Principal
from app.workers.worker import Worker


def _percentile(values, p):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * p))]


def run(scans: int, concurrency: int) -> dict:
    if scans < 1 or concurrency not in (1, 10, 50):
        raise ValueError("scans must be positive and concurrency must be 1, 10, or 50")
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteQueueStore(str(Path(directory) / "load.sqlite"), capacity=scans + 1)
        principal = Principal("synthetic-user", "synthetic-tenant")

        def submit(index):
            start = time.perf_counter()
            store.submit(f"scan-{index}", principal, f"key-{index}", str(index), "{}")
            return (time.perf_counter() - start) * 1000

        start = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            acceptance = list(pool.map(submit, range(scans)))
        acceptance_elapsed = time.perf_counter() - start
        worker = Worker(store, "p0-queue-load")
        start = time.perf_counter()
        completed = 0
        while completed < scans:
            if worker.run_once():
                completed += 1
            else:
                time.sleep(0.001)
        processing_elapsed = time.perf_counter() - start
        latency = [max(0, row["result_at"] - row["enqueued_at"]) * 1000 for row in store.db.execute(
            "SELECT results.created_at AS result_at,scans.created_at AS enqueued_at FROM results JOIN scans USING(scan_id)"
        ).fetchall()]
        output = {
            "benchmark": "synthetic_queue_only", "scans": scans, "concurrency": concurrency,
            "acceptance_scans_per_second": round(scans / acceptance_elapsed, 2),
            "completed_scans_per_second": round(completed / processing_elapsed, 2),
            "acceptance_latency_ms": {"p50": round(statistics.median(acceptance), 3), "p95": round(_percentile(acceptance, .95), 3)},
            "queue_to_commit_latency_ms": {"p50": round(statistics.median(latency), 3), "p95": round(_percentile(latency, .95), 3)},
            "processing_elapsed_seconds": round(processing_elapsed, 4), "errors": 0,
            "scope": "SQLite synthetic queue and no-op worker only; excludes media, inference, API networking, polling, and production storage.",
        }
        store.close()
        return output


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--scans", type=int, required=True)
    parser.add_argument("--concurrency", type=int, choices=(1, 10, 50), required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.scans, args.concurrency), sort_keys=True))
