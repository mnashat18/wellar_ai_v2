from deployment.directus_snapshot_compat import inspect_snapshot


def test_snapshot_check_reports_schema_gaps_and_never_authorizes_live_writes():
    schema = {
        "collections": [{"collection": name} for name in (
            "wellness_scans", "scan_media", "scan_results", "business_profile_members"
        )],
        "fields": [
            {"collection": "wellness_scans", "field": field,
             "meta": {"options": {"choices": [
                 {"value": value} for value in
                 ("pending", "media_ready", "processing", "completed", "failed")
             ]}} if field == "status" else {}}
            for field in ("id", "user", "business_profile", "status")
        ] + [
            {"collection": "scan_media", "field": field}
            for field in ("scan_id", "video_file", "audio_file")
        ] + [{"collection": "scan_results", "field": "scan_id"}] + [
            {"collection": "business_profile_members", "field": field}
            for field in ("user", "business_profile", "status")
        ],
        "relations": [],
    }
    result = inspect_snapshot(schema, {"data": [
        {"collection": "wellness_scans", "action": "create"},
        {"collection": "scan_results", "action": "update"},
    ]})
    assert result["evidence_kind"] == "checked_in_snapshot_only"
    assert result["all_expected_processing_states_present"] is True
    assert result["abstention_envelope_storage_present"] is False
    assert result["v2_scan_idempotency_field_present"] is False
    assert result["permission_snapshot_actions"]["wellness_scans"] == ["create"]
    assert result["permissions_authoritative"] is False
    assert result["safe_to_enable_writeback"] is False


def test_snapshot_checker_rejects_missing_statuses_and_does_not_claim_compatibility():
    result = inspect_snapshot({
        "collections": [{"collection": "wellness_scans"}],
        "fields": [{"collection": "wellness_scans", "field": field,
                    "meta": {"options": {"choices": [{"value": "pending"}]}}}
                   for field in ("id", "user", "business_profile", "status")],
        "relations": [],
    })
    assert result["all_expected_processing_states_present"] is False
    assert result["required_collection_fields_missing"]
    assert result["safe_to_enable_writeback"] is False


def test_scored_only_legacy_enum_cannot_represent_v2_abstention():
    schema = {
        "collections": [{"collection": name} for name in (
            "wellness_scans", "scan_media", "scan_results", "business_profile_members"
        )],
        "fields": [
            {"collection": "wellness_scans", "field": field}
            for field in ("id", "user", "business_profile", "status")
        ] + [
            {"collection": "scan_media", "field": field}
            for field in ("scan_id", "video_file", "audio_file")
        ] + [
            {"collection": "scan_results", "field": "scan_id"},
            {"collection": "scan_results", "field": "risk_level", "meta": {
                "options": {"choices": [
                    {"value": label} for label in (
                        "stable", "low_focus", "elevated_fatigue", "high_risk"
                    )
                ]}
            }},
        ] + [
            {"collection": "business_profile_members", "field": field}
            for field in ("user", "business_profile", "status")
        ],
        "relations": [],
    }
    result = inspect_snapshot(schema, [
        {"collection": "scan_results", "action": "read"},
        {"collection": "wellness_scans", "action": "update"},
    ])
    assert result["scan_result_risk_level_choices"] == [
        "elevated_fatigue", "high_risk", "low_focus", "stable"
    ]
    assert result["legacy_result_can_represent_abstention"] is False
    assert result["permission_snapshot_actions"]["scan_results"] == ["read"]
    assert result["permission_snapshot_actions"]["wellness_scans"] == ["update"]
    assert any("risk_level does not allow insufficient_evidence" in blocker
               for blocker in result["blockers"])
    assert result["safe_to_enable_writeback"] is False
