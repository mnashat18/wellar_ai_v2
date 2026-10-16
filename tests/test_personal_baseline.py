from app.evidence.baseline import RollingBaseline


def test_baseline_emits_mad_deviation_with_non_diagnostic_label():
    baseline = RollingBaseline(minimum_scans=4)
    for value in (1, 2, 3, 4):
        baseline.add({"ear": value})
    result = baseline.deviations({"ear": 5})["measurements"]["ear"]
    assert result["status"] == "available"
    assert result["baseline_median"] == 2.5
    assert result["baseline_mad"] == 1.0
    assert result["z_score"] == (5 - 2.5) / 1.4826
    assert "non_diagnostic" in result["label"]


def test_baseline_fails_closed_before_minimum_scans_and_for_zero_variation():
    baseline = RollingBaseline(minimum_scans=3)
    baseline.add({"ear": 1})
    assert baseline.deviations({"ear": 3})["measurements"]["ear"]["reason"] == "baseline_not_ready"
    baseline.add({"ear": 1})
    baseline.add({"ear": 1})
    result = baseline.deviations({"ear": 2})["measurements"]["ear"]
    assert result["status"] == "insufficient_evidence"
    assert result["reason"] == "baseline_variation_zero"


def test_invalid_or_missing_values_do_not_contaminate_baseline():
    baseline = RollingBaseline(minimum_scans=2)
    for value in (1, float("nan"), None, True, 3):
        baseline.add({"ear": value})
    assert baseline.deviations({"ear": 2})["measurements"]["ear"]["status"] == "available"
