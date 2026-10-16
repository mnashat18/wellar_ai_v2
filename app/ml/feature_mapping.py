"""Explicit bridge from extractor summaries to release-model feature ids.

This mapping defines software compatibility only. It does not establish that a
feature predicts any clinical or behavioral target.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

FEATURE_MAPPING_VERSION = "1.1"

# `reaction_task` is intentionally unsupported: current reaction timestamps are
# client-reported and are not trusted measurements of objective vigilance.
SUPPORTED_FEATURES = frozenset({
    "video.observation_count",
    "video.valid_face_count",
    "video.expected_sample_count",
    "video.face_coverage",
    "video.sampling_coverage",
    "video.overall_coverage",
    "video.missingness",
    "video.closure_event_count",
    "video.blink_event_count",
    "video.closure_duration_mean_seconds",
    "video.sampled_closed_fraction",
    "video.mouth_open_event_count",
    "video.mouth_open_event_duration_mean_seconds",
    "video.sampled_mouth_open_fraction",
    "video.mean_sampled_luminance",
    "image.width_px",
    "image.height_px",
    "image.gray_mean",
    "image.gray_std",
    "image.laplacian_variance",
    "image.clipped_pixel_fraction",
    "audio.duration_seconds",
    "audio.sample_rate_hz",
    "audio.channels",
    "audio.rms_dbfs",
    "audio.peak_dbfs",
    "audio.dc_offset_max",
    "audio.ac_rms_dbfs",
    "audio.silence_window_fraction",
    "audio.clipped_sample_fraction",
})


def map_features(
    envelopes: Mapping[str, Mapping[str, Any]],
    feature_names: Sequence[str],
) -> tuple[dict[str, dict[str, float | int]], dict[str, str], float]:
    """Select only declared, finite extractor scalars and compute bounded missingness."""
    names = tuple(feature_names)
    if not names or len(names) > 128 or len(set(names)) != len(names):
        raise ValueError("model feature schema is invalid")
    if any(not isinstance(name, str) or name not in SUPPORTED_FEATURES for name in names):
        raise ValueError("model feature schema contains unsupported features")

    mapped: dict[str, dict[str, float | int]] = {}
    statuses: dict[str, str] = {}
    missing = 0
    for identifier in names:
        modality, feature_name = identifier.split(".", 1)
        envelope = envelopes.get(modality, {})
        extraction_status = envelope.get("extraction_status", "not_provided")
        statuses[modality] = extraction_status
        values = envelope.get("features", {})
        value = values.get(feature_name) if isinstance(values, Mapping) else None
        if extraction_status != "available" or value is None or isinstance(value, bool):
            missing += 1
            continue
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            missing += 1
            continue
        mapped.setdefault(modality, {})[feature_name] = value
    return mapped, statuses, missing / len(names)
