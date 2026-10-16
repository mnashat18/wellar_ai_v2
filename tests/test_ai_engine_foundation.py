import hashlib
import wave

import numpy as np
import pytest

from app.media.extractors import InvalidMediaError, extract_audio, extract_video, temporal_eye_features
from app.ml.artifact_guard import ModelActivationDenied
from app.ml.runtime import (
    MODEL_INTERFACE_VERSION,
    PredictionRequest,
    create_predictor,
)


def _request(*, target="reference_sleepiness", statuses=None, missingness=0.0):
    return PredictionRequest(
        target=target,
        features={"video": {"sampled_closed_fraction": 0.2}},
        modality_status=statuses or {"video": "available"},
        missingness=missingness,
    )


class _Backend:
    interface_version = MODEL_INTERFACE_VERSION
    model_version = "sleepiness-test-model-1"

    def __init__(self, response=None):
        self.response = response or {"status": "available", "value": 6.0, "probability": 0.8, "calibrated": True}
        self.calls = 0

    def predict(self, target, features):
        self.calls += 1
        return self.response


def _manifest(artifact):
    return {
        "artifact_class": "validated_operational",
        "research_only": False,
        "validation_status": "validated",
        "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "target": "reference_sleepiness",
        "protocol_sha256": "a" * 64,
        "held_out_evaluation_id": "approved-evaluation-1",
        "approval_reference": "release-approval-1",
        "model_interface_version": MODEL_INTERFACE_VERSION,
        "model_version": "sleepiness-test-model-1",
        "required_modalities": ["video"],
        "max_missingness": 0.25,
        "calibrated": True,
    }


def _activate(tmp_path, *, response=None, manifest_updates=None, loader=None):
    artifact = tmp_path / "model.bin"
    artifact.write_bytes(b"test-only-not-a-model")
    manifest = _manifest(artifact)
    manifest.update(manifest_updates or {})
    backend = _Backend(response)
    predictor = create_predictor(
        enabled=True,
        manifest=manifest,
        artifact_path=artifact,
        backend_loader=loader or (lambda path, checked_manifest: backend),
    )
    return predictor, backend, artifact, manifest


def test_prediction_activation_is_disabled_by_default_and_output_is_tenant_safe(tmp_path):
    def forbidden_loader(*args):
        raise AssertionError("loader must not run while disabled")

    predictor = create_predictor(enabled=False, backend_loader=forbidden_loader)
    result = predictor.predict(_request()).as_dict()
    assert result["status"] == "unavailable"
    assert result["value"] is None and result["probability"] is None
    assert "tenant_id" not in result and "subject_id" not in result


def test_validated_artifact_passes_guard_and_produces_versioned_result(tmp_path):
    predictor, backend, _, _ = _activate(tmp_path)
    result = predictor.predict(_request()).as_dict()
    assert backend.calls == 1
    assert result == {
        "schema_version": "1.0",
        "target": "reference_sleepiness",
        "status": "available",
        "value": 6.0,
        "probability": 0.8,
        "calibrated": True,
        "model_version": "sleepiness-test-model-1",
        "reason": None,
    }


@pytest.mark.parametrize(
    "updates",
    [
        {"artifact_class": "research_only"},
        {"model_interface_version": "9.0"},
        {"target": "fatigue_guess"},
        {"protocol_sha256": "unverified"},
        {"required_modalities": ["video", "video"]},
        {"max_missingness": float("nan")},
    ],
)
def test_invalid_or_unsupported_manifests_fail_closed(tmp_path, updates):
    artifact = tmp_path / "model.bin"
    artifact.write_bytes(b"test-only-not-a-model")
    manifest = _manifest(artifact)
    manifest.update(updates)
    called = []
    with pytest.raises(ModelActivationDenied):
        create_predictor(enabled=True, manifest=manifest, artifact_path=artifact,
                         backend_loader=lambda *args: called.append(True))
    assert called == []


def test_artifact_digest_mismatch_and_malformed_loader_fail_closed(tmp_path):
    artifact = tmp_path / "model.bin"
    artifact.write_bytes(b"altered")
    manifest = _manifest(artifact)
    manifest["artifact_sha256"] = "0" * 64
    with pytest.raises(ModelActivationDenied, match="digest"):
        create_predictor(enabled=True, manifest=manifest, artifact_path=artifact,
                         backend_loader=lambda *args: pytest.fail("loader must not run"))

    manifest = _manifest(artifact)
    with pytest.raises(ModelActivationDenied, match="rejected"):
        create_predictor(enabled=True, manifest=manifest, artifact_path=artifact,
                         backend_loader=lambda *args: (_ for _ in ()).throw(ValueError("malformed artifact")))


def test_required_modality_and_missingness_gate_before_model_invocation(tmp_path):
    predictor, backend, _, _ = _activate(tmp_path)
    missing = predictor.predict(_request(statuses={"video": "insufficient_quality"}))
    too_missing = predictor.predict(_request(missingness=0.5))
    assert missing.status == too_missing.status == "abstained"
    assert missing.reason == too_missing.reason == "quality_failure"
    assert missing.value is None and too_missing.probability is None
    assert backend.calls == 0


def test_model_can_abstain_on_multimodal_disagreement_without_emitting_value(tmp_path):
    predictor, backend, _, _ = _activate(
        tmp_path,
        response={"status": "abstained", "reason": "multimodal_disagreement", "value": 8.0},
    )
    result = predictor.predict(_request()).as_dict()
    assert result["status"] == "abstained"
    assert result["value"] is None and result["probability"] is None
    assert result["reason"] == "multimodal_disagreement"
    assert backend.calls == 1


def test_malformed_backend_output_and_unsupported_backend_version_abstain_or_deny(tmp_path):
    predictor, _, _, _ = _activate(tmp_path, response={"status": "available", "value": float("nan")})
    assert predictor.predict(_request()).reason == "invalid_model_response"

    artifact = tmp_path / "wrong-version.bin"
    artifact.write_bytes(b"test-only-not-a-model")
    manifest = _manifest(artifact)
    backend = _Backend()
    backend.interface_version = "99.0"
    with pytest.raises(ModelActivationDenied, match="interface version"):
        create_predictor(enabled=True, manifest=manifest, artifact_path=artifact,
                         backend_loader=lambda *args: backend)


def test_short_and_invalid_temporal_inputs_are_rejected():
    with pytest.raises(InvalidMediaError, match="duration"):
        temporal_eye_features([], 0.0, 4.0)
    with pytest.raises(InvalidMediaError, match="sampling"):
        temporal_eye_features([], 1.0, 0.0)


def test_constant_dc_audio_is_not_accepted_as_useful_signal(tmp_path):
    path = tmp_path / "constant-dc.wav"
    samples = np.full(16000, 8192, dtype="<i2")
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(samples.tobytes())

    extracted = extract_audio(path, max_seconds=10)
    assert extracted["status"] == "insufficient_quality"
    assert extracted["quality"]["reason"] == "short_silent_dc_offset_or_clipped_recording"
    assert extracted["features"]["ac_rms_dbfs"] < -200
    assert extracted["features"]["dc_offset_max"] > 0.2
    assert extracted["provenance"]["quality_scope"] == "signal_integrity_only_speech_noise_and_ood_not_assessed"


def test_one_frame_video_is_not_marked_temporally_usable(tmp_path, monkeypatch):
    import cv2

    class _Capture:
        def __init__(self, path):
            self.reads = 0

        def isOpened(self):
            return True

        def get(self, prop):
            return {
                cv2.CAP_PROP_FPS: 30.0,
                cv2.CAP_PROP_FRAME_COUNT: 1.0,
                cv2.CAP_PROP_FRAME_WIDTH: 80.0,
                cv2.CAP_PROP_FRAME_HEIGHT: 80.0,
            }[prop]

        def read(self):
            self.reads += 1
            return (True, np.zeros((80, 80, 3), dtype=np.uint8)) if self.reads == 1 else (False, None)

        def release(self):
            pass

    class _Mesh:
        def process(self, frame):
            points = [type("Point", (), {"x": 0.0, "y": 0.0})() for _ in range(400)]
            for indexes in ((33, 160, 158, 133, 153, 144), (362, 385, 387, 263, 373, 380)):
                for index, xy in zip(indexes, ((0, 0), (0.4, .2), (.6, .2), (1, 0), (.6, -.2), (.4, -.2))):
                    points[index].x, points[index].y = xy
            face = type("Face", (), {"landmark": points})()
            return type("Result", (), {"multi_face_landmarks": [face]})()

    monkeypatch.setattr(cv2, "VideoCapture", _Capture)
    monkeypatch.setattr(cv2, "cvtColor", lambda frame, code: frame)
    result = extract_video(tmp_path / "synthetic.avi", max_seconds=10, max_frames=100,
                           sample_hz=4.0, max_pixels=10000, face_mesh=_Mesh())
    assert result["status"] == "insufficient_quality"
    assert result["quality"]["reason"] == "insufficient_temporal_samples"
