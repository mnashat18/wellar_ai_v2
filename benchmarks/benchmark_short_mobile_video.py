"""Measure actual OpenCV+MediaPipe CPU feature extraction on generated no-face media."""
from __future__ import annotations

import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.media.extractors import _face_mesh_factory, extract_video


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="wellar-short-mobile-bench-") as directory:
        video = Path(directory) / "generated-no-face-720p-30fps.avi"
        writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 30, (1280, 720))
        if not writer.isOpened():
            raise RuntimeError("MJPG 720p encoder unavailable")
        blank = np.zeros((720, 1280, 3), dtype=np.uint8)
        for _ in range(150):
            writer.write(blank)
        writer.release()

        init_start = time.perf_counter()
        mesh = _face_mesh_factory(static_image_mode=False)
        init_ms = (time.perf_counter() - init_start) * 1000
        results = []
        try:
            for _ in range(5):
                results.append(extract_video(video, max_seconds=30, max_frames=1800,
                    sample_hz=4, max_pixels=4_000_000, face_mesh=mesh))
        finally:
            mesh.close()

        warm = results[1:]
        total = [row["timing_ms"]["total_ms"] for row in results]
        decode = [row["timing_ms"]["decode_ms"] for row in results]
        feature = [row["timing_ms"]["feature_extraction_ms"] for row in results]
        print(json.dumps({
            "media": "generated all-black 5s 1280x720 30fps MJPG, no participant media",
            "extractor": "actual OpenCV decode and MediaPipe Face Mesh CPU path",
            "face_mesh_initialization_ms": round(init_ms, 3),
            "repetitions": 5,
            "median_total_ms_including_decode": round(statistics.median(total), 3),
            "median_decode_ms": round(statistics.median(decode), 3),
            "median_feature_extraction_ms_inclusive_of_face_detection": round(statistics.median(feature), 3),
            "median_face_detection_ms": round(statistics.median([r["timing_ms"]["face_detection_ms"] for r in results]), 3),
            "warm_median_total_ms": round(statistics.median(r["timing_ms"]["total_ms"] for r in warm), 3),
            "warm_median_decode_ms": round(statistics.median(r["timing_ms"]["decode_ms"] for r in warm), 3),
            "warm_median_feature_extraction_ms": round(statistics.median(r["timing_ms"]["feature_extraction_ms"] for r in warm), 3),
            "frames_decoded": results[-1]["timing_ms"]["frames_decoded"],
            "frames_sampled": results[-1]["timing_ms"]["frames_sampled"],
            "actual_sample_hz": results[-1]["features"]["actual_sample_hz"],
            "face_valid_samples": results[-1]["features"]["short_eye_valid_observation_count"],
            "short_eye_feature_status": results[-1]["features"]["short_eye_feature_status"],
            "short_eye_closure_fraction": results[-1]["features"]["short_eye_closure_fraction"],
            "model_prediction_ms": None,
            "upload_ms": None,
        }, sort_keys=True))


if __name__ == "__main__":
    main()
