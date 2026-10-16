import json
import time

from app.workers.worker import Worker


def completed_scan(client, auth, store, key):
    accepted = client.post("/api/v1/scans", headers={**auth, "Idempotency-Key": key}, json={"schema_version": "1.0", "media_refs": []})
    scan_id = accepted.json()["scan_id"]
    assert Worker(store, f"worker-{key}", client.app.state.worker_processor).run_once()
    return scan_id


def test_kss_consent_off_does_not_retain_label_or_features(client, auth, store):
    scan_id = completed_scan(client, auth, store, "kss-off")
    result = client.get(f"/api/v1/scans/{scan_id}/result", headers=auth).json()
    indicator = result["drowsiness_indicator"]
    assert indicator["status"] == "insufficient_quality"
    assert "state" not in indicator and "level" not in indicator
    assert "confidence" not in indicator and "accuracy" not in indicator
    assert "indicative, non-diagnostic" in indicator["label"]
    response = client.post(f"/api/v1/scans/{scan_id}/labels/kss", headers=auth, json={"kss": 8, "training_consent": False})
    assert response.status_code == 202
    assert response.json()["label_status"] == "not_retained"
    assert store.db.execute("SELECT COUNT(*) FROM training_labels").fetchone()[0] == 0


def test_kss_consent_on_retains_only_allowlisted_features(client, auth, store, monkeypatch):
    monkeypatch.setenv("WELLAR_LABEL_RETENTION_DAYS", "30")
    scan_id = completed_scan(client, auth, store, "kss-on")
    result = {"evidence": {"modalities": {"video": {"features": {
        "short_clip_eye_closure_fraction": 0.2, "perclos_estimate": 0.3, "blink_rate_per_minute": 22.0,
        "media_path": "private/path", "media_ref": "media://secret",
    }}}}}
    store.db.execute("UPDATE results SET result_json=? WHERE scan_id=?", (json.dumps(result), scan_id))
    response = client.post(f"/api/v1/scans/{scan_id}/labels/kss", headers=auth, json={"kss": 6, "training_consent": True})
    assert response.status_code == 202
    row = store.db.execute("SELECT kss,features_json FROM training_labels WHERE scan_id=?", (scan_id,)).fetchone()
    assert row["kss"] == 6
    assert json.loads(row["features_json"]) == {"video.short_clip_eye_closure_fraction": 0.2, "video.blink_rate_per_minute": 22.0}
    assert "media" not in row["features_json"]


def test_consent_requires_explicit_retention_and_withdrawal_is_idempotent(client, auth, store, monkeypatch):
    scan_id = completed_scan(client, auth, store, "kss-retention-required")
    monkeypatch.delenv("WELLAR_LABEL_RETENTION_DAYS", raising=False)
    payload = {"kss": 7, "fatigue": 8, "focus": 2, "shift": "night", "time_of_day": "22:00", "training_consent": True}
    rejected = client.post(f"/api/v1/scans/{scan_id}/labels/kss", headers=auth, json=payload)
    assert rejected.status_code == 503
    assert rejected.json()["error"]["code"] == "label_retention_unconfigured"
    assert store.db.execute("SELECT COUNT(*) FROM training_labels").fetchone()[0] == 0
    monkeypatch.setenv("WELLAR_LABEL_RETENTION_DAYS", "1")
    accepted = client.post(f"/api/v1/scans/{scan_id}/labels/kss", headers=auth, json=payload)
    assert accepted.status_code == 202
    row = store.db.execute("SELECT fatigue,focus,shift,time_of_day,expires_at,consented_at,consent_text_version FROM training_labels WHERE scan_id=?", (scan_id,)).fetchone()
    assert row["fatigue"] == 8 and row["focus"] == 2 and row["shift"] == "night"
    assert row["expires_at"] > time.time() and row["consented_at"] > 0
    assert row["consent_text_version"] == "wellar-v2-training-consent-v1"
    assert client.delete(f"/api/v1/scans/{scan_id}/labels", headers=auth).status_code == 204
    assert client.delete(f"/api/v1/scans/{scan_id}/labels", headers=auth).status_code == 204
    assert store.db.execute("SELECT COUNT(*) FROM training_labels").fetchone()[0] == 0


def test_me_label_withdrawal_and_expiry_purge(client, auth, store, monkeypatch):
    scan_id = completed_scan(client, auth, store, "kss-expiry")
    monkeypatch.setenv("WELLAR_LABEL_RETENTION_DAYS", "1")
    response = client.post(f"/api/v1/scans/{scan_id}/labels/kss", headers=auth, json={"kss": 5, "training_consent": True})
    assert response.status_code == 202
    assert store.purge_expired_training_labels(now=time.time()+2*86400) == 1
    assert client.delete("/api/v1/me/labels", headers=auth).status_code == 204
    assert client.delete("/api/v1/me/labels", headers=auth).status_code == 204
    assert store.db.execute("SELECT COUNT(*) FROM training_labels").fetchone()[0] == 0


def test_indicator_contract_has_no_confidence_or_accuracy():
    from app.drowsiness_indicator import classify
    output = classify({"short_clip_eye_closure_fraction": .03, "blink_rate_per_minute": 10, "mean_eye_closure_seconds": .1,
                       "max_eye_closure_seconds": .2, "face_detection_ratio": .9,
                       "mean_luminance": 80, "mean_blur_variance": 40,
                       "temporal_coverage": .8, "yaw_proxy": .01, "pitch_proxy": .01})
    assert output["state"] in {"Stable", "Low Focus", "Elevated Fatigue", "High Risk"}
    assert "confidence" not in output and "accuracy" not in output
    assert "indicative, non-diagnostic" in output["label"]
