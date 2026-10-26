from __future__ import annotations

import os
import time
import sys
from types import SimpleNamespace

import pytest

from app.media.isolated_decode import DecodeChildError,DecodeMemoryLimitError,DecodeTimeoutError,run_killable


@pytest.fixture(autouse=True)
def synthetic_psutil(monkeypatch):
    class Process:
        def __init__(self,pid):self.pid=pid
        def memory_info(self):return SimpleNamespace(rss=16*1024*1024)
    monkeypatch.setitem(sys.modules,"psutil",SimpleNamespace(Process=Process))


def _hang():
    while True:time.sleep(.05)


def _crash():
    os._exit(17)


def _identity(value):
    return value


def test_wall_timeout_kills_child_process():
    started=time.monotonic()
    with pytest.raises(DecodeTimeoutError):
        run_killable(_hang,(),timeout_seconds=.35,memory_limit_mb=512,poll_seconds=.01)
    assert time.monotonic()-started<3


def test_crashed_child_returns_control_to_parent():
    with pytest.raises(DecodeChildError):
        run_killable(_crash,(),timeout_seconds=3,memory_limit_mb=512)


def test_child_exit_during_rss_check_is_treated_as_normal_exit(monkeypatch):
    import io
    import json
    import app.media.isolated_decode as isolated_decode

    class NoSuchProcess(Exception): pass
    class ExitedChild:
        pid=123
        returncode=0
        stdin=io.StringIO()
        stdout=io.StringIO(json.dumps({"status":"ok","result":"done"}))
        def __init__(self):self.polls=0
        def poll(self):
            self.polls+=1
            return None if self.polls==1 else 0
        def kill(self):raise AssertionError("an exited child must not be killed")
        def wait(self,timeout=None):return 0

    class GoneProcess:
        def __init__(self,pid):pass
        def memory_info(self):raise NoSuchProcess("child exited")

    monkeypatch.setitem(sys.modules,"psutil",SimpleNamespace(Process=GoneProcess,Error=NoSuchProcess))
    monkeypatch.setattr(isolated_decode.subprocess,"Popen",lambda *args,**kwargs:ExitedChild())
    assert run_killable(_identity,("ignored",),timeout_seconds=3,memory_limit_mb=512)=="done"


def test_memory_limit_kills_child_process(monkeypatch):
    class HighMemoryProcess:
        def __init__(self,pid):self.pid=pid
        def memory_info(self):return SimpleNamespace(rss=128*1024*1024)
    monkeypatch.setitem(sys.modules,"psutil",SimpleNamespace(Process=HighMemoryProcess))
    with pytest.raises(DecodeMemoryLimitError):run_killable(_hang,(),timeout_seconds=5,memory_limit_mb=96,poll_seconds=.01)


def test_corrupt_and_huge_dimension_images_fail_before_landmark_inference(tmp_path):
    from app.media.extractors import InvalidMediaError
    from app.media.isolated_decode import _decode_media
    from tests.media_fixtures import png_dimension_bomb
    corrupt=tmp_path/"corrupt.jpg";corrupt.write_bytes(b"not-an-image")
    bomb=tmp_path/"huge.png";bomb.write_bytes(png_dimension_bomb())
    limits={"max_video_seconds":5,"max_video_frames":100,"video_sample_hz":3,"max_image_pixels":1_000_000,"max_audio_seconds":5}
    for path,reason in ((corrupt,"image_decode_failed"),(bomb,"image_dimensions_out_of_range")):
        with pytest.raises(InvalidMediaError,match=reason):
            run_killable(_decode_media,("image",str(path),limits),timeout_seconds=5,memory_limit_mb=512)


def test_decode_timeout_releases_worker_lease_and_deletes_media(store,tmp_path,monkeypatch):
    import hashlib
    import json
    from app.media.analysis import MultimodalFeatureProcessor
    from app.media.isolated_decode import DecodeTimeoutError
    from app.security.auth import Principal
    from app.workers.worker import Worker
    principal=Principal("subject-a","tenant-a")
    ref="media://opaque/video"
    body=json.dumps({"media_refs":[ref],"_media_manifest":[{"media_ref":ref,"modality":"video"}]})
    store.submit("scan-timeout",principal,"idem-timeout",hashlib.sha256(body.encode()).hexdigest(),body)
    path=tmp_path/"clip.mp4";path.write_bytes(b"synthetic")
    class Media:
        def __init__(self):self.deleted=[]
        def resolve(self,*args,**kwargs):return {"modality":"video","path":path,"available":True,"integrity_ok":True}
        def delete(self,*args):self.deleted.append(args);return True
        def release(self,*args):pass
    media=Media()
    processor=MultimodalFeatureProcessor(media,max_video_seconds=5,max_audio_seconds=5,max_image_pixels=100000,
        max_video_frames=100,video_sample_hz=3,decode_timeout_seconds=.2,decode_memory_limit_mb=128)
    monkeypatch.setattr("app.media.analysis.run_killable",lambda *args,**kwargs: (_ for _ in ()).throw(DecodeTimeoutError("synthetic timeout")))
    assert Worker(store,"timeout-worker",processor).run_once()
    assert store.db.execute("SELECT status FROM jobs WHERE scan_id='scan-timeout'").fetchone()[0]=="completed"
    result=store.get_result("scan-timeout","tenant-a","subject-a")
    assert result["analysis_status"]=="retake_required" and result["prediction"] is None
    # The terminal retake result is persisted before native media cleanup.
    assert media.deleted
    processor.close()

