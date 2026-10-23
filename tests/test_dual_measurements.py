"""Contract tests for measured outputs; fixtures here are software-only timings."""
import pytest
from pydantic import ValidationError

from app.measurements.dual_output import (
    dual_measurement_output,
    score_approved_fatigue_response,
    vigilance_measurement,
)
from app.scans.schemas import ReactionTask, SubjectiveFatigue


def _task(deltas, *, spacing=10_000, **kwargs):
    trials = []
    for index, delta in enumerate(deltas):
        stimulus = 10_000 + index * spacing
        trials.append({
            "trial_index": index,
            "stimulus_timestamp_ms": stimulus,
            "response_timestamp_ms": None if delta is None else stimulus + delta,
        })
    return ReactionTask(task_type="simple_reaction", trials=trials, **kwargs).model_dump(mode="json")


def test_fatigue_is_independently_unavailable_without_consented_response():
    result = dual_measurement_output(None)
    fatigue = result["fatigue"]
    assert result["schema_version"] == "wellar.dual-measurements/1.0"
    assert fatigue["measurement_type"] == "self_reported_fatigue"
    assert fatigue["status"] == "unavailable"
    assert fatigue["release_status"] == "not_available"
    assert fatigue["value"] is None and fatigue["units"] is None
    assert fatigue["ai_validation_status"] == "not_validated"
    assert result["vigilance"]["status"] == "not_provided"


def test_consented_legacy_custom_score_is_observed_but_protocol_is_unverified():
    result = dual_measurement_output(None,
        instrument_candidate={"instrument_id": "OFER15_IR"},
        legacy_reference={"instrument": "wellar_custom_fatigue_rating_0_10", "score": 7})
    fatigue = result["fatigue"]
    result = dual_measurement_output(None, legacy_reference={"instrument": "wellar_custom_fatigue_rating_0_10", "score": 7, "collection_consent": True, "question_version": "legacy_prompt_unverified"})
    fatigue = result["fatigue"]
    assert fatigue["status"] == "observed_legacy_self_report_protocol_unverified"
    assert fatigue["value"] == 7
    assert fatigue["source"] == "participant_entered"
    assert fatigue["question"] is None and fatigue["timeframe"] is None
    assert fatigue["ai_validation_status"] == "not_validated"


@pytest.mark.parametrize("score", [0, 10])
def test_versioned_custom_fatigue_item_exposes_exact_question_and_anchors(score):
    fatigue = dual_measurement_output(None, legacy_reference={
        "score": score, "collection_consent": True,
        "question_version": "wellar-fatigue-self-report/1.0",
    })["fatigue"]
    assert fatigue["status"] == "observed_self_report"
    assert fatigue["value"] == score
    assert fatigue["question"] == "Right now, how physically or mentally fatigued do you feel before starting work?"
    assert fatigue["timeframe"] == "Right now, before starting work."
    assert fatigue["scale_anchors"] == {"0": "Not fatigued at all", "10": "Extremely fatigued"}
    assert fatigue["ai_validation_status"] == "not_validated"


def test_fatigue_is_not_inferred_from_media_and_consent_is_required():
    absent_consent = dual_measurement_output(None, legacy_reference={"score": 7, "collection_consent": False, "question_version": "wellar-fatigue-self-report/1.0"})["fatigue"]
    assert absent_consent["value"] is None
    assert absent_consent["source"] is None


def test_fatigue_score_runtime_validation_rejects_invalid_type_or_range():
    for score in (-1, 11, True, 4.2):
        fatigue = dual_measurement_output(None, legacy_reference={"score": score, "collection_consent": True, "question_version": "wellar-fatigue-self-report/1.0"})["fatigue"]
        assert fatigue["value"] is None and fatigue["status"] == "unavailable"


def test_fatigue_submission_requires_explicit_response_and_discards_without_opt_in():
    with pytest.raises(ValidationError, match="score is required"):
        SubjectiveFatigue(score=None, collection_consent=True)
    report = SubjectiveFatigue(score=8, collection_consent=False)
    assert report.score is None
    with pytest.raises(ValidationError):
        SubjectiveFatigue(score=11, collection_consent=True)
    with pytest.raises(ValidationError):
        SubjectiveFatigue(score=4, collection_consent=True, question_version="future/99")
    with pytest.raises(ValidationError):
        SubjectiveFatigue(score=4, collection_consent=True, question_version="wellar-fatigue-self-report/1.0", instrument="validated_occupational_scale")


def test_future_fatigue_scoring_registry_requires_all_independent_approval_gates():
    called = []
    result = score_approved_fatigue_response(
        instrument_id="candidate-scale",
        instrument_version="1",
        subscale="candidate",
        response={"opaque_response_reference": "ref-only"},
        approved_registry={
            ("candidate-scale", "1", "candidate"): {
                "rights_verified": True,
                "language_approved": True,
                "intended_use_approved": False,
                "scoring_version": "1",
                "units": "points",
                "scorer": lambda _response: called.append(True) or 5,
            }
        },
    )
    assert result["status"] == "unavailable"
    assert result["value"] is None
    assert called == []


def test_valid_late_false_start_and_missed_trials_have_separate_observed_counts():
    result = vigilance_measurement(_task([300, 80, 2501, None, 400],
        device_clock="monotonic", timestamp_resolution_ms=1,
        planned_trial_count=5, client_reported_completed=True))
    value = result["value"]
    assert result["measurement_type"] == "brief_reaction_task_observation"
    assert result["status"] == "observed_unverified"
    assert value["valid_reaction_times_ms"] == [300, 400]
    assert value["median_valid_rt_ms"] == 350
    assert value["p90_valid_rt_ms"] == 400
    assert value["valid_trial_count"] == 2
    assert value["late_response_count"] == 1
    assert value["false_start_count"] == 1
    assert value["missed_response_count"] == 1
    assert value["timing_quality"] == "insufficient_valid_trials"
    assert value["task_completion_status"] == "client_reported_complete"
    assert value["device_timestamp_reliability"]["status"] == "unverified_client_clock"
    assert value["device_timestamp_reliability"]["server_verified"] is False
    assert value["clinical_pvt_equivalence"] == "not_established"
    assert result["release_status"] == "direct_observed_measurement"


def test_adequate_count_remains_unverified_and_never_claims_validated_pvt():
    result = vigilance_measurement(_task([250, 310, 405], client_reported_completed=True))
    assert result["status"] == "observed_unverified"
    assert result["quality"]["status"] == "unverified_client_timing"
    assert result["value"]["task_completion_status"] == "client_reported_complete_unverifiable_plan"
    assert "not an equivalently validated clinical PVT" in " ".join(result["limitations"])


def test_cross_trial_timestamp_is_counted_as_anomaly_not_reaction_time():
    task = {
        "task_type": "pvt",
        "trials": [
            {"trial_index": 0, "stimulus_timestamp_ms": 100, "response_timestamp_ms": 350},
            {"trial_index": 1, "stimulus_timestamp_ms": 300, "response_timestamp_ms": 600},
        ],
    }
    value = vigilance_measurement(task)["value"]
    assert value["ambiguous_timestamp_count"] == 1
    assert value["valid_reaction_times_ms"] == [300]
    assert value["trial_observations"][0]["status"] == "timer_or_trial_attribution_anomaly"


def test_invalid_trial_order_is_rejected_at_api_schema_boundary():
    with pytest.raises(ValidationError, match="strictly increasing"):
        ReactionTask(task_type="simple_reaction", trials=[
            {"trial_index": 0, "stimulus_timestamp_ms": 200},
            {"trial_index": 1, "stimulus_timestamp_ms": 100},
        ])


def test_task_size_bounds_remain_enforced():
    trials = [{"trial_index": i, "stimulus_timestamp_ms": i * 1000} for i in range(101)]
    with pytest.raises(ValidationError):
        ReactionTask(task_type="simple_reaction", trials=trials)
