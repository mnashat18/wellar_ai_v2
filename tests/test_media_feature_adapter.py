"""Synthetic software coverage for the research-only feature adapter."""
import math
import time
from types import SimpleNamespace

import numpy as np
import pytest

from app.media.extractors import InvalidMediaError, extract_audio, extract_image, extract_video
from app.workers.worker import Worker
from research.evaluation_v1 import ResearchDataError
from research.media_feature_adapter import (
    FEATURES, MODALITY_ORDER, build_feature_record, validate_dataset_readiness,
    build_feature_record_from_worker_result, validate_feature_record,
)
from tests.media_fixtures import image_bytes, mp4_video_file, synthetic_face_image_bytes, video_file, wav_bytes


def _audio_output(status="available"):
    features = {name: None for name in FEATURES["audio"]}
    features.update(duration_seconds=2.0, sample_rate_hz=8000.0, channels=1.0,
                    rms_dbfs=-20.0, peak_dbfs=-10.0, dc_offset_max=0.0,
                    ac_rms_dbfs=-20.0, silence_window_fraction=0.0,
                    clipped_sample_fraction=0.0)
    return {"status": status, "quality": {"reason": None if status == "available" else "short_silent_dc_offset_or_clipped_recording",
            "duration_seconds": 2.0}, "features": features,
            "provenance": {"extractor": "python_wave_pcm_acoustic_summary", "version": "wellar-audio-contract-1.1",
                           "quality_scope": "signal_integrity_only_speech_noise_and_ood_not_assessed"},
            "timing_ms": {"decode_and_features": 1.0}}


def _record(i, output=None):
    p = f"p_{i:016x}"
    s = f"s_{i:016x}"
    return build_feature_record(participant_id=p, session_id=s, sample_id=f"sample_{i:016x}",
                                extractor_outputs={"audio": output or _audio_output()})


def test_adapter_has_fixed_typed_schema_missingness_and_provenance():
    record = _record(1)
    validate_feature_record(record)
    assert record["modality_order"] == list(MODALITY_ORDER)
    assert record["feature_order"] == [f"{m}.{f}" for m in MODALITY_ORDER for f in FEATURES[m]]
    assert record["modalities"]["audio"]["status"] == "available"
    assert record["modalities"]["image"]["status"] == "not_provided"
    assert record["modalities"]["image"]["missingness"] == 1.0
    assert record["provenance"]["audio"]["version"] == "wellar-audio-contract-1.1"
    assert "labels" not in record and "fatigue" not in record


def test_rejected_quality_keeps_reason_but_never_exposes_measurements():
    record = _record(2, _audio_output("insufficient_quality"))
    audio = record["modalities"]["audio"]
    assert audio["status"] == "insufficient_quality"
    assert audio["reason"] == "short_silent_dc_offset_or_clipped_recording"
    assert audio["source_coverage"] == 1.0
    assert audio["coverage"] == 0.0 and audio["missingness"] == 1.0
    assert audio["features"]["rms_dbfs"] is None
    record["feature_values"]["audio.rms_dbfs"] = -12.0
    with pytest.raises(ResearchDataError, match="flat feature values differ"):
        validate_feature_record(record)


@pytest.mark.parametrize("outputs", [
    {"name": "person"},
    {"audio": {"status": "available", "quality": {}, "features": {"media_bytes": "x"}, "provenance": {}}},
    {"audio": {"status": "available", "quality": {}, "features": {"rms_dbfs": math.nan}, "provenance": {}}},
])
def test_rejects_identifiers_raw_media_and_nonfinite_values(outputs):
    with pytest.raises(ResearchDataError):
        build_feature_record(participant_id="p_0000000000000001", session_id="s_0000000000000001",
                             sample_id="sample_0000000000000001", extractor_outputs=outputs)


def test_rejects_direct_identifier_pseudonym_and_unknown_status():
    with pytest.raises(ResearchDataError, match="opaque p_ pseudonym"):
        build_feature_record(participant_id="Jane Doe", session_id="s_0000000000000001",
                             sample_id="sample_0000000000000001", extractor_outputs={})
    bad = _audio_output()
    bad["status"] = "unknown"
    with pytest.raises(ResearchDataError, match="unsupported extractor status"):
        _record(3, bad)


def test_deterministic_order_and_cross_field_consistency_fail_closed():
    first = _record(8)
    second = _record(8)
    assert first == second
    assert list(first["feature_values"]) == first["feature_order"]
    bad = _audio_output()
    bad["quality"]["reason"] = "rejected_but_marked_available"
    with pytest.raises(ResearchDataError, match="cannot carry a rejection reason"):
        _record(9, bad)
    malformed_worker_envelope = {
        "schema_version": "2.0", "modality": "audio", "presence": "present",
        "extraction_status": "available",
        "quality": {"schema_version": "1.0", "modality": "audio", "status": "failed"},
        "features": {}, "provenance": {"extractor": "test", "version": "1"},
    }
    with pytest.raises(ResearchDataError, match="status and quality status disagree"):
        build_feature_record(participant_id="p_0000000000000009", session_id="s_0000000000000009",
                             sample_id="sample_0000000000000009", extractor_outputs={"audio": malformed_worker_envelope})


def test_provenance_rejects_path_or_url_values():
    output = _audio_output()
    output["provenance"]["version"] = "C:/private/build"
    with pytest.raises(ResearchDataError, match="safe version/rule tokens"):
        _record(10, output)


@pytest.mark.parametrize("status,presence,reason", [
    ("insufficient_quality", "present", "low_quality"),
    ("failed", "present", "processing_failed"),
    ("invalid_media", "present", "decode_failed"),
    ("not_provided", "missing", None),
    ("not_processed", "present", "processor_not_run"),
])
def test_all_nonavailable_worker_states_remain_missing(status, presence, reason):
    envelope = {"schema_version": "2.0", "modality": "audio", "presence": presence,
                "extraction_status": status,
                "quality": {"schema_version": "1.0", "modality": "audio", "status": status,
                            "coverage": None, "missingness": None, "provenance": None, "reason": reason},
                "features": {"error_code": reason} if reason else {},
                "provenance": ({"extractor": "wellar-v2-media-test", "version": "1.0"} if reason else {})}
    record = build_feature_record(participant_id="p_0000000000000011", session_id="s_0000000000000011",
                                  sample_id="sample_0000000000000011", extractor_outputs={"audio": envelope})
    audio = record["modalities"]["audio"]
    assert audio["status"] == status and audio["reason"] == (reason or "modality_not_provided")
    assert audio["coverage"] == 0.0 and audio["missingness"] == 1.0
    assert all(value is None for value in audio["features"].values())


def test_readiness_uses_existing_participant_disjoint_leakage_check():
    records = [_record(i) for i in range(1, 7)]
    for i, record in enumerate(records):
        participant = i // 2 + 1
        record["participant_id"] = f"p_{participant:016x}"
        record["session_id"] = f"s_{participant:016x}"
    split = {r["sample_id"]: ("train" if i < 2 else "validation" if i < 4 else "test")
             for i, r in enumerate(records)}
    validate_dataset_readiness(records, split)
    split[records[1]["sample_id"]] = "validation"
    with pytest.raises(ResearchDataError, match="participant_id leakage"):
        validate_dataset_readiness(records, split)


def test_synthetic_wav_extractor_to_ready_record_dry_run(tmp_path):
    path = tmp_path / "synthetic.wav"
    rate = 8000
    path.write_bytes(wav_bytes(seconds=2,rate=rate))
    extracted = extract_audio(path, max_seconds=5)
    assert extracted["status"] == "available"
    record = build_feature_record(participant_id="p_0000000000000001", session_id="s_0000000000000001",
                                  sample_id="sample_0000000000000001", extractor_outputs={"audio": extracted})
    validate_feature_record(record)
    validate_dataset_readiness([record], {record["sample_id"]: "train"})
    assert record["feature_values"]["audio.duration_seconds"] == 2.0
    assert record["modalities"]["audio"]["coverage"] == 1.0


def test_actual_synthetic_image_and_video_extractor_envelopes_are_adapted(tmp_path):
    class NoFaceMesh:
        def process(self, _rgb):
            return SimpleNamespace(multi_face_landmarks=[])

        def close(self):
            pass

    image_path = tmp_path / "synthetic.png"
    image_path.write_bytes(image_bytes())
    image_output = extract_image(image_path, max_pixels=1_000_000, face_mesh=NoFaceMesh())
    video_path = tmp_path / "synthetic.avi"
    video_file(video_path, seconds=2, fps=4)
    video_output = extract_video(video_path, max_seconds=5, max_frames=40, sample_hz=2,
                                 max_pixels=1_000_000, face_mesh=NoFaceMesh())
    record = build_feature_record(participant_id="p_0000000000000002", session_id="s_0000000000000002",
                                  sample_id="sample_0000000000000002",
                                  extractor_outputs={"image": image_output, "video": video_output})
    assert record["modalities"]["image"]["status"] == "insufficient_quality"
    assert record["modalities"]["video"]["status"] == "insufficient_quality"
    assert all(value is None for modality in ("image", "video")
               for value in record["modalities"][modality]["features"].values())
    assert record["provenance"]["video"]["extractor"] == video_output["provenance"]["extractor"]


def test_detectable_synthetic_face_normal_and_low_light_quality_baseline(tmp_path):
    outputs = {}
    durations = {}
    for label, brightness in (("normal", 1.0), ("low", 0.1)):
        path = tmp_path / f"face-{label}.png"
        path.write_bytes(synthetic_face_image_bytes(brightness=brightness))
        started = time.perf_counter()
        outputs[label] = extract_image(path, max_pixels=1_000_000)
        durations[label] = (time.perf_counter() - started) * 1000
    normal, low = outputs["normal"], outputs["low"]
    assert normal["status"] == "available" and normal["quality"]["face_visible"] is True
    assert low["status"] == "insufficient_quality" and low["quality"]["face_visible"] is True
    assert low["quality"]["reason"] == "low_light"
    normal_record = build_feature_record(participant_id="p_0000000000000003", session_id="s_0000000000000003",
                                         sample_id="sample_0000000000000003", extractor_outputs={"image": normal})
    low_record = build_feature_record(participant_id="p_0000000000000004", session_id="s_0000000000000004",
                                      sample_id="sample_0000000000000004", extractor_outputs={"image": low})
    assert normal_record["feature_values"]["image.mean_luminance"] == pytest.approx(normal["features"]["mean_luminance"])
    assert normal_record["feature_values"]["image.eye_aspect_ratio"] == pytest.approx(normal["features"]["eye_aspect_ratio"])
    assert normal_record["provenance"]["image"]["version"] == normal["provenance"]["version"]
    assert normal_record["modalities"]["image"]["coverage"] == 1.0
    assert low_record["modalities"]["image"]["reason"] == "low_light"
    assert low_record["modalities"]["image"]["source_coverage"] == 1.0
    assert low_record["modalities"]["image"]["coverage"] == 0.0
    assert all(value is None for value in low_record["modalities"]["image"]["features"].values())
    print(f"PHASE3E_IMAGE_BASELINE normal_luminance={normal['features']['mean_luminance']:.3f} "
          f"low_luminance={low['features']['mean_luminance']:.3f} "
          f"normal_extractor_ms={durations['normal']:.3f} low_extractor_ms={durations['low']:.3f}")


def test_synthetic_mp4_no_face_and_invalid_wav_fail_closed(tmp_path):
    mp4_path = tmp_path / "no-face.mp4"
    mp4_video_file(mp4_path, seconds=2, fps=4)
    class NoFaceMesh:
        def process(self, _rgb):
            return SimpleNamespace(multi_face_landmarks=[])

        def close(self):
            pass
    started = time.perf_counter()
    video = extract_video(mp4_path, max_seconds=5, max_frames=40, sample_hz=2,
                          max_pixels=1_000_000, face_mesh=NoFaceMesh())
    video_ms = (time.perf_counter() - started) * 1000
    assert video["status"] == "insufficient_quality"
    assert video["quality"]["reason"] == "face_not_detected"
    video_record = build_feature_record(participant_id="p_0000000000000005", session_id="s_0000000000000005",
                                        sample_id="sample_0000000000000005", extractor_outputs={"video": video})
    assert video_record["modalities"]["video"]["source_coverage"] == 0.0
    assert all(value is None for value in video_record["modalities"]["video"]["features"].values())

    invalid_path = tmp_path / "invalid.wav"
    invalid_path.write_bytes(b"RIFF-not-a-valid-wave-file")
    with pytest.raises(InvalidMediaError, match="audio_decode_failed"):
        extract_audio(invalid_path, max_seconds=5)
    invalid_envelope = {"schema_version": "2.0", "modality": "audio", "presence": "present",
                        "extraction_status": "invalid_media",
                        "quality": {"schema_version": "1.0", "modality": "audio", "status": "invalid_media",
                                    "coverage": None, "missingness": None, "provenance": "wellar-v2-media-validator-1.0",
                                    "reason": "audio_decode_failed"},
                        "features": {"error_code": "audio_decode_failed"},
                        "provenance": {"extractor": "wellar-v2-media-validator", "version": "1.0"}}
    invalid_record = build_feature_record(participant_id="p_0000000000000006", session_id="s_0000000000000006",
                                          sample_id="sample_0000000000000006",
                                          extractor_outputs={"audio": invalid_envelope})
    assert invalid_record["modalities"]["audio"]["status"] == "invalid_media"
    assert invalid_record["modalities"]["audio"]["reason"] == "audio_decode_failed"
    assert all(value is None for value in invalid_record["modalities"]["audio"]["features"].values())
    print(f"PHASE3E_MP4_VIDEO extractor_ms={video_ms:.3f} source_coverage={video_record['modalities']['video']['source_coverage']:.3f}")


def test_actual_worker_result_to_adapter_path(client, auth, tmp_path):
    wav_path = tmp_path / "worker-input.wav"
    wav_path.write_bytes(wav_bytes(seconds=2))
    extractor_started = time.perf_counter()
    extracted = extract_audio(wav_path, max_seconds=5)
    extractor_ms = (time.perf_counter() - extractor_started) * 1000
    upload = client.post("/api/v1/media/audio", headers={**auth, "Content-Type": "audio/wav"},
                         content=wav_path.read_bytes())
    assert upload.status_code == 201
    accepted = client.post("/api/v1/scans", headers={**auth, "Idempotency-Key": "phase-3e-adapter"},
                           json={"schema_version": "1.0", "media_refs": [upload.json()["media_ref"]],
                                 "reaction_task": None, "consent": {"media_processing": True, "consent_version": "operational-media-v1", "research_use": False}})
    assert accepted.status_code == 202
    worker = Worker(client.app.state.store, "phase-3e-adapter-worker", client.app.state.worker_processor)
    worker_started = time.perf_counter()
    assert worker.run_once()
    worker_ms = (time.perf_counter() - worker_started) * 1000
    result = client.get(f"/api/v1/scans/{accepted.json()['scan_id']}/result", headers=auth).json()
    adapter_started = time.perf_counter()
    record = build_feature_record_from_worker_result(
        participant_id="p_0000000000000007", session_id="s_0000000000000007",
        sample_id="sample_0000000000000007", worker_result=result)
    adapter_ms = (time.perf_counter() - adapter_started) * 1000
    assert result["features"]["audio"]["schema_version"] == "2.0"
    assert record["modalities"]["audio"]["status"] == extracted["status"] == "available"
    assert record["feature_values"]["audio.duration_seconds"] == pytest.approx(2.0)
    assert record["provenance"]["audio"]["extractor"] == extracted["provenance"]["extractor"]
    print(f"PHASE3E_AUDIO extractor_ms={extractor_ms:.3f} adapter_ms={adapter_ms:.3f} "
          f"worker_end_to_end_ms={worker_ms:.3f} valid_coverage={record['modalities']['audio']['coverage']:.3f}")
