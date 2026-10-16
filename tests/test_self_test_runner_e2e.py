from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.error
import urllib.request
from queue import Empty, Queue
from threading import Thread
from pathlib import Path

from tests.media_fixtures import image_bytes


def test_disposable_runner_retries_transient_windows_cleanup_lock(monkeypatch):
    import app.self_test_runner as runner

    root = Path(tempfile.mkdtemp(prefix="wellar-v2-self-test-cleanup-"))
    (root / "synthetic-state").write_text("temporary", encoding="utf-8")
    original_rmtree = runner.shutil.rmtree
    calls = 0

    def fail_twice(path):
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise PermissionError("injected transient Windows file lock")
        return original_rmtree(path)

    monkeypatch.setattr(runner.shutil, "rmtree", fail_twice)
    try:
        runner._remove_disposable_state(root, attempts=4, retry_delay=0)
    finally:
        monkeypatch.setattr(runner.shutil, "rmtree", original_rmtree)
        if root.exists():
            original_rmtree(root)

    assert calls == 3
    assert not root.exists()


def _request(url: str, token: str, *, method: str = "GET", body: bytes | None = None,
             content_type: str | None = None, idempotency_key: str | None = None,
             timeout: float = 15):
    headers = {"Authorization": f"Bearer {token}"}
    if content_type:
        headers["Content-Type"] = content_type
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as response:
        raw = response.read()
        return response.code, json.loads(raw) if raw else None


def test_disposable_runner_real_http_worker_extraction_and_cleanup():
    process = subprocess.Popen(
        [sys.executable, "-u", "-m", "app.self_test_runner", "--no-browser"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    token = base_url = state_dir = None
    output_lines = []
    output_queue: Queue[str] = Queue()

    def drain_output():
        for output in process.stdout:
            output_queue.put(output)

    reader = Thread(target=drain_output, daemon=True)
    reader.start()
    failure = None
    try:
        startup_deadline = time.monotonic() + 35
        while time.monotonic() < startup_deadline:
            try:
                line = output_queue.get(timeout=1)
            except Empty:
                if process.poll() is not None:
                    break
                continue
            output_lines.append(line.rstrip())
            if line.startswith("Temporary bearer token"):
                token = output_queue.get(timeout=5).strip()
                output_lines.append("[one-use token omitted]")
            elif line.startswith("Self-test page: "):
                base_url = line.split("/self-test", 1)[0].split(": ", 1)[1]
            elif line.startswith("Disposable state directory: "):
                state_dir = Path(line.split(": ", 1)[1].strip())
            if token and base_url and state_dir:
                break
        assert token and base_url and state_dir, "runner did not print startup details: " + "\n".join(output_lines[-20:])

        body = image_bytes(width=320, height=240, kind="texture")
        status, uploaded = _request(base_url + "/api/v1/media/image", token,
                                    method="POST", body=body, content_type="image/jpeg")
        assert status == 201
        reference = uploaded["media_ref"]
        scan = {"schema_version": "1.0", "media_refs": [reference], "reaction_task": None,
                "consent": {"media_processing": True, "research_use": True}}
        status, accepted = _request(base_url + "/api/v1/scans", token, method="POST",
                                    body=json.dumps(scan).encode(), content_type="application/json",
                                    idempotency_key="local-self-test-once")
        assert status == 202

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            _, current = _request(base_url + f"/api/v1/scans/{accepted['scan_id']}", token)
            if current["status"] == "completed":
                break
            assert current["status"] != "failed", "separate worker returned a terminal failure"
            time.sleep(.25)
        else:
            diagnostic = "unavailable"
            db_path = state_dir / "self-test.sqlite3"
            if db_path.is_file():
                db = sqlite3.connect(db_path, timeout=2)
                try:
                    diagnostic = repr(db.execute("SELECT status,attempts,last_error,worker_id FROM jobs").fetchall())
                finally:
                    db.close()
            raise AssertionError(f"separate worker did not complete within 30 seconds; job state={diagnostic}")

        _, result = _request(base_url + f"/api/v1/scans/{accepted['scan_id']}/result", token)
        assert result["status"] == "insufficient_evidence"
        assert result["model_invocation_status"] == "not_invoked_no_validated_model"
        assert result["targets"]["reference_sleepiness"]["value"] is None
        assert result["targets"]["vigilance_impairment"]["value"] is None
        image = result["features"]["image"]
        assert image["presence"] == "present"
        assert image["provenance"].get("extractor") == "opencv_image_quality_mediapipe_face_mesh_visibility"
        assert image["extraction_status"] in {"available", "insufficient_quality"}

        deleted, _ = _request(base_url + "/api/v1/media?" + urllib.parse.urlencode({"media_ref": reference}),
                              token, method="DELETE")
        # The worker consumes and deletes media immediately after processing.
        assert deleted == 404
        media_id = reference.rsplit("/", 1)[1]
        db = sqlite3.connect(state_dir / "self-test.sqlite3", timeout=2)
        try:
            assert db.execute("SELECT COUNT(*) FROM media_assets WHERE media_id=?", (media_id,)).fetchone()[0] == 0
        finally:
            db.close()
        assert not list((state_dir / "media").rglob(media_id + ".jpg"))
    except Exception as exc:
        failure = exc
    finally:
        if process.poll() is None:
            process.stdin.write("\n")
            process.stdin.flush()
            process.wait(timeout=30)
        if process.stdout:
            reader.join(timeout=5)
            while True:
                try:
                    output_lines.append(output_queue.get_nowait().rstrip())
                except Empty:
                    break
            process.stdout.close()
        if process.stdin:
            process.stdin.close()
    if failure is not None:
        events = [line for line in output_lines if any(name in line for name in ("scan.", "media.", "worker.", "reaction_task"))]
        raise AssertionError(f"process-level self-test failed: {failure}; diagnostic output: " + "\n".join(events[-40:])) from failure
    assert process.returncode == 0
    assert token not in "\n".join(output_lines)
    assert any("Disposable state cleanup verified." in line for line in output_lines)
    assert state_dir is not None and not state_dir.exists()
