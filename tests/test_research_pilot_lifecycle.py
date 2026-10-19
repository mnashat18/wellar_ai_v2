"""Synthetic-only consent/session lifecycle tests; no media or participant data."""
from copy import deepcopy

import pytest

from research.evaluation_v1 import ResearchDataError
from research.pilot_lifecycle import MODALITIES, SCHEMA_VERSION, SyntheticPilotStore, validate_session

NOW = "2026-05-01T12:00:00Z"


def session(*, status="granted", modality_consent=None):
    scopes = {name: False for name in MODALITIES}
    if status == "granted":
        scopes.update(modality_consent or {name: True for name in MODALITIES})
    return {
        "schema_version": SCHEMA_VERSION,
        "participant_id": "synthetic-participant-01",
        "session_id": "synthetic-session-01",
        "consent": {"status": status, "reference": "synthetic-consent-record", "version": "consent-v1",
                    "granted_at_utc": NOW if status != "refused" else None,
                    "withdrawn_at_utc": None, "research": status == "granted", "modalities": scopes},
        "timing": {"started_at_utc": NOW, "ended_at_utc": "2026-05-01T12:10:00Z"},
        "instrument": {"id": status if status in {"refused", "withdrawn"} else "none",
                       "version": status if status in {"refused", "withdrawn"} else "none",
                       "approval_status": "not_used",
                       "provenance": status if status in {"refused", "withdrawn"} else "none"},
        "modalities": {name: {"status": "not_collected" if scopes[name] else "not_consented",
                              "quality_status": "not_assessed",
                              "missing_reason": "not_collected" if scopes[name] else "not_consented",
                              "feature_metadata": None} for name in MODALITIES},
        "references": {"sleepiness": {"status": "not_collected", "instrument_id": "none", "version": "none",
                                      "approval_status": "not_used", "measured_at_utc": None,
                                      "missing_reason": "not_collected"}},
        "retention": {"policy_id": "synthetic-test-one-day", "delete_after_utc": "2026-05-02T12:10:00Z"},
    }


def test_granted_partial_modality_consent_and_missing_reference_are_explicit():
    item = session(modality_consent={"image": True, "video": False, "audio": True})
    item["modalities"]["image"] = {"status": "available", "quality_status": "acceptable",
                                   "missing_reason": None, "feature_metadata": {"extractor": "synthetic-v1"}}
    validate_session(item)
    store = SyntheticPilotStore()
    store.grant(item)
    saved = store.get(item["session_id"])
    assert saved["modalities"]["video"]["status"] == "not_consented"
    assert saved["modalities"]["video"]["feature_metadata"] is None
    assert saved["references"]["sleepiness"]["status"] == "not_collected"


def test_refused_consent_is_not_persisted():
    store = SyntheticPilotStore()
    store.refuse(session(status="refused"))
    assert store.session_count == 0


def test_withdrawal_revokes_scopes_and_clears_measurement_metadata():
    store = SyntheticPilotStore()
    item = session()
    item["modalities"]["image"] = {"status": "available", "quality_status": "acceptable",
                                   "missing_reason": None, "feature_metadata": {"extractor": "synthetic-v1"}}
    store.grant(item)
    store.withdraw(item["session_id"], "2026-05-01T12:05:00Z")
    withdrawn = store.get(item["session_id"])
    assert withdrawn["consent"]["status"] == "withdrawn"
    assert not withdrawn["consent"]["research"] and not any(withdrawn["consent"]["modalities"].values())
    assert all(m["feature_metadata"] is None for m in withdrawn["modalities"].values())
    assert withdrawn["references"]["sleepiness"]["status"] == "withdrawn"


def test_withdrawal_after_session_end_keeps_end_and_sets_expiry_at_withdrawal():
    store = SyntheticPilotStore()
    store.grant(session())
    store.withdraw("synthetic-session-01", "2026-05-01T12:20:00Z")
    withdrawn = store.get("synthetic-session-01")
    assert withdrawn["timing"]["ended_at_utc"] == "2026-05-01T12:10:00Z"
    assert withdrawn["retention"]["delete_after_utc"] == "2026-05-01T12:20:00Z"


@pytest.mark.parametrize(("path", "value", "error"), [
    (("consent", "granted_at_utc"), "2026-05-01T12:01:00Z", "grant must not follow session start"),
    (("timing", "ended_at_utc"), "2026-05-01T11:59:00Z", "session end precedes start"),
    (("retention", "delete_after_utc"), "2026-05-01T12:09:59Z", "retention expiry precedes session end"),
])
def test_chronology_rejects_invalid_grant_session_and_expiry_order(path, value, error):
    item = session()
    cursor = item
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value
    with pytest.raises(ResearchDataError, match=error):
        validate_session(item)


@pytest.mark.parametrize(("status", "quality"), [
    ("available", "not_assessed"),
    ("available", "insufficient_quality"),
    ("insufficient_quality", "acceptable"),
    ("failed", "not_assessed"),
    ("not_collected", "failed"),
    ("not_consented", "acceptable"),
])
def test_modality_status_quality_pairs_must_match(status, quality):
    item = session()
    modality = item["modalities"]["image"]
    modality.update(status=status, quality_status=quality, missing_reason="synthetic-invalid-state")
    if status == "available":
        modality.update(missing_reason=None, feature_metadata={"extractor": "synthetic-v1"})
    with pytest.raises(ResearchDataError, match="status and quality_status are inconsistent"):
        validate_session(item)


def test_refused_and_withdrawn_sessions_reject_available_reference_measurements():
    refused = session(status="refused")
    refused["references"]["sleepiness"].update(status="available", approval_status="approved",
        instrument_id="approved-test", version="1", measured_at_utc=NOW, missing_reason=None)
    with pytest.raises(ResearchDataError, match="cannot retain reference measurements"):
        validate_session(refused)

    store = SyntheticPilotStore()
    store.grant(session())
    store.withdraw("synthetic-session-01", "2026-05-01T12:05:00Z")
    withdrawn = store.get("synthetic-session-01")
    withdrawn["references"]["sleepiness"].update(status="available", approval_status="approved",
        instrument_id="approved-test", version="1", measured_at_utc=NOW, missing_reason=None)
    with pytest.raises(ResearchDataError, match="cannot retain reference measurements"):
        validate_session(withdrawn)


@pytest.mark.parametrize("status", ["refused", "withdrawn"])
def test_refused_and_withdrawn_sessions_reject_instrument_metadata(status):
    if status == "refused":
        item = session(status="refused")
    else:
        store = SyntheticPilotStore()
        store.grant(session())
        store.withdraw("synthetic-session-01", "2026-05-01T12:05:00Z")
        item = store.get("synthetic-session-01")
    item["instrument"].update(id="restricted-instrument", version="1", provenance="synthetic-provenance")
    with pytest.raises(ResearchDataError, match="cannot retain instrument metadata"):
        validate_session(item)


def test_invalid_withdrawal_is_atomic_and_repeat_withdrawal_is_rejected():
    store = SyntheticPilotStore()
    store.grant(session())
    before = deepcopy(store.get("synthetic-session-01"))
    with pytest.raises(ResearchDataError, match="predates consent grant"):
        store.withdraw("synthetic-session-01", "2026-04-30T12:00:00Z")
    assert store.get("synthetic-session-01") == before

    store.withdraw("synthetic-session-01", "2026-05-01T12:05:00Z")
    withdrawn = store.get("synthetic-session-01")
    with pytest.raises(ResearchDataError, match="already withdrawn or inactive"):
        store.withdraw("synthetic-session-01", "2026-05-01T12:06:00Z")
    assert store.get("synthetic-session-01") == withdrawn


def test_withdrawn_contract_rejects_withdrawal_before_session_end():
    store = SyntheticPilotStore()
    store.grant(session())
    store.withdraw("synthetic-session-01", "2026-05-01T12:05:00Z")
    item = store.get("synthetic-session-01")
    item["timing"]["ended_at_utc"] = "2026-05-01T12:06:00Z"
    with pytest.raises(ResearchDataError, match="withdrawal must not precede session end"):
        validate_session(item)


def test_deletion_is_idempotent_with_explicit_boolean_result():
    store = SyntheticPilotStore()
    store.grant(session())
    assert store.delete_session("synthetic-session-01") is True
    assert store.delete_session("synthetic-session-01") is False


def test_low_quality_rejection_cannot_expose_measurement_features():
    item = session()
    item["modalities"]["image"] = {"status": "insufficient_quality", "quality_status": "insufficient_quality",
                                   "missing_reason": "underexposed", "feature_metadata": None}
    validate_session(item)
    item["modalities"]["image"]["feature_metadata"] = {"eye_aspect_ratio": 0.2}
    with pytest.raises(ResearchDataError, match="provenance metadata only|rejected measurements"):
        validate_session(item)


@pytest.mark.parametrize("bad", [
    {"name": "Synthetic Person"},
    {"raw_media": "data:image/png;base64,not-real"},
    {"media_path": "synthetic.jpg"},
])
def test_direct_identifiers_media_payloads_and_paths_are_rejected(bad):
    item = session()
    item["modalities"]["image"]["feature_metadata"] = bad
    with pytest.raises(ResearchDataError, match="identifiers|media paths"):
        validate_session(item)


def test_session_and_participant_cleanup_and_expiry_remove_ephemeral_records():
    store = SyntheticPilotStore()
    first, second = session(), session()
    second["session_id"] = "synthetic-session-02"
    store.grant(first)
    store.grant(second)
    assert store.delete_session(first["session_id"])
    assert store.delete_participant(first["participant_id"]) == 1
    assert store.get(second["session_id"]) is None

    third = session()
    third["session_id"] = "synthetic-session-03"
    store.grant(third)
    assert store.purge_expired("2026-05-03T00:00:00Z") == 1
    assert store.session_count == 0


def test_store_copies_records_and_never_writes_external_state():
    store = SyntheticPilotStore()
    item = session()
    store.grant(item)
    item["consent"]["status"] = "withdrawn"
    returned = store.get(item["session_id"])
    returned["consent"]["status"] = "refused"
    assert store.get(item["session_id"])["consent"]["status"] == "granted"
    assert store.session_count == 1


def test_fatigue_reference_cannot_claim_unapproved_score():
    item = session()
    item["references"]["occupational_fatigue"] = {"status": "not_approved", "instrument_id": "pending",
        "version": "pending", "approval_status": "not_approved", "measured_at_utc": None,
        "missing_reason": "scientific approval pending"}
    validate_session(item)
    item["references"]["occupational_fatigue"].update(status="available", approval_status="approved",
        measured_at_utc=NOW, missing_reason=None, value=4.2)
    with pytest.raises(ResearchDataError, match="incomplete"):
        validate_session(item)


def test_session_contract_rejects_measurement_values_in_feature_metadata():
    item = session()
    item["modalities"]["image"] = {"status": "available", "quality_status": "acceptable",
        "missing_reason": None, "feature_metadata": {"extractor": "synthetic-v1", "eye_aspect_ratio": 0.2}}
    with pytest.raises(ResearchDataError, match="provenance metadata only"):
        validate_session(item)
