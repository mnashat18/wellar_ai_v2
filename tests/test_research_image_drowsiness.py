import hashlib

import pytest

from app.ml import research_image_drowsiness as model
from app.core.config import Settings


def test_research_model_is_disabled_without_explicit_opt_in():
    assert model.ResearchImageDrowsinessClassifier.load("unused", enabled=False) is None


def test_research_model_cannot_be_loaded_in_production():
    with pytest.raises(ValueError, match="non-production"):
        model.ResearchImageDrowsinessClassifier.load("unused", enabled=True, non_production=False)


def test_artifact_pin_verifies_hash_and_size_and_rejects_tampering(tmp_path, monkeypatch):
    artifact = tmp_path / "best_model.pt"
    artifact.write_bytes(b"safe model bytes")
    monkeypatch.setattr(model, "MODEL_BYTES", artifact.stat().st_size)
    monkeypatch.setattr(model, "MODEL_SHA256", hashlib.sha256(artifact.read_bytes()).hexdigest())
    assert model.verify_artifact(tmp_path) == tmp_path
    artifact.write_bytes(b"tampered model")
    with pytest.raises(ValueError, match="integrity"):
        model.verify_artifact(tmp_path)


def test_enabled_load_rejects_invalid_cpu_thread_limit(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="CPU thread limit"):
        model.ResearchImageDrowsinessClassifier.load(
            tmp_path, enabled=True, non_production=True, cpu_threads=0
        )


def test_settings_reject_research_model_in_production():
    settings = Settings(
        environment="production",
        research_image_drowsiness_enabled=True,
        research_image_drowsiness_artifact_dir="models/pretrained/doze-guard-rldd-29a292f7",
    )
    with pytest.raises(ValueError, match="restricted to development/test"):
        settings.validate()
