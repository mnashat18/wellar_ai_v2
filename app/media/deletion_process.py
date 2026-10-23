"""Small subprocess boundary for storage deletes that need a hard wall deadline.

The parent always terminates and reaps this child on timeout. Payload is sent
over stdin so object keys and local paths do not appear in process arguments.
"""
from __future__ import annotations

import json
import os
import subprocess
import shutil
import sys
import threading
import time
from pathlib import Path


class StorageDeleteTimeout(TimeoutError):
    """A bounded storage operation exceeded its hard subprocess deadline."""


class StorageDeleteCapacityTimeout(TimeoutError):
    """No per-process storage deletion child slot became available in time."""


# Shared by local and S3 adapters in this Python process. This deliberately is
# not presented as a host-wide or distributed limit.
MAX_CONCURRENT_STORAGE_CHILDREN = 2
_CHILD_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_STORAGE_CHILDREN)


def run_bounded_storage_action(request: dict, timeout_seconds: float) -> dict:
    if timeout_seconds<=0:raise ValueError("storage operation timeout must be positive")
    started=time.monotonic()
    if not _CHILD_SLOTS.acquire(timeout=timeout_seconds):
        raise StorageDeleteCapacityTimeout("storage deletion capacity deadline exceeded")
    process=None
    try:
        remaining=timeout_seconds-(time.monotonic()-started)
        if remaining<=0:raise StorageDeleteCapacityTimeout("storage deletion capacity deadline exceeded")
        process=subprocess.Popen([sys.executable,str(Path(__file__).resolve())],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
            close_fds=True)
        try:
            stdout,stderr=process.communicate(input=json.dumps(request,separators=(",",":")),timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            _kill_and_reap(process)
            raise StorageDeleteTimeout("storage deletion exceeded its operation deadline") from exc
        except BaseException:
            # Parent cancellation and shutdown must not leave a child running.
            _kill_and_reap(process)
            raise
    finally:
        _CHILD_SLOTS.release()
    if process.returncode!=0:
        # stderr intentionally is not included: it may contain SDK details.
        raise OSError("storage deletion subprocess failed")
    try:return json.loads(stdout)
    except (TypeError,ValueError) as exc:raise OSError("storage deletion subprocess returned invalid status") from exc


def _kill_and_reap(process) -> None:
    if process.poll() is None:
        try:process.kill()
        except OSError:
            # The child may have exited between poll() and kill() (notably on
            # Windows). Propagate only if it is still running.
            if process.poll() is None:raise
    process.communicate()


def _perform(request: dict) -> dict:
    action=request.get("action")
    if action=="unlink":
        Path(request["path"]).unlink(missing_ok=True)
        return {"removed":1}
    if action=="s3_delete":
        import boto3
        from botocore.config import Config
        credentials=request.get("credentials") or {}
        client=boto3.client("s3",region_name=request.get("region"),endpoint_url=request.get("endpoint_url"),
            aws_access_key_id=credentials.get("access_key"),aws_secret_access_key=credentials.get("secret_key"),
            aws_session_token=credentials.get("session_token"),
            config=Config(connect_timeout=float(request["connect_timeout"]),
                          read_timeout=float(request["read_timeout"]),
                          max_pool_connections=1,retries={"total_max_attempts":1,"mode":"standard"}))
        client.delete_object(Bucket=request["bucket"],Key=request["key"])
        return {"removed":1}
    if action=="sweep_local":
        root=Path(request["root"])
        cutoff=float(request["cutoff"])
        maximum=max(1,int(request["maximum"]))
        removed=0
        for directory,dirs,files in os.walk(root):
            dirs.sort();files.sort()
            for name in files:
                if not name.endswith(".partial"):continue
                path=Path(directory)/name
                if path.stat().st_mtime<cutoff:
                    path.unlink(missing_ok=True);removed+=1
                    if removed>=maximum:return {"removed":removed}
        return {"removed":removed}
    if action=="sweep_s3_scratch":
        root=Path(request["root"]);current=Path(request["current"]).resolve()
        cutoff=float(request["cutoff"]);maximum=max(1,int(request["maximum"]))
        removed=0
        for path in sorted(root.iterdir(),key=lambda item:item.name):
            if removed>=maximum:break
            if path.resolve()==current or not path.name.startswith("wellar-s3-") or path.stat().st_mtime>=cutoff:continue
            if path.is_dir():shutil.rmtree(path)
            elif path.is_file():path.unlink(missing_ok=True)
            removed+=1
        return {"removed":removed}
    if action=="remove_s3_workdir":
        root=Path(request["root"]).resolve();target=Path(request["target"])
        if target.parent.resolve()!=root or not target.name.startswith("wellar-s3-"):
            raise ValueError("unsafe scratch target")
        if target.is_dir() and not target.is_symlink():shutil.rmtree(target)
        elif target.exists() or target.is_symlink():target.unlink(missing_ok=True)
        return {"removed":1}
    raise ValueError("unsupported deletion action")


def main() -> None:
    try:
        result=_perform(json.load(sys.stdin))
        sys.stdout.write(json.dumps(result,separators=(",",":")))
    except BaseException:
        # Do not leak paths, keys, credentials, or SDK messages to the parent logs.
        raise SystemExit(2) from None


if __name__=="__main__":main()
