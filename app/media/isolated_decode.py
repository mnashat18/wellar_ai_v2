from __future__ import annotations

import importlib
import json
import csv
import os
import subprocess
import sys
import time
from pathlib import Path


class DecodeTimeoutError(TimeoutError):
    pass


class DecodeMemoryLimitError(MemoryError):
    pass


class DecodeChildError(RuntimeError):
    pass


def _decode_media(modality: str, path: str, limits: dict):
    from app.media.extractors import InvalidMediaError,extract_audio,extract_image,extract_video
    file_path=Path(path)
    if modality=="video":
        return extract_video(file_path,max_seconds=limits["max_video_seconds"],max_frames=limits["max_video_frames"],
            sample_hz=limits["video_sample_hz"],max_pixels=limits["max_image_pixels"])
    if modality=="image":
        return extract_image(file_path,max_pixels=limits["max_image_pixels"])
    if modality=="audio":
        return extract_audio(file_path,max_seconds=limits["max_audio_seconds"])
    raise InvalidMediaError("unsupported_media_modality")


def _child_entry(module_name: str,qualname: str):
    try:
        request=json.load(sys.stdin)
        target=importlib.import_module(module_name)
        for part in qualname.split("."):target=getattr(target,part)
        response={"status":"ok","result":target(*request["args"])}
    except BaseException as exc:
        message=str(exc)[:160] if type(exc).__name__=="InvalidMediaError" else "decode child failed"
        response={"status":"error","type":type(exc).__name__,"message":message}
    json.dump(response,sys.stdout,separators=(",",":"));sys.stdout.flush()


def _rss_bytes(pid: int) -> int:
    """Read child resident memory through psutil (Windows and POSIX)."""
    try:
        import psutil
        return int(psutil.Process(pid).memory_info().rss)
    except ImportError:
        if os.name=="nt":
            result=subprocess.run(["tasklist","/FI",f"PID eq {pid}","/FO","CSV","/NH"],
                capture_output=True,text=True,timeout=2,check=False)
            rows=list(csv.reader(line for line in result.stdout.splitlines() if line.strip()))
            if not rows or len(rows[0])<5 or "no tasks" in rows[0][0].lower():raise OSError("cannot query decoder process memory")
            value=rows[0][4].strip().replace(",","").replace(" ","")
            if value.lower().endswith("k"):return int(float(value[:-1])*1024)
            if value.lower().endswith("m"):return int(float(value[:-1])*1024*1024)
            raise OSError("cannot parse decoder process memory")
        with open(f"/proc/{pid}/statm",encoding="ascii") as stream:
            resident=int(stream.read().split()[1])
        return resident*os.sysconf("SC_PAGE_SIZE")


def run_killable(target, args: tuple, *, timeout_seconds: float, memory_limit_mb: int, poll_seconds: float=0.02):
    """Run untrusted/native decode in a spawned process with deadline and RSS cap."""
    if timeout_seconds<=0 or memory_limit_mb<32:raise ValueError("decode process limits are invalid")
    module_name=getattr(target,"__module__",None);qualname=getattr(target,"__qualname__",None)
    if not module_name or not qualname or "<locals>" in qualname:raise ValueError("decode target must be an importable top-level function")
    request=json.dumps({"args":args},separators=(",",":"))
    process=subprocess.Popen([sys.executable,"-m","app.media.isolated_decode","--child",module_name,qualname],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
    try:process.stdin.write(request);process.stdin.close()
    except Exception:
        process.kill();process.wait(timeout=1);raise DecodeChildError("media decode child input failed") from None
    deadline=time.monotonic()+timeout_seconds;failure=None
    try:
        while process.poll() is None:
            if time.monotonic()>=deadline:
                failure=DecodeTimeoutError("media decode deadline exceeded");break
            try:
                rss=_rss_bytes(process.pid)
            except OSError:
                if process.poll() is not None:break
                failure=DecodeChildError("media decode memory cannot be monitored");break
            if rss>memory_limit_mb*1024*1024:
                failure=DecodeMemoryLimitError("media decode memory limit exceeded");break
            time.sleep(poll_seconds)
        if failure is not None:
            process.kill();process.wait(timeout=1)
            raise failure
        stdout=process.stdout.read() if process.stdout else ""
        if process.returncode!=0 or not stdout:raise DecodeChildError("media decode child exited without a result")
        try:message=json.loads(stdout)
        except json.JSONDecodeError as exc:raise DecodeChildError("media decode child returned invalid output") from exc
        if message.get("status")=="ok":return message["result"]
        if message.get("type")=="InvalidMediaError":
            from app.media.extractors import InvalidMediaError
            raise InvalidMediaError(message.get("message","invalid_media"))
        raise DecodeChildError("media decode child raised " + str(message.get("type","unknown")))
    finally:
        if process.poll() is None:
            process.kill();process.wait(timeout=1)
        if process.stdin and not process.stdin.closed:process.stdin.close()
        if process.stdout:process.stdout.close()


if __name__=="__main__" and len(sys.argv)>=4 and sys.argv[1]=="--child":
    _child_entry(sys.argv[2],sys.argv[3])
