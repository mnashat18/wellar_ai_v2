"""Metadata-only compatibility checks for external drowsiness datasets.

This module never downloads, opens, transforms, or inspects media or observation
rows. Provider profiles summarize public metadata; they do not establish a
license or authorize training. The result is a preflight only, not a substitute
for the trainer's row-level schema and rights validation.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from research.evaluation_v1 import ResearchDataError

MAX_METADATA_BYTES = 64 * 1024
TARGETS = {"reference_sleepiness", "subjective_fatigue", "vigilance_impairment"}
MODALITIES = {"video", "audio", "image", "reaction_task"}


@dataclass(frozen=True)
class DatasetProfile:
    dataset_id: str
    participant_count: int
    source_modalities: frozenset[str]
    observed_label_fields: frozenset[str]
    label_sources: Mapping[str, str]
    license_status: str
    official_url: str
    known_limits: tuple[str, ...]


PROFILES = {
    "DROZY": DatasetProfile(
        dataset_id="DROZY",
        participant_count=14,
        source_modalities=frozenset({"video_nir_mp4", "image_nir16_png", "depth16_png", "psg_edf_512hz", "pvt_reaction_times"}),
        observed_label_fields=frozenset({"KSS.txt.score_1_9", "pvt-rt.reaction_times"}),
        label_sources={
            "reference_sleepiness": "KSS.txt.score_1_9",
            "vigilance_impairment": "pvt-rt.reaction_times",
        },
        license_status="published_license_research_and_evaluation_only_no_commercial_development",
        official_url="https://www.drozy.ulg.ac.be/",
        known_limits=(
            "KSS row for subject 7 test 1 is a missing score represented as 0; exclude as missing, never treat as KSS.",
            "Some tests are absent or lost; usable target-specific participant count must be measured from authorized metadata.",
            "Video is near-infrared intensity, not ordinary RGB; Wellar extraction/domain compatibility is unverified.",
            "Depth maps and PSG/EEG are not supported by the current Wellar modality feature schema.",
            "PVT reaction times require a preregistered target metric/class rule and must not leak into a vigilance prediction input.",
        ),
    ),
    "UTA-RLDD": DatasetProfile(
        dataset_id="UTA-RLDD",
        participant_count=60,
        source_modalities=frozenset({"video_rgb_mp4"}),
        observed_label_fields=frozenset({"self_reported_drowsiness_category_0_5_10"}),
        label_sources={},
        license_status="no_explicit_license_or_commercial_training_permission_identified_on_provider_page",
        official_url="https://sites.google.com/view/utarldd/home",
        known_limits=(
            "Three participant-reported predominant-state categories are encoded 0, 5, and 10; these are not raw KSS measurements.",
            "The categories are based on KSS descriptions, but no individual raw KSS values or independent PVT outcomes are specified.",
            "Five provider-defined folds each contain 12 participants; subject-disjoint evaluation is possible if identities/folds are preserved.",
            "Video is RGB, but the target-label contract is incompatible with current sleepiness and vigilance targets without a new separately validated target protocol.",
            "No separate qualified subjective-fatigue instrument is identified.",
        ),
    ),
}


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_subject_splits(participant_by_sample: Mapping[str, str], split_by_sample: Mapping[str, str]) -> None:
    """Check only pseudonymous IDs/split metadata; never return an ID in errors."""
    if not isinstance(participant_by_sample, Mapping) or not isinstance(split_by_sample, Mapping):
        raise ResearchDataError("participant and split metadata must be objects")
    if not participant_by_sample or set(participant_by_sample) != set(split_by_sample):
        raise ResearchDataError("every sample requires exactly one participant and split assignment")
    participant_splits: dict[str, set[str]] = {}
    for sample, participant in participant_by_sample.items():
        split = split_by_sample.get(sample)
        if not _nonempty(sample) or not _nonempty(participant):
            raise ResearchDataError("sample and pseudonymous participant identifiers are required")
        if not isinstance(split, str) or split not in {"train", "validation", "test"}:
            raise ResearchDataError("split values must be train, validation, or test")
        participant_splits.setdefault(participant, set()).add(split)
    if any(len(splits) != 1 for splits in participant_splits.values()):
        raise ResearchDataError("participant leakage across splits is not permitted")


def _validate_feature_mapping(profile: DatasetProfile, mapping: Any, target: str, blockers: list[str]) -> None:
    if not isinstance(mapping, Mapping):
        blockers.append("feature_mappings must be an object keyed by Wellar modality.feature_name")
        return
    if not mapping:
        blockers.append("at least one explicit Wellar feature-to-source mapping is required")
        return
    for feature, source in mapping.items():
        if not _nonempty(feature) or feature.count(".") != 1:
            blockers.append("feature mapping uses an invalid Wellar feature identifier")
            continue
        modality, feature_name = feature.split(".", 1)
        if modality not in MODALITIES or not feature_name or not all(c.isalnum() or c == "_" for c in feature_name):
            blockers.append("feature mapping uses an unsupported Wellar modality or feature name")
            continue
        if not _nonempty(source) or source not in profile.source_modalities:
            blockers.append(f"feature {feature} maps from a source modality absent from {profile.dataset_id}")
            continue
        expected = {
            "video": {"video_rgb_mp4", "video_nir_mp4"},
            "image": {"image_rgb", "image_nir16_png"},
            "audio": {"audio_pcm"},
            "reaction_task": {"pvt_reaction_times"},
        }[modality]
        if source not in expected:
            blockers.append(f"source {source} cannot be mapped to Wellar {modality} features")
        if source in {"video_nir_mp4", "image_nir16_png"}:
            blockers.append(f"{source} requires extractor and domain validation before Wellar RGB feature reuse")
        if target == "vigilance_impairment" and source == "pvt_reaction_times":
            blockers.append("PVT measurements cannot be both vigilance labels and model inputs for the same target")
        if source == "psg_edf_512hz" or source == "depth16_png":
            blockers.append(f"{source} is unsupported by the current Wellar modality feature schema")


def qualify_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Return blockers and compatibility; never authorizes training or validates legal rights."""
    if not isinstance(metadata, Mapping):
        raise ResearchDataError("dataset metadata must be an object")
    dataset_id = metadata.get("dataset_id")
    profile = PROFILES.get(dataset_id) if isinstance(dataset_id, str) else None
    if profile is None:
        raise ResearchDataError("dataset_id must identify a supported metadata profile")

    blockers: list[str] = []
    if not _nonempty(metadata.get("participant_id_field")):
        blockers.append("a stable pseudonymous participant ID field is required for subject-disjoint splits")
    if not _nonempty(metadata.get("sample_id_field")) or not _nonempty(metadata.get("session_id_field")):
        blockers.append("sample_id and session_id field mappings are required")

    rights = metadata.get("rights")
    if not isinstance(rights, Mapping):
        blockers.append("documented rights and consent metadata are required")
    else:
        if rights.get("research_evaluation") is not True or rights.get("model_training") is not True:
            blockers.append("research-evaluation and model-training consent/rights must be explicitly attested")
        if rights.get("commercial_model_training") is not True:
            blockers.append("commercial model-training permission is not explicitly attested")
        if not isinstance(rights.get("cross_site_transfer"), bool):
            blockers.append("cross-site transfer permission must be explicitly true or false")
        if not _nonempty(rights.get("written_permission_reference")):
            blockers.append("written provider permission/license reference is required")
        if not _nonempty(rights.get("consent_scope_reference")):
            blockers.append("participant consent scope for commercial model training is required")
        if not _nonempty(rights.get("ethics_or_governance_approval_reference")):
            blockers.append("applicable ethics/governance approval reference is required")
    if profile.dataset_id == "DROZY":
        if not isinstance(rights, Mapping) or not _nonempty(rights.get("written_permission_reference")):
            blockers.append("published DROZY license excludes commercial development; written amended permission is required")
        else:
            blockers.append("published DROZY license excludes commercial development; referenced amendment requires manual legal and provider verification")
    elif not isinstance(rights, Mapping) or not _nonempty(rights.get("written_permission_reference")):
        blockers.append("UTA-RLDD provider page does not establish a dataset license; written commercial-use terms are required")
    else:
        blockers.append("referenced UTA-RLDD permission requires manual legal, consent-scope, and provider verification")

    targets = metadata.get("requested_targets", [])
    label_sources = metadata.get("label_sources", {})
    classification_rules = metadata.get("classification_rule_ids", {})
    if not isinstance(targets, list) or not targets or any(not isinstance(t, str) or t not in TARGETS for t in targets):
        blockers.append("requested_targets must list supported independent Wellar targets")
        targets = []
    if not isinstance(label_sources, Mapping):
        label_sources = {}
        blockers.append("label_sources must be an object")
    if not isinstance(classification_rules, Mapping):
        classification_rules = {}
        blockers.append("classification_rule_ids must be an object")

    compatible_targets = []
    for target in targets:
        supplied = label_sources.get(target)
        expected = profile.label_sources.get(target)
        if target == "subjective_fatigue":
            blockers.append(f"{profile.dataset_id} has no identified independent qualified subjective-fatigue label")
        elif expected is None:
            if _nonempty(supplied) and supplied in profile.observed_label_fields:
                blockers.append(f"{profile.dataset_id} source {supplied} is not an eligible {target} label; no proxy-label conversion is allowed")
            else:
                blockers.append(f"{profile.dataset_id} does not provide an eligible {target} label source")
        elif supplied != expected:
            blockers.append(f"{profile.dataset_id} label source for {target} is incompatible; no proxy-label conversion is allowed")
        elif target == "vigilance_impairment" and not _nonempty(classification_rules.get(target)):
            blockers.append("vigilance labels require a preregistered PVT metric and classification rule")
        else:
            compatible_targets.append(target)

    for target in targets:
        _validate_feature_mapping(profile, metadata.get("feature_mappings", {}), target, blockers)

    # Even a clean metadata preflight cannot show that the source rows, consent,
    # labels, feature extraction, and rights documents satisfy the trainer.
    return {
        "dataset_id": profile.dataset_id,
        "provider_metadata_url": profile.official_url,
        "known_participant_count": profile.participant_count,
        "published_license_status": profile.license_status,
        "compatible_label_targets": sorted(set(compatible_targets)),
        "known_limits": list(profile.known_limits),
        "metadata_preflight": "blocked" if blockers else "metadata_compatible_requires_manual_evidence_review",
        "training_authorized": False,
        "training_ready": False,
        "blockers": sorted(set(blockers)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Metadata-only Wellar dataset compatibility preflight")
    parser.add_argument("--metadata", type=Path, required=True, help="local JSON metadata request; no raw media")
    args = parser.parse_args(argv)
    try:
        if args.metadata.stat().st_size > MAX_METADATA_BYTES:
            raise ResearchDataError("metadata request exceeds 64 KiB")
        data = json.loads(args.metadata.read_text(encoding="utf-8"))
        result = qualify_metadata(data)
    except (OSError, json.JSONDecodeError, ResearchDataError) as exc:
        print(json.dumps({"status": "invalid_metadata", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result["metadata_preflight"] != "blocked" else 1


if __name__ == "__main__":
    raise SystemExit(main())
