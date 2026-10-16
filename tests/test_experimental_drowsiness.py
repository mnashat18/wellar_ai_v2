"""Deterministic adapter tests only; fixtures are synthetic, not model evidence."""
import numpy as np

from app.ml.experimental_drowsiness import (
    EXPERIMENTAL_ADAPTER_VERSION,
    ExperimentalDrowsinessAdapter,
    FrameSample,
)


def _samples(count=3, *, quality="passed", face=True, low_light=False):
    return [
        FrameSample(np.zeros((24, 24, 3), dtype=np.uint8), quality, face, low_light)
        for _ in range(count)
    ]


class _SyntheticBackend:
    model_revision = "synthetic-fixture-only"

    def __init__(self, scores):
        self.scores = scores
        self.calls = 0

    def predict_drowsy_score(self, rgb_frames):
        self.calls += 1
        return self.scores


def test_default_is_disabled_and_does_not_invoke_backend():
    backend = _SyntheticBackend([0.2, 0.3, 0.4])
    result = ExperimentalDrowsinessAdapter(backend=backend).predict(_samples(), input_kind="video")
    assert result.status == "unavailable"
    assert result.raw_drowsy_score is None
    assert result.reason == "experimental_activation_disabled"
    assert backend.calls == 0


def test_production_configuration_cannot_enable_experimental_inference():
    backend = _SyntheticBackend([0.2, 0.3, 0.4])
    result = ExperimentalDrowsinessAdapter(enabled=True, non_production=False, backend=backend).predict(
        _samples(), input_kind="video"
    )
    assert result.status == "unavailable"
    assert backend.calls == 0


def test_nonproduction_video_returns_only_uncalibrated_experimental_aggregate():
    backend = _SyntheticBackend([0.2, 0.3, 0.4])
    result = ExperimentalDrowsinessAdapter(enabled=True, non_production=True, backend=backend).predict(
        _samples(), input_kind="video"
    )
    body = result.as_dict()
    assert result.schema_version == EXPERIMENTAL_ADAPTER_VERSION
    assert result.status == "experimental"
    assert result.raw_drowsy_score == 0.3
    assert result.calibrated is False
    assert body["claim_scope"] == "experimental_driver_drowsiness_only_not_validated_workplace_measurement"
    assert backend.calls == 1


def test_nonproduction_image_uses_single_frame_score_without_temporal_claim():
    backend = _SyntheticBackend([0.7])
    result = ExperimentalDrowsinessAdapter(enabled=True, non_production=True, backend=backend).predict(
        _samples(1), input_kind="image"
    )
    assert result.status == "experimental"
    assert result.raw_drowsy_score == 0.7
    assert result.usable_frame_count == 1


def test_quality_gates_remove_bad_frames_and_abstain_if_temporal_coverage_is_too_small():
    backend = _SyntheticBackend([0.3])
    frames = _samples(3)
    frames[0] = FrameSample(frames[0].rgb, "failed", True, False)
    frames[1] = FrameSample(frames[1].rgb, "passed", False, False)
    result = ExperimentalDrowsinessAdapter(enabled=True, non_production=True, backend=backend).predict(
        frames, input_kind="video"
    )
    assert result.status == "abstained"
    assert result.reason == "insufficient_quality_frames"
    assert backend.calls == 0


def test_low_light_and_missing_face_never_reach_model():
    backend = _SyntheticBackend([0.3])
    frames = _samples(3, low_light=True)
    result = ExperimentalDrowsinessAdapter(enabled=True, non_production=True, backend=backend).predict(
        frames, input_kind="video"
    )
    assert result.status == "abstained" and result.raw_drowsy_score is None
    assert backend.calls == 0


def test_temporal_disagreement_abstains():
    backend = _SyntheticBackend([0.0, 0.5, 1.0])
    result = ExperimentalDrowsinessAdapter(enabled=True, non_production=True, backend=backend).predict(
        _samples(), input_kind="video"
    )
    assert result.status == "abstained"
    assert result.reason == "temporal_disagreement"
    assert result.raw_drowsy_score is None


def test_bad_backend_scores_and_exceptions_fail_closed_without_leaking_details():
    for scores in ([float("nan")] * 3, [0.1]):
        result = ExperimentalDrowsinessAdapter(
            enabled=True, non_production=True, backend=_SyntheticBackend(scores)
        ).predict(_samples(), input_kind="video")
        assert result.status == "abstained"
        assert result.raw_drowsy_score is None
    class FailingBackend:
        model_revision = "not-a-secret"

        def predict_drowsy_score(self, rgb_frames):
            raise RuntimeError("private path and token=must-not-escape")

    result = ExperimentalDrowsinessAdapter(enabled=True, non_production=True, backend=FailingBackend()).predict(
        _samples(), input_kind="video"
    )
    assert result.reason == "inference_failed"
    assert "must-not-escape" not in str(result.as_dict())


def test_backend_timeout_signal_fails_closed():
    class TimedOutBackend:
        model_revision = "synthetic-timeout-fixture"

        def predict_drowsy_score(self, rgb_frames):
            raise TimeoutError("backend timed out")

    result = ExperimentalDrowsinessAdapter(enabled=True, non_production=True, backend=TimedOutBackend()).predict(
        _samples(), input_kind="video"
    )
    assert result.status == "abstained" and result.raw_drowsy_score is None
    assert result.reason == "inference_failed"


def test_frame_and_batch_limits_and_image_shape_are_enforced():
    backend = _SyntheticBackend([0.2] * 31)
    adapter = ExperimentalDrowsinessAdapter(enabled=True, non_production=True, backend=backend)
    assert adapter.predict(_samples(31), input_kind="video").reason == "frame_count_out_of_bounds"
    assert adapter.predict(_samples(3), input_kind="image").reason == "frame_count_out_of_bounds"
    malformed = FrameSample(np.zeros((24, 24), dtype=np.uint8), "passed", True, False)
    assert adapter.predict([malformed], input_kind="image").reason == "invalid_frame"
    too_large = FrameSample(np.zeros((1415, 1415, 3), dtype=np.uint8), "passed", True, False)
    assert adapter.predict([too_large], input_kind="image").reason == "frame_resource_limit"
    large_samples = [FrameSample(np.zeros((1400, 1400, 3), dtype=np.uint8), "passed", True, False) for _ in range(5)]
    assert adapter.predict(large_samples, input_kind="video").reason == "batch_resource_limit"
