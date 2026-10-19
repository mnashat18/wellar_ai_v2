"""Strict validator for the Wellar pilot manifest contract.

This module validates metadata only. It does not verify consent, legal rights,
scientific qualification, or authorize collection, evaluation, or training.
Fatigue mappings are deliberately empty until a qualified review approves one.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from research.evaluation_v1 import ResearchDataError, check_split_leakage

SCHEMA_VERSION = "wellar-pilot-manifest/1.0"
STATUS = {"available", "not_collected", "not_consented", "not_provided", "invalid",
          "failed", "insufficient_quality", "withdrawn", "temporally_unmatched"}
QUALITY = {"available", "not_provided", "invalid", "failed", "insufficient_quality"}
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,127}$")
_CODE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,63}$")
_DIGEST = re.compile(r"^[a-fA-F0-9]{64}$")
_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_PHONE = re.compile(r"(?<!\w)\+?\d[\d ().-]{8,}\d(?!\w)")
_BASE64_PAYLOAD = re.compile(r"^[A-Za-z0-9+/]{128,}={0,2}$")
_FORBIDDEN_KEYS = {
    "name", "firstname", "lastname", "fullname", "email", "phone", "address",
    "employeeid", "employeenumber", "userid", "accountid", "directusid",
    "password", "token", "credential", "rawmedia", "mediabytes", "mediapayload",
    "imagebytes", "videobytes", "audiobytes", "facialtemplate", "biometrictemplate",
}
_PERMISSIONS = {
    "video_capture_and_analysis", "image_capture_and_analysis", "audio_capture_and_analysis",
    "raw_media_storage", "research_evaluation", "model_training", "cross_site_transfer", "commercial_use",
}
_MODALITY_PERMISSION = {
    "video": "video_capture_and_analysis", "image": "image_capture_and_analysis",
    "audio": "audio_capture_and_analysis",
}
_APPROVED_FATIGUE_MAPPINGS: frozenset[tuple[str, str, str]] = frozenset()


def fatigue_mapping_is_approved(instrument_id: str, subscale: str, scoring_version: str) -> bool:
    """Return whether qualified review has explicitly approved this exact mapping."""
    return (instrument_id, subscale, scoring_version) in _APPROVED_FATIGUE_MAPPINGS


def _fail(condition: bool, message: str) -> None:
    if not condition:
        raise ResearchDataError(message)


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _utc(value: Any, field: str, *, nullable: bool = False) -> datetime | None:
    if value is None and nullable:
        return None
    _fail(isinstance(value, str), f"{field} must be an ISO-8601 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ResearchDataError(f"{field} must be an ISO-8601 UTC timestamp") from None
    _fail(parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0),
          f"{field} must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _scan_sensitive(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = "".join(ch for ch in str(key).lower() if ch.isalnum())
            _fail(normalized not in _FORBIDDEN_KEYS,
                  "direct identifier, credential, biometric template, or raw-media field is forbidden")
            _scan_sensitive(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _scan_sensitive(child)
    elif isinstance(value, str):
        _fail(len(value) <= 1024 and _EMAIL.search(value) is None and _PHONE.search(value) is None and
              _BASE64_PAYLOAD.fullmatch(value) is None and
              not value.lower().startswith(("data:image/", "data:video/", "data:audio/")),
              "direct contact information or embedded media payload is forbidden")


def _exact_keys(value: Any, required: set[str], field: str, optional: set[str] = frozenset()) -> Mapping[str, Any]:
    _fail(isinstance(value, Mapping), f"{field} must be an object")
    keys = set(value)
    _fail(required <= keys and keys <= required | set(optional), f"{field} has missing or unsupported fields")
    return value


def _validate_reference(name: str, ref: Any, session_start: datetime) -> None:
    required = {"instrument", "version", "value", "measured_at_utc", "status", "missing_reason"}
    if name == "occupational_fatigue":
        required |= {"instrument_id", "subscale", "scoring_version", "units_or_range"}
    elif name == "pvt":
        required |= {"protocol_id", "started_at_utc", "ended_at_utc", "metrics"}
    r = _exact_keys(ref, required, f"references.{name}")
    _fail(r["status"] in STATUS, f"references.{name}.status is unsupported")
    available = r["status"] == "available"
    if available:
        _fail(_nonempty(r["instrument"]) and _nonempty(r["version"]),
              f"available {name} requires instrument and version")
        _fail(r["missing_reason"] is None, f"available {name} cannot have a missing reason")
        measured = _utc(r["measured_at_utc"], f"references.{name}.measured_at_utc")
        _fail(measured >= session_start, f"references.{name} occurs before session collection time")
    else:
        _fail(r["missing_reason"] is not None and _nonempty(r["missing_reason"]),
              f"missing {name} requires an explicit missing_reason")
        _fail(r["value"] is None, f"missing {name} value must be null")
        _fail(r["measured_at_utc"] is None, f"missing {name} timestamp must be null")
        return

    if name == "kss":
        _fail(r["instrument"] == "KSS", "KSS target must use the KSS instrument")
        _fail(isinstance(r["value"], int) and not isinstance(r["value"], bool) and 1 <= r["value"] <= 9,
              "available KSS value must be an integer from 1 through 9")
    elif name == "occupational_fatigue":
        _fail(_finite_number(r["value"]), "available occupational fatigue score must be finite and numeric")
        mapping = (r["instrument_id"], r["subscale"], r["scoring_version"])
        _fail(all(_nonempty(part) for part in mapping), "fatigue target mapping metadata is required")
        _fail(mapping in _APPROVED_FATIGUE_MAPPINGS,
              "fatigue target mapping is not scientifically approved; no conversion is inferred")
        _fail(_nonempty(r["units_or_range"]), "fatigue score units or range are required")
    else:
        _fail(r["instrument"] == "PVT", "vigilance target must use an independently administered PVT")
        _fail(_nonempty(r["protocol_id"]), "available PVT requires a protocol_id")
        started = _utc(r["started_at_utc"], "references.pvt.started_at_utc")
        ended = _utc(r["ended_at_utc"], "references.pvt.ended_at_utc")
        _fail(started >= session_start and ended >= started, "PVT timestamps are inconsistent with session order")
        _fail(measured >= started and measured <= ended, "PVT measured_at_utc must fall within its start/end interval")
        _fail(isinstance(r["metrics"], Mapping) and bool(r["metrics"]), "available PVT requires summary metrics")
        _fail(all(_finite_number(v) for v in r["metrics"].values()), "PVT metrics must be finite numeric summaries")
        _fail(r["value"] is None, "PVT summaries belong in metrics; scalar value must be null")


def validate_manifest_record(record: Mapping[str, Any], *, allow_synthetic_fixture: bool = False) -> None:
    """Validate one session manifest record; does not qualify a dataset or authorize use."""
    _fail(isinstance(record, Mapping), "manifest record must be an object")
    _scan_sensitive(record)
    required = {"schema_version", "study_id", "protocol_version", "protocol_sha256", "sample_id",
                "participant_id", "session_id", "session_sequence", "context", "consent", "v2_provenance",
                "modalities", "references", "protocol_deviations", "data_quality_flags"}
    optional = {"fixture_marker"}
    r = _exact_keys(record, required, "manifest record", optional)
    _fail(r["schema_version"] == SCHEMA_VERSION, "unsupported manifest schema_version")
    _fail("fixture_marker" not in r or
          (allow_synthetic_fixture and r["fixture_marker"] == "unit_test_synthetic"),
          "synthetic fixture records are rejected outside explicit unit tests")
    for field in ("study_id", "protocol_version"):
        _fail(_nonempty(r[field]), f"{field} is required")
        _fail(_CODE.fullmatch(r[field]) is not None, f"{field} must be coded, not free text")
    _fail(isinstance(r["protocol_sha256"], str) and _DIGEST.fullmatch(r["protocol_sha256"]) is not None,
          "protocol_sha256 must be a 64-character digest")
    for field in ("sample_id", "participant_id", "session_id"):
        _fail(isinstance(r[field], str) and _ID.fullmatch(r[field]) is not None,
              f"{field} must be an opaque pseudonymous identifier")
    _fail(isinstance(r["session_sequence"], int) and not isinstance(r["session_sequence"], bool)
          and r["session_sequence"] >= 1, "session_sequence must be a positive integer")

    context_fields = {"pre_or_post_shift", "site_code", "device_code", "os_code", "industry_code",
                      "occupation_code", "collection_time_utc", "clock_sync_offset_ms",
                      "clock_sync_uncertainty_ms"}
    context = _exact_keys(r["context"], context_fields, "context")
    _fail(context["pre_or_post_shift"] in {"pre_shift", "post_shift", "unknown"},
          "context.pre_or_post_shift is unsupported")
    for field in ("site_code", "device_code", "os_code", "industry_code", "occupation_code"):
        _fail(context[field] is None or
              (isinstance(context[field], str) and _CODE.fullmatch(context[field]) is not None),
              f"context.{field} must be a coded string or null")
    session_start = _utc(context["collection_time_utc"], "context.collection_time_utc")
    for field in ("clock_sync_offset_ms", "clock_sync_uncertainty_ms"):
        value = context[field]
        _fail(value is None or _finite_number(value), f"context.{field} must be finite numeric or null")
    if context["clock_sync_uncertainty_ms"] is not None:
        _fail(context["clock_sync_uncertainty_ms"] >= 0, "clock uncertainty cannot be negative")

    consent_fields = {"consent_record_ref", "consent_version", "status", "permissions", "withdrawn_at_utc"}
    consent = _exact_keys(r["consent"], consent_fields, "consent")
    _fail(_nonempty(consent["consent_record_ref"]) and _nonempty(consent["consent_version"]),
          "consent reference and version are required")
    _fail(consent["status"] in {"granted", "withdrawn", "unknown"}, "consent status is unsupported")
    permissions = _exact_keys(consent["permissions"], _PERMISSIONS, "consent.permissions")
    _fail(all(isinstance(value, bool) for value in permissions.values()), "all consent scopes must be explicit booleans")
    withdrawn = _utc(consent["withdrawn_at_utc"], "consent.withdrawn_at_utc", nullable=True)
    _fail((consent["status"] == "withdrawn") == (withdrawn is not None),
          "withdrawal status and timestamp must agree")
    _fail(consent["status"] == "granted" and permissions["research_evaluation"],
          "active research-evaluation consent is required")

    provenance_fields = {"api_contract_version", "client_build_hash", "extractor_version",
                         "feature_mapping_version", "worker_build_hash", "configuration_digest", "processed_at_utc"}
    provenance = _exact_keys(r["v2_provenance"], provenance_fields, "v2_provenance")
    for field in ("api_contract_version", "feature_mapping_version"):
        _fail(_nonempty(provenance[field]), f"v2_provenance.{field} is required")
    for field in ("client_build_hash", "worker_build_hash"):
        _fail(provenance[field] == "unknown" or
              (isinstance(provenance[field], str) and _DIGEST.fullmatch(provenance[field]) is not None),
              f"v2_provenance.{field} must be a digest or 'unknown'")
    _fail(isinstance(provenance["configuration_digest"], str) and
          _DIGEST.fullmatch(provenance["configuration_digest"]) is not None,
          "v2_provenance.configuration_digest must be a digest")
    processed_at = _utc(provenance["processed_at_utc"], "v2_provenance.processed_at_utc")
    extractors = _exact_keys(provenance["extractor_version"], {"video", "image", "audio"},
                             "v2_provenance.extractor_version")
    _fail(all(_nonempty(v) for v in extractors.values()), "extractor versions are required for every modality")

    modalities = _exact_keys(r["modalities"], {"video", "image", "audio"}, "modalities")
    media_capture_ends: list[datetime] = []
    for name, modality in modalities.items():
        fields = {"availability", "capture_start_utc", "capture_end_utc", "quality_status",
                  "missing_reason", "feature_values", "raw_media_ref"}
        allowed_optional = {"duration_ms", "format", "dimensions_px", "fps", "sample_hz"}
        m = _exact_keys(modality, fields, f"modalities.{name}", allowed_optional)
        _fail(m["availability"] in STATUS, f"modalities.{name}.availability is unsupported")
        _fail(m["quality_status"] in QUALITY, f"modalities.{name}.quality_status is unsupported")
        present = m["availability"] == "available"
        _fail((m["missing_reason"] is None) == present,
              f"modalities.{name} requires a reason exactly when unavailable")
        _fail(m["quality_status"] == "available" if present else m["quality_status"] != "available",
              f"modalities.{name} availability and quality_status disagree")
        start = _utc(m["capture_start_utc"], f"modalities.{name}.capture_start_utc", nullable=True)
        end = _utc(m["capture_end_utc"], f"modalities.{name}.capture_end_utc", nullable=True)
        _fail((start is not None and end is not None) if present else (start is None and end is None),
              f"modalities.{name} capture timestamps disagree with availability")
        if present:
            _fail(start >= session_start and end >= start, f"modalities.{name} timestamps are out of order")
            _fail(processed_at >= end, f"modalities.{name} processing predates media capture completion")
            _fail(permissions[_MODALITY_PERMISSION[name]], f"{name} processing consent is required")
            media_capture_ends.append(end)
        if m["availability"] == "not_consented":
            _fail(not permissions[_MODALITY_PERMISSION[name]],
                  f"modalities.{name} is marked not_consented despite granted modality scope")
        values = m["feature_values"]
        _fail(isinstance(values, Mapping) and all(v is None or _finite_number(v) for v in values.values()),
              f"modalities.{name}.feature_values must contain only finite numbers or null")
        _fail(m["raw_media_ref"] is None or
              (permissions["raw_media_storage"] and isinstance(m["raw_media_ref"], str) and _ID.fullmatch(m["raw_media_ref"])),
              f"modalities.{name}.raw_media_ref requires storage consent and an opaque object ID")
        for field in allowed_optional:
            if field in m and m[field] is not None:
                value = m[field]
                if field == "duration_ms":
                    _fail(_finite_number(value) and value >= 0, f"modalities.{name}.{field} must be nonnegative")
                elif field == "dimensions_px":
                    _fail(isinstance(value, list) and len(value) == 2 and
                          all(isinstance(x, int) and not isinstance(x, bool) and x > 0 for x in value),
                          f"modalities.{name}.dimensions_px must contain two positive integers")
                elif field in {"fps", "sample_hz"}:
                    _fail(_finite_number(value) and value > 0, f"modalities.{name}.{field} must be positive")
                else:
                    _fail(_nonempty(value), f"modalities.{name}.{field} must be a nonempty string")

    references = _exact_keys(r["references"], {"kss", "occupational_fatigue", "pvt"}, "references")
    for name, ref in references.items():
        _validate_reference(name, ref, session_start)
        if ref["status"] == "available" and name in {"kss", "occupational_fatigue"} and media_capture_ends:
            measured_at = _utc(ref["measured_at_utc"], f"references.{name}.measured_at_utc")
            _fail(measured_at >= max(media_capture_ends),
                  f"references.{name} measurement predates media capture completion")
    pvt = references["pvt"]
    if pvt["status"] == "available":
        questionnaire_times = [
            _utc(references[name]["measured_at_utc"], f"references.{name}.measured_at_utc")
            for name in ("kss", "occupational_fatigue") if references[name]["status"] == "available"
        ]
        if questionnaire_times:
            pvt_start = _utc(pvt["started_at_utc"], "references.pvt.started_at_utc")
            _fail(pvt_start >= max(questionnaire_times), "PVT must start after the fatigue/sleepiness questionnaires")

    _fail(isinstance(r["protocol_deviations"], list) and
          all(isinstance(x, str) and _CODE.fullmatch(x) is not None for x in r["protocol_deviations"]),
          "protocol_deviations must be a list of coded values")
    _fail(isinstance(r["data_quality_flags"], list) and
          all(isinstance(x, str) and _CODE.fullmatch(x) is not None for x in r["data_quality_flags"]),
          "data_quality_flags must be a list of coded values")


def validate_manifest_splits(records: Sequence[Mapping[str, Any]], split_by_sample: Mapping[str, str]) -> None:
    """Validate independent split-vault assignments and participant/session isolation."""
    _fail(isinstance(records, Sequence) and not isinstance(records, (str, bytes)) and bool(records),
          "manifest records are required for split validation")
    _fail(isinstance(split_by_sample, Mapping), "split assignments must be an object")
    samples = [row.get("sample_id") for row in records if isinstance(row, Mapping)]
    _fail(len(samples) == len(records) and len(set(samples)) == len(samples),
          "manifest sample_id values must be unique")
    _fail(set(samples) == set(split_by_sample), "every manifest sample needs exactly one split assignment")
    check_split_leakage(records, split_by_sample)
