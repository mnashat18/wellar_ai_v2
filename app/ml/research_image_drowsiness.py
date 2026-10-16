"""Explicitly non-production adapter for a pinned still-image drowsiness model.

The released model is not independently validated for Wellar users. Its raw
binary image classification is research evidence only and never satisfies the
operational sleepiness/fatigue/vigilance predictor interfaces.
"""
from __future__ import annotations

import hashlib
import math
import threading
import time
from pathlib import Path
from typing import Any


MODEL_ID = "mosesb/drowsiness-detection-mobileViT-v2"
MODEL_REVISION = "1aa87742178ae3a57b259d797b318bec696b02e1"
MODEL_SHA256 = "fcbe35c8e0c8149bed84189ab3cf0a06429107a968667a9f681ff113bed35867"
MODEL_BYTES = 69_935_051
MODEL_SCHEMA_VERSION = "wellar-research-image-drowsiness/1.0"
MAX_IMAGE_PIXELS = 4_000_000
_LOAD_LOCK = threading.Lock()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_artifact(artifact_dir: str | Path) -> Path:
    root = Path(artifact_dir)
    artifact = root / "best_model.pt"
    if root.is_symlink() or artifact.is_symlink() or not root.is_dir() or not artifact.is_file():
        raise ValueError("pinned research model artifact is unavailable")
    if artifact.stat().st_size != MODEL_BYTES or _sha256(artifact) != MODEL_SHA256:
        raise ValueError("pinned research model artifact integrity check failed")
    return root


class ResearchImageDrowsinessClassifier:
    """Cached CPU inference session; outputs are explicitly uncalibrated."""

    def __init__(self, root: Path, *, model: Any, processor: Any, torch_module: Any, cpu_threads: int):
        self.root = root
        self.model = model
        self.processor = processor
        self.torch = torch_module
        self.cpu_threads = cpu_threads
        self._inference_lock = threading.Lock()

    @classmethod
    def load(
        cls,
        artifact_dir: str | Path,
        *,
        enabled: bool = False,
        non_production: bool = False,
        cpu_threads: int = 1,
    ) -> "ResearchImageDrowsinessClassifier | None":
        if not enabled:
            return None
        if non_production is not True:
            raise ValueError("research image model is restricted to non-production")
        if isinstance(cpu_threads, bool) or not isinstance(cpu_threads, int) or not 1 <= cpu_threads <= 4:
            raise ValueError("research model CPU thread limit must be between one and four")
        root = verify_artifact(artifact_dir)
        with _LOAD_LOCK:
            try:
                import timm
                import torch
                from torchvision import transforms
            except ImportError:
                raise RuntimeError("optional research model runtime dependencies are unavailable") from None
            if timm.__version__ != "1.0.15" or not torch.__version__.startswith("2.6.0"):
                raise RuntimeError("research model runtime requires timm 1.0.15 and PyTorch 2.6.0")
            torch.set_num_threads(cpu_threads)
            try:
                torch.set_num_interop_threads(1)
            except RuntimeError:
                # PyTorch permits setting this only before parallel work starts.
                pass
            try:
                state = torch.load(root / "best_model.pt", map_location="cpu", weights_only=True)
                if not isinstance(state, dict) or not state or any(not isinstance(k, str) or not torch.is_tensor(v) for k, v in state.items()):
                    raise ValueError("checkpoint is not a plain tensor state dict")
                model = timm.create_model("mobilevitv2_200", pretrained=False, num_classes=2).to("cpu").eval()
                model.load_state_dict(state, strict=True)
                processor = transforms.Compose([
                    transforms.Resize((224, 224)),
                    transforms.ToTensor(),
                    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
                ])
            except Exception:
                raise RuntimeError("pinned research model failed safe local loading") from None
        return cls(root, model=model, processor=processor, torch_module=torch, cpu_threads=cpu_threads)

    def predict(self, image_path: str | Path) -> dict[str, Any]:
        from PIL import Image, UnidentifiedImageError

        started = time.perf_counter()
        path = Path(image_path)
        try:
            with Image.open(path) as decoded:
                width, height = decoded.size
                if width < 80 or height < 80 or width * height > MAX_IMAGE_PIXELS:
                    return self._abstain("image_resource_or_dimensions")
                decoded.verify()
            with Image.open(path) as decoded:
                image = decoded.convert("RGB")
            inputs = self.processor(image).unsqueeze(0)
        except (OSError, UnidentifiedImageError, ValueError):
            return self._abstain("image_decode_failed")
        try:
            with self._inference_lock, self.torch.inference_mode():
                output = self.model(inputs)
                logits = output.detach().to("cpu", dtype=self.torch.float32)
                if tuple(logits.shape) != (1, 2) or not bool(self.torch.isfinite(logits).all()):
                    return self._abstain("invalid_model_output")
                scores = self.torch.softmax(logits, dim=1)[0].tolist()
        except Exception:
            return self._abstain("inference_failed")
        if len(scores) != 2 or any(not math.isfinite(float(score)) or not 0 <= float(score) <= 1 for score in scores):
            return self._abstain("invalid_model_output")
        index = int(self.torch.argmax(logits, dim=1).item())
        return {
            "schema_version": MODEL_SCHEMA_VERSION,
            "status": "research_only",
            "task": "still_image_driver_drowsiness_binary_classification",
            "raw_argmax_label": "drowsy" if index == 0 else "non_drowsy",
            "raw_softmax_scores_uncalibrated": {"drowsy": float(scores[0]), "non_drowsy": float(scores[1])},
            "calibrated": False,
            "production_prediction": False,
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "model_id": MODEL_ID,
            "model_revision": MODEL_REVISION,
            "artifact_sha256": MODEL_SHA256,
            "validation_status": "not_independently_validated_for_wellar_or_subject_disjoint_generalization",
        }

    @staticmethod
    def _abstain(reason: str) -> dict[str, Any]:
        return {
            "schema_version": MODEL_SCHEMA_VERSION,
            "status": "abstained",
            "task": "still_image_driver_drowsiness_binary_classification",
            "reason": reason,
            "raw_argmax_label": None,
            "raw_softmax_scores_uncalibrated": None,
            "calibrated": False,
            "production_prediction": False,
        }

    def provenance(self) -> dict[str, Any]:
        return {
            "name": MODEL_ID,
            "revision": MODEL_REVISION,
            "artifact_sha256": MODEL_SHA256,
            "license_id": "MIT as declared by model publisher",
            "task": "still-image driver drowsiness binary classification",
            "deployment_scope": "research-only non-production",
            "validation_status": "not_independently_validated_for_wellar_or_subject_disjoint_generalization",
        }

    def close(self) -> None:
        with self._inference_lock:
            self.model = None
            self.processor = None
