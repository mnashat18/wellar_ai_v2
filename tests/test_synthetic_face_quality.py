"""Non-identifying procedural face checks for image signal quality only."""
from __future__ import annotations

import io
from types import SimpleNamespace

import numpy as np
from PIL import Image
from PIL import ImageFilter

from app.media.extractors import _face_mesh_factory,extract_image
from app.workers.worker import Worker
from tests.media_fixtures import synthetic_face_image_bytes


def _write(tmp_path,name,data):
    path=tmp_path/name
    path.write_bytes(data)
    return path


def _stable_fields(result):
    return result["status"],result["quality"],result["features"],result["provenance"]


def test_synthetic_face_normal_and_low_light_quality_are_repeatable(tmp_path):
    normal_path=_write(tmp_path,"synthetic-face-normal.png",synthetic_face_image_bytes())
    low_path=_write(tmp_path,"synthetic-face-low.png",synthetic_face_image_bytes(brightness=.08))
    normal_rgb=np.asarray(Image.open(io.BytesIO(normal_path.read_bytes())).convert("RGB"))
    low_rgb=np.asarray(Image.open(io.BytesIO(low_path.read_bytes())).convert("RGB"))
    mesh=_face_mesh_factory(static_image_mode=True)
    try:
        detected=mesh.process(normal_rgb).multi_face_landmarks
        assert detected and len(detected[0].landmark)>=468
        low_detected=mesh.process(low_rgb).multi_face_landmarks
        assert low_detected and len(low_detected[0].landmark)>=468

        normal=extract_image(normal_path,max_pixels=4_000_000,face_mesh=mesh)
        normal_repeat=extract_image(normal_path,max_pixels=4_000_000,face_mesh=mesh)
        low=extract_image(low_path,max_pixels=4_000_000,face_mesh=mesh)
        low_repeat=extract_image(low_path,max_pixels=4_000_000,face_mesh=mesh)
    finally:
        mesh.close()

    assert normal["status"]=="available"
    assert normal["quality"]["face_visible"] is True
    assert normal["features"]["face_detection_ratio"]==1.0
    assert normal["features"]["eye_aspect_ratio"] is not None
    assert normal["features"]["mouth_aspect_ratio"] is not None
    assert normal["features"]["mean_luminance"]>20.0

    assert low["status"]=="insufficient_quality"
    assert low["quality"]["reason"]=="low_light"
    assert low["quality"]["face_visible"] is True
    assert low["features"]["face_detection_ratio"]==1.0
    assert low["features"]["eye_aspect_ratio"] is not None
    assert low["features"]["mouth_aspect_ratio"] is not None
    assert low["features"]["mean_luminance"]<20.0
    assert low["features"]["width_px"]==normal["features"]["width_px"]==512
    assert low["features"]["height_px"]==normal["features"]["height_px"]==512
    assert _stable_fields(normal)==_stable_fields(normal_repeat)
    assert _stable_fields(low)==_stable_fields(low_repeat)


def test_synthetic_no_face_keeps_landmarks_missing_and_reports_zero_detection(tmp_path):
    path=tmp_path/"synthetic-no-face.png"
    Image.new("RGB",(512,512),(128,128,128)).save(path,format="PNG")
    result=extract_image(path,max_pixels=4_000_000)
    assert result["status"]=="insufficient_quality"
    assert result["quality"]["reason"]=="face_not_detected"
    assert result["features"]["face_detection_ratio"]==0.0
    assert result["features"]["eye_aspect_ratio"] is None
    assert result["features"]["mouth_aspect_ratio"] is None


def test_quality_rejects_ambiguous_multiple_faces_and_blur(tmp_path):
    normal_path=_write(tmp_path,"synthetic-face-for-branch.png",synthetic_face_image_bytes())
    with Image.open(normal_path) as image:
        blurred_path=tmp_path/"synthetic-face-blurred.png"
        image.filter(ImageFilter.GaussianBlur(radius=18)).save(blurred_path)
    normal_rgb=np.asarray(Image.open(normal_path).convert("RGB"))
    detector=_face_mesh_factory(static_image_mode=True)
    try:
        detected=detector.process(normal_rgb).multi_face_landmarks
        assert detected
        landmarks=detected[0].landmark
    finally:
        detector.close()

    class MultipleFaces:
        def process(self,_image):
            return SimpleNamespace(multi_face_landmarks=[SimpleNamespace(landmark=landmarks),SimpleNamespace(landmark=landmarks)])

    multiple=extract_image(normal_path,max_pixels=4_000_000,face_mesh=MultipleFaces())
    blurred=extract_image(blurred_path,max_pixels=4_000_000,face_mesh=SimpleNamespace(process=lambda _image:SimpleNamespace(multi_face_landmarks=[SimpleNamespace(landmark=landmarks)])))
    assert multiple["status"]=="insufficient_quality"
    assert multiple["quality"]["reason"]=="multiple_faces_detected"
    assert multiple["features"]["eye_aspect_ratio"] is None
    assert blurred["status"]=="insufficient_quality"
    assert blurred["quality"]["reason"]=="blurred_image"
    assert blurred["features"]["mean_blur_variance"]<10.0


def test_real_extractor_detects_two_synthetic_faces_and_abstains(tmp_path):
    path=tmp_path/"two-synthetic-faces.png"
    face=Image.open(io.BytesIO(synthetic_face_image_bytes())).convert("RGB")
    canvas=Image.new("RGB",(1024,512),(128,128,128))
    canvas.paste(face,(0,0)); canvas.paste(face,(512,0)); canvas.save(path)
    result=extract_image(path,max_pixels=2_000_000)
    assert result["status"]=="insufficient_quality"
    assert result["quality"]["reason"]=="multiple_faces_detected"
    assert result["quality"]["face_count"]==2
    assert result["features"]["eye_aspect_ratio"] is None


def test_real_extractor_detects_blur_and_missing_landmark_branch(tmp_path):
    from PIL import ImageFilter

    source=Image.open(io.BytesIO(synthetic_face_image_bytes())).convert("RGB")
    blurred_path=tmp_path/"actual-blurred-face.png"
    source.filter(ImageFilter.GaussianBlur(radius=22)).save(blurred_path)
    blurred=extract_image(blurred_path,max_pixels=2_000_000)
    assert blurred["status"]=="insufficient_quality"
    assert blurred["quality"]["face_visible"] is True
    assert blurred["quality"]["reason"]=="blurred_image"

    class FaceWithoutLandmarks:
        def process(self,_image):
            return SimpleNamespace(multi_face_landmarks=[SimpleNamespace(landmark=[])])

    normal_path=tmp_path/"normal-for-missing-landmarks.png"
    source.save(normal_path)
    missing=extract_image(normal_path,max_pixels=2_000_000,face_mesh=FaceWithoutLandmarks())
    assert missing["status"]=="insufficient_quality"
    assert missing["quality"]["reason"]=="missing_eye_landmarks"
    assert missing["features"]["eye_aspect_ratio"] is None


def test_worker_propagates_low_light_quality_without_fatigue_prediction(client,auth,tmp_path):
    path=tmp_path/"synthetic-face-low.png"
    path.write_bytes(synthetic_face_image_bytes(brightness=.08))
    upload=client.post("/api/v1/media/image",headers={**auth,"Content-Type":"image/png"},content=path.read_bytes())
    assert upload.status_code==201
    ref=upload.json()["media_ref"]
    accepted=client.post("/api/v1/scans",headers={**auth,"Idempotency-Key":"synthetic-low-light-face"},json={
        "schema_version":"1.0","media_refs":[ref],"reaction_task":None,
        "consent":{"media_processing":True,"consent_version":"operational-media-v1","research_use":False},
    })
    assert accepted.status_code==202
    assert Worker(client.app.state.store,"synthetic-low-light-worker",client.app.state.worker_processor).run_once()
    result=client.get(f"/api/v1/scans/{accepted.json()['scan_id']}/result",headers=auth).json()
    image=result["features"]["image"]
    assert image["extraction_status"]=="insufficient_quality"
    assert image["quality"]["reason"]=="low_light"
    assert image["features"]["face_detection_ratio"]==1.0
    assert image["features"]["eye_aspect_ratio"] is not None
    assert result["status"]=="insufficient_evidence"
    assert all(target["value"] is None for target in result["targets"].values())
    assert result["drowsiness_indicator"]["status"]=="not_performed"
    assert result["drowsiness_indicator"]["production_prediction"] is False
    assert client.app.state.media_store.resolve(ref,"tenant-a","user-a") is None
