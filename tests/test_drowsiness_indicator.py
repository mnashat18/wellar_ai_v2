from copy import deepcopy

from app.drowsiness_indicator import classify, load_thresholds, personal_perclos_deviation, fresh_baseline_values


def measures(**updates):
    value = {"short_clip_eye_closure_fraction": .05, "blink_rate_per_minute": 12, "mean_eye_closure_seconds": .1,
             "max_eye_closure_seconds": .3, "face_detection_ratio": .95, "mean_luminance": 90,
             "mean_blur_variance": 50, "temporal_coverage": .8, "yaw_proxy": .01, "pitch_proxy": .01}
    value.update(updates)
    return value


def test_all_four_rule_states_and_threshold_config():
    t = load_thresholds()
    assert classify(measures(), t)["state"] == "Stable"
    assert classify(measures(short_clip_eye_closure_fraction=.12), t)["state"] == "Low Focus"
    assert classify(measures(short_clip_eye_closure_fraction=.22), t)["state"] == "Elevated Fatigue"
    assert classify(measures(short_clip_eye_closure_fraction=.42), t)["state"] == "High Risk"
    assert classify(measures(short_clip_eye_closure_fraction=.42), t)["level"] == "high"


def test_face_light_blur_and_audio_quality_abstain_without_state_or_level():
    for update in ({"face_detection_ratio": .1}, {"mean_luminance": 1}, {"mean_blur_variance": 0}):
        result = classify(measures(**update))
        assert result["status"] == "insufficient_quality"
        assert "state" not in result and "level" not in result
    result = classify(measures(), audio_quality_status="insufficient_quality")
    assert result["status"] == "insufficient_quality"
    assert "state" not in result
    result = classify(measures(yaw_proxy=.8))
    assert result["status"] == "insufficient_quality" and result["reason_code"] == "pose_away"


def test_no_confidence_or_accuracy_and_optional_baseline_is_fail_closed():
    result = classify(measures(), baseline_z_score=2.0, prior_scans=4, minimum_prior_scans=5)
    assert "confidence" not in result and "accuracy" not in result
    assert "personal_baseline" not in result
    result = classify(measures(), baseline_z_score=2.0, prior_scans=5, minimum_prior_scans=5)
    assert result["personal_baseline"]["z_score"] == 2.0
    assert result["label"] == "indicative, non-diagnostic, not a validated classifier"
    assert result["scope"] == "research_only_non_production"
    assert result["production_prediction"] is False
    assert result["config_sha256"] and result["config_version"] == "1.0"


def test_invalid_config_version_or_non_monotonic_threshold_is_rejected(tmp_path):
    import json
    config = load_thresholds()
    config["schema_version"] = "999"
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    try:
        load_thresholds(path)
    except ValueError:
        pass
    else:
        raise AssertionError("unsupported config version accepted")


def test_personal_baseline_uses_prior_scans_and_omits_until_minimum():
    z_score, count = personal_perclos_deviation(.4, [.1, .2, .3, .4], minimum_scans=5)
    assert z_score is None and count == 4
    z_score, count = personal_perclos_deviation(.5, [.1, .2, .3, .4, .5], minimum_scans=5)
    assert z_score is not None and count == 5
    z_score, count = personal_perclos_deviation(.5, [.2, .2, .2, .2, .2], minimum_scans=5)
    assert z_score is None and count == 5


def test_config_rejects_unknown_missing_malformed_and_out_of_range_values(tmp_path):
    import json
    base = load_thresholds()
    base.pop("config_sha256")
    cases = []
    unknown = deepcopy(base); unknown["surprise"] = 1; cases.append(unknown)
    missing = deepcopy(base); del missing["minimum_blur_variance"]; cases.append(missing)
    malformed = deepcopy(base); malformed["states"]["low_focus"]["unexpected"] = 1; cases.append(malformed)
    out_of_range = deepcopy(base); out_of_range["minimum_temporal_coverage"] = 1.1; cases.append(out_of_range)
    nonfinite = deepcopy(base); nonfinite["states"]["high_risk"]["max_eye_closure_seconds_at_least"] = float("inf"); cases.append(nonfinite)
    for index, config in enumerate(cases):
        path = tmp_path / f"bad-{index}.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        try:
            load_thresholds(path)
        except (ValueError, TypeError):
            pass
        else:
            raise AssertionError(f"invalid configuration {index} accepted")


def test_baseline_freshness_discards_stale_rows_and_reports_reason_codes():
    history = [
        {"measurements": {"short_clip_eye_closure_fraction": .2}, "created_at": 900},
        {"measurements": {"short_clip_eye_closure_fraction": .3}, "created_at": 199995},
        {"measurements": {"short_clip_eye_closure_fraction": .4}, "created_at": 200001},
    ]
    values, reason = fresh_baseline_values(history, 1, now=200000)
    assert values == [.3]
    assert reason is None
    values, reason = fresh_baseline_values([history[0]], 1, now=200000)
    assert values == []
    assert reason == "baseline_stale_history"
