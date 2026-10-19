from pathlib import Path

from app.media.analysis import MultimodalFeatureProcessor


class _Store:
    def resolve(self, ref, tenant_id, subject_id, verify_integrity):
        return {"modality": "image", "path": Path("research-only.jpg"), "integrity_ok": True, "available": True}

    def release(self, asset):
        pass


class _ResearchClassifier:
    def __init__(self):
        self.calls = 0

    def predict(self, path):
        self.calls += 1
        return {
            "schema_version": "wellar-research-image-drowsiness/1.0",
            "status": "research_only",
            "raw_argmax_label": "drowsy",
            "raw_softmax_scores_uncalibrated": {"drowsy": 0.7, "non_drowsy": 0.3},
            "calibrated": False,
            "production_prediction": False,
            "latency_ms": 2.0,
        }

    def provenance(self):
        return {"name": "test-fixture-only", "deployment_scope": "research-only non-production"}


def test_worker_research_result_is_separate_from_operational_targets(monkeypatch):
    import app.media.analysis as analysis

    # The processor delegates decoding to a separate killable process. Stub
    # that boundary here so this test covers result orchestration without
    # bypassing or changing the production decoder implementation.
    monkeypatch.setattr(analysis, "run_killable", lambda *args, **kwargs: {
        "status": "available",
        "quality": {"overall_coverage": 1.0, "missingness": 0.0},
        "features": {"face_visible": True, "eye_aspect_ratio": 0.28},
        "timing_ms": {"total": 1.0},
        "provenance": {"extractor": "unit-test-fixture", "version": "test-only"},
    })
    classifier = _ResearchClassifier()
    processor = MultimodalFeatureProcessor(
        _Store(), max_video_seconds=5, max_audio_seconds=5,
        max_image_pixels=1_000_000, max_video_frames=100,
        video_sample_hz=4, research_image_classifier=classifier,
    )
    result = processor({
        "media_refs": ["media://test/image"],
        "_worker_context": {"scan_id": "test", "tenant_id": "tenant", "subject_id": "subject"},
    })
    try:
        assert classifier.calls == 1
        research = result["evidence"]["research_model_inference"]
        assert research["scope"] == "non_production_research_only"
        assert research["results"][0]["status"] == "research_only"
        assert research["results"][0]["calibrated"] is False
        assert result["prediction_extension"]["targets"]["reference_sleepiness"]["status"] == "unavailable"
        assert result["prediction_extension"]["targets"]["subjective_fatigue"]["status"] == "unavailable"
        assert result["prediction_extension"]["targets"]["vigilance_impairment"]["status"] == "unavailable"
        assert result["targets"]["reference_sleepiness"]["value"] is None
    finally:
        processor.close()
