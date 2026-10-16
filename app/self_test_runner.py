"""Launch a disposable localhost API and separate worker for a human self-test."""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_ready(process: subprocess.Popen, url: str, timeout: float = 30.0) -> None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("A self-test process exited before becoming ready; inspect its terminal output.")
        try:
            with opener.open(url, timeout=1.0) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.2)
    raise RuntimeError("The local API did not become ready within 30 seconds.")


def _stop(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _remove_disposable_state(root: Path, *, attempts: int = 20, retry_delay: float = 0.1) -> None:
    """Remove only this runner's temp directory, retrying transient Windows locks."""
    if not root.exists():
        return
    resolved = root.resolve()
    if resolved.parent != Path(tempfile.gettempdir()).resolve() or not resolved.name.startswith("wellar-v2-self-test-"):
        raise RuntimeError("Refusing to remove a path outside the self-test temporary directory")
    last_error: OSError | None = None
    for _ in range(attempts):
        try:
            shutil.rmtree(resolved)
            return
        except FileNotFoundError:
            return
        except OSError as exc:
            last_error = exc
            time.sleep(retry_delay)
    if resolved.exists():
        raise RuntimeError("The disposable self-test directory remains after cleanup retries") from last_error


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a disposable localhost Wellar V2 self-test API and separate worker")
    parser.add_argument("--no-browser", action="store_true", help="print the localhost URL without opening a browser")
    args = parser.parse_args()
    token = secrets.token_urlsafe(32)
    port = _free_local_port()
    processes: list[subprocess.Popen] = []
    root: Path | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="wellar-v2-self-test-", ignore_cleanup_errors=True) as temporary:
            root = Path(temporary).resolve()
            env = os.environ.copy()
            env.update({
                "WELLAR_ENV": "test",
                "WELLAR_LOG_LEVEL": "INFO",
                "WELLAR_DB_PATH": str(root / "self-test.sqlite3"),
                "WELLAR_MEDIA_ROOT": str(root / "media"),
                "WELLAR_QUEUE_BACKEND": "sqlite",
                "WELLAR_MEDIA_BACKEND": "local",
                "WELLAR_AUTH_MODE": "development",
                "WELLAR_DEV_AUTH_TOKENS_JSON": json.dumps({token: {"sub": "self-tester", "tenant_id": "self-test-tenant"}}),
                "WELLAR_MEDIA_RETENTION_DAYS": "1",
            })
            try:
                worker_env = dict(env, WELLAR_LOG_LEVEL="WARNING")
                worker = subprocess.Popen([sys.executable, "-m", "app.workers.entrypoint"], env=worker_env)
                processes.append(worker)
                api = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app",
                                        "--host", "127.0.0.1", "--port", str(port), "--no-access-log"], env=env)
                processes.append(api)
                url = f"http://127.0.0.1:{port}"
                _wait_ready(api, url + "/ready")
                if worker.poll() is not None:
                    raise RuntimeError("The separate worker exited during startup; inspect its terminal output.")
                print("Wellar V2 self-test is running on localhost with a separate worker.")
                print("Temporary bearer token (paste into the self-test page):")
                print(token)
                print(f"Self-test page: {url}/self-test")
                print(f"Disposable state directory: {root}")
                print("State and media are isolated in that temporary directory and removed when this runner exits.")
                if not args.no_browser:
                    try:
                        webbrowser.open(url + "/self-test")
                    except Exception:
                        pass
                try:
                    input("Complete the self-test, then press Enter here to stop and delete temporary state. ")
                except EOFError:
                    print("No interactive terminal was available; stopping the disposable self-test environment.")
            finally:
                for process in reversed(processes):
                    _stop(process)
    finally:
        if root is not None:
            _remove_disposable_state(root)
            print("Disposable state cleanup verified." if not root.exists() else "ERROR: disposable state directory remains.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Stopping the disposable self-test environment.")
