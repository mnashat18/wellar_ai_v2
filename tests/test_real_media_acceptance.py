"""Extractor/importer acceptance using generated synthetic media only."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from app.media.extractors import InvalidMediaError, extract_audio, extract_image, extract_video
from research.evaluation_v1 import ResearchDataError, load_jsonl
from research.pilot_dataset import import_session
from tests.media_fixtures import image_bytes, video_file, wav_bytes


NOW = "2026-10-15T12:00:00Z"


def _manifest(media):
    return {
        "pilot_schema_version": "wellar-owned-pilot/1.0",
        "dataset_id": "pilot-dataset-test01", "dataset_version": "1.0",
        "study_id": "synthetic-test-study", "acquisition_protocol_id": "synthetic-fixture-protocol",
        "curator_role": "test-curator", "participant_id": "participant-test-01",
        "session_id": "session-test-01",
        "identity_linkage": {"pseudonymized": True, "linkage_key_stored_separately": True,
                             "linkage_governance_id": "test-governance-01"},
        "consent": {"status": "granted", "consented_at_utc": NOW, "consent_reference": "test-consent-01",
                    "permissions": {"media_analysis": True, "media_storage": True,
                                    "model_training": False, "evaluation": False,
                                    "cross_site_transfer": False}},
        "recorded_at_utc": NOW,
        "context": {"site_id": "synthetic-test", "device_id": "fixture-device", "industry": "unknown",
                    "occupation": "unknown", "lighting": "synthetic", "camera_position": "front",
                    "posture": "seated", "audio_environment": "synthetic"},
        "media": media,
        "fixture_marker": "unit_test_synthetic",
    }


def _write_manifest(root: Path, media):
    path = root / "session.json"
    path.write_text(json.dumps(_manifest(media)), encoding="utf-8")
    return path


def test_pilot_import_runs_real_image_video_and_pcm_wav_extractors(tmp_path):
    Image.new("RGB", (160, 120), (128, 128, 128)).save(tmp_path / "image.png")
    video_file(tmp_path / "video.avi", seconds=2, fps=4)
    (tmp_path / "audio.wav").write_bytes(wav_bytes(seconds=1.5))
    media = [
        {"sample_id": "image-sample-01", "modality": "image", "path": "image.png", "captured_at_utc": NOW},
        {"sample_id": "video-sample-01", "modality": "video", "path": "video.avi", "captured_at_utc": NOW},
        {"sample_id": "audio-sample-01", "modality": "audio", "path": "audio.wav", "captured_at_utc": NOW},
    ]
    manifest = _write_manifest(tmp_path, media)
    output = tmp_path / "features.jsonl"
    summary = import_session(manifest, output, allow_unit_test_fixture=True)
    rows = load_jsonl(output, allow_unit_test_fixture=True)
    by_modality = {row["sample_id"]: row for row in rows}

    assert summary["records"] == 3 and summary["raw_media_copied"] == 0
    assert by_modality["image-sample-01"]["modality_availability"]["image"] == "failed"
    assert by_modality["image-sample-01"]["features"]["image"]["values"]["eye_aspect_ratio"] is None
    assert by_modality["image-sample-01"]["features"]["image"]["values"]["mouth_aspect_ratio"] is None
    assert "gray_mean" in by_modality["image-sample-01"]["features"]["image"]["values"]
    assert "mean_sampled_luminance" in by_modality["video-sample-01"]["features"]["video"]["values"]
    assert "face_coverage" in by_modality["video-sample-01"]["features"]["video"]["values"]
    assert "mouth_open_event_count" in by_modality["video-sample-01"]["features"]["video"]["values"]
    audio = by_modality["audio-sample-01"]["features"]["audio"]["values"]
    assert audio["sample_rate_hz"] == 16000 and audio["ac_rms_dbfs"] > -200
    assert by_modality["audio-sample-01"]["modality_availability"]["audio"] == "available"

    serialized = output.read_text(encoding="utf-8")
    for forbidden in (str(tmp_path), "image.png", "video.avi", "audio.wav", "Authorization", "secret"):
        assert forbidden not in serialized
    assert "fixture_marker" in serialized
    assert all(row["consent"]["permissions"]["model_training"] is False for row in rows)
    assert all(row["consent"]["permissions"]["evaluation"] is False for row in rows)


def test_actual_extractors_report_no_face_low_light_short_video_and_silence(tmp_path):
    blank = tmp_path / "blank.png"
    Image.new("RGB", (160, 120), (0, 0, 0)).save(blank)
    image_result = extract_image(blank, max_pixels=20_000)
    assert image_result["status"] == "insufficient_quality"
    assert image_result["quality"]["reason"] == "face_not_detected"

    black_video = video_file(tmp_path / "black.avi", seconds=2, fps=4)
    del black_video
    video_result = extract_video(tmp_path / "black.avi", max_seconds=30, max_frames=1800,
                                 sample_hz=4.0, max_pixels=4_000_000, include_sample_features=True)
    assert video_result["quality"]["low_light"] is True
    assert video_result["features"]["mean_sampled_luminance"] < 20
    assert video_result["status"] == "insufficient_quality"

    one_frame = video_file(tmp_path / "short.avi", seconds=1, fps=1)
    del one_frame
    short_result = extract_video(tmp_path / "short.avi", max_seconds=30, max_frames=1800,
                                 sample_hz=4.0, max_pixels=4_000_000)
    assert short_result["status"] == "insufficient_quality"
    assert short_result["timing_ms"]["frames_sampled"] == 1

    silence = tmp_path / "silence.wav"
    silence.write_bytes(wav_bytes(seconds=1.5, silence=True))
    audio_result = extract_audio(silence, max_seconds=30)
    assert audio_result["status"] == "insufficient_quality"
    assert audio_result["quality"]["reason"] == "silence_detected"


@pytest.mark.parametrize(("name", "claimed_modality", "payload"), [
    ("corrupt.png", "image", b"not an image"),
    ("wrong.wav", "audio", b"not a WAV"),
])
def test_import_rejects_corrupt_or_wrong_modality_media(tmp_path, name, claimed_modality, payload):
    (tmp_path / name).write_bytes(payload)
    manifest = _write_manifest(tmp_path, [{"sample_id": "bad-sample-01", "modality": claimed_modality,
                                           "path": name, "captured_at_utc": NOW}])
    with pytest.raises(InvalidMediaError):
        import_session(manifest, tmp_path / "must-not-exist.jsonl", allow_unit_test_fixture=True)
    assert not (tmp_path / "must-not-exist.jsonl").exists()


def test_import_rejects_valid_image_claimed_as_audio(tmp_path):
    (tmp_path / "image.jpg").write_bytes(image_bytes())
    manifest = _write_manifest(tmp_path, [{"sample_id": "wrong-modality-01", "modality": "audio",
                                           "path": "image.jpg", "captured_at_utc": NOW}])
    with pytest.raises(InvalidMediaError):
        import_session(manifest, tmp_path / "must-not-exist.jsonl", allow_unit_test_fixture=True)
    assert not (tmp_path / "must-not-exist.jsonl").exists()


@pytest.mark.parametrize(("modality", "limit"), [
    ("image", 5 * 1024 * 1024), ("video", 20 * 1024 * 1024), ("audio", 8 * 1024 * 1024),
])
def test_import_rejects_media_over_modality_size_bound_before_decoding(tmp_path, modality, limit):
    suffix = {"image": ".png", "video": ".mp4", "audio": ".wav"}[modality]
    oversized = tmp_path / f"oversized{suffix}"
    oversized.write_bytes(b"x" * (limit + 1))
    manifest = _write_manifest(tmp_path, [{"sample_id": f"large-{modality}-01", "modality": modality,
                                           "path": oversized.name, "captured_at_utc": NOW}])
    with pytest.raises(ResearchDataError, match="size limit"):
        import_session(manifest, tmp_path / "must-not-exist.jsonl", allow_unit_test_fixture=True)
    assert not (tmp_path / "must-not-exist.jsonl").exists()


def test_trainer_checks_permissions_on_every_sample(tmp_path):
    from research.training import TrainingConfig, train_target

    Image.new("RGB", (160, 120), (128, 128, 128)).save(tmp_path / "one.png")
    Image.new("RGB", (160, 120), (96, 96, 96)).save(tmp_path / "two.png")
    media = [
        {"sample_id": "image-sample-11", "modality": "image", "path": "one.png", "captured_at_utc": NOW},
        {"sample_id": "image-sample-12", "modality": "image", "path": "two.png", "captured_at_utc": NOW},
    ]
    manifest = _write_manifest(tmp_path, media)
    output = tmp_path / "features.jsonl"
    import_session(manifest, output, allow_unit_test_fixture=True)
    rows = load_jsonl(output, allow_unit_test_fixture=True)
    rows[0]["consent"]["permissions"].update({"model_training": True, "evaluation": True})
    rows[1]["consent"]["permissions"].update({"model_training": False, "evaluation": True})
    rights = {"dataset_id": "pilot-dataset-test01", "dataset_version": "1.0", "license_id": "test-only",
              "approval_reference": "test-only", "protocol_sha256": "a" * 64,
              "model_training_authorized": True, "consent_confirmed": True}
    with pytest.raises(ResearchDataError, match="separate media-analysis, storage, model-training and evaluation permission"):
        train_target(rows, rights, TrainingConfig("eye_state_event", ("image.eye_aspect_ratio",)),
                     allow_unit_test_fixture=True)
