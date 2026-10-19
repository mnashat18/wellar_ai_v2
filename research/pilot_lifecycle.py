"""Ephemeral synthetic-only pilot consent/session lifecycle demonstration.

This module has no file, database, media-store, or network integration. It is a
small software-readiness harness, not an enrollment or collection service.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import re
from typing import Any, Mapping

from research.evaluation_v1 import ResearchDataError

SCHEMA_VERSION = "wellar-synthetic-pilot-session/1.0"
MODALITIES = ("image", "video", "audio")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,127}$")
_FORBIDDEN = {"name", "email", "phone", "address", "employeeid", "userid", "rawmedia",
              "mediabytes", "mediapayload", "imagebytes", "videobytes", "audiobytes",
              "facialtemplate", "biometrictemplate", "path", "filepath", "mediapath"}
_FEATURE_METADATA = {"extractor", "version", "schema_version", "coverage", "rejection_code"}


def _fail(ok: bool, message: str) -> None:
    if not ok:
        raise ResearchDataError(message)


def _utc(value: Any, field: str) -> datetime:
    _fail(isinstance(value, str), f"{field} must be an ISO-8601 UTC timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ResearchDataError(f"{field} must be an ISO-8601 UTC timestamp") from None
    _fail(result.tzinfo is not None and result.utcoffset() == timedelta(0),
          f"{field} must include a UTC offset")
    return result.astimezone(timezone.utc)


def _safe_metadata(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = "".join(c for c in str(key).lower() if c.isalnum())
            _fail(normalized not in _FORBIDDEN,
                  "direct identifiers, media payloads, and media paths are forbidden")
            _safe_metadata(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _safe_metadata(item)
    elif isinstance(value, str):
        _fail(len(value) <= 1024 and "@" not in value and not value.lower().startswith("data:"),
              "contact information or embedded media is forbidden")


def validate_session(session: Mapping[str, Any]) -> None:
    """Validate a metadata-only synthetic session envelope."""
    _fail(isinstance(session, Mapping), "session must be an object")
    _safe_metadata(session)
    required = {"schema_version", "participant_id", "session_id", "consent", "timing",
                "instrument", "modalities", "references", "retention"}
    _fail(set(session) == required, "session has missing or unsupported fields")
    _fail(session["schema_version"] == SCHEMA_VERSION, "unsupported session schema")
    for field in ("participant_id", "session_id"):
        _fail(isinstance(session[field], str) and _ID.fullmatch(session[field]) is not None,
              f"{field} must be pseudonymous")
    consent = session["consent"]
    _fail(isinstance(consent, Mapping) and set(consent) == {"status", "reference", "version", "granted_at_utc", "withdrawn_at_utc", "research", "modalities"},
          "consent fields are incomplete")
    _fail(consent["status"] in {"granted", "refused", "withdrawn"}, "invalid consent state")
    _fail(isinstance(consent["research"], bool), "research consent must be explicit")
    _fail(isinstance(consent["modalities"], Mapping) and set(consent["modalities"]) == set(MODALITIES)
          and all(isinstance(v, bool) for v in consent["modalities"].values()),
          "per-modality consent must be explicit")
    _fail(all(isinstance(consent[k], str) and consent[k] for k in ("reference", "version")),
          "consent provenance is required")
    granted_at = consent["granted_at_utc"]
    withdrawn_at = consent["withdrawn_at_utc"]
    if consent["status"] == "granted":
        _fail(consent["research"] and granted_at is not None and withdrawn_at is None,
              "active session requires granted research consent")
        grant_time = _utc(granted_at, "consent.granted_at_utc")
    elif consent["status"] == "refused":
        _fail(not consent["research"] and granted_at is None and withdrawn_at is None
              and not any(consent["modalities"].values()), "refused consent cannot authorize collection")
    else:
        _fail(not consent["research"] and granted_at is not None and withdrawn_at is not None
              and not any(consent["modalities"].values()), "withdrawn consent must revoke every scope")
        grant_time = _utc(granted_at, "consent.granted_at_utc")
        withdrawal_time = _utc(withdrawn_at, "consent.withdrawn_at_utc")
    timing = session["timing"]
    _fail(isinstance(timing, Mapping) and set(timing) == {"started_at_utc", "ended_at_utc"},
          "session timing fields are incomplete")
    start = _utc(timing["started_at_utc"], "timing.started_at_utc")
    end = _utc(timing["ended_at_utc"], "timing.ended_at_utc")
    _fail(end >= start, "session end precedes start")
    if consent["status"] != "refused":
        _fail(grant_time <= start, "consent grant must not follow session start")
    if consent["status"] == "withdrawn":
        _fail(withdrawal_time >= grant_time, "withdrawal predates consent grant")
        _fail(withdrawal_time >= end, "withdrawal must not precede session end")
    instrument = session["instrument"]
    _fail(isinstance(instrument, Mapping) and set(instrument) == {"id", "version", "approval_status", "provenance"},
          "instrument approval provenance is required")
    _fail(instrument["approval_status"] in {"approved", "pending", "not_approved", "not_used"},
          "invalid instrument approval status")
    _fail(all(isinstance(instrument[k], str) and instrument[k] for k in instrument),
          "instrument metadata must be explicit; questionnaire content is not accepted")
    if consent["status"] in {"refused", "withdrawn"}:
        _fail(instrument == {"id": consent["status"], "version": consent["status"],
                             "approval_status": "not_used", "provenance": consent["status"]},
              f"{consent['status']} sessions cannot retain instrument metadata")
    modalities = session["modalities"]
    _fail(isinstance(modalities, Mapping) and set(modalities) == set(MODALITIES),
          "all modality states must be explicit")
    for modality, state in modalities.items():
        _fail(isinstance(state, Mapping) and set(state) == {"status", "quality_status", "missing_reason", "feature_metadata"},
              f"{modality} state is incomplete")
        _fail(state["status"] in {"not_consented", "not_collected", "available", "insufficient_quality", "failed"},
              f"{modality} availability is invalid")
        if state["feature_metadata"] is not None:
            _fail(isinstance(state["feature_metadata"], Mapping)
                  and set(state["feature_metadata"]) <= _FEATURE_METADATA,
                  f"{modality} may contain provenance metadata only, not measurements or media")
        _fail(state["quality_status"] in {"not_assessed", "acceptable", "insufficient_quality", "failed"},
              f"{modality} quality state is invalid")
        expected_quality = {
            "available": "acceptable",
            "insufficient_quality": "insufficient_quality",
            "failed": "failed",
            "not_collected": "not_assessed",
            "not_consented": "not_assessed",
        }[state["status"]]
        _fail(state["quality_status"] == expected_quality,
              f"{modality} status and quality_status are inconsistent")
        if state["status"] == "available":
            _fail(state["missing_reason"] is None and state["feature_metadata"] is not None,
                  f"available {modality} requires provenance and no missing reason")
        if not consent["modalities"][modality]:
            _fail(state["status"] in {"not_consented", "not_collected"} and state["feature_metadata"] is None,
                  f"{modality} data cannot exist without modality consent")
        if state["status"] in {"not_consented", "not_collected", "insufficient_quality", "failed"}:
            _fail(state["missing_reason"] is not None and isinstance(state["missing_reason"], str)
                  and bool(state["missing_reason"]), f"{modality} missing/rejected state needs a reason")
        if state["status"] == "insufficient_quality":
            _fail(state["quality_status"] == "insufficient_quality" and state["feature_metadata"] is None,
                  f"{modality} rejected measurements cannot be exposed as available features")
    references = session["references"]
    _fail(isinstance(references, Mapping) and references and all(isinstance(k, str) for k in references),
          "reference measurement states are required")
    for target, reference in references.items():
        _fail(isinstance(reference, Mapping) and set(reference) == {"status", "instrument_id", "version", "approval_status", "measured_at_utc", "missing_reason"},
              f"{target} reference state is incomplete")
        _fail(reference["status"] in {"available", "not_collected", "not_approved", "withdrawn"},
              f"{target} reference status is invalid")
        if consent["status"] in {"refused", "withdrawn"}:
            _fail(reference["status"] != "available",
                  f"{consent['status']} sessions cannot retain reference measurements")
        _fail(reference["approval_status"] in {"approved", "pending", "not_approved", "not_used"},
              f"{target} approval state is invalid")
        if reference["status"] != "available":
            _fail(reference["measured_at_utc"] is None and isinstance(reference["missing_reason"], str)
                  and bool(reference["missing_reason"]), f"{target} missingness must be explicit")
        else:
            _fail(reference["approval_status"] == "approved" and reference["measured_at_utc"] is not None
                  and reference["missing_reason"] is None, f"{target} available measurement requires approval and timing")
            measured = _utc(reference["measured_at_utc"], f"references.{target}.measured_at_utc")
            _fail(start <= measured <= end, f"{target} measurement must fall within the session")
    retention = session["retention"]
    _fail(isinstance(retention, Mapping) and set(retention) == {"policy_id", "delete_after_utc"}
          and isinstance(retention["policy_id"], str) and retention["policy_id"]
          and retention["delete_after_utc"] is not None, "retention policy and expiry are required")
    expiry = _utc(retention["delete_after_utc"], "retention.delete_after_utc")
    _fail(expiry >= end, "retention expiry precedes session end")
    if consent["status"] == "withdrawn":
        _fail(expiry >= withdrawal_time, "retention expiry precedes withdrawal")


class SyntheticPilotStore:
    """In-memory only store for contract tests; never accepts raw media or labels."""

    def __init__(self) -> None:
        self._sessions: dict[str, dict[str, Any]] = {}

    @property
    def session_count(self) -> int:
        return len(self._sessions)

    def grant(self, session: Mapping[str, Any]) -> None:
        validate_session(session)
        _fail(session["consent"]["status"] == "granted", "only granted consent can be recorded")
        key = session["session_id"]
        _fail(key not in self._sessions, "session already exists")
        self._sessions[key] = deepcopy(dict(session))

    def refuse(self, session: Mapping[str, Any]) -> None:
        validate_session(session)
        _fail(session["consent"]["status"] == "refused", "refusal state required")
        # Refusal is deliberately not persisted, including as an identifiable tombstone.

    def get(self, session_id: str) -> dict[str, Any] | None:
        item = self._sessions.get(session_id)
        return deepcopy(item) if item is not None else None

    def withdraw(self, session_id: str, withdrawn_at_utc: str) -> None:
        record = self._sessions.get(session_id)
        _fail(record is not None, "unknown session")
        _fail(record["consent"]["status"] == "granted", "session is already withdrawn or inactive")
        withdrawn_at = _utc(withdrawn_at_utc, "withdrawn_at_utc")
        grant_time = _utc(record["consent"]["granted_at_utc"], "consent.granted_at_utc")
        started = _utc(record["timing"]["started_at_utc"], "timing.started_at_utc")
        _fail(withdrawn_at >= grant_time, "withdrawal predates consent grant")
        _fail(withdrawn_at >= started, "withdrawal predates session start")
        candidate = deepcopy(record)
        candidate["consent"].update(status="withdrawn", research=False, withdrawn_at_utc=withdrawn_at_utc,
                                    modalities={name: False for name in MODALITIES})
        candidate["instrument"] = {"id": "withdrawn", "version": "withdrawn",
                                   "approval_status": "not_used", "provenance": "withdrawn"}
        for name in MODALITIES:
            candidate["modalities"][name] = {"status": "not_consented", "quality_status": "not_assessed",
                                             "missing_reason": "withdrawn", "feature_metadata": None}
        for target in candidate["references"]:
            reference = candidate["references"][target]
            reference.update(status="withdrawn", measured_at_utc=None, missing_reason="withdrawn",
                             approval_status="not_approved")
            reference["instrument_id"] = "withdrawn"
            reference["version"] = "withdrawn"
        candidate["retention"] = {"policy_id": "immediate_withdrawal_deletion", "delete_after_utc": withdrawn_at_utc}
        current_end = _utc(candidate["timing"]["ended_at_utc"], "timing.ended_at_utc")
        if withdrawn_at < current_end:
            candidate["timing"]["ended_at_utc"] = withdrawn_at_utc
        validate_session(candidate)
        self._sessions[session_id] = candidate

    def delete_session(self, session_id: str) -> bool:
        return self._sessions.pop(session_id, None) is not None

    def delete_participant(self, participant_id: str) -> int:
        keys = [key for key, value in self._sessions.items() if value["participant_id"] == participant_id]
        for key in keys:
            del self._sessions[key]
        return len(keys)

    def purge_expired(self, now_utc: str) -> int:
        now = _utc(now_utc, "now_utc")
        keys = [key for key, value in self._sessions.items()
                if _utc(value["retention"]["delete_after_utc"], "retention.delete_after_utc") <= now]
        for key in keys:
            del self._sessions[key]
        return len(keys)
