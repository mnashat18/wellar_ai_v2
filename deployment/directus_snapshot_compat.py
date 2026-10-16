"""Read-only compatibility check for an exported Directus schema snapshot.

This tool never contacts Directus and never treats an export as live authority.
It emits only aggregate field/status/action compatibility facts.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


REQUIRED_FIELDS = {
    "wellness_scans": {"id", "user", "business_profile", "status"},
    "scan_media": {"scan_id", "video_file", "audio_file"},
    "scan_results": {"scan_id"},
    "business_profile_members": {"user", "business_profile", "status"},
}
PROCESSING_STATES = {"pending", "media_ready", "processing", "completed", "failed"}
ABSTENTION_TARGETS = {
    "reference_sleepiness", "subjective_fatigue", "vigilance_impairment"
}


def inspect_snapshot(schema: dict[str, Any], permissions: dict[str, Any] | None = None) -> dict[str, Any]:
    """Summarize whether a local export can represent the V2 projection contract."""
    collections = {
        item.get("collection") for item in schema.get("collections", [])
        if isinstance(item, dict) and isinstance(item.get("collection"), str)
    }
    fields_by_collection: dict[str, set[str]] = {}
    field_metadata: dict[tuple[str, str], dict[str, Any]] = {}
    for item in schema.get("fields", []):
        if not isinstance(item, dict):
            continue
        collection, field = item.get("collection"), item.get("field")
        if isinstance(collection, str) and isinstance(field, str):
            fields_by_collection.setdefault(collection, set()).add(field)
            field_metadata[(collection, field)] = item

    missing = {
        collection: sorted(required - fields_by_collection.get(collection, set()))
        for collection, required in REQUIRED_FIELDS.items()
        if collection not in collections or required - fields_by_collection.get(collection, set())
    }
    scan_status = field_metadata.get(("wellness_scans", "status"), {})
    choices = scan_status.get("meta", {}).get("options", {}).get("choices", [])
    statuses = sorted({
        choice.get("value") for choice in choices
        if isinstance(choice, dict) and isinstance(choice.get("value"), str)
    })

    relations = {
        (item.get("collection"), item.get("field"), item.get("related_collection"))
        for item in schema.get("relations", []) if isinstance(item, dict)
    }
    media_file_relations = all(
        ("scan_media", field, "directus_files") in relations
        for field in ("video_file", "audio_file")
    )
    result_fields = fields_by_collection.get("scan_results", set())
    risk_level = field_metadata.get(("scan_results", "risk_level"), {})
    risk_choices = risk_level.get("meta", {}).get("options", {}).get("choices", [])
    risk_labels = sorted({
        choice.get("value") for choice in risk_choices
        if isinstance(choice, dict) and isinstance(choice.get("value"), str)
    })
    legacy_result_can_represent_abstention = "insufficient_evidence" in risk_labels
    has_abstention_storage = bool(
        {"v2_result", "result_json", "analysis_status"} & result_fields
    )
    has_v2_idempotency_field = bool(
        {"wellar_v2_scan_id", "v2_scan_id"} & fields_by_collection.get("wellness_scans", set())
    )

    permission_actions: dict[str, list[str]] = {}
    if isinstance(permissions, dict):
        entries = permissions.get("data", permissions.get("permissions", []))
    elif isinstance(permissions, list):
        entries = permissions
    else:
        entries = []
    if isinstance(entries, list):
        for collection in REQUIRED_FIELDS:
            permission_actions[collection] = sorted({
                item.get("action") for item in entries
                if isinstance(item, dict) and item.get("collection") == collection
                and isinstance(item.get("action"), str)
            })

    return {
        "evidence_kind": "checked_in_snapshot_only",
        "live_verified": False,
        "required_collection_fields_missing": missing,
        "wellness_scan_status_choices": statuses,
        "all_expected_processing_states_present": PROCESSING_STATES.issubset(statuses),
        "scan_media_uses_directus_file_relations": media_file_relations,
        "scan_result_risk_level_choices": risk_labels,
        "legacy_result_can_represent_abstention": legacy_result_can_represent_abstention,
        "abstention_envelope_storage_present": has_abstention_storage,
        "v2_scan_idempotency_field_present": has_v2_idempotency_field,
        "permission_snapshot_actions": permission_actions,
        "permissions_authoritative": False,
        "safe_to_enable_writeback": False,
        "blockers": [
            "live service identity and least-privilege policies require staging verification",
            "Directus file relations cannot reference V2 object-store keys as Directus file IDs",
            "V2 abstention envelope storage is absent from the observed scan_results fields"
            if not has_abstention_storage else "live result field compatibility still requires verification",
            "scan_results.risk_level does not allow insufficient_evidence; an abstention cannot be written honestly"
            if not legacy_result_can_represent_abstention else "live abstention result behavior still requires verification",
            "a unique V2 scan correlation field is absent for lost-response creation recovery"
            if not has_v2_idempotency_field else "live uniqueness and idempotent create behavior still require verification",
            "snapshot permissions do not prove deployed authorization",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", required=True, type=Path, help="Local Directus schema JSON export")
    parser.add_argument("--permissions", type=Path, help="Optional local permission export JSON")
    args = parser.parse_args()
    try:
        schema = json.loads(args.schema.read_text(encoding="utf-8-sig"))
        permissions = json.loads(args.permissions.read_text(encoding="utf-8-sig")) if args.permissions else None
        if not isinstance(schema, dict) or not isinstance(schema.get("fields"), list):
            raise ValueError("schema snapshot has an unsupported structure")
        result = inspect_snapshot(schema, permissions)
    except (OSError, json.JSONDecodeError, ValueError):
        print(json.dumps({"status": "invalid_snapshot", "safe_to_enable_writeback": False}))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
