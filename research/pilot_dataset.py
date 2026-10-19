"""Local-only, consent-gated importer for Wellar-owned pilot recordings.

The importer reads media in place and emits feature/label JSONL only. It never
copies or uploads raw recordings. The command-line interface has no synthetic
fixture override; that hook is available only for isolated unit tests.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.media.extractors import InvalidMediaError, extract_audio, extract_image, extract_video
from research.evaluation_v1 import ResearchDataError, load_jsonl, validate_record

PILOT_SCHEMA_VERSION = "wellar-owned-pilot/1.0"
DATA_SCHEMA_VERSION = "1.2"
PERMISSIONS = ("media_analysis", "media_storage", "model_training", "evaluation", "cross_site_transfer")
MODALITIES = ("image", "video", "audio", "reaction_task")
COGNITIVE_TARGETS = ("reference_sleepiness", "subjective_fatigue", "vigilance_performance")
EVENT_TARGETS = ("eye_state_event", "mouth_opening_event")
MAX_MANIFEST_BYTES = 128 * 1024
MAX_MEDIA_FILES = 64
MAX_VIDEO_ANNOTATIONS = 1800
MAX_RECORDS = 5000
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_LABEL_OFFSET_SECONDS = 15 * 60
_ANNOTATOR_ROLES = {"participant_self", "trained_annotator", "research_staff"}
VIDEO_LIMITS = {"max_seconds": 30, "max_frames": 1800, "sample_hz": 4.0, "max_pixels": 4_000_000}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ResearchDataError(message)


def _utc(value: Any, field: str) -> datetime:
    _require(isinstance(value, str), f"{field} must be an ISO-8601 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ResearchDataError(f"{field} must be an ISO-8601 UTC timestamp") from None
    _require(parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0), f"{field} must be UTC")
    return parsed.astimezone(timezone.utc)


def _read_json(path: Path, limit: int) -> dict[str, Any]:
    _require(path.stat().st_size <= limit, "pilot JSON file exceeds its size limit")

    def reject_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON keys")
            result[key] = value
        return result

    try:
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicates,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid JSON constant")))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        raise ResearchDataError("pilot JSON file is invalid") from None
    _require(isinstance(data, dict), "pilot JSON root must be an object")
    return data


def _safe_identifier(value: Any, name: str) -> str:
    _require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{2,63}", value) is not None,
             f"{name} must be an opaque pseudonymous identifier")
    return value


def template() -> tuple[dict[str, Any], dict[str, Any]]:
    """Return denied-by-default session and rights templates."""
    session = {
        "pilot_schema_version": PILOT_SCHEMA_VERSION,
        "dataset_id": "REPLACE_WITH_LOCAL_DATASET_ID",
        "dataset_version": "1.0",
        "study_id": "REPLACE_WITH_PROTOCOL_ID",
        "acquisition_protocol_id": "REPLACE_BEFORE_COLLECTION",
        "curator_role": "participant_curator",
        "participant_id": "participant-pseudonym",
        "session_id": "session-pseudonym",
        "identity_linkage": {"pseudonymized": True, "linkage_key_stored_separately": True,
                             "linkage_governance_id": "REPLACE_WITH_LOCAL_GOVERNANCE_REFERENCE"},
        "consent": {"status": "not_granted", "consented_at_utc": None,
                    "consent_reference": None,
                    "permissions": {permission: False for permission in PERMISSIONS}},
        "recorded_at_utc": None,
        "context": {"site_id": "wellar-owned-local", "device_id": "unknown", "industry": "unknown",
                    "occupation": "unknown", "lighting": "unknown", "camera_position": "unknown",
                    "posture": "unknown", "audio_environment": "unknown"},
        "media": [],
    }
    rights = {
        "dataset_id": "REPLACE_WITH_SESSION_DATASET_ID",
        "dataset_version": "1.0",
        "license_id": "REPLACE_WITH_DOCUMENTED_LOCAL_RIGHTS_BASIS",
        "approval_reference": "",
        "protocol_sha256": "",
        "model_training_authorized": False,
        "consent_confirmed": False,
    }
    return session, rights


def _validate_session(data: Mapping[str, Any], *, allow_unit_test_fixture: bool = False) -> None:
    required = {"pilot_schema_version", "dataset_id", "dataset_version", "study_id", "acquisition_protocol_id",
                "curator_role", "participant_id", "session_id", "identity_linkage", "consent",
                "recorded_at_utc", "context", "media"}
    expected = required | ({"fixture_marker"} if allow_unit_test_fixture else set())
    _require(set(data) == expected, "pilot session fields do not match the supported schema")
    _require(not data.get("fixture_marker") or
             (allow_unit_test_fixture and data.get("fixture_marker") == "unit_test_synthetic"),
             "synthetic/unit-test fixtures are not eligible pilot data")
    _require(data["pilot_schema_version"] == PILOT_SCHEMA_VERSION, "unsupported pilot session version")
    for field in ("dataset_id", "dataset_version", "study_id", "acquisition_protocol_id", "curator_role"):
        _require(isinstance(data[field], str) and data[field].strip() and "REPLACE" not in data[field].upper(),
                 f"{field} must be completed before importing")
    _safe_identifier(data["participant_id"], "participant_id")
    _safe_identifier(data["session_id"], "session_id")
    _utc(data["recorded_at_utc"], "recorded_at_utc")
    linkage = data["identity_linkage"]
    _require(isinstance(linkage, Mapping) and linkage.get("pseudonymized") is True and
             linkage.get("linkage_key_stored_separately") is True and
             isinstance(linkage.get("linkage_governance_id"), str) and linkage["linkage_governance_id"].strip() and
             "REPLACE" not in linkage["linkage_governance_id"].upper(),
             "pseudonymous identity linkage and separate local key governance are required")
    consent = data["consent"]
    _require(isinstance(consent, Mapping) and consent.get("status") == "granted" and
             isinstance(consent.get("consent_reference"), str) and consent["consent_reference"].strip() and
             "REPLACE" not in consent["consent_reference"].upper(), "documented participant consent is required")
    _safe_identifier(consent.get("consent_reference"), "consent_reference")
    _utc(consent.get("consented_at_utc"), "consented_at_utc")
    permissions = consent.get("permissions")
    _require(isinstance(permissions, Mapping) and set(permissions) == set(PERMISSIONS) and
             all(isinstance(permissions[name], bool) for name in PERMISSIONS),
             "all five independent pilot permissions must be explicit booleans")
    _require(permissions["media_analysis"], "media_analysis permission must be granted for import")
    _require(permissions["media_storage"], "media_storage permission must be granted to persist the feature dataset")
    context = data["context"]
    context_keys = {"site_id", "device_id", "industry", "occupation", "lighting", "camera_position", "posture", "audio_environment"}
    _require(isinstance(context, Mapping) and set(context) == context_keys and
             all(isinstance(value, str) and len(value) <= 80 for value in context.values()),
             "recording context must use bounded categories without personal notes")
    media = data["media"]
    _require(isinstance(media, list) and 1 <= len(media) <= MAX_MEDIA_FILES, "media must contain 1 to 64 local assets")
    sample_ids = set()
    for item in media:
        _require(isinstance(item, Mapping) and set(item).issubset({"sample_id", "modality", "path", "captured_at_utc", "target_labels", "annotations"}) and
                 {"sample_id", "modality", "path", "captured_at_utc"}.issubset(item),
                 "media entry fields are invalid")
        sample_id = _safe_identifier(item["sample_id"], "sample_id")
        _require(sample_id not in sample_ids, "sample_id values must be unique")
        sample_ids.add(sample_id)
        _require(item["modality"] in {"image", "video", "audio"}, "media modality must be image, video, or audio")
        _require(isinstance(item["path"], str) and item["path"] and not Path(item["path"]).is_absolute(),
                 "media path must be relative to the session manifest")
        _utc(item["captured_at_utc"], "media.captured_at_utc")
        labels = item.get("target_labels", {})
        _require(isinstance(labels, Mapping) and set(labels).issubset(COGNITIVE_TARGETS),
                 "target_labels may contain only separately defined sleepiness, fatigue, and PVT labels")
        annotations = item.get("annotations", [])
        _require(isinstance(annotations, list) and len(annotations) <= MAX_VIDEO_ANNOTATIONS,
                 "visual annotations exceed the per-asset limit")
        if annotations:
            _require(item["modality"] in {"image", "video"}, "audio cannot carry visual annotations")
            seen_timestamps = set()
            for annotation in annotations:
                _require(isinstance(annotation, Mapping) and
                         set(annotation).issubset({"timestamp_ms", "eye_state", "mouth_opening", "annotation_reference", "assessor_role"}) and
                         {"timestamp_ms", "eye_state", "mouth_opening"}.issubset(annotation),
                         "visual annotation fields are invalid")
                ts = annotation["timestamp_ms"]
                _require(isinstance(ts, int) and not isinstance(ts, bool) and 0 <= ts <= 30_000,
                         "annotation timestamp_ms must be a bounded nonnegative integer")
                _require(ts not in seen_timestamps, "visual annotation timestamps must be unique per asset")
                seen_timestamps.add(ts)
                _require(annotation["eye_state"] in {"open", "closed", "unknown"} and
                         annotation["mouth_opening"] in {"open", "closed", "unknown"},
                         "eye_state and mouth_opening must be direct open/closed/unknown annotations")
                if annotation["eye_state"] != "unknown" or annotation["mouth_opening"] != "unknown":
                    _require(isinstance(annotation.get("annotation_reference"), str) and annotation["annotation_reference"].strip() and
                             re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{2,63}", annotation["annotation_reference"]) is not None and
                             annotation.get("assessor_role") in _ANNOTATOR_ROLES,
                             "observed events require an opaque annotation reference and assessor role")


def _media_path(root: Path, relative: str, modality: str) -> Path:
    base = root.resolve()
    _require(".." not in Path(relative).parts, "media path must remain inside the session directory")
    candidate = root / relative
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        raise ResearchDataError("local media asset is unavailable") from None
    _require(resolved.is_relative_to(base) and resolved.is_file(), "media path must remain inside the session directory")
    current = candidate
    while current != root:
        _require(not current.is_symlink(), "symlink media paths are not accepted")
        current = current.parent
    limits = {"image": 5 * 1024 * 1024, "video": 20 * 1024 * 1024, "audio": 8 * 1024 * 1024}
    _require(resolved.stat().st_size <= limits[modality], "local media asset exceeds the modality size limit")
    return resolved


def _numeric_values(values: Mapping[str, Any]) -> dict[str, float | int | None]:
    result = {}
    for name, value in values.items():
        if value is None:
            result[name] = None
        elif isinstance(value, bool):
            continue
        elif isinstance(value, (int, float)) and math.isfinite(value):
            result[name] = value
    return result


def _missing_label(target: str, reason: str = "not_measured") -> dict[str, Any]:
    return {"status": "unknown", "raw_value": None, "class_label": None,
            "label_definition_id": f"{target}/1.0", "missing_reason": reason}


def _event_label(target: str, state: str, annotation: Mapping[str, Any], event_time: str) -> dict[str, Any]:
    if state == "unknown":
        return _missing_label(target, "not_annotated")
    event_type = "eye_state" if target == "eye_state_event" else "mouth_opening"
    _safe_identifier(annotation.get("annotation_reference"), "annotation_reference")
    _require(annotation.get("assessor_role") in _ANNOTATOR_ROLES, "assessor_role must be an allowed non-identifying role")
    class_value = int(state == "closed") if event_type == "eye_state" else int(state == "open")
    return {"status": "observed", "raw_value": class_value, "class_label": class_value,
            "classification_rule_id": "eye-open-0-closed-1/1.0" if event_type == "eye_state" else "mouth-closed-0-open-1/1.0",
            "label_definition_id": f"{target}/1.0", "instrument": "direct_visual_annotation",
            "event_type": event_type, "unit": "binary_direct_event_class", "source_reference": annotation["annotation_reference"],
            "assessor_role": annotation["assessor_role"], "measured_at_utc": event_time}


def _validate_label_alignment(labels: Mapping[str, Any], captured_at: datetime) -> None:
    for target in COGNITIVE_TARGETS:
        label = labels.get(target)
        if isinstance(label, Mapping) and label.get("status") == "observed":
            measured = _utc(label.get("measured_at_utc"), f"{target}.measured_at_utc")
            _require(abs((measured - captured_at).total_seconds()) <= MAX_LABEL_OFFSET_SECONDS,
                     f"{target} measurement must be within 15 minutes of its media sample")
            _safe_identifier(label.get("source_reference"), f"{target}.source_reference")
            _require(label.get("assessor_role") in _ANNOTATOR_ROLES,
                     f"{target}.assessor_role must be a role category, not a person identifier")


def _extract(modality: str, path: Path, *, include_video_samples: bool = False) -> dict[str, Any]:
    if modality == "image":
        return extract_image(path, max_pixels=4_000_000)
    if modality == "video":
        return extract_video(path, **VIDEO_LIMITS, include_sample_features=include_video_samples)
    return extract_audio(path, max_seconds=30)


def _make_record(data: Mapping[str, Any], media: Mapping[str, Any], *, sample_id: str,
                 feature_modality: str, feature_values: Mapping[str, Any], feature_provenance: Mapping[str, str],
                 visual_labels: Mapping[str, Mapping[str, Any]], captured_at: datetime,
                 fixture_marker: str | None = None) -> dict[str, Any]:
    all_features = {name: {"values": {}, "provenance": {}} for name in MODALITIES}
    all_features[feature_modality] = {"values": _numeric_values(feature_values),
                                      "provenance": dict(feature_provenance)}
    availability = {name: "missing" for name in MODALITIES}
    availability[feature_modality] = "available" if feature_provenance.get("status") == "available" else "failed"
    target_labels = media.get("target_labels", {})
    labels = {}
    for target in COGNITIVE_TARGETS:
        label = target_labels.get(target)
        labels[target] = dict(label) if isinstance(label, Mapping) else _missing_label(target)
    labels.update({target: dict(visual_labels[target]) for target in EVENT_TARGETS})
    permissions = data["consent"]["permissions"]
    record = {
        "schema_version": DATA_SCHEMA_VERSION,
        "sample_id": sample_id,
        "participant_id": data["participant_id"],
        "session_id": data["session_id"],
        "identity_linkage": dict(data["identity_linkage"]),
        "provenance": {"study_id": data["study_id"], "dataset_id": data["dataset_id"],
                       "dataset_version": data["dataset_version"], "source_reference": sample_id,
                       "acquisition_protocol_id": data["acquisition_protocol_id"], "curator_role": data["curator_role"]},
        "consent": {"status": "granted", "consent_reference": data["consent"]["consent_reference"],
                    "consented_at_utc": data["consent"]["consented_at_utc"], "permissions": dict(permissions)},
        "context": {"recorded_at_utc": captured_at.isoformat().replace("+00:00", "Z"),
                    **{name: data["context"][name] for name in ("site_id", "device_id", "industry", "occupation")}},
        "modality_availability": availability,
        "features": all_features,
        "labels": labels,
    }
    if fixture_marker is not None:
        record["fixture_marker"] = fixture_marker
    return record


def import_session(manifest_path: str | Path, output_path: str | Path, *,
                   allow_unit_test_fixture: bool = False) -> dict[str, int | str]:
    """Extract local assets and create metadata/features only; outputs never overwrite."""
    manifest_file = Path(manifest_path).resolve(strict=True)
    data = _read_json(manifest_file, MAX_MANIFEST_BYTES)
    _validate_session(data, allow_unit_test_fixture=allow_unit_test_fixture)
    root = manifest_file.parent
    records: list[dict[str, Any]] = []
    for media in data["media"]:
        modality = media["modality"]
        path = _media_path(root, media["path"], modality)
        captured_at = _utc(media["captured_at_utc"], "media.captured_at_utc")
        _validate_label_alignment(media.get("target_labels", {}), captured_at)
        annotations = media.get("annotations", [])
        extracted = _extract(modality, path, include_video_samples=modality == "video" and bool(annotations))
        if modality == "video" and annotations:
            sample_observations = extracted.get("sample_observations", [])
            _require(bool(sample_observations), "video extractor produced no timestamped samples for annotation alignment")
            for annotation in annotations:
                timestamp_ms = annotation["timestamp_ms"]
                sample = min(sample_observations, key=lambda row: abs(row["timestamp_ms"] - timestamp_ms))
                _require(abs(sample["timestamp_ms"] - timestamp_ms) <= 1000 / (2 * VIDEO_LIMITS["sample_hz"]) + 1,
                         "video annotation does not align with a sampled frame")
                event_time = (captured_at + timedelta(milliseconds=sample["timestamp_ms"])).isoformat().replace("+00:00", "Z")
                visual_labels = {"eye_state_event": _event_label("eye_state_event", annotation["eye_state"], annotation, event_time),
                                 "mouth_opening_event": _event_label("mouth_opening_event", annotation["mouth_opening"], annotation, event_time)}
                values = {"eye_aspect_ratio": sample["eye_aspect_ratio"], "mouth_aspect_ratio": sample["mouth_aspect_ratio"],
                          "mean_sampled_luminance": extracted["features"].get("mean_sampled_luminance")}
                record = _make_record(data, media, sample_id=f"{media['sample_id']}-{timestamp_ms}",
                                      feature_modality="video", feature_values=values,
                                      feature_provenance={**extracted["provenance"], "status": "available" if sample["face_valid"] else "insufficient_quality"},
                                      visual_labels=visual_labels, captured_at=captured_at, fixture_marker=data.get("fixture_marker") if allow_unit_test_fixture else None)
                validate_record(record, allow_unit_test_fixture=allow_unit_test_fixture)
                records.append(record)
        else:
            event_annotations = annotations if modality == "image" else []
            selected_annotations = event_annotations or [{"timestamp_ms": 0, "eye_state": "unknown", "mouth_opening": "unknown"}]
            for annotation in selected_annotations:
                if modality == "image" and annotation["timestamp_ms"] != 0:
                    raise ResearchDataError("image event annotations must align to timestamp_ms 0")
                visual_labels = {target: _event_label(target, annotation["eye_state"] if target == "eye_state_event" else annotation["mouth_opening"], annotation,
                                 captured_at.isoformat().replace("+00:00", "Z")) for target in EVENT_TARGETS}
                record = _make_record(data, media, sample_id=media["sample_id"], feature_modality=modality,
                                      feature_values=extracted.get("features", {}), feature_provenance={**extracted.get("provenance", {}), "status": extracted.get("status", "failed")},
                                      visual_labels=visual_labels, captured_at=captured_at, fixture_marker=data.get("fixture_marker") if allow_unit_test_fixture else None)
                validate_record(record, allow_unit_test_fixture=allow_unit_test_fixture)
                records.append(record)
        _require(len(records) <= MAX_RECORDS, "pilot import exceeds the sample limit")

    encoded = b"".join(json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n" for row in records)
    _require(len(encoded) <= MAX_OUTPUT_BYTES, "pilot feature dataset exceeds its size limit")
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("xb") as output:
            output.write(encoded)
    except FileExistsError:
        raise ResearchDataError("output already exists; choose a new path to preserve existing data") from None
    return {"records": len(records), "participants": len({row["participant_id"] for row in records}),
            "output_bytes": len(encoded), "raw_media_copied": 0, "status": "local_features_written"}


def _write_new(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as output:
            json.dump(payload, output, indent=2, sort_keys=True)
            output.write("\n")
    except FileExistsError:
        raise ResearchDataError("template output already exists; existing files are preserved") from None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local-only Wellar V2 consent-gated pilot dataset tools")
    commands = parser.add_subparsers(dest="command", required=True)
    template_parser = commands.add_parser("template", help="create denied-by-default session and rights templates")
    template_parser.add_argument("--directory", type=Path, required=True)
    import_parser = commands.add_parser("import", help="extract local media into a feature-only JSONL dataset")
    import_parser.add_argument("--manifest", type=Path, required=True)
    import_parser.add_argument("--output", type=Path, required=True)
    validate_parser = commands.add_parser("validate", help="validate an imported feature/label JSONL dataset")
    validate_parser.add_argument("--data", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "template":
            session, rights = template()
            _write_new(args.directory / "session.template.json", session)
            _write_new(args.directory / "rights.template.json", rights)
            result = {"status": "templates_created_permissions_denied", "files": 2}
        elif args.command == "import":
            result = import_session(args.manifest, args.output)
        else:
            rows = load_jsonl(args.data)
            result = {"status": "valid", "records": len(rows),
                      "participants": len({row["participant_id"] for row in rows}),
                      "training_consented_records": sum(row["consent"]["permissions"].get("model_training", False) is True for row in rows),
                      "evaluation_consented_records": sum(row["consent"]["permissions"].get("evaluation", False) is True for row in rows)}
    except OSError:
        print("pilot workflow blocked: local input or output file is unavailable")
        return 2
    except (ResearchDataError, InvalidMediaError, ValueError) as exc:
        print(f"pilot workflow blocked: {exc}")
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
