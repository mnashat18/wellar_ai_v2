"""Pure projection from V2 rule output to the legacy scan_results shape."""
from __future__ import annotations


PROJECTION_VERSION = "1.0"
ALLOWED_STATES = {"Stable", "Low Focus", "Elevated Fatigue", "High Risk"}


def project_v1(result: dict) -> dict:
    indicator = result.get("drowsiness_indicator") or result.get("evidence", {}).get("drowsiness_indicator", {})
    baseline_used = bool(indicator.get("personal_baseline", {}).get("status") == "available")
    if indicator.get("status") != "available" or indicator.get("state") not in ALLOWED_STATES:
        return {
            "projection_version": PROJECTION_VERSION,
            "status": "retake_required",
            "risk_level": None,
            "readiness_state": None,
            "overall_state": None,
            "readiness_score": None,
            "fatigue_score": None,
            "focus_score": None,
            "risk_score": None,
            "confidence_score": None,
            "ai_model_version": f"wellar-v2-rules-{indicator.get('config_version', 'unknown')}",
            "baseline_used": baseline_used,
            "analysis_mode": "rule_based_indicator",
            "label": "indicative, non-diagnostic, not a validated classifier",
        }
    state = indicator["state"]
    return {
        "projection_version": PROJECTION_VERSION,
        "status": "available",
        "risk_level": state,
        "readiness_state": state,
        "overall_state": state,
        "readiness_score": None,
        "fatigue_score": None,
        "focus_score": None,
        "risk_score": None,
        "confidence_score": None,
        "ai_model_version": f"wellar-v2-rules-{indicator.get('config_version', 'unknown')}",
        "baseline_used": baseline_used,
        "analysis_mode": "rule_based_indicator",
        "label": "indicative, non-diagnostic, not a validated classifier",
    }
