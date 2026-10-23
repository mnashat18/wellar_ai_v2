from __future__ import annotations

import pytest

import app.media.analysis as analysis
from app.media.isolated_decode import _warm_media_runtime
from app.media.analysis import MultimodalFeatureProcessor


class NoopMediaStore:
    pass


def make_processor():
    return MultimodalFeatureProcessor(NoopMediaStore(),max_video_seconds=2,max_audio_seconds=2,
        max_image_pixels=4096,max_video_frames=30,video_sample_hz=2,
        decode_timeout_seconds=7,decode_memory_limit_mb=384)


def test_worker_warmup_runs_bounded_native_runtime_check_before_ready(monkeypatch):
    calls=[]
    def fake_run_killable(target,args,**kwargs):
        calls.append((target,args,kwargs))
        return {"opencv_version":"test-opencv","mediapipe_version":"test-mediapipe"}
    monkeypatch.setattr(analysis,"run_killable",fake_run_killable)
    processor=make_processor()
    assert processor.warmup()=={"opencv_version":"test-opencv","mediapipe_version":"test-mediapipe"}
    assert calls==[(_warm_media_runtime,(),{"timeout_seconds":7,"memory_limit_mb":384})]


def test_worker_warmup_failure_is_fatal_and_redacted(monkeypatch,caplog):
    def fail(*args,**kwargs):raise RuntimeError("private model path must not leak")
    monkeypatch.setattr(analysis,"run_killable",fail)
    with pytest.raises(RuntimeError,match="warmup failed"):
        make_processor().warmup()
    assert "worker.warmup.failed" in caplog.text
    assert "private model path" not in caplog.text
