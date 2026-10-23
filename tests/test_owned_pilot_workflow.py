"""Deterministic software tests. All fixture records/media are synthetic and non-scientific."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from app.ml.artifact_guard import ModelActivationDenied, require_operational_artifact
from app.ml.json_backend import JsonLinearBackend
from research.evaluation_v1 import ResearchDataError, load_jsonl, validate_record
from research.pilot_dataset import PERMISSIONS, import_session, template
from research.training import TrainingConfig, train_target


NOW = "2026-05-01T12:00:00Z"


def _session(root, *, participants=True, media=None, permissions=None):
    root.mkdir(parents=True, exist_ok=True)
    (root / "image.jpg").write_bytes(b"synthetic-image-pixels-not-a-human-recording")
    granted = {name: False for name in PERMISSIONS}
    granted.update({"media_analysis": True, "media_storage": True, "model_training": True, "evaluation": True})
    granted.update(permissions or {})
    return {
        "pilot_schema_version": "wellar-owned-pilot/1.0",
        "dataset_id": "pilot-owned-test", "dataset_version": "1.0", "study_id": "pilot-protocol-test",
        "acquisition_protocol_id": "direct-annotation-test", "curator_role": "test-curator",
        "participant_id": "participant-0001", "session_id": "session-0001",
        "identity_linkage": {"pseudonymized": True, "linkage_key_stored_separately": True,
                             "linkage_governance_id": "test-governance"},
        "consent": {"status": "granted", "consented_at_utc": NOW, "consent_reference": "synthetic-test-consent",
                    "permissions": granted},
        "recorded_at_utc": NOW,
        "context": {"site_id": "local-test", "device_id": "fixture-camera", "industry": "unknown",
                    "occupation": "unknown", "lighting": "indoor", "camera_position": "front",
                    "posture": "seated", "audio_environment": "quiet"},
        "media": media or [{"sample_id": "sample-0001", "modality": "image", "path": "image.jpg",
                             "captured_at_utc": NOW,
                             "annotations": [{"timestamp_ms": 0, "eye_state": "closed", "mouth_opening": "closed",
                                              "annotation_reference": "annotation-0001", "assessor_role": "trained_annotator"}]}],
    }


def _fake_extractors(monkeypatch, pilot_module):
    def image(path, **kwargs):
        closed = "closed" in path.name
        return {"status": "available", "features": {"eye_aspect_ratio": 0.12 if closed else 0.31,
                "mouth_aspect_ratio": 0.15, "gray_mean": 110.0, "gray_std": 20.0},
                "provenance": {"extractor": "synthetic-test", "version": "fixture"}}

    def video(path, **kwargs):
        return {"status": "available", "features": {"mean_sampled_luminance": 100.0},
                "provenance": {"extractor": "synthetic-test", "version": "fixture"},
                "sample_observations": [{"timestamp_ms": 500, "eye_aspect_ratio": 0.2,
                                         "mouth_aspect_ratio": 0.18, "face_valid": True}]}

    def audio(path, **kwargs):
        return {"status": "available", "features": {"ac_rms_dbfs": -20.0, "duration_seconds": 2.0},
                "provenance": {"extractor": "synthetic-test", "version": "fixture"}}

    monkeypatch.setattr(pilot_module, "extract_image", image)
    monkeypatch.setattr(pilot_module, "extract_video", video)
    monkeypatch.setattr(pilot_module, "extract_audio", audio)


def test_templates_deny_every_permission_and_preserve_existing_files(tmp_path):
    session, rights = template()
    assert session["consent"]["status"] == "not_granted"
    assert all(session["consent"]["permissions"][permission] is False for permission in PERMISSIONS)
    assert rights["model_training_authorized"] is False and rights["consent_confirmed"] is False


def test_local_import_processes_image_video_audio_without_copying_media(tmp_path, monkeypatch):
    import research.pilot_dataset as pilot

    _fake_extractors(monkeypatch, pilot)
    manifest = _session(tmp_path, media=[
        {"sample_id": "image-001", "modality": "image", "path": "image.jpg", "captured_at_utc": NOW,
         "annotations": [{"timestamp_ms": 0, "eye_state": "closed", "mouth_opening": "open",
                          "annotation_reference": "ann-image", "assessor_role": "participant_self"}]},
        {"sample_id": "video-001", "modality": "video", "path": "image.jpg", "captured_at_utc": NOW,
         "annotations": [{"timestamp_ms": 500, "eye_state": "open", "mouth_opening": "closed",
                          "annotation_reference": "ann-video", "assessor_role": "participant_self"}]},
        {"sample_id": "audio-001", "modality": "audio", "path": "image.jpg", "captured_at_utc": NOW},
    ], permissions={"media_storage": True, "model_training": False, "evaluation": False})
    manifest_path = tmp_path / "session.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "features.jsonl"
    summary = import_session(manifest_path, output)
    rows = load_jsonl(output)

    assert summary["records"] == 3 and summary["raw_media_copied"] == 0
    assert {row["features"]["image"]["provenance"].get("extractor") for row in rows if row["modality_availability"]["image"] == "available"} == {"synthetic-test"}
    image_row = next(row for row in rows if row["sample_id"] == "image-001")
    assert image_row["labels"]["eye_state_event"]["class_label"] == 1
    assert image_row["labels"]["mouth_opening_event"]["class_label"] == 1
    video_row = next(row for row in rows if row["sample_id"] == "video-001-500")
    assert video_row["labels"]["eye_state_event"]["class_label"] == 0
    assert video_row["features"]["video"]["values"]["eye_aspect_ratio"] == 0.2
    audio_row = next(row for row in rows if row["sample_id"] == "audio-001")
    assert audio_row["features"]["audio"]["values"]["ac_rms_dbfs"] == -20.0
    assert audio_row["labels"]["eye_state_event"]["status"] == "unknown"
    assert all(row["consent"]["permissions"]["media_storage"] is True for row in rows)
    encoded = output.read_text(encoding="utf-8")
    assert "image.jpg" not in encoded and "synthetic-image-pixels" not in encoded


def test_import_requires_explicit_permissions_and_rejects_traversal(tmp_path, monkeypatch):
    import research.pilot_dataset as pilot

    _fake_extractors(monkeypatch, pilot)
    manifest = _session(tmp_path, permissions={"media_analysis": False})
    path = tmp_path / "session.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ResearchDataError, match="media_analysis permission"):
        import_session(path, tmp_path / "blocked.jsonl")

    manifest = _session(tmp_path, permissions={"media_storage": False})
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ResearchDataError, match="media_storage permission"):
        import_session(path, tmp_path / "storage-denied.jsonl")

    manifest = _session(tmp_path)
    manifest["media"][0]["path"] = "../outside.jpg"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ResearchDataError, match="inside the session directory"):
        import_session(path, tmp_path / "blocked.jsonl")


def test_target_labels_keep_kss_fatigue_and_pvt_protocols_separate(tmp_path, monkeypatch):
    import research.pilot_dataset as pilot

    _fake_extractors(monkeypatch, pilot)
    manifest = _session(tmp_path)
    manifest["media"][0]["target_labels"] = {
        "reference_sleepiness": {"status": "observed", "raw_value": 7, "class_label": 1,
            "classification_rule_id": "qualified-kss-rule-v1", "label_definition_id": "KSS-1-9/v1",
            "instrument": "KSS", "qualified": True, "qualification_reference": "qualified-KSS-protocol-v1",
            "unit": "KSS points", "source_reference": "kss-session-form-01", "assessor_role": "participant_self",
            "measured_at_utc": NOW},
    }
    path = tmp_path / "session.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "features.jsonl"
    import_session(path, output)
    row = load_jsonl(output)[0]
    assert row["labels"]["reference_sleepiness"]["raw_value"] == 7
    assert row["labels"]["subjective_fatigue"]["status"] == "unknown"
    assert row["labels"]["vigilance_performance"]["status"] == "unknown"

    manifest["media"][0]["target_labels"] = {"subjective_fatigue": {**manifest["media"][0]["target_labels"]["reference_sleepiness"]}}
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ResearchDataError, match="non-KSS instrument"):
        import_session(path, tmp_path / "invalid.jsonl")

    manifest["media"][0]["target_labels"] = {"vigilance_performance": {
        "status": "observed", "raw_value": 5, "class_label": 1, "label_definition_id": "pvt-v1",
        "classification_rule_id": "pvt-lapse-rule-v1", "instrument": "self_report", "metric": "lapse_count",
        "protocol_id": "pvt-protocol-v1", "unit": "lapses", "source_reference": "pvt-form-01",
        "assessor_role": "participant_self", "measured_at_utc": NOW}}
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ResearchDataError, match="PVT"):
        import_session(path, tmp_path / "not-pvt.jsonl")


def test_cognitive_reference_label_must_align_to_the_recording_time(tmp_path, monkeypatch):
    import research.pilot_dataset as pilot

    _fake_extractors(monkeypatch, pilot)
    manifest = _session(tmp_path)
    manifest["media"][0]["target_labels"] = {"reference_sleepiness": {
        "status": "observed", "raw_value": 7, "class_label": 1, "classification_rule_id": "kss-rule-v1",
        "label_definition_id": "KSS-1-9/v1", "instrument": "KSS", "qualified": True,
        "qualification_reference": "qualified-kss-v1", "unit": "KSS points", "source_reference": "kss-form-01",
        "assessor_role": "participant_self", "measured_at_utc": "2026-05-01T12:30:00Z"}}
    path = tmp_path / "session.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ResearchDataError, match="within 15 minutes"):
        import_session(path, tmp_path / "misaligned.jsonl")


def test_single_participant_cannot_claim_generalization(tmp_path, monkeypatch):
    import research.pilot_dataset as pilot
    from research.evaluation_v1 import evaluate_target

    _fake_extractors(monkeypatch, pilot)
    manifest = _session(tmp_path)
    path = tmp_path / "session.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "records.jsonl"
    import_session(path, output)
    record = load_jsonl(output)[0]
    with pytest.raises(ResearchDataError, match="at least two distinct participants"):
        evaluate_target([record], {record["sample_id"]: {"prediction": 1}}, target="eye_state_event")


@pytest.mark.parametrize("denied_permission", ["model_training", "evaluation"])
def test_training_and_evaluation_permissions_are_independent_gates(tmp_path, monkeypatch, denied_permission):
    import research.pilot_dataset as pilot

    _fake_extractors(monkeypatch, pilot)
    manifest = _session(tmp_path, permissions={denied_permission: False})
    path = tmp_path / "session.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    data = tmp_path / "features.jsonl"
    import_session(path, data)
    rights = {"dataset_id": "pilot-owned-test", "dataset_version": "1.0", "license_id": "local-test-rights",
              "approval_reference": "test-only", "protocol_sha256": "a" * 64,
              "model_training_authorized": True, "consent_confirmed": True}
    records = load_jsonl(data)
    with pytest.raises(ResearchDataError, match="model-training and evaluation permission"):
        train_target(records, rights, TrainingConfig("eye_state_event", ("image.eye_aspect_ratio",)))


def test_only_direct_visual_targets_are_trainable_and_candidate_is_not_operational(tmp_path, monkeypatch):
    import research.pilot_dataset as pilot

    _fake_extractors(monkeypatch, pilot)
    rows = []
    for participant in range(12):
        media = []
        for state in ("open", "closed"):
            filename = f"{participant}-{state}.jpg"
            (tmp_path / filename).write_bytes(b"synthetic-event-frame")
            media.append({"sample_id": f"sample-{participant:02}-{state}", "modality": "image", "path": filename,
                          "captured_at_utc": NOW,
                          "annotations": [{"timestamp_ms": 0, "eye_state": state, "mouth_opening": "unknown",
                                           "annotation_reference": f"ann-{participant}-{state}", "assessor_role": "trained_annotator"}]})
        manifest = _session(tmp_path, media=media)
        manifest["participant_id"] = f"participant-{participant:02}"
        manifest["session_id"] = f"session-{participant:02}"
        manifest["fixture_marker"] = "unit_test_synthetic"
        manifest_path = tmp_path / f"session-{participant:02}.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        output = tmp_path / f"records-{participant:02}.jsonl"
        import_session(manifest_path, output, allow_unit_test_fixture=True)
        rows.extend(json.loads(line) for line in output.read_text(encoding="utf-8").splitlines())

    rights = {"dataset_id": "pilot-owned-test", "dataset_version": "1.0", "license_id": "synthetic-test-only",
              "approval_reference": "test-only", "protocol_sha256": "b" * 64,
              "model_training_authorized": True, "consent_confirmed": True}
    artifact, result = train_target(rows, rights, TrainingConfig("eye_state_event", ("image.eye_aspect_ratio",), seed=9),
                                    allow_unit_test_fixture=True)
    assert result["manifest"]["artifact_class"] == "research_candidate"
    assert artifact["target"] == "eye_state_event"
    assert artifact["metadata"]["training_data_class"] == "unit_test_synthetic"
    backend = JsonLinearBackend(artifact)
    prediction = backend.predict("eye_state_event", {"image": {"eye_aspect_ratio": 0.12}})
    assert prediction["status"] in {"available", "abstained"}
    with pytest.raises(ModelActivationDenied):
        require_operational_artifact(result["manifest"])

    with pytest.raises(ResearchDataError, match="12 distinct participants"):
        train_target(rows[:2], rights, TrainingConfig("eye_state_event", ("image.eye_aspect_ratio",)),
                     allow_unit_test_fixture=True)


def test_event_target_rejects_feature_label_leakage_schema_or_fake_fixture_by_default(tmp_path, monkeypatch):
    import research.pilot_dataset as pilot

    _fake_extractors(monkeypatch, pilot)
    manifest = _session(tmp_path)
    manifest_path = tmp_path / "session.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "records.jsonl"
    import_session(manifest_path, output)
    record = load_jsonl(output)[0]
    with pytest.raises(ResearchDataError, match="geometry and image-quality"):
        TrainingConfig("eye_state_event", ("image.eye_aspect_ratio", "image.face_visible")).validate()

    record["labels"]["eye_state_event"]["class_label"] = True
    with pytest.raises(ResearchDataError, match="directly observed binary"):
        validate_record(record)


def test_existing_image_extractor_emits_explicit_eye_and_mouth_geometry(tmp_path):
    from app.media.extractors import extract_image

    points = [type("Point", (), {"x": 0.5, "y": 0.5})() for _ in range(400)]
    eye_points = ((0, 0), (0.4, 0.2), (0.6, 0.2), (1, 0), (0.6, -0.2), (0.4, -0.2))
    for indexes in ((33, 160, 158, 133, 153, 144), (362, 385, 387, 263, 373, 380)):
        for index, xy in zip(indexes, eye_points):
            points[index].x, points[index].y = xy
    for index, xy in zip((13, 14, 78, 308), ((0.5, 0.5), (0.5, 0.6), (0.4, 0.55), (0.6, 0.55))):
        points[index].x, points[index].y = xy

    class Mesh:
        def process(self, image):
            face = type("Face", (), {"landmark": points})()
            return type("Result", (), {"multi_face_landmarks": [face]})()

    image_path = tmp_path / "synthetic.png"
    checker = (np.indices((80, 80)).sum(axis=0) % 2).astype(bool)
    textured = np.where(checker[:, :, None], 176, 80).astype(np.uint8)
    textured = np.repeat(textured, 3, axis=2)
    Image.fromarray(textured).save(image_path)
    result = extract_image(image_path, max_pixels=10_000, face_mesh=Mesh())
    assert result["status"] == "available"
    assert result["features"]["eye_aspect_ratio"] is not None
    assert result["features"]["mouth_aspect_ratio"] == pytest.approx(0.5)
    assert "research_annotation_only" in result["provenance"]["eye_aspect_ratio"]


def test_pilot_schema_json_is_parseable_and_names_independent_permissions():
    schema_path = Path(__file__).resolve().parents[1] / "research" / "schema_v1_2.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["properties"]["schema_version"]["const"] == "1.2"
    assert set(schema["properties"]["consent"]["properties"]["permissions"]["required"]) == set(PERMISSIONS)
    assert "eye_state_event" in schema["properties"]["labels"]["required"]


def test_video_extractor_can_return_bounded_timestamped_geometry_for_annotation_alignment(monkeypatch, tmp_path):
    import cv2
    from app.media.extractors import extract_video

    points = [type("Point", (), {"x": 0.5, "y": 0.5})() for _ in range(400)]
    for indexes in ((33, 160, 158, 133, 153, 144), (362, 385, 387, 263, 373, 380)):
        for index, xy in zip(indexes, ((0, 0), (0.4, 0.2), (0.6, 0.2), (1, 0), (0.6, -0.2), (0.4, -0.2))):
            points[index].x, points[index].y = xy
    for index, xy in zip((13, 14, 78, 308), ((0.5, 0.5), (0.5, 0.6), (0.4, 0.55), (0.6, 0.55))):
        points[index].x, points[index].y = xy

    checker = (np.indices((80, 80)).sum(axis=0) % 2).astype(bool)
    textured = np.where(checker[:, :, None], 176, 80).astype(np.uint8)
    textured = np.repeat(textured, 3, axis=2)

    class Capture:
        def __init__(self, path):
            self.index = 0

        def isOpened(self):
            return True

        def get(self, prop):
            return {cv2.CAP_PROP_FPS: 4.0, cv2.CAP_PROP_FRAME_COUNT: 4.0,
                    cv2.CAP_PROP_FRAME_WIDTH: 80.0, cv2.CAP_PROP_FRAME_HEIGHT: 80.0}[prop]

        def read(self):
            if self.index >= 4:
                return False, None
            self.index += 1
            return True, textured.copy()

        def release(self):
            pass

    class Mesh:
        def process(self, frame):
            face = type("Face", (), {"landmark": points})()
            return type("Result", (), {"multi_face_landmarks": [face]})()

    monkeypatch.setattr(cv2, "VideoCapture", Capture)
    monkeypatch.setattr(cv2, "cvtColor", lambda frame, code: frame)
    result = extract_video(tmp_path / "synthetic.mp4", max_seconds=2, max_frames=10, sample_hz=4,
                           max_pixels=10_000, face_mesh=Mesh(), include_sample_features=True)
    assert result["status"] == "available"
    assert [sample["timestamp_ms"] for sample in result["sample_observations"]] == [0, 250, 500, 750]
    assert all(sample["eye_aspect_ratio"] is not None and sample["mouth_aspect_ratio"] == pytest.approx(0.5)
               for sample in result["sample_observations"])
