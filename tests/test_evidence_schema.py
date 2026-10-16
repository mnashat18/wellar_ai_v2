import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.evidence.schemas import EvidenceV1, unvalidated_outcomes


def _document():
    return {
        "scan_id": "scan-test-1", "schema_version": "1.0", "model_versions": {},
        "modalities": {"video": {"status": "missing", "measurements": {}, "quality_scores": {}, "missingness_flags": ["video"]}},
        "quality_scores": {}, "missingness_flags": ["video"],
        **unvalidated_outcomes(), "abstentions": [], "limitations": ["No validated classifier registered."],
    }


def test_evidence_v1_validates_and_matches_checked_in_json_schema():
    schema = json.loads((Path(__file__).parents[1] / "evidence_schema.json").read_text(encoding="utf-8"))
    assert schema["$id"].endswith("evidence_schema.json")
    assert EvidenceV1.model_validate(_document()).schema_version == "1.0"


def test_no_unregistered_class_or_confidence_can_be_emitted():
    document = _document()
    for target in [document["fatigue"], document["focus"], *document["risk_states"].values()]:
        assert target == {"status": "insufficient_evidence", "reason": "no_validated_classifier_registered"}
    document["fatigue"] = {"status": "available", "class": "high", "confidence": 0.99}
    with pytest.raises(ValidationError):
        EvidenceV1.model_validate(document)


def test_schema_rejects_out_of_range_quality():
    document = _document()
    document["quality_scores"]["video"] = 2.0
    with pytest.raises(ValidationError):
        EvidenceV1.model_validate(document)
