"""Real OpenCV/MediaPipe extraction on code-generated face videos."""
from __future__ import annotations

import io

import cv2
import numpy as np
from PIL import Image

from app.media.extractors import extract_video
from tests.media_fixtures import synthetic_face_image_bytes


def _write_video(path, frames, fps=4):
    height,width=frames[0].shape[:2]
    writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*"MJPG"),fps,(width,height))
    assert writer.isOpened(),"synthetic MJPG encoder unavailable"
    try:
        for frame in frames:
            writer.write(frame)
    finally:
        writer.release()
    return path


def _extract(path,sample_hz=4):
    return extract_video(path,max_seconds=30,max_frames=1800,sample_hz=sample_hz,max_pixels=4_000_000)


def test_real_static_face_video_has_bounded_spatial_continuity(tmp_path):
    rgb=np.asarray(Image.open(io.BytesIO(synthetic_face_image_bytes())).convert("RGB"))
    frame=cv2.cvtColor(rgb,cv2.COLOR_RGB2BGR)
    path=_write_video(tmp_path/"continuous-face.avi",[frame.copy() for _ in range(8)])
    result=_extract(path)
    assert result["quality"]["face_track_continuity_established"] is True
    assert result["features"]["face_track_continuity_status"]=="spatial_continuity_established"
    assert result["status"]=="available"
    assert result["features"]["observation_count"]==8
    assert result["provenance"]["face_continuity_rule"].endswith("not_identity_tracking")


def test_real_spatial_face_switch_abstains_without_combining_temporal_metrics(tmp_path):
    face=Image.open(io.BytesIO(synthetic_face_image_bytes())).convert("RGB").resize((300,300))
    frames=[]
    for x in (0,212):
        canvas=Image.new("RGB",(512,512),(210,218,225)); canvas.paste(face,(x,106))
        frames.append(cv2.cvtColor(np.asarray(canvas),cv2.COLOR_RGB2BGR))
    path=_write_video(tmp_path/"spatial-switch.avi",frames,fps=1)
    result=_extract(path,sample_hz=1)
    assert result["status"]=="insufficient_quality"
    assert result["quality"]["face_track_continuity_established"] is False
    assert result["quality"]["reason"]=="face_track_continuity_unestablished"
    assert result["features"]["face_track_continuity_status"]=="unestablished"
    assert result["features"]["blink_event_count"] is None
    assert result["features"]["short_clip_eye_closure_fraction"] is None
