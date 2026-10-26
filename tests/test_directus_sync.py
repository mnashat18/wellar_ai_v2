from __future__ import annotations

import hashlib
import json
import time

import pytest

from app.integrations.directus_sync import (
    DirectusBindingEvidence,
    DirectusOutboxPublisher,
    VerifiedDirectusState,
    build_directus_v2_result_contract_v1,
)
from app.queue.sqlite_store import SQLiteQueueStore
from app.security.auth import Principal
from app.compat.directus_facade import DirectusResultWriter


DIRECTUS_SCAN_ID = "00000000-0000-4000-8000-000000000001"
OTHER_DIRECTUS_SCAN_ID = "00000000-0000-4000-8000-000000000002"


def result(scan_id):
    return {"schema_version":"2.0","scan_id":scan_id,"status":"insufficient_evidence",
            "message":"Inference is disabled.","drowsiness_indicator":{"risk_level":"high_risk",
            "confidence_level":"low","model_version":"rules-v1","contributing_indicators":["eye_closure"],
            "baseline":{"used":True},"raw_measurements":{"perclos":0.4}}}


def completed_store(tmp_path, suffix="one"):
    store=SQLiteQueueStore(str(tmp_path/f"sync-{suffix}.sqlite"),max_attempts=3,lease_seconds=2)
    scan_id=f"v2-{suffix}"
    payload="{}"
    store.submit(scan_id,Principal("subject-a","tenant-a"),f"key-{suffix}",hashlib.sha256(payload.encode()).hexdigest(),payload)
    job=store.claim("worker")
    store.complete(job,result(scan_id))
    return store,scan_id


def evidence(scan_id, directus_id=DIRECTUS_SCAN_ID, **changes):
    base=dict(scan_id=scan_id,directus_scan_id=directus_id,tenant_id="tenant-a",subject_id="subject-a",
              remote_owner_id="subject-a",remote_tenant_id="tenant-a",remote_status="media_ready",
              membership_active=True,media_owner_verified=True,verified_at=time.time())
    base.update(changes)
    return DirectusBindingEvidence(**base)


class IdempotentRemote:
    def __init__(self, lose_first_response=False):
        self.records={}
        self.lose_first_response=lose_first_response
        self.calls=0

    def ensure_synced(self, event):
        self.calls+=1
        assert "result_json" not in event
        if event["event_type"]=="scan.completed":
            assert event["v2_result_payload"]["risk_level"]=="high_risk"
            assert event["v2_result_payload"]["scan_id"]==event["directus_scan_id"]
        remote_id=event["directus_scan_id"]
        self.records[remote_id]={"status":"completed","result_scan_id":remote_id}
        if self.lose_first_response and self.calls==1:
            error=TimeoutError("response lost")
            error.error_code="directus_timeout"
            raise error
        saved=self.records[remote_id]
        return VerifiedDirectusState(remote_id,saved["result_scan_id"],saved["status"])


def test_binding_requires_verified_owner_tenant_membership_state_and_media(tmp_path):
    store,scan_id=completed_store(tmp_path)
    try:
        with pytest.raises(ValueError):
            store.bind_directus_scan(evidence(scan_id,remote_owner_id="other"))
        with pytest.raises(ValueError):
            store.bind_directus_scan(evidence(scan_id,remote_status="processing"))
        with pytest.raises(ValueError):
            store.bind_directus_scan(evidence(scan_id,membership_active=False))
        with pytest.raises(ValueError):
            store.bind_directus_scan(evidence(scan_id,media_owner_verified=False))
        assert store.get_directus_binding(scan_id,"tenant-a","subject-a") is None
    finally:
        store.close()


def test_binding_is_durable_unique_and_tenant_subject_scoped(tmp_path):
    store,scan_id=completed_store(tmp_path)
    try:
        proof=evidence(scan_id)
        assert store.bind_directus_scan(proof) is True
        assert store.bind_directus_scan(proof) is False
        assert store.get_directus_binding(scan_id,"tenant-a","subject-a")["directus_scan_id"]==DIRECTUS_SCAN_ID
        assert store.get_directus_binding(scan_id,"tenant-b","subject-a") is None
        with pytest.raises(ValueError,match="different Directus binding"):
            store.bind_directus_scan(evidence(scan_id,OTHER_DIRECTUS_SCAN_ID))
        other_scan="v2-two"
        payload="{}"
        store.submit(other_scan,Principal("subject-a","tenant-a"),"key-two",hashlib.sha256(payload.encode()).hexdigest(),payload)
        other_job=store.claim("worker-two")
        store.complete(other_job,result(other_scan))
        with pytest.raises(ValueError,match="already bound"):
            store.bind_directus_scan(evidence(other_scan,DIRECTUS_SCAN_ID))
    finally:
        store.close()


def test_pending_directus_scan_can_be_bound_without_claiming_media_verification(tmp_path):
    store,scan_id=completed_store(tmp_path)
    try:
        proof=evidence(scan_id,remote_status="pending",media_owner_verified=False)
        assert store.bind_directus_scan(proof) is True
        binding=store.get_directus_binding(scan_id,"tenant-a","subject-a")
        assert binding["remote_scan_status"]=="pending"
        assert binding["media_owner_verified"] in (False,0)
    finally:
        store.close()


def test_unbound_completion_is_not_claimed_and_binding_releases_durable_event(tmp_path):
    store,scan_id=completed_store(tmp_path)
    try:
        assert store.claim_directus_outbox("publisher") is None
        store.bind_directus_scan(evidence(scan_id))
        claimed=store.claim_directus_outbox("publisher")
        assert claimed["scan_id"]==scan_id
        assert claimed["directus_scan_id"]==DIRECTUS_SCAN_ID
        assert claimed["result_json"]["status"]=="insufficient_evidence"
        assert claimed["result_json"]["drowsiness_indicator"]["risk_level"]=="high_risk"
    finally:
        store.close()


def test_binding_and_pending_sync_survive_local_process_restart(tmp_path):
    store,scan_id=completed_store(tmp_path,"restart")
    path=store.path
    store.bind_directus_scan(evidence(scan_id))
    store.close()
    restarted=SQLiteQueueStore(path)
    try:
        binding=restarted.get_directus_binding(scan_id,"tenant-a","subject-a")
        assert binding["verification_version"]=="directus-binding-v1"
        assert binding["media_owner_verified"]==1
        event=restarted.claim_directus_outbox("after-restart")
        assert event["directus_scan_id"]==DIRECTUS_SCAN_ID
    finally:
        restarted.close()


def test_publisher_recovers_remote_success_with_lost_response_idempotently(tmp_path):
    store,scan_id=completed_store(tmp_path)
    try:
        store.bind_directus_scan(evidence(scan_id))
        remote=IdempotentRemote(lose_first_response=True)
        publisher=DirectusOutboxPublisher(store,remote,max_attempts=3,retry_base_seconds=.001)
        assert publisher.run_once("publisher-a")=="retry_scheduled"
        row=store.db.execute("SELECT delivery_status,last_error_code FROM outbox").fetchone()
        assert row["delivery_status"]=="pending" and row["last_error_code"]=="directus_timeout"
        time.sleep(.005)
        assert publisher.run_once("publisher-b")=="delivered"
        assert remote.calls==2
        assert store.db.execute("SELECT delivery_status,published_at FROM outbox").fetchone()["delivery_status"]=="delivered"
    finally:
        store.close()


def test_expired_publisher_lease_fences_stale_ack_and_allows_recovery(tmp_path):
    store,scan_id=completed_store(tmp_path)
    try:
        store.bind_directus_scan(evidence(scan_id))
        instant=time.time()
        first=store.claim_directus_outbox("old",lease_seconds=1,now=instant)
        second=store.claim_directus_outbox("new",lease_seconds=2,now=instant+2)
        assert second["fencing_token"]==first["fencing_token"]+1
        assert not store.ack_directus_outbox(first["event_id"],"old",first["fencing_token"],DIRECTUS_SCAN_ID,"completed",DIRECTUS_SCAN_ID,now=instant+2)
        assert store.ack_directus_outbox(second["event_id"],"new",second["fencing_token"],DIRECTUS_SCAN_ID,"completed",DIRECTUS_SCAN_ID,now=instant+2)
    finally:
        store.close()


def test_local_ack_failure_recovers_after_lease_without_duplicate_remote_record(tmp_path):
    store,scan_id=completed_store(tmp_path)
    try:
        store.bind_directus_scan(evidence(scan_id))
        remote=IdempotentRemote()
        publisher=DirectusOutboxPublisher(store,remote,lease_seconds=.02)
        real_ack=store.ack_directus_outbox
        calls={"n":0}
        def fail_ack_once(*args,**kwargs):
            calls["n"]+=1
            if calls["n"]==1: raise OSError("local acknowledgement unavailable")
            return real_ack(*args,**kwargs)
        store.ack_directus_outbox=fail_ack_once
        with pytest.raises(OSError): publisher.run_once("publisher-a")
        time.sleep(.03)
        assert publisher.run_once("publisher-b")=="delivered"
        assert remote.calls==2
        assert len(remote.records)==1
    finally:
        store.close()


def test_directus_schema_failure_retries_with_safe_category_and_no_error_text(tmp_path):
    store,scan_id=completed_store(tmp_path)
    try:
        store.bind_directus_scan(evidence(scan_id))
        class SchemaFailure:
            def ensure_synced(self,event):
                error=RuntimeError("private response body")
                error.error_code="directus_schema_mismatch"
                raise error
        publisher=DirectusOutboxPublisher(store,SchemaFailure(),retry_base_seconds=.001)
        assert publisher.run_once("publisher")=="retry_scheduled"
        row=store.db.execute("SELECT delivery_status,last_error_code,payload_json FROM outbox").fetchone()
        assert row["last_error_code"]=="directus_schema_mismatch"
        assert "private response body" not in row["payload_json"]
    finally:
        store.close()


def test_rejected_least_privilege_directus_writer_credential_fails_closed(tmp_path):
    store,scan_id=completed_store(tmp_path)
    try:
        store.bind_directus_scan(evidence(scan_id))
        class ForbiddenWriter:
            def ensure_synced(self,event):
                error=PermissionError("server authorization denied")
                error.error_code="directus_forbidden"
                raise error
        publisher=DirectusOutboxPublisher(store,ForbiddenWriter(),retry_base_seconds=.001)
        assert publisher.run_once("publisher")=="retry_scheduled"
        row=store.db.execute("SELECT delivery_status,last_error_code FROM outbox").fetchone()
        assert row["delivery_status"]=="pending" and row["last_error_code"]=="directus_forbidden"
    finally:
        store.close()


def test_publisher_rejects_malformed_result_before_remote_write(tmp_path):
    store,scan_id=completed_store(tmp_path)
    try:
        store.db.execute("UPDATE results SET result_json=?",(json.dumps({"scan_id":scan_id,"status":"completed","targets":{"reference_sleepiness":{"value":0.1}}}),))
        store.bind_directus_scan(evidence(scan_id))
        remote=IdempotentRemote()
        publisher=DirectusOutboxPublisher(store,remote,retry_base_seconds=.001)
        assert publisher.run_once("publisher")=="failed"
        assert remote.calls==0
        row=store.db.execute("SELECT delivery_status,attempts FROM outbox").fetchone()
        assert row["delivery_status"]=="dead" and row["attempts"]==1
        assert publisher.run_once("publisher")=="idle"
        assert remote.calls==0
    finally:
        store.close()


def test_terminal_failure_is_transactionally_queued_and_verified(tmp_path):
    store=SQLiteQueueStore(str(tmp_path/"failed-sync.sqlite"),max_attempts=1,lease_seconds=2)
    try:
        payload="{}"; scan_id="v2-failed"
        store.submit(scan_id,Principal("subject-a","tenant-a"),"failed-key",hashlib.sha256(payload.encode()).hexdigest(),payload)
        job=store.claim("worker")
        store.fail(job,"processing_error")
        event=store.db.execute("SELECT event_type FROM outbox").fetchone()
        assert event["event_type"]=="scan.failed"
        store.bind_directus_scan(evidence(scan_id))
        class FailureRemote:
            def ensure_synced(self,event):
                assert event["event_type"]=="scan.failed" and event["v2_result_payload"] is None
                return VerifiedDirectusState(DIRECTUS_SCAN_ID,None,"failed")
        publisher=DirectusOutboxPublisher(store,FailureRemote())
        assert publisher.run_once("publisher")=="delivered"
    finally:
        store.close()


def test_terminal_failure_and_directus_intent_roll_back_atomically(tmp_path):
    store=SQLiteQueueStore(str(tmp_path/"failed-rollback.sqlite"),max_attempts=1,lease_seconds=2)
    try:
        payload="{}"; scan_id="v2-failed-rollback"
        store.submit(scan_id,Principal("subject-a","tenant-a"),"failed-key",hashlib.sha256(payload.encode()).hexdigest(),payload)
        job=store.claim("worker")
        store.db.execute("CREATE TRIGGER reject_failed_sync BEFORE INSERT ON outbox WHEN NEW.event_type='scan.failed' BEGIN SELECT RAISE(ABORT,'injected'); END")
        with pytest.raises(Exception): store.fail(job,"processing_error")
        assert store.db.execute("SELECT status FROM jobs WHERE job_id=?",(job["job_id"],)).fetchone()[0]=="running"
        assert store.db.execute("SELECT status FROM scans WHERE scan_id=?",(scan_id,)).fetchone()[0]=="processing"
        assert store.db.execute("SELECT count(*) FROM outbox").fetchone()[0]==0
    finally:
        store.close()


def test_remote_identity_or_state_mismatch_is_never_acknowledged(tmp_path):
    store,scan_id=completed_store(tmp_path)
    try:
        store.bind_directus_scan(evidence(scan_id))
        class WrongRemote:
            def ensure_synced(self,event):
                return VerifiedDirectusState("another-directus-id","another-directus-id","completed")
        publisher=DirectusOutboxPublisher(store,WrongRemote(),retry_base_seconds=.001)
        assert publisher.run_once("publisher")=="retry_scheduled"
        row=store.db.execute("SELECT delivery_status,last_error_code,published_at FROM outbox").fetchone()
        assert row["delivery_status"]=="pending"
        assert row["last_error_code"]=="remote_state_mismatch"
        assert row["published_at"] is None
    finally:
        store.close()


def test_directus_contract_maps_indicator_even_when_inference_is_disabled():
    source=result("v2-contract")
    import uuid
    directus_scan_id=str(uuid.uuid4())
    payload=build_directus_v2_result_contract_v1(source,directus_scan_id)
    assert payload["scan_id"]==directus_scan_id
    assert payload["risk_level"]=="high_risk"
    assert payload["confidence"]==0.3
    assert payload["explanation"]=="eye_closure; Confidence level: low; Inference is disabled."
    assert payload["suggested_action"]=="Stop the activity and rest."
    assert payload["ai_model_version"]=="rules-v1"
    assert payload["baseline_used"] is True
    assert payload["face_metrics"]=={"perclos":0.4}
    assert payload["readiness_score"] is None


def test_real_v2_sample_payload_values_match_scan_results_column_types():
    import uuid
    from app.integrations.directus_sync import NUMERIC_FIELDS, JSON_OBJECT_FIELDS, validate_scan_result_payload
    source=result(str(uuid.uuid4()))
    source["drowsiness_indicator"]["risk_level"]="stable"
    source["drowsiness_indicator"]["baseline"]["used"]=False
    source["features"]={"video":{"features":{"face_detection_ratio":0.875}},
        "audio":{"features":{"duration_seconds":12.5,"rms_dbfs":-22.4,"peak_dbfs":-3.1,
            "speech_like_window_fraction":0.4,"voice_activity_window_fraction":0.35,
            "clipped_sample_fraction":0.01}}}
    payload=build_directus_v2_result_contract_v1(source,str(uuid.uuid4()))
    assert payload["confidence"]==0.3
    assert payload["camera_confidence"]==0.875
    assert payload["voice_confidence"]==0.35
    assert payload["voice_metrics"]=={"duration_seconds":12.5,"rms_dbfs":-22.4,"peak_dbfs":-3.1,
        "speech_like_window_fraction":0.4,"voice_activity_window_fraction":0.35,"clipped_sample_fraction":0.01}
    assert payload["readiness_score"] is None and payload["task_performance_score"] is None
    assert validate_scan_result_payload(payload)==payload
    for field in NUMERIC_FIELDS:
        value=payload.get(field)
        assert value is None or (isinstance(value,(int,float)) and not isinstance(value,bool) and abs(value)<=99999.99999)
    assert payload.get("task_performance_score") is None
    internal_analysis=payload.get("internal_analysis")
    assert internal_analysis is None or (isinstance(internal_analysis,str) and len(internal_analysis)<=255)
    assert isinstance(payload["ai_model_version"],str) and len(payload["ai_model_version"])<=255
    assert isinstance(payload["baseline_used"],bool) and payload["baseline_used"] is False
    assert all(payload.get(field) is None or isinstance(payload[field],dict) for field in JSON_OBJECT_FIELDS)
    def finite_json(value):
        import math
        if isinstance(value,float): return math.isfinite(value)
        if isinstance(value,dict): return all(finite_json(child) for child in value.values())
        if isinstance(value,list): return all(finite_json(child) for child in value)
        return True
    assert all(finite_json(payload[field]) for field in JSON_OBJECT_FIELDS if isinstance(payload.get(field),dict))
    assert payload["scan_id"]==str(uuid.UUID(payload["scan_id"]))
    assert "Confidence level: low" in payload["explanation"]
    unknown_source={**source,"drowsiness_indicator":{**source["drowsiness_indicator"],"confidence_level":"unknown"}}
    unknown_payload=build_directus_v2_result_contract_v1(unknown_source,str(uuid.uuid4()))
    assert unknown_payload["confidence"] is None
    assert validate_scan_result_payload(unknown_payload)==unknown_payload
    oversized_internal={**unknown_payload,"internal_analysis":"{"+"x"*300+"}"}
    normalized=validate_scan_result_payload(oversized_internal)
    assert normalized["internal_analysis"] is None


def test_v2_result_without_audio_or_video_features_has_no_confidence_or_voice_metrics():
    import uuid
    payload=build_directus_v2_result_contract_v1(result(str(uuid.uuid4())),str(uuid.uuid4()))
    assert payload["camera_confidence"] is None
    assert payload["voice_confidence"] is None
    assert payload["voice_metrics"] is None


def test_result_writer_upserts_same_directus_row_on_repeated_delivery():
    class Remote:
        def __init__(self): self.row=None; self.status="pending"; self.creates=0; self.updates=0
        def write_v2_result(self,scan_id,payload):
            if self.row is None:self.creates+=1
            else:self.updates+=1
            self.row={"id":"result-row","scan_id":scan_id,**payload}
        def set_scan_status(self,scan_id,status,failure_reason=None): self.status=status
        def get_scan(self,scan_id): return {"id":scan_id,"status":self.status}
        def existing_result(self,scan_id): return self.row
    remote=Remote(); writer=DirectusResultWriter(remote)
    import uuid
    directus_scan_id=str(uuid.uuid4())
    event={"event_type":"scan.completed","directus_scan_id":directus_scan_id,
           "v2_result_payload":{"scan_id":directus_scan_id,"risk_level":"stable"}}
    assert writer.ensure_synced(event)==VerifiedDirectusState(directus_scan_id,directus_scan_id,"completed")
    assert writer.ensure_synced(event)==VerifiedDirectusState(directus_scan_id,directus_scan_id,"completed")
    assert remote.creates==1 and remote.updates==1
