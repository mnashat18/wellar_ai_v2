"""Bounded real-API, local-SQLite, separate-worker synthetic scan benchmark.

Run with: .venv\\Scripts\\python.exe -m benchmarks.e2e_scan
No external service or database is contacted. All state is temporary.
"""
from __future__ import annotations

import ctypes
import argparse
import base64
import json
import math
import os
import platform
import socket
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import cv2
import numpy as np
from PIL import Image

TOKEN = "sprint04c-synthetic-token"
TENANT = "sprint04c-synthetic-tenant"
SUBJECT = "sprint04c-synthetic-subject"
CONSENT = {"media_processing": True, "research_use": True}
SIZES = (1, 10, 25, 50)
CONCURRENCY_LEVELS = (1, 5, 10, 25, 50)
CONCURRENCY_REPEATS = max(1, min(5, int(os.getenv("WELLAR_BENCH_CONCURRENCY_REPEATS", "1"))))


def percentile(values, p):
    values = sorted(values)
    return round(values[min(len(values)-1, max(0, math.ceil(len(values)*p)-1))], 3) if values else None


def percentiles(values):
    return {"p50": percentile(values, .50), "p95": percentile(values, .95), "p99": percentile(values, .99)}


def percentile_summary(values):
    return {"sample_count":len(values),**percentiles(values)}


def call(url, method="GET", body=None, headers=None, timeout=5):
    data = body if isinstance(body, bytes) else json.dumps(body).encode() if body is not None else None
    request_headers=dict(headers or {})
    if body is not None and not isinstance(body,bytes): request_headers.setdefault("Content-Type","application/json")
    req = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try: payload = json.loads(raw)
        except Exception: payload = None
        return exc.code, payload


def make_media(root: Path):
    rgb = np.zeros((180, 240, 3), dtype=np.uint8)
    # Deterministic synthetic pattern exercises image decode without human features.
    rgb[30:150, 50:190] = 90
    jpeg = root / "synthetic.jpg"
    png = root / "synthetic.png"
    Image.fromarray(rgb, "RGB").save(jpeg, "JPEG", quality=85)
    Image.fromarray(rgb, "RGB").save(png, "PNG")
    video = root / "synthetic.mp4"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 4, (160, 120))
    if not writer.isOpened(): raise RuntimeError("installed OpenCV build cannot encode synthetic MP4")
    frame = np.zeros((120, 160, 3), dtype=np.uint8)
    frame[20:100, 40:120] = 80
    for _ in range(8): writer.write(frame)
    writer.release()
    audio = root / "synthetic.wav"
    rate = 16000
    t = np.arange(rate, dtype=np.float64) / rate
    samples = (np.sin(2 * np.pi * 440 * t) * 5000).astype("<i2")
    with wave.open(str(audio), "wb") as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(rate); out.writeframes(samples.tobytes())
    return {"image": (jpeg, "image/jpeg"), "png": (png, "image/png"), "video": (video, "video/mp4"), "audio": (audio, "audio/wav")}


def json_events(path):
    events = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                item = json.loads(line)
                if isinstance(item, dict) and "event_name" in item: events.append(item)
            except ValueError: pass
    except OSError: pass
    return events


def machine_details():
    details = {
        "os": platform.platform(),
        "architecture": platform.machine(),
        "cpu": platform.processor() or None,
        "logical_processors": os.cpu_count(),
    }
    if os.name == "nt":
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        memory = MEMORYSTATUSEX()
        memory.dwLength = ctypes.sizeof(memory)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            details["physical_memory_total_bytes"] = int(memory.ullTotalPhys)
            details["physical_memory_available_at_start_bytes"] = int(memory.ullAvailPhys)
    return details


def local_queue_counts(database: Path) -> dict[str, int]:
    """Read the disposable SQLite queue only; this path is created by this run."""
    connection = sqlite3.connect(str(database), timeout=1)
    try:
        counts = dict(connection.execute("SELECT status, count(*) FROM jobs GROUP BY status"))
        return {"queued": int(counts.get("queued", 0)), "running": int(counts.get("running", 0))}
    finally:
        connection.close()


def win_process_stats(pid):
    """Return process CPU seconds and peak working set via Windows process APIs."""
    if os.name != "nt": return None
    dollar=chr(36)
    command=f"{dollar}p=Get-Process -Id {int(pid)} -ErrorAction Stop; [pscustomobject]@{{CPU={dollar}p.CPU;PeakWorkingSet64={dollar}p.PeakWorkingSet64}} | ConvertTo-Json -Compress"
    encoded=base64.b64encode(command.encode("utf-16le")).decode("ascii")
    try:
        measured=subprocess.run(["powershell.exe","-NoProfile","-NonInteractive","-EncodedCommand",encoded],capture_output=True,text=True,timeout=5,check=True)
        values=json.loads(measured.stdout)
        return {"cpu_seconds":round(float(values.get("CPU") or 0),4),"peak_working_set_mb":round(float(values["PeakWorkingSet64"])/1024/1024,2)}
    except (OSError,subprocess.SubprocessError,ValueError,KeyError):
        pass
    # Fallback uses process APIs if PowerShell counters are unavailable.
    PROCESS_QUERY_INFORMATION, PROCESS_VM_READ = 0x0400, 0x0010
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle: return None
    try:
        class FILETIME(ctypes.Structure): _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]
        class PMC(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_uint32), ("PageFaultCount", ctypes.c_uint32), ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        created, exited, kernel, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
        pmc = PMC(); pmc.cb = ctypes.sizeof(pmc)
        kernel32.GetProcessTimes.argtypes = [ctypes.c_void_p, ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME)]
        kernel32.GetProcessTimes.restype = ctypes.c_int
        psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(PMC), ctypes.c_uint32]
        psapi.GetProcessMemoryInfo.restype = ctypes.c_int
        if not kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)): return None
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb): return None
        ticks = lambda ft: (ft.high << 32) | ft.low
        return {"cpu_seconds": round((ticks(kernel)+ticks(user))/10_000_000, 4), "peak_working_set_mb": round(pmc.PeakWorkingSetSize/1024/1024, 2)}
    finally:
        kernel32.CloseHandle(handle)


def terminate(proc):
    if proc is None or proc.poll() is not None: return
    proc.terminate()
    try: proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill(); proc.wait(timeout=3)


def verify_resource_counters():
    """Validate the Windows counters with a temporary known CPU/RAM reference."""
    code = "import time; b=bytearray(64*1024*1024); [b.__setitem__(i,1) for i in range(0,len(b),4096)]; end=time.perf_counter()+0.8; x=0;\nwhile time.perf_counter()<end: x+=1\ntime.sleep(2)"
    reference = subprocess.Popen([sys.executable, "-c", code])
    try:
        time.sleep(.7)
        stats = win_process_stats(reference.pid)
        valid = bool(stats and stats["cpu_seconds"] >= .1 and stats["peak_working_set_mb"] >= 32)
        return {"passed": valid, "observed": stats, "expected_minimums": {"cpu_seconds": .1, "peak_working_set_mb": 32}}
    finally:
        terminate(reference)


def main():
    if os.name != "nt": raise RuntimeError("This bounded harness currently measures Windows process resources only")
    parser = argparse.ArgumentParser(description="Run bounded, synthetic-only HTTP scan cohorts against local V2 API and worker processes")
    parser.add_argument("--output", type=Path, default=Path("SPRINT_07_BENCHMARK_RESULTS.json"),
                        help="new JSON result path inside the V2 project; existing files are never overwritten")
    parser.add_argument("--repetitions", type=int, default=CONCURRENCY_REPEATS,
                        help="repetitions per concurrency cohort (1-5)")
    args = parser.parse_args()
    if not 1 <= args.repetitions <= 5:
        raise ValueError("--repetitions must be between 1 and 5")
    repetitions = args.repetitions
    project_root = Path(__file__).resolve().parents[1]
    output_path = (Path.cwd() / args.output).resolve() if not args.output.is_absolute() else args.output.resolve()
    if not output_path.is_relative_to(project_root):
        raise ValueError("benchmark output must remain inside the V2 project")
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite benchmark result: {output_path}")
    run_started_at = datetime.now(timezone.utc)
    resource_validation=verify_resource_counters()
    measurements = {"upload_ms": [], "upload_observations": [], "queue_acceptance_ms": [], "queue_wait_ms": [], "queue_wait_clock_counts": {}, "extraction_ms": {"image": [], "video": [], "audio": []}, "result_persistence_ms": [], "client_e2e_ms": [], "upload_start_to_result_ms": [], "concurrent_post_to_result_ms": [], "concurrent_submit_to_terminal_ms": [], "result_visibility_overhead_ms": []}
    run_summaries = []
    with tempfile.TemporaryDirectory(prefix="wellar-s04d-",ignore_cleanup_errors=True) as directory:
        root = Path(directory)
        media = make_media(root)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0)); port = probe.getsockname()[1]
        env = os.environ.copy()
        env.update({"WELLAR_ENV":"test", "WELLAR_LOG_LEVEL":"INFO", "WELLAR_DB_PATH":str(root/"queue.sqlite"), "WELLAR_MEDIA_ROOT":str(root/"media"), "WELLAR_QUEUE_BACKEND":"sqlite", "WELLAR_MEDIA_BACKEND":"local", "WELLAR_AUTH_MODE":"development", "WELLAR_POSTGRES_DSN":"", "WELLAR_S3_BUCKET":"", "WELLAR_S3_ENDPOINT_URL":"", "WELLAR_DEV_AUTH_TOKENS_JSON":json.dumps({TOKEN:{"sub":SUBJECT,"tenant_id":TENANT}}), "WELLAR_QUEUE_CAPACITY":"100", "WELLAR_LEASE_SECONDS":"3", "WELLAR_RETRY_BASE_SECONDS":"0.05", "WELLAR_MAX_ATTEMPTS":"3"})
        api_log = root/"api.jsonl"; worker_log = root/"worker.jsonl"
        api_out = api_log.open("w", encoding="utf-8", buffering=1)
        api_start = time.perf_counter()
        api = subprocess.Popen([sys.executable,"-m","uvicorn","app.main:app","--host","127.0.0.1","--port",str(port),"--log-level","warning"],env=env,stdout=api_out,stderr=subprocess.STDOUT)
        worker = None; worker_out = None
        base = f"http://127.0.0.1:{port}"
        auth = {"Authorization":f"Bearer {TOKEN}"}
        try:
            for _ in range(200):
                if api.poll() is not None: raise RuntimeError("API exited during startup")
                try:
                    status, ready = call(base+"/ready", timeout=1)
                    if status == 200 and ready.get("status") == "ready": break
                except (OSError, urllib.error.URLError): pass
                time.sleep(.05)
            else: raise TimeoutError("API did not become ready within 10 seconds")
            cold_api_startup = (time.perf_counter()-api_start)*1000
            worker_out = worker_log.open("w", encoding="utf-8", buffering=1)
            worker_start = time.perf_counter()
            worker = subprocess.Popen([sys.executable,"-m","app.workers.entrypoint"],env=env,stdout=worker_out,stderr=subprocess.STDOUT)
            worker_ready_deadline=time.monotonic()+10
            cold_worker_startup_ms=None
            while time.monotonic()<worker_ready_deadline:
                if worker.poll() is not None: raise RuntimeError("worker exited during cold startup")
                if any(e.get("event_name")=="worker.started" for e in json_events(worker_log)):
                    cold_worker_startup_ms=round((time.perf_counter()-worker_start)*1000,3)
                    break
                time.sleep(.025)
            if cold_worker_startup_ms is None: raise TimeoutError("worker did not start for cold startup measurement")

            # Upload all accepted formats; PNG is exercised separately because one image per scan is allowed.
            uploaded = {}
            for name, (path, content_type) in media.items():
                blob = path.read_bytes(); started=time.perf_counter()
                status, body=call(base+f"/api/v1/media/{'image' if name in ('image','png') else name}","POST",blob,{**auth,"Content-Type":content_type,"Content-Length":str(len(blob))})
                elapsed=(time.perf_counter()-started)*1000
                if status != 201: raise RuntimeError(f"synthetic {name} upload rejected: HTTP {status}")
                measurements["upload_observations"].append({"modality":"image" if name in ("image", "png") else name,"name":name,"size_bytes":len(blob),"duration_ms":elapsed})
                uploaded[name]=body["media_ref"]; measurements["upload_ms"].append(elapsed)

            def process_one(index, image_key="image", idem=None):
                key = idem or f"s04c-{index}"
                payload={"schema_version":"1.0","media_refs":[uploaded[image_key],uploaded["video"],uploaded["audio"]],"reaction_task":None,"consent":CONSENT}
                from app.scans.schemas import ScanSubmission
                ScanSubmission.model_validate(payload)
                started=time.perf_counter(); status, accepted=call(base+"/api/v1/scans","POST",payload,{**auth,"Idempotency-Key":key}); accepted_ms=(time.perf_counter()-started)*1000
                if status != 202: raise RuntimeError(f"scan submission failed: HTTP {status}, error={accepted}")
                measurements["queue_acceptance_ms"].append(accepted_ms)
                if not accepted.get("created") and idem is None: raise AssertionError("unexpected idempotent replay")
                deadline=time.monotonic()+25
                while time.monotonic()<deadline:
                    poll_status, result=call(base+f"/api/v1/scans/{accepted['scan_id']}/result","GET",headers=auth,timeout=2)
                    if poll_status==200: break
                    if poll_status!=404: raise RuntimeError(f"result poll failed: HTTP {poll_status}")
                    time.sleep(.025)
                else: raise TimeoutError("scan did not reach a persisted result")
                measurements["client_e2e_ms"].append((time.perf_counter()-started)*1000)
                assert result["status"]=="insufficient_evidence"
                assert result["targets"]["reference_sleepiness"]["value"] is None
                assert result["targets"]["vigilance_impairment"]["value"] is None
                for modality in ("image", "video", "audio"):
                    envelope=result["features"][modality]
                    assert envelope["presence"]=="present" and envelope["extraction_status"]!="not_provided"
                    assert envelope["provenance"].get("extractor")
                return accepted["scan_id"], result

            # Sequential workload: one worker, one outstanding scan; increases only after stable completion.
            for count in SIZES:
                batch=[]
                start=time.perf_counter()
                for i in range(count): batch.append(process_one(f"{count}-{i}"))
                run_summaries.append({"scans":count,"mode":"sequential","elapsed_seconds":round(time.perf_counter()-start,3),"completed":len(batch)})

            # Same bytes through PNG validation/extraction path, one authenticated scan.
            png_scan, png_result=process_one("png-path", image_key="png")
            assert png_result["features"]["image"]["modality"]=="image"

            # Concurrent complete scans: each cohort uploads its own assets, then a barrier
            # releases all authenticated scan POSTs together. One worker keeps resource use bounded.
            concurrent_runs=[]
            media_blobs={name:(path.read_bytes(),content_type) for name,(path,content_type) in media.items() if name in ("image","video","audio")}
            for repetition in range(1, repetitions + 1):
              for level in CONCURRENCY_LEVELS:
                barrier=threading.Barrier(level)
                upload_429_retries=[]
                upload_client_slots=threading.BoundedSemaphore(4)
                accepted_ids=[]
                terminal_failed_ids=[]
                def concurrent_scan(index):
                    upload_started=time.perf_counter()
                    refs=[]
                    for name,(blob,content_type) in media_blobs.items():
                        modality=name
                        headers={**auth,"Content-Type":content_type,"Content-Length":str(len(blob))}
                        for attempt in range(6):
                            upload_request_started=time.perf_counter()
                            with upload_client_slots:
                                code,body=call(base+f"/api/v1/media/{modality}","POST",blob,headers,timeout=10)
                            if code==201:
                                upload_duration=(time.perf_counter()-upload_request_started)*1000
                                measurements["upload_observations"].append({"modality":modality,"name":name,"size_bytes":len(blob),"duration_ms":upload_duration})
                                refs.append(body["media_ref"]); break
                            if code!=429: raise RuntimeError(f"concurrent {modality} upload returned HTTP {code}")
                            upload_429_retries.append(1)
                            time.sleep(.025*(attempt+1))
                        else: raise TimeoutError("concurrent media upload retry budget exhausted")
                    barrier.wait(timeout=30)
                    payload={"schema_version":"1.0","media_refs":refs,"reaction_task":None,"consent":CONSENT}
                    post_started=time.perf_counter()
                    code,accepted=call(base+"/api/v1/scans","POST",payload,{**auth,"Idempotency-Key":f"s04e-concurrent-{repetition}-{level}-{index}"},timeout=10)
                    if code!=202: raise RuntimeError(f"concurrent scan submission returned HTTP {code}")
                    accepted_ids.append(accepted["scan_id"])
                    measurements["queue_acceptance_ms"].append((time.perf_counter()-post_started)*1000)
                    queue_state=local_queue_counts(root/"queue.sqlite")
                    deadline=time.monotonic()+40
                    terminal_visible_ms=None
                    while time.monotonic()<deadline:
                        code,current=call(base+f"/api/v1/scans/{accepted['scan_id']}","GET",headers=auth,timeout=10)
                        if code!=200: raise RuntimeError(f"concurrent status poll returned HTTP {code}")
                        if current["status"]=="failed":
                            terminal_failed_ids.append(accepted["scan_id"])
                            raise RuntimeError("scan reached terminal failed status")
                        if current["status"]=="completed":
                            terminal_visible_ms=(time.perf_counter()-post_started)*1000
                            break
                        time.sleep(.025)
                    else: raise TimeoutError("concurrent scan result deadline expired")
                    code,result=call(base+f"/api/v1/scans/{accepted['scan_id']}/result","GET",headers=auth,timeout=10)
                    if code!=200: raise RuntimeError(f"result retrieval after terminal status returned HTTP {code}")
                    assert result["status"]=="insufficient_evidence"
                    assert result["targets"]["reference_sleepiness"]["value"] is None
                    assert result["targets"]["vigilance_impairment"]["value"] is None
                    for modality in ("image","video","audio"):
                        assert result["features"][modality]["presence"]=="present"
                        assert result["features"][modality]["provenance"].get("extractor")
                    post_e2e=(time.perf_counter()-post_started)*1000
                    upload_e2e=(time.perf_counter()-upload_started)*1000
                    measurements["upload_start_to_result_ms"].append(upload_e2e)
                    measurements["concurrent_post_to_result_ms"].append(post_e2e)
                    measurements["concurrent_submit_to_terminal_ms"].append(terminal_visible_ms)
                    measurements["result_visibility_overhead_ms"].append(max(0,post_e2e-terminal_visible_ms))
                    return {"scan_id":accepted["scan_id"],"upload_start_to_result_ms":upload_e2e,"post_to_result_ms":post_e2e,"queue_state_after_accept":queue_state}

                cohort_start=time.perf_counter(); outcomes=[]; failures=[]; run_failure_details=[]; timeouts=0
                with ThreadPoolExecutor(max_workers=level,thread_name_prefix=f"wellar-s04d-{level}") as pool:
                    futures=[pool.submit(concurrent_scan,i) for i in range(level)]
                    for future in as_completed(futures):
                        try: outcomes.append(future.result())
                        except Exception as exc:
                            failures.append(type(exc).__name__)
                            run_failure_details.append(f"{type(exc).__name__}: {str(exc)[:160]}")
                            if isinstance(exc,(TimeoutError,urllib.error.URLError)): timeouts+=1
                            try: barrier.abort()
                            except Exception: pass
                elapsed=time.perf_counter()-cohort_start
                stable=not failures and len(outcomes)==level
                queue_samples=[x["queue_state_after_accept"] for x in outcomes]
                concurrent_runs.append({"repetition":repetition,"simultaneous_scans":level,"mode":"concurrent_upload_barrier_then_concurrent_scan_posts; single worker process","accepted":len(accepted_ids),"completed":len(outcomes),"failed_terminal":len(terminal_failed_ids),"incomplete":len(accepted_ids)-len(outcomes)-len(terminal_failed_ids),"request_or_assertion_failures":len(failures),"failure_types":failures,"failure_details":run_failure_details,"timeouts":timeouts,"upload_capacity_retries_429":sum(upload_429_retries),"queue_state_samples":len(queue_samples),"max_queued_after_accept":max((x["queued"] for x in queue_samples),default=0),"max_running_after_accept":max((x["running"] for x in queue_samples),default=0),"elapsed_seconds":round(elapsed,3),"throughput_scans_per_second":round(len(outcomes)/elapsed,3),"upload_start_to_result_ms":{"sample_count":len(outcomes),**percentiles([x["upload_start_to_result_ms"] for x in outcomes])},"post_to_result_ms":{"sample_count":len(outcomes),**percentiles([x["post_to_result_ms"] for x in outcomes])},"scan_ids":[x["scan_id"] for x in outcomes],"stable":stable})
                if not stable: break

            # Verify logs show extractor events for all three real modalities and include stage timings.
            deadline=time.monotonic()+2
            events=[]
            expected_modality_events=3*(86+1+sum(len(x["scan_ids"]) for x in concurrent_runs))
            while time.monotonic()<deadline:
                events=json_events(worker_log)
                if sum(e.get("event_name")=="media.modality.completed" for e in events)>=expected_modality_events: break
                time.sleep(.05)
            by_scan={}
            for event in events:
                sid=event.get("scan_id"); name=event.get("event_name")
                if name=="scan.processing.stage_completed" and event.get("processing_stage")=="queue_wait":
                    measurements["queue_wait_ms"].append(event.get("duration_ms",0))
                    clock=event.get("duration_clock","missing")
                    measurements["queue_wait_clock_counts"][clock]=measurements["queue_wait_clock_counts"].get(clock,0)+1
                if name=="scan.processing.stage_completed" and event.get("processing_stage")=="result_commit": measurements["result_persistence_ms"].append(event.get("duration_ms",0))
                if name=="media.modality.completed" and event.get("modality") in measurements["extraction_ms"]: measurements["extraction_ms"][event["modality"]].append(event.get("duration_ms",0))
                if sid:
                    stages=by_scan.setdefault(sid,{"media_processing_ms":0.0})
                    if name=="scan.processing.stage_completed" and event.get("processing_stage")=="queue_wait": stages["queue_wait_ms"]=event.get("duration_ms",0)
                    if name=="scan.processing.stage_completed" and event.get("processing_stage")=="result_commit": stages["result_persistence_ms"]=event.get("duration_ms",0)
                    if name in {"media.modality.completed","media.modality.invalid","media.modality.failed"}: stages["media_processing_ms"]+=event.get("duration_ms",0) or 0
            counts={m:len(v) for m,v in measurements["extraction_ms"].items()}
            if any(n<86 for n in counts.values()): raise AssertionError(f"real extractor event evidence incomplete: {counts}")
            if not measurements["queue_wait_ms"] or set(measurements["queue_wait_clock_counts"])!={"monotonic"}:
                raise AssertionError(f"SQLite queue wait durations were not measured monotonically: {measurements['queue_wait_clock_counts']}")
            detector_timing={}
            for modality in ("image","video"):
                values=[e.get("duration_ms",0) for e in events if e.get("event_name")=="media.modality.completed" and e.get("modality")==modality]
                detector_timing[modality]={"first_detector_and_extraction_ms":values[0] if values else None,"warm_extraction_ms":{"sample_count":max(0,len(values)-1),**percentiles(values[1:])}}
            for run in concurrent_runs:
                per_scan=[by_scan[sid] for sid in run["scan_ids"] if sid in by_scan]
                run["queue_wait_ms"]={"sample_count":len(per_scan),**percentiles([x["queue_wait_ms"] for x in per_scan if "queue_wait_ms" in x])}
                run["media_processing_ms"]={"sample_count":len(per_scan),**percentiles([x["media_processing_ms"] for x in per_scan])}
                run["result_persistence_ms"]={"sample_count":len(per_scan),**percentiles([x["result_persistence_ms"] for x in per_scan if "result_persistence_ms" in x])}

            # Duplicate submission must return the same scan/result and not create extra work.
            key="s04c-duplicate"
            duplicate_id, _=process_one("duplicate",idem=key)
            status, duplicate=call(base+"/api/v1/scans","POST",{"schema_version":"1.0","media_refs":[uploaded["image"],uploaded["video"],uploaded["audio"]],"reaction_task":None,"consent":CONSENT},{**auth,"Idempotency-Key":key})
            if status!=202 or duplicate["scan_id"]!=duplicate_id or duplicate["created"]: raise AssertionError("duplicate submission was not idempotent")

            # Release concurrent requests with the same new idempotency key together.
            replay_key="s07-concurrent-idempotency"
            replay_payload={"schema_version":"1.0","media_refs":[uploaded["image"],uploaded["video"],uploaded["audio"]],"reaction_task":None,"consent":CONSENT}
            replay_barrier=threading.Barrier(10)
            def submit_same_key(_):
                replay_barrier.wait(timeout=10)
                return call(base+"/api/v1/scans","POST",replay_payload,{**auth,"Idempotency-Key":replay_key},timeout=10)
            with ThreadPoolExecutor(max_workers=10,thread_name_prefix="wellar-s07-idempotency") as pool:
                replay_responses=list(pool.map(submit_same_key,range(10)))
            if any(code!=202 for code,_ in replay_responses):
                raise AssertionError("concurrent idempotent replay returned a non-202 response")
            replay_ids={body["scan_id"] for _,body in replay_responses}
            replay_created=sum(bool(body.get("created")) for _,body in replay_responses)
            if len(replay_ids)!=1 or replay_created!=1:
                raise AssertionError("concurrent idempotent replay created more than one scan")
            concurrent_idempotency={"requests":len(replay_responses),"accepted":sum(code==202 for code,_ in replay_responses),"distinct_scan_ids":len(replay_ids),"created_count":replay_created}
            replay_scan_id=next(iter(replay_ids))
            replay_deadline=time.monotonic()+25
            while time.monotonic()<replay_deadline:
                replay_status,replay_body=call(base+f"/api/v1/scans/{replay_scan_id}/result","GET",headers=auth,timeout=5)
                if replay_status==200: break
                if replay_status!=404: raise RuntimeError(f"concurrent replay result returned HTTP {replay_status}")
                time.sleep(.025)
            else: raise TimeoutError("concurrent replay scan did not produce a result")
            assert replay_body["status"]=="insufficient_evidence"

            # Corrupt image bytes with valid JPEG signature pass intake but must not fabricate analysis.
            corrupt=b"\xff\xd8\xff"+b"synthetic-corrupt-payload"*8
            status, corrupt_upload=call(base+"/api/v1/media/image","POST",corrupt,{**auth,"Content-Type":"image/jpeg","Content-Length":str(len(corrupt))})
            if status!=201: raise AssertionError("signature-valid corrupt JPEG should reach extractor validation")
            bad_payload={"schema_version":"1.0","media_refs":[corrupt_upload["media_ref"]],"reaction_task":None,"consent":CONSENT}
            status,bad=call(base+"/api/v1/scans","POST",bad_payload,{**auth,"Idempotency-Key":"s04c-corrupt"})
            assert status==202
            until=time.monotonic()+10
            while time.monotonic()<until:
                s,b=call(base+f"/api/v1/scans/{bad['scan_id']}/result","GET",headers=auth)
                if s==200: break
                time.sleep(.025)
            assert s==200 and b["targets"]["reference_sleepiness"]["value"] is None
            assert b["features"]["image"]["extraction_status"] in {"invalid_media","failed"}

            # Inject result persistence errors into the disposable SQLite DB, then verify retries exhaust.
            import sqlite3
            connection=sqlite3.connect(root/"queue.sqlite",timeout=2)
            connection.execute("CREATE TRIGGER s04c_reject_result BEFORE INSERT ON results BEGIN SELECT RAISE(ABORT,'synthetic persistence fault'); END")
            connection.commit(); connection.close()
            status, persist=call(base+"/api/v1/scans","POST",{"schema_version":"1.0","media_refs":[],"reaction_task":None},{**auth,"Idempotency-Key":"s04c-persistence-error"})
            assert status==202
            until=time.monotonic()+15
            terminal=None
            while time.monotonic()<until:
                connection=sqlite3.connect(root/"queue.sqlite",timeout=2)
                terminal=connection.execute("SELECT status FROM scans WHERE scan_id=?",(persist["scan_id"],)).fetchone()[0]
                connection.close()
                if terminal=="failed": break
                time.sleep(.05)
            assert terminal=="failed", "persistence failure did not exhaust configured retry attempts"
            connection=sqlite3.connect(root/"queue.sqlite",timeout=2); connection.execute("DROP TRIGGER s04c_reject_result"); connection.commit(); connection.close()

            # In-flight worker interruption: wait for a durable running claim, kill it, expire only that
            # temporary lease, then verify another separate worker recovers and persists the result.
            worker_used_stats=win_process_stats(worker.pid)
            terminate(worker); worker_out.close(); worker=None
            worker_out=worker_log.open("a",encoding="utf-8",buffering=1)
            worker=subprocess.Popen([sys.executable,"-m","app.workers.entrypoint"],env=env,stdout=worker_out,stderr=subprocess.STDOUT)
            status, interrupted=call(base+"/api/v1/scans","POST",{"schema_version":"1.0","media_refs":[uploaded["image"],uploaded["video"],uploaded["audio"]],"reaction_task":None,"consent":CONSENT},{**auth,"Idempotency-Key":"s04c-worker-interruption"})
            assert status==202
            import sqlite3
            connection=sqlite3.connect(root/"queue.sqlite",timeout=2)
            until=time.monotonic()+8; interrupted_in_flight=False
            while time.monotonic()<until:
                state=connection.execute("SELECT status FROM jobs WHERE scan_id=?",(interrupted["scan_id"],)).fetchone()[0]
                if state=="running":
                    interrupted_in_flight=True
                    worker.kill(); worker.wait(timeout=3); worker_out.close(); worker=None
                    connection.execute("UPDATE jobs SET lease_until=? WHERE scan_id=?",(time.time()-1,interrupted["scan_id"]))
                    connection.commit(); break
                if state=="completed": break
                time.sleep(.005)
            connection.close()
            if not interrupted_in_flight: raise AssertionError("worker interruption did not catch an in-flight job")
            worker_out=worker_log.open("a",encoding="utf-8",buffering=1)
            worker=subprocess.Popen([sys.executable,"-m","app.workers.entrypoint"],env=env,stdout=worker_out,stderr=subprocess.STDOUT)
            until=time.monotonic()+10
            recovered=False
            while time.monotonic()<until:
                s,r=call(base+f"/api/v1/scans/{interrupted['scan_id']}/result","GET",headers=auth)
                if s==200: recovered=True; break
                time.sleep(.025)
            assert recovered, "scan did not recover after worker restart"

            # Saturate only the disposable queue: stop its sole worker, concurrently fill
            # the configured 100-job capacity in batches of 25, then verify a 101st returns 503.
            terminate(worker); worker_out.close(); worker=None
            saturation_ids=[]; saturation_statuses=[]
            def saturation_submit(i):
                return call(base+"/api/v1/scans","POST",{"schema_version":"1.0","media_refs":[],"reaction_task":None},{**auth,"Idempotency-Key":f"s04d-saturation-{i}"},timeout=10)
            for first in range(0,100,25):
                with ThreadPoolExecutor(max_workers=25,thread_name_prefix="wellar-s04d-saturate") as pool:
                    responses=list(pool.map(saturation_submit,range(first,first+25)))
                saturation_statuses.extend(code for code,_ in responses)
                saturation_ids.extend(body["scan_id"] for code,body in responses if code==202)
            overflow_code,overflow=saturation_submit(100)
            if len(saturation_ids)!=100 or any(code!=202 for code in saturation_statuses) or overflow_code!=503 or overflow["error"]["code"]!="queue_full":
                raise AssertionError(f"queue saturation injection failed: accepted={len(saturation_ids)}, overflow={overflow_code}")
            worker_out=worker_log.open("a",encoding="utf-8",buffering=1)
            worker=subprocess.Popen([sys.executable,"-m","app.workers.entrypoint"],env=env,stdout=worker_out,stderr=subprocess.STDOUT)
            until=time.monotonic()+30
            while time.monotonic()<until:
                status,ready=call(base+"/ready",timeout=2)
                queue=ready.get("queue") or {}
                if status==200 and queue.get("queued")==0 and queue.get("running")==0: break
                time.sleep(.05)
            else: raise TimeoutError("queue did not drain after saturation recovery")
            import sqlite3
            connection=sqlite3.connect(root/"queue.sqlite",timeout=2)
            completed_saturated=connection.execute("SELECT count(*) FROM scans WHERE scan_id IN (%s) AND status='completed'" % ",".join("?" for _ in saturation_ids),saturation_ids).fetchone()[0]
            connection.close()
            if completed_saturated!=100: raise AssertionError(f"saturated queue recovered only {completed_saturated}/100 scans")

            api_stats=win_process_stats(api.pid)
            previous_worker_starts=sum(e.get("event_name")=="worker.started" for e in json_events(worker_log))
            terminate(worker); worker_out.close()
            worker_out=worker_log.open("a",encoding="utf-8",buffering=1)
            warm_worker_started=time.perf_counter()
            worker=subprocess.Popen([sys.executable,"-m","app.workers.entrypoint"],env=env,stdout=worker_out,stderr=subprocess.STDOUT)
            until=time.monotonic()+10; warm_worker_startup_ms=None
            while time.monotonic()<until:
                started_events=[e for e in json_events(worker_log) if e.get("event_name")=="worker.started"]
                if len(started_events)>previous_worker_starts:
                    warm_worker_startup_ms=round((time.perf_counter()-warm_worker_started)*1000,3)
                    break
                if worker.poll() is not None: raise RuntimeError("worker exited during warm startup")
                time.sleep(.025)
            if warm_worker_startup_ms is None: raise TimeoutError("worker did not start for warm startup measurement")

            terminate(api); api_out.close()
            api_out=api_log.open("a",encoding="utf-8",buffering=1)
            warm_api_start=time.perf_counter()
            api=subprocess.Popen([sys.executable,"-m","uvicorn","app.main:app","--host","127.0.0.1","--port",str(port),"--log-level","warning"],env=env,stdout=api_out,stderr=subprocess.STDOUT)
            until=time.monotonic()+10; warm_api_startup_ms=None
            while time.monotonic()<until:
                if api.poll() is not None: raise RuntimeError("API exited during warm startup")
                try:
                    status, ready=call(base+"/ready",timeout=1)
                    if status==200 and ready.get("status")=="ready":
                        warm_api_startup_ms=round((time.perf_counter()-warm_api_start)*1000,3); break
                except (OSError,urllib.error.URLError): pass
                time.sleep(.05)
            if warm_api_startup_ms is None: raise TimeoutError("API did not become ready for warm startup measurement")

            # Startup durations use local monotonic observations; wall timestamps remain correlation-only.
            upload_by_modality={}
            for modality in ("image","video","audio"):
                uploads=[item for item in measurements["upload_observations"] if item["modality"]==modality]
                upload_by_modality[modality]={"successful_uploads":len(uploads),"successful_bytes":sum(item["size_bytes"] for item in uploads),"latency_ms":{"sample_count":len(uploads),**percentiles([item["duration_ms"] for item in uploads])}}
            upload_summaries={"successful_uploads":len(measurements["upload_observations"]),"successful_bytes":sum(item["size_bytes"] for item in measurements["upload_observations"]),"latency_ms":{"sample_count":len(measurements["upload_ms"]),**percentiles(measurements["upload_ms"])},"by_modality":upload_by_modality}
            highest_level=max((x["simultaneous_scans"] for x in concurrent_runs if x["stable"]),default=0)
            worker_events=json_events(worker_log)
            worker_completed_count=sum(e.get("event_name")=="scan.processing.completed" for e in worker_events)
            worker_retry_count=sum(e.get("event_name")=="scan.processing.retry_scheduled" for e in worker_events)
            worker_failure_count=sum(e.get("event_name")=="scan.processing.failed" for e in worker_events)
            run_finished_at=datetime.now(timezone.utc)
            summary={
                "benchmark":"real_v2_api_sqlite_separate_worker_media_e2e","synthetic_only":True,"internet_used":False,
                "run_started_at_utc":run_started_at.isoformat(),"run_finished_at_utc":run_finished_at.isoformat(),"duration_seconds":round((run_finished_at-run_started_at).total_seconds(),3),
                "machine":machine_details(),
                "configuration":{"api_workers":1,"separate_worker_processes":1,"queue_backend":"sqlite","media_backend":"local","auth_mode":"development_synthetic_token","queue_capacity":100,"concurrent_cohorts":list(CONCURRENCY_LEVELS),"repetitions_per_cohort":repetitions,"client_upload_slots":4,"result_poll_interval_ms":25,"result_deadline_seconds":40,"synthetic_modalities":["image/jpeg","video/mp4","audio/wav"],"output_path":str(output_path.relative_to(project_root))},
                "workloads":run_summaries,"concurrent_workloads":[{k:v for k,v in item.items() if k!="scan_ids"} for item in concurrent_runs],"highest_stable_concurrency":highest_level,
                "startup_ms":{"cold_api_launch_to_ready":round(cold_api_startup,3),"warm_api_relaunch_to_ready":warm_api_startup_ms,"cold_worker_launch_to_started_event":cold_worker_startup_ms,"warm_worker_relaunch_to_started_event":warm_worker_startup_ms},
                "detector_initialization_and_reuse":detector_timing,"resource_counter_reference_validation":resource_validation,
                "measurements":{"media_uploads":upload_summaries,"queue_acceptance_ms":percentile_summary(measurements["queue_acceptance_ms"]),"queue_wait_ms":percentile_summary(measurements["queue_wait_ms"]),"extraction_ms":{m:percentile_summary(v) for m,v in measurements["extraction_ms"].items()},"result_persistence_ms":percentile_summary(measurements["result_persistence_ms"]),"client_visible_e2e_ms":percentile_summary(measurements["client_e2e_ms"]),"concurrent_upload_start_to_result_ms":percentile_summary(measurements["upload_start_to_result_ms"]),"concurrent_submit_to_terminal_ms":percentile_summary(measurements["concurrent_submit_to_terminal_ms"]),"result_visibility_overhead_after_terminal_ms":percentile_summary(measurements["result_visibility_overhead_ms"]),"concurrent_post_start_to_result_ms":percentile_summary(measurements["concurrent_post_to_result_ms"])},
                "queue_wait_duration_clock_counts":measurements["queue_wait_clock_counts"],
                "worker":{"processes":1,"completed_jobs_observed":worker_completed_count,"retry_events":worker_retry_count,"terminal_failure_events":worker_failure_count,"cohort_throughput_scans_per_second":[{"repetition":x["repetition"],"concurrency":x["simultaneous_scans"],"completed":x["completed"],"scans_per_second":x["throughput_scans_per_second"]} for x in concurrent_runs]},
                "process_resources":{"api":api_stats if resource_validation["passed"] else None,"worker_scan_worker":worker_used_stats if resource_validation["passed"] else None},"extractor_event_counts":counts,
                "concurrent_idempotency":concurrent_idempotency,
                "failure_injections":{"duplicate_submission":"passed","concurrent_same_key_replay":"passed","corrupt_media":"passed without score fabrication","persistence_error_retry_exhaustion":"passed","in_flight_worker_kill_and_lease_recovery":"passed","client_deadline_expiry":"not injected; cohort timeout observations are reported per run","queue_capacity_100_concurrent_fill":"passed (100 accepted, overflow rejected, then recovered)"},
                "limitations":["Concurrent cohorts use one separate worker process and local SQLite/local media.","Process CPU/peak working set are omitted because Windows process counter validation failed.","Whole-machine memory is a point-in-time Windows API reading; CPU utilization is not measured.","Cohort queue depths are sampled once after each successful submission response, not a continuous peak trace.","Synthetic media proves extractor invocation and wiring, not scientific validity.","This local fixture benchmark is not a production-capacity or real-device test."]
            }
            # Log leak check: credentials and media sample contents never appear in logs or result JSON.
            all_log_text=api_log.read_text(errors="replace")+worker_log.read_text(errors="replace")
            forbidden=[TOKEN,"synthetic-corrupt-payload", "\ufffd\ufffd\ufffd"]
            if any(value in all_log_text for value in forbidden): raise AssertionError("sensitive fixture/token sentinel leaked into logs")
            api_events=json_events(api_log)
            unexpected_api_errors={}
            for item in api_events:
                if item.get("event_name")=="request.failed.unexpected":
                    kind=item.get("exception_type","unknown")
                    unexpected_api_errors[kind]=unexpected_api_errors.get(kind,0)+1
            summary["api_unexpected_error_event_counts"]=unexpected_api_errors
            sqlite_failures={}
            for item in api_events:
                if item.get("event_name")=="sqlite.operation.failed":
                    signature=(item.get("operation","unknown"),item.get("exception_type","unknown"),item.get("sqlite_error_name") or "code_unavailable")
                    label="/".join(signature)
                    sqlite_failures[label]=sqlite_failures.get(label,0)+1
            summary["sqlite_operation_failure_counts"]=sqlite_failures
            output_path.parent.mkdir(parents=True,exist_ok=True)
            serialized=json.dumps(summary,sort_keys=True,indent=2)
            with output_path.open("x",encoding="utf-8",newline="\n") as result_file:
                result_file.write(serialized+"\n")
            print(serialized)
        finally:
            terminate(worker); terminate(api)
            if worker_out: worker_out.close()
            api_out.close()
            # Retry deletion only for this harness-owned root; Windows can release a
            # redirected child stdout handle slightly after process termination.
            if root.parent.resolve()!=Path(tempfile.gettempdir()).resolve() or not root.name.startswith("wellar-s04d-"):
                raise RuntimeError("Refusing to remove benchmark state outside its owned temporary directory")
            last_cleanup_error=None
            for _ in range(20):
                try: shutil.rmtree(root)
                except FileNotFoundError: break
                except OSError as exc:
                    last_cleanup_error=exc
                    time.sleep(.1)
                else: break
            if root.exists():
                raise RuntimeError("Disposable benchmark state remains after cleanup retries") from last_cleanup_error


if __name__=="__main__": main()
