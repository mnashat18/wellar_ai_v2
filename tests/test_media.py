from __future__ import annotations

import io
import logging
import logging.handlers
import time
import wave

import pytest

from app.workers.worker import Worker
from app.core.logging import JsonFormatter
from tests.media_fixtures import image_bytes,png_dimension_bomb,video_file,wav_bytes
from app.media.extractors import temporal_eye_features

AUTH={"Authorization":"Bearer token-a"}
CONSENT={"media_processing":True,"research_use":True}


def upload(client,kind,data,content_type):
    return client.post(f"/api/v1/media/{kind}",headers={**AUTH,"Content-Type":content_type},content=data)


def test_media_intake_validates_type_signature_limits_and_ownership(client,auth):
    valid=image_bytes()
    response=upload(client,"image",valid,"image/jpeg")
    assert response.status_code==201
    ref=response.json()["media_ref"]
    assert "path" not in response.json() and response.json()["size_bytes"]==len(valid)
    assert client.post("/api/v1/media/image",headers={**auth,"Content-Type":"image/png"},content=valid).status_code==422
    assert client.post("/api/v1/media/image",headers={**auth,"Content-Type":"text/plain"},content=b"abc").status_code==415
    client.app.state.media_store.caps["image"]=32
    assert client.post("/api/v1/media/image",headers={**auth,"Content-Type":"image/jpeg"},content=valid).status_code==413
    assert client.post("/api/v1/scans",headers={"Authorization":"Bearer token-b","Idempotency-Key":"cross-owner"},json={"schema_version":"1.0","media_refs":[ref],"reaction_task":None,"consent":CONSENT}).status_code==404


def test_media_read_delete_and_scan_references_are_subject_scoped(client,auth):
    ref=upload(client,"image",image_bytes(),"image/jpeg").json()["media_ref"]
    other_subject={"Authorization":"Bearer token-c"}
    body={"schema_version":"1.0","media_refs":[ref],"reaction_task":None,"consent":CONSENT}
    assert client.post("/api/v1/scans",headers={**other_subject,"Idempotency-Key":"spoofed-subject"},json=body).status_code==404
    assert client.delete("/api/v1/media",headers=other_subject,params={"media_ref":ref}).status_code==404
    assert client.post("/api/v1/scans",headers={**auth,"Idempotency-Key":"valid-owner"},json=body).status_code==202
    assert client.delete("/api/v1/media",headers=auth,params={"media_ref":ref}).status_code==204


def test_idempotency_keys_are_scoped_to_verified_tenant_and_subject(client,auth):
    body={"schema_version":"1.0","media_refs":[],"reaction_task":None}
    first=client.post("/api/v1/scans",headers={**auth,"Idempotency-Key":"same-principal-key"},json=body)
    second=client.post("/api/v1/scans",headers={"Authorization":"Bearer token-other","Idempotency-Key":"same-principal-key"},json=body)
    assert first.status_code==202 and second.status_code==202
    assert first.json()["created"] and second.json()["created"]
    assert first.json()["scan_id"]!=second.json()["scan_id"]


def test_operational_media_processing_does_not_require_research_use(client,auth):
    ref=upload(client,"audio",wav_bytes(),"audio/wav").json()["media_ref"]
    body={"schema_version":"1.0","media_refs":[ref],"reaction_task":None,"consent":{"media_processing":True,"research_use":False}}
    response=client.post("/api/v1/scans",headers={**auth,"Idempotency-Key":"operational-consent"},json=body)
    assert response.status_code==202
    assert response.json()["status"]=="queued"


def test_processing_consent_false_is_rejected_even_when_research_permission_true(client,auth):
    ref=upload(client,"audio",wav_bytes(),"audio/wav").json()["media_ref"]
    body={"schema_version":"1.0","media_refs":[ref],"reaction_task":None,"consent":{"media_processing":False,"research_use":True}}
    assert client.post("/api/v1/scans",headers={**auth,"Idempotency-Key":"processing-consent-denied"},json=body).status_code==422


def test_tenant_media_storage_quota_is_enforced(client,auth):
    client.app.state.media_store.max_tenant_bytes=100
    response=client.post("/api/v1/media/image",headers={**auth,"Content-Type":"image/jpeg"},content=image_bytes())
    assert response.status_code==429 and response.json()["error"]["code"]=="media_storage_quota"


def test_media_intake_backpressures_when_upload_slots_are_full(client):
    slots=client.app.state.media_store._upload_slots
    acquired=[slots.acquire(blocking=False) for _ in range(4)]
    assert all(acquired)
    try:
        response=upload(client,"image",image_bytes(),"image/jpeg")
    finally:
        for _ in acquired: slots.release()
    assert response.status_code==429
    assert response.json()["error"]["code"]=="upload_capacity_exceeded"


def test_retention_preserves_media_until_queued_scan_completes(client,auth):
    ref=upload(client,"audio",wav_bytes(),"audio/wav").json()["media_ref"]
    media_id=ref.rsplit("/",1)[1]
    client.app.state.store.db.execute("UPDATE media_assets SET created_at=0 WHERE media_id=?",(media_id,))
    body={"schema_version":"1.0","media_refs":[ref],"reaction_task":None,"consent":CONSENT}
    accepted=client.post("/api/v1/scans",headers={**auth,"Idempotency-Key":"retention-active"},json=body)
    assert accepted.status_code==202
    assert client.app.state.media_store.purge_expired(1)==0
    Worker(client.app.state.store,"retention-worker",client.app.state.worker_processor).run_once()
    assert client.app.state.media_store.purge_expired(1)==0
    assert client.app.state.media_store.resolve(ref,"tenant-a","user-a") is None


def test_retention_preserves_media_through_retries_then_releases_terminal_failure(client,auth):
    ref=upload(client,"audio",wav_bytes(),"audio/wav").json()["media_ref"]
    media_id=ref.rsplit("/",1)[1]
    client.app.state.store.db.execute("UPDATE media_assets SET created_at=0 WHERE media_id=?",(media_id,))
    accepted=client.post("/api/v1/scans",headers={**auth,"Idempotency-Key":"retention-terminal-failure"},json={
        "schema_version":"1.0","media_refs":[ref],"reaction_task":None,"consent":CONSENT,
    })
    assert accepted.status_code==202
    def failing_processor(_payload):raise RuntimeError("synthetic processor failure")
    failing_processor.media_store=client.app.state.media_store
    worker=Worker(client.app.state.store,"retention-failure-worker",failing_processor)
    assert client.app.state.media_store.purge_expired(1)==0
    assert worker.run_once()
    assert client.app.state.store.get_scan(accepted.json()["scan_id"],"tenant-a","user-a")["status"]=="queued"
    assert client.app.state.media_store.purge_expired(1)==0
    assert client.app.state.media_store.resolve(ref,"tenant-a","user-a") is not None
    time.sleep(.01)
    assert worker.run_once()
    assert client.app.state.store.get_scan(accepted.json()["scan_id"],"tenant-a","user-a")["status"]=="failed"
    assert client.app.state.media_store.resolve(ref,"tenant-a","user-a") is None
    assert client.app.state.media_store.purge_expired(1)==0


def test_media_scan_requires_explicit_consent_and_records_owned_reference(client,auth):
    ref=upload(client,"audio",wav_bytes(),"audio/wav").json()["media_ref"]
    missing=client.post("/api/v1/scans",headers={**auth,"Idempotency-Key":"no-consent"},json={"schema_version":"1.0","media_refs":[ref],"reaction_task":None})
    assert missing.status_code==422
    accepted=client.post("/api/v1/scans",headers={**auth,"Idempotency-Key":"audio-scan"},json={"schema_version":"1.0","media_refs":[ref],"reaction_task":None,"consent":CONSENT})
    assert accepted.status_code==202
    assert Worker(client.app.state.store,"media-test",client.app.state.worker_processor).run_once()
    result=client.get(f"/api/v1/scans/{accepted.json()['scan_id']}/result",headers=auth)
    assert result.status_code==200
    body=result.json()
    assert body["schema_version"]=="2.0" and body["status"]=="insufficient_evidence"
    assert body["features"]["audio"]["extraction_status"]=="available"
    assert body["features"]["video"]["extraction_status"]=="not_provided"
    assert body["targets"]["reference_sleepiness"]["value"] is None


def test_invalid_decode_and_partial_modality_failure_are_distinct(client,auth):
    bad=upload(client,"image",b"\xff\xd8\xffnot-a-real-jpeg","image/jpeg")
    good=upload(client,"audio",wav_bytes(),"audio/wav")
    assert bad.status_code==201 and good.status_code==201
    refs=[bad.json()["media_ref"],good.json()["media_ref"]]
    accepted=client.post("/api/v1/scans",headers={**auth,"Idempotency-Key":"partial"},json={"schema_version":"1.0","media_refs":refs,"reaction_task":None,"consent":CONSENT})
    assert accepted.status_code==202
    stream=io.StringIO(); handler=logging.StreamHandler(stream); handler.setFormatter(JsonFormatter()); root=logging.getLogger(); root.addHandler(handler)
    try: assert Worker(client.app.state.store,"partial-test",client.app.state.worker_processor).run_once()
    finally: root.removeHandler(handler)
    body=client.get(f"/api/v1/scans/{accepted.json()['scan_id']}/result",headers=auth).json()
    assert body["features"]["image"]["extraction_status"]=="invalid_media"
    assert body["features"]["audio"]["extraction_status"]=="available"
    log_output=stream.getvalue()
    assert "not-a-real-jpeg" not in log_output and "Bearer token-a" not in log_output
    assert "request_id" in log_output and "scan_id" in log_output and "tenant_ref" in log_output


def test_black_image_and_silent_audio_are_insufficient_quality(client,auth):
    iref=upload(client,"image",image_bytes(),"image/jpeg").json()["media_ref"]
    aref=upload(client,"audio",wav_bytes(silence=True),"audio/wav").json()["media_ref"]
    accepted=client.post("/api/v1/scans",headers={**auth,"Idempotency-Key":"poor-quality"},json={"schema_version":"1.0","media_refs":[iref,aref],"reaction_task":None,"consent":CONSENT})
    assert accepted.status_code==202
    Worker(client.app.state.store,"quality-test",client.app.state.worker_processor).run_once()
    result=client.get(f"/api/v1/scans/{accepted.json()['scan_id']}/result",headers=auth).json()
    assert result["features"]["image"]["extraction_status"]=="insufficient_quality"
    assert result["features"]["image"]["quality"]["reason"]=="face_not_detected"
    assert result["features"]["audio"]["extraction_status"]=="insufficient_quality"


def test_image_decompression_pixel_limit_rejected_by_extractor(client,auth):
    ref=upload(client,"image",image_bytes(),"image/jpeg").json()["media_ref"]
    client.app.state.worker_processor.limits["max_image_pixels"]=100
    accepted=client.post("/api/v1/scans",headers={**auth,"Idempotency-Key":"pixel-cap"},json={"schema_version":"1.0","media_refs":[ref],"reaction_task":None,"consent":CONSENT})
    assert accepted.status_code==202
    Worker(client.app.state.store,"limit-test",client.app.state.worker_processor).run_once()
    result=client.get(f"/api/v1/scans/{accepted.json()['scan_id']}/result",headers=auth).json()
    assert result["features"]["image"]["extraction_status"]=="invalid_media"
    assert result["features"]["image"]["features"]["error_code"]=="image_dimensions_out_of_range"


def test_png_dimension_bomb_stopped_before_pixel_decode(client,auth):
    ref=upload(client,"image",png_dimension_bomb(),"image/png").json()["media_ref"]
    accepted=client.post("/api/v1/scans",headers={**auth,"Idempotency-Key":"png-bomb"},json={"schema_version":"1.0","media_refs":[ref],"reaction_task":None,"consent":CONSENT})
    assert accepted.status_code==202
    Worker(client.app.state.store,"png-limit",client.app.state.worker_processor).run_once()
    result=client.get(f"/api/v1/scans/{accepted.json()['scan_id']}/result",headers=auth).json()
    assert result["features"]["image"]["extraction_status"]=="invalid_media"
    assert result["features"]["image"]["features"]["error_code"]=="image_dimensions_out_of_range"


def test_audio_duration_limit(client,auth):
    ref=upload(client,"audio",wav_bytes(seconds=31),"audio/wav").json()["media_ref"]
    accepted=client.post("/api/v1/scans",headers={**auth,"Idempotency-Key":"audio-too-long"},json={"schema_version":"1.0","media_refs":[ref],"reaction_task":None,"consent":CONSENT})
    assert accepted.status_code==202
    Worker(client.app.state.store,"audio-limit",client.app.state.worker_processor).run_once()
    result=client.get(f"/api/v1/scans/{accepted.json()['scan_id']}/result",headers=auth).json()
    assert result["features"]["audio"]["extraction_status"]=="invalid_media"
    assert result["features"]["audio"]["features"]["error_code"]=="audio_format_or_duration_limit"


def test_worker_detects_tampered_local_media(client,auth):
    original=image_bytes(); ref=upload(client,"image",original,"image/jpeg").json()["media_ref"]
    asset=client.app.state.media_store.resolve(ref,"tenant-a","user-a")
    asset["path"].write_bytes(image_bytes(kind="texture"))
    accepted=client.post("/api/v1/scans",headers={**auth,"Idempotency-Key":"tampered"},json={"schema_version":"1.0","media_refs":[ref],"reaction_task":None,"consent":CONSENT})
    assert accepted.status_code==202
    Worker(client.app.state.store,"integrity-test",client.app.state.worker_processor).run_once()
    result=client.get(f"/api/v1/scans/{accepted.json()['scan_id']}/result",headers=auth).json()
    assert result["features"]["image"]["extraction_status"]=="failed"
    assert result["features"]["image"]["features"]["error_code"]=="media_integrity_failed"


def test_synthetic_video_decode_sampling_and_no_face_quality(client,auth,tmp_path):
    video_path=tmp_path/"no-face.avi"; blob=video_file(video_path)
    ref=upload(client,"video",blob,"video/x-msvideo").json()["media_ref"]
    accepted=client.post("/api/v1/scans",headers={**auth,"Idempotency-Key":"video-test"},json={"schema_version":"1.0","media_refs":[ref],"reaction_task":None,"consent":CONSENT})
    assert accepted.status_code==202
    Worker(client.app.state.store,"video-test",client.app.state.worker_processor).run_once()
    result=client.get(f"/api/v1/scans/{accepted.json()['scan_id']}/result",headers=auth).json()
    feature=result["features"]["video"]
    assert feature["presence"]=="present"
    assert feature["extraction_status"]=="insufficient_quality"
    assert feature["features"]["perclos_estimate"] is None
    assert "not_clinically_validated" in feature["features"]["perclos_status"]


def test_temporal_face_observations_report_blinks_and_mouth_opening_without_fatigue_labels():
    rows = [
        {"timestamp_seconds":0.0,"face_valid":True,"closed":False,"mouth_open":False},
        {"timestamp_seconds":0.1,"face_valid":True,"closed":True,"mouth_open":True},
        {"timestamp_seconds":0.2,"face_valid":True,"closed":True,"mouth_open":True},
        {"timestamp_seconds":0.3,"face_valid":True,"closed":False,"mouth_open":True},
        {"timestamp_seconds":0.4,"face_valid":True,"closed":False,"mouth_open":True},
        {"timestamp_seconds":0.5,"face_valid":True,"closed":False,"mouth_open":True},
        {"timestamp_seconds":0.6,"face_valid":True,"closed":False,"mouth_open":False},
    ]
    result = temporal_eye_features(rows, 0.7, 10)
    assert result["blink_event_count"] == 1
    assert result["mouth_open_event_count"] == 1
    assert result["mouth_event_semantics"] == "observed_mouth_opening_not_yawn_or_sleepiness_label"
    assert result["perclos_estimate"] is None


def test_video_duration_limit_and_media_idempotent_replay(client,auth,tmp_path):
    path=tmp_path/"too-long.avi"; ref=upload(client,"video",video_file(path,seconds=31,fps=1),"video/x-msvideo").json()["media_ref"]
    body={"schema_version":"1.0","media_refs":[ref],"reaction_task":None,"consent":CONSENT}; headers={**auth,"Idempotency-Key":"video-limit"}
    first=client.post("/api/v1/scans",headers=headers,json=body); replay=client.post("/api/v1/scans",headers=headers,json=body)
    assert first.status_code==202 and replay.json()["scan_id"]==first.json()["scan_id"] and replay.json()["created"] is False
    Worker(client.app.state.store,"duration-test",client.app.state.worker_processor).run_once()
    result=client.get(f"/api/v1/scans/{first.json()['scan_id']}/result",headers=auth).json()
    assert result["features"]["video"]["extraction_status"]=="invalid_media"
    assert result["features"]["video"]["features"]["error_code"]=="video_resource_limit"


def test_reaction_task_validates_order_and_marks_client_report_unverified(client,auth):
    task={"task_type":"pvt","trials":[{"trial_index":i,"stimulus_timestamp_ms":1200000+10000*i,"response_timestamp_ms":1200000+10000*i+320+i} for i in range(4)]}
    accepted=client.post("/api/v1/scans",headers={**auth,"Idempotency-Key":"reaction"},json={"schema_version":"1.0","media_refs":[],"reaction_task":task,"consent":CONSENT})
    assert accepted.status_code==202
    Worker(client.app.state.store,"reaction-test",client.app.state.worker_processor).run_once()
    result=client.get(f"/api/v1/scans/{accepted.json()['scan_id']}/result",headers=auth).json()
    feature=result["features"]["reaction_task"]["features"]
    assert feature["integrity_status"]=="unverified_client_report" and feature["server_verified"] is False
    assert result["status"]=="insufficient_evidence"
    invalid={**task,"trials":[task["trials"][0],task["trials"][0]]}
    response=client.post("/api/v1/scans",headers={**auth,"Idempotency-Key":"reaction-invalid"},json={"schema_version":"1.0","media_refs":[],"reaction_task":invalid,"consent":CONSENT})
    assert response.status_code==422


def test_reaction_metrics_keep_omissions_false_starts_and_optional_subjective_reference(client,auth):
    task={"task_type":"simple_reaction","timing_metadata":{"device_clock_resolution_ms":8,"foreground":True},"trials":[
        {"trial_index":0,"stimulus_timestamp_ms":100,"response_timestamp_ms":90},
        {"trial_index":1,"stimulus_timestamp_ms":200,"response_timestamp_ms":520},
        {"trial_index":2,"stimulus_timestamp_ms":300,"response_timestamp_ms":None},
        {"trial_index":3,"stimulus_timestamp_ms":400,"response_timestamp_ms":650},
    ]}
    body={"schema_version":"1.0","media_refs":[],"reaction_task":task,"consent":CONSENT,
          "subjective_fatigue":{"score":7,"collection_consent":True}}
    accepted=client.post("/api/v1/scans",headers={**auth,"Idempotency-Key":"reaction-self-report"},json=body)
    assert accepted.status_code==202
    Worker(client.app.state.store,"reaction-self-report",client.app.state.worker_processor).run_once()
    result=client.get(f"/api/v1/scans/{accepted.json()['scan_id']}/result",headers=auth).json()
    feature=result["features"]["reaction_task"]["features"]
    assert feature["omission_count"]==1 and feature["false_start_count"]==1
    assert feature["pvt_equivalence"]=="not_established"
    assert feature["timing_metadata"]["device_clock_resolution_ms"]==8
    assert result["subjective_reference"]["score"]==7
    assert result["subjective_reference"]["training_consent"] is False
    assert result["evidence"]["schema_version"]=="1.2"
    assert result["evidence"]["fusion"]["status"]=="inactive_no_validated_release"
    assert "video" in result["evidence"]["missing_modalities"]
    no_opt_in={**body,"subjective_fatigue":{"score":7,"collection_consent":False}}
    rejected=client.post("/api/v1/scans",headers={**auth,"Idempotency-Key":"reaction-self-report-no-optin"},json=no_opt_in)
    assert rejected.status_code==202
    Worker(client.app.state.store,"reaction-self-report-declined",client.app.state.worker_processor).run_once()
    declined_result=client.get(f"/api/v1/scans/{rejected.json()['scan_id']}/result",headers=auth).json()
    assert declined_result["subjective_reference"] is None
    assert declined_result["evidence"]["subjective_reference"] is None


def test_short_eye_fraction_uses_timestamped_observed_intervals_and_is_not_perclos():
    rows = [{"timestamp_seconds": i / 4, "face_valid": True,
             "closed": i % 2 == 0, "mouth_open": False} for i in range(21)]
    result = temporal_eye_features(rows, 5.0, 4.0)
    assert result["short_eye_feature_status"] == "available"
    assert result["short_eye_closure_fraction"] == pytest.approx(0.5)
    assert result["short_clip_eye_closure_fraction"] == pytest.approx(0.5)
    assert result["short_eye_closure_observed_seconds"] == pytest.approx(5.0)
    assert result["short_eye_time_coverage"] == pytest.approx(1.0)
    assert result["short_eye_feature_version"] == "wellar-short-mobile-eye/1.0"
    assert result["perclos_estimate"] is None
    assert "not_PERCLOS" in result["short_eye_feature_semantics"]


def test_short_eye_fraction_excludes_face_loss_and_abstains_when_too_short():
    rows = [{"timestamp_seconds": i / 4, "face_valid": not 5 <= i <= 10,
             "closed": i % 2 == 0 if not 5 <= i <= 10 else None, "mouth_open": False} for i in range(21)]
    result = temporal_eye_features(rows, 5.0, 4.0)
    assert result["short_eye_feature_status"] == "insufficient_eye_time_coverage"
    assert result["short_clip_eye_closure_fraction"] is None
    assert result["short_eye_time_coverage"] == pytest.approx(0.65)
    assert result["short_eye_closure_fraction"] is None
    assert result["short_clip_eye_closure_fraction"] is None
    assert result["perclos_estimate"] is None

    short_rows = [{"timestamp_seconds": i / 4, "face_valid": True,
                   "closed": False, "mouth_open": False} for i in range(3)]
    short = temporal_eye_features(short_rows, 0.5, 4.0)
    assert short["short_eye_feature_status"] == "insufficient_observed_eye_time"
    assert short["short_eye_closure_fraction"] is None
