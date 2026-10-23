"""Run actual MediaPipe/OpenCV extraction on generated synthetic video only."""
from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
import statistics
import tempfile
import time
from pathlib import Path
import sys

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.media.extractors import _face_mesh_factory, extract_video


def make_clip(path: Path, width: int, height: int, fps: int = 30, seconds: int = 5) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (width, height))
    if not writer.isOpened():
        raise RuntimeError("synthetic video writer unavailable")
    for index in range(fps * seconds):
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        frame[:, (index * 7) % width:(index * 7) % width + max(1, width // 40)] = (0, 60, 110)
        writer.write(frame)
    writer.release()


def summarize(samples: list[float]) -> dict:
    ordered = sorted(samples)
    def percentile(p):
        return ordered[min(len(ordered)-1, max(0, round((len(ordered)-1)*p)))]
    return {"n": len(samples), "p50_seconds": percentile(.50), "p95_seconds": percentile(.95), "p99_seconds": percentile(.99), "max_seconds": max(samples)}


def rss_megabytes() -> float | None:
    try:
        if os.name == "nt":
            class Counters(ctypes.Structure):
                _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
            counters=Counters(); counters.cb=ctypes.sizeof(counters)
            psapi=ctypes.WinDLL("Psapi.dll"); kernel=ctypes.WinDLL("Kernel32.dll")
            ok=psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(),ctypes.byref(counters),counters.cb)
            return counters.WorkingSetSize/(1024*1024) if ok else None
        import resource
        value=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return value/(1024*1024) if sys.platform != "darwin" else value/(1024*1024)
    except Exception:
        return None


def run(repeats: int) -> dict:
    results = []
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="v2-loadtest-", dir=root / "data") as directory:
        folder = Path(directory)
        for width, height, label in ((854, 480, "480p"), (1280, 720, "720p")):
            clip = folder / f"synthetic-{label}.avi"
            make_clip(clip, width, height)
            for sample_hz in (10, 15, 30):
                cold, warm, cpu_samples, rss_samples = [], [], [], []
                shared_mesh = _face_mesh_factory(static_image_mode=False)
                try:
                    for _ in range(repeats):
                        started, cpu = time.perf_counter(), time.process_time()
                        mesh = _face_mesh_factory(static_image_mode=False)
                        try:
                            extract_video(clip, max_seconds=5, max_frames=180, sample_hz=sample_hz, max_pixels=4_000_000, face_mesh=mesh)
                        finally:
                            mesh.close()
                        cold.append(time.perf_counter()-started)
                        cpu_samples.append(time.process_time()-cpu)
                        rss = rss_megabytes()
                        if rss is not None: rss_samples.append(rss)
                    # Reuse the loaded graph to measure a warm invocation.
                    for _ in range(repeats):
                        started = time.perf_counter()
                        extracted = extract_video(clip, max_seconds=5, max_frames=180, sample_hz=sample_hz, max_pixels=4_000_000, face_mesh=shared_mesh)
                        warm.append(time.perf_counter()-started)
                finally:
                    shared_mesh.close()
                results.append({"resolution": label, "source_fps": 30, "sampling_hz": sample_hz,
                    "fixture": "generated synthetic non-human black/stripe video; not pilot media",
                    "cold": summarize(cold), "warm": summarize(warm),
                    "cpu_seconds_per_scan_median": statistics.median(cpu_samples),
                    "process_rss_mb_after_extraction": max(rss_samples) if rss_samples else None,
                    "last_quality_status": extracted["status"]})
    return {"scope": "local extractor only; no upload, queue, storage or network", "results": results}


def run_measurements_burst(count: int, concurrency: int) -> dict:
    """Exercise the measurements API locally with synthetic data and dummy test auth."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from fastapi.testclient import TestClient
    from app.core.config import Settings
    from app.main import create_app
    from app.security.rate_limit import TenantRateLimiter
    payload={"short_clip_eye_closure_fraction":0.03,"blink_rate_per_minute":10,
        "mean_eye_closure_seconds":0.1,"max_eye_closure_seconds":0.2,"face_detection_ratio":0.9,
        "mean_luminance":80,"mean_blur_variance":40,"temporal_coverage":0.8,"yaw_proxy":0.01,"pitch_proxy":0.01}
    settings=Settings(environment="test",db_path=":memory:",dev_auth_tokens={"synthetic-test-token":{"sub":"synthetic-user","tenant_id":"synthetic-tenant"}},queue_capacity=max(1000,count+1))
    app=create_app(settings)
    app.state.rate_limiter=TenantRateLimiter(max(count,concurrency))
    statuses=[];latencies=[]
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("wellar.request").setLevel(logging.WARNING)
    logging.getLogger("wellar.app").setLevel(logging.WARNING)
    with TestClient(app) as client:
        def call_one(_):
            started=time.perf_counter()
            response=client.post("/api/v1/scans/measurements",headers={"Authorization":"Bearer synthetic-test-token"},json=payload)
            return response.status_code,(time.perf_counter()-started)
        started=time.perf_counter(); cpu=time.process_time()
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures=[pool.submit(call_one,index) for index in range(count)]
            for future in as_completed(futures):
                status,latency=future.result();statuses.append(status);latencies.append(latency)
        wall=time.perf_counter()-started; cpu_seconds=time.process_time()-cpu
    latencies.sort()
    def pct(p): return latencies[min(len(latencies)-1,round((len(latencies)-1)*p))]
    return {"scope":"in-process TestClient; no network, media, queue worker or database service",
        "synthetic_fixture":True,"requests":count,"concurrency":concurrency,"status_counts":{str(status):statuses.count(status) for status in sorted(set(statuses))},
        "p50_seconds":pct(.50),"p95_seconds":pct(.95),"p99_seconds":pct(.99),"max_seconds":max(latencies),
        "throughput_requests_per_second":count/wall,"cpu_seconds_total":cpu_seconds}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--measurements-burst", type=int, default=0, help="run local synthetic API burst (max 2000)")
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--measurements-only", action="store_true")
    args = parser.parse_args()
    if args.repeats < 1 or args.repeats > 20:
        parser.error("repeats must be from 1 to 20")
    if args.measurements_burst < 0 or args.measurements_burst > 2000 or args.concurrency < 1:
        parser.error("measurements-burst must be 0..2000 and concurrency must be positive")
    result = {} if args.measurements_only else run(args.repeats)
    if args.measurements_burst:
        result["measurements_only_burst"] = run_measurements_burst(args.measurements_burst,args.concurrency)
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        output=args.output.resolve()
        if Path(__file__).resolve().parents[1] not in output.parents:
            parser.error("output must be inside the V2 workspace")
        output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
