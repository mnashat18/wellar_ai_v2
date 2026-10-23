"""Synthetic MP4 intake and worker decoding coverage."""
from __future__ import annotations

import pytest

from app.media.extractors import InvalidMediaError, extract_video
from app.workers.worker import Worker
from tests.media_fixtures import mp4_video_file, video_file


AUTH={"Authorization":"Bearer token-a"}
CONSENT={"media_processing":True,"consent_version":"operational-media-v1","research_use":False}


def _mp4(path, *, seconds=2, fps=4):
    try:
        return mp4_video_file(path,seconds=seconds,fps=fps)
    except RuntimeError as exc:
        pytest.skip(str(exc))


def test_mp4_mime_and_container_validation(client,tmp_path):
    mp4=_mp4(tmp_path/"synthetic.mp4")
    avi=video_file(tmp_path/"synthetic.avi")

    accepted=client.post("/api/v1/media/video",headers={**AUTH,"Content-Type":"video/mp4"},content=mp4)
    assert accepted.status_code==201
    assert accepted.json()["content_type"]=="video/mp4"

    wrong_mime=client.post("/api/v1/media/video",headers={**AUTH,"Content-Type":"video/x-msvideo"},content=mp4)
    wrong_container=client.post("/api/v1/media/video",headers={**AUTH,"Content-Type":"video/mp4"},content=avi)
    unsupported=client.post("/api/v1/media/video",headers={**AUTH,"Content-Type":"video/quicktime"},content=mp4)
    assert wrong_mime.status_code==422
    assert wrong_container.status_code==422
    assert unsupported.status_code==415


@pytest.mark.parametrize(("max_seconds","max_frames"),[(1,100),(30,7)])
def test_mp4_duration_and_frame_count_limits_are_enforced(tmp_path,max_seconds,max_frames):
    path=tmp_path/"bounded.mp4"
    _mp4(path,seconds=2,fps=4)
    with pytest.raises(InvalidMediaError,match="video_resource_limit"):
        extract_video(path,max_seconds=max_seconds,max_frames=max_frames,sample_hz=4,max_pixels=4_000_000)


def test_mp4_is_decoded_sampled_and_cleaned_after_worker_processing(client,auth,tmp_path):
    blob=_mp4(tmp_path/"valid.mp4",seconds=2,fps=4)
    upload=client.post("/api/v1/media/video",headers={**auth,"Content-Type":"video/mp4"},content=blob)
    assert upload.status_code==201
    ref=upload.json()["media_ref"]
    asset=client.app.state.media_store.resolve(ref,"tenant-a","user-a")
    assert asset is not None and asset["path"].is_file()

    accepted=client.post("/api/v1/scans",headers={**auth,"Idempotency-Key":"synthetic-mp4"},json={
        "schema_version":"1.0","media_refs":[ref],"reaction_task":None,"consent":CONSENT,
    })
    assert accepted.status_code==202
    assert Worker(client.app.state.store,"mp4-worker",client.app.state.worker_processor).run_once()
    result=client.get(f"/api/v1/scans/{accepted.json()['scan_id']}/result",headers=auth).json()
    video=result["features"]["video"]
    assert video["presence"]=="present"
    assert video["extraction_status"]=="insufficient_quality"
    assert video["quality"]["reason"]=="face_not_detected"
    assert video["features"]["video_duration_seconds"]==pytest.approx(2.0,abs=.1)
    assert video["features"]["observation_count"]==8
    assert video["features"]["sampling_coverage"]==pytest.approx(1.0)
    assert video["provenance"]["extractor"]=="opencv_decode_mediapipe_face_mesh_eye_mouth_geometry"
    assert client.app.state.media_store.resolve(ref,"tenant-a","user-a") is None
    assert not asset["path"].exists()


def test_truncated_mp4_is_rejected_by_decoder_and_cleaned(client,auth,tmp_path):
    complete=_mp4(tmp_path/"complete.mp4")
    truncated=complete[:64]
    assert truncated[4:8]==b"ftyp"
    upload=client.post("/api/v1/media/video",headers={**auth,"Content-Type":"video/mp4"},content=truncated)
    assert upload.status_code==201  # Intake checks the container signature; decode validates the file.
    ref=upload.json()["media_ref"]
    asset=client.app.state.media_store.resolve(ref,"tenant-a","user-a")
    assert asset is not None and asset["path"].is_file()
    accepted=client.post("/api/v1/scans",headers={**auth,"Idempotency-Key":"truncated-mp4"},json={
        "schema_version":"1.0","media_refs":[ref],"reaction_task":None,"consent":CONSENT,
    })
    assert accepted.status_code==202
    assert Worker(client.app.state.store,"truncated-mp4-worker",client.app.state.worker_processor).run_once()
    result=client.get(f"/api/v1/scans/{accepted.json()['scan_id']}/result",headers=auth).json()
    feature=result["features"]["video"]
    assert feature["extraction_status"]=="invalid_media"
    assert feature["features"]["error_code"] in {"video_decode_failed","invalid_video_metadata"}
    assert client.app.state.media_store.resolve(ref,"tenant-a","user-a") is None
    assert not asset["path"].exists()
