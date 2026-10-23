"""Safely benchmark the pinned MobileViT drowsiness checkpoint locally only."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.media.extractors import extract_image


MODEL_ID = "mosesb/drowsiness-detection-mobileViT-v2"
MODEL_REVISION = "1aa87742178ae3a57b259d797b318bec696b02e1"
MODEL_SHA256 = "fcbe35c8e0c8149bed84189ab3cf0a06429107a968667a9f681ff113bed35867"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--consent-file", required=True, type=Path)
    parser.add_argument("--session-file", required=True, type=Path)
    parser.add_argument("--media-dir", required=True, type=Path)
    parser.add_argument("--artifact-dir", required=True, type=Path)
    args = parser.parse_args()
    consent = json.loads(args.consent_file.read_text(encoding="utf-8"))
    if consent.get("scope_confirmed_before_local_acknowledgement") is not True:
        raise SystemExit("consent scope is not confirmed")
    permissions = consent.get("permissions", {})
    if permissions.get("media_analysis") is not True or permissions.get("model_training") is not False or permissions.get("evaluation") is not False:
        raise SystemExit("consent does not allow this local diagnostic")
    session = json.loads(args.session_file.read_text(encoding="utf-8"))
    root = args.media_dir.resolve()
    record = next((x for x in session.get("media", []) if isinstance(x, dict) and x.get("modality") == "image"), None)
    if record is None:
        raise SystemExit("no manifested image exists")
    path = (args.session_file.parent / record.get("path", "")).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise SystemExit("manifested image escapes the authorized media directory") from None
    if not path.is_file() or path.is_symlink():
        raise SystemExit("manifested image is unavailable")

    artifact = args.artifact_dir / "best_model.pt"
    if args.artifact_dir.is_symlink() or artifact.is_symlink() or not artifact.is_file():
        raise SystemExit("pinned MobileViT weights are unavailable")
    if hashlib.sha256(artifact.read_bytes()).hexdigest() != MODEL_SHA256:
        raise SystemExit("pinned MobileViT weights failed SHA-256 verification")

    features = extract_image(path, max_pixels=4_000_000)
    if features["status"] != "available" or features["features"].get("face_visible") is not True:
        raise SystemExit("image failed the existing V2 face quality gate")

    import numpy as np
    import timm
    import torch
    from PIL import Image
    from torchvision import transforms

    torch.set_num_threads(1)
    started = time.perf_counter()
    state = torch.load(artifact, map_location="cpu", weights_only=True)
    if not isinstance(state, dict) or not state or any(not isinstance(k, str) or not torch.is_tensor(v) for k, v in state.items()):
        raise SystemExit("safe checkpoint did not contain a plain tensor state dict")
    model = timm.create_model("mobilevitv2_200", pretrained=False, num_classes=2).to("cpu").eval()
    model.load_state_dict(state, strict=True)
    cold_load_ms = round((time.perf_counter() - started) * 1000, 3)
    preprocessing = transforms.Compose([
        transforms.Resize((224, 224)),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])
    with Image.open(path) as decoded:
        image = decoded.convert("RGB")
    input_tensor = preprocessing(image).unsqueeze(0)
    timings = []
    for _ in range(6):
        tick = time.perf_counter()
        with torch.inference_mode():
            output = model(input_tensor)
        timings.append((time.perf_counter() - tick) * 1000)
        if tuple(output.shape) != (1, 2) or not bool(torch.isfinite(output).all()):
            raise SystemExit("MobileViT output schema validation failed")
    warm = timings[1:]
    print(json.dumps({
        "model_id": MODEL_ID,
        "revision": MODEL_REVISION,
        "sha256_verified": True,
        "weights_only_safe_load": True,
        "strict_state_dict_load": True,
        "face_quality_gate": "passed",
        "real_inference_count": len(timings),
        "cold_load_ms": cold_load_ms,
        "warm_inference_ms_n5": [round(x, 3) for x in warm],
        "warm_median_ms": round(statistics.median(warm), 3),
        "rss_bytes": __import__("psutil").Process().memory_info().rss,
        "cpu_threads": torch.get_num_threads(),
        "raw_private_prediction_suppressed": True,
        "training_or_evaluation_performed": False,
    }, sort_keys=True))
    del output, input_tensor, image, model


if __name__ == "__main__":
    main()
