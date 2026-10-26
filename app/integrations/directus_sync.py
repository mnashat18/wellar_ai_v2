from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Protocol


@dataclass(frozen=True)
class VerifiedDirectusState:
    """Remote state observed after an idempotent Directus write attempt."""
    scan_id: str
    result_scan_id: str | None
    status: str


@dataclass(frozen=True)
class DirectusBindingEvidence:
    """Internal proof contract; populated only after server-side identity, membership,
    scan-state and linked-media ownership checks. No HTTP endpoint accepts this object.
    """
    scan_id: str
    directus_scan_id: str
    tenant_id: str
    subject_id: str
    remote_owner_id: str
    remote_tenant_id: str
    remote_status: str
    membership_active: bool
    media_owner_verified: bool
    verified_at: float

    def validate(self) -> None:
        identifiers=(self.scan_id,self.directus_scan_id,self.tenant_id,self.subject_id,
                     self.remote_owner_id,self.remote_tenant_id)
        if (not all(isinstance(value,str) and value and len(value)<=256 for value in identifiers)
                or self.remote_owner_id!=self.subject_id or self.remote_tenant_id!=self.tenant_id
                or self.remote_status not in {"pending", "media_ready"} or not self.membership_active
                or not isinstance(self.media_owner_verified,bool)
                or (not self.media_owner_verified and self.remote_status!="pending")
                or not isinstance(self.verified_at,(int,float))
                or not math.isfinite(self.verified_at) or self.verified_at<=0):
            raise ValueError("Directus scan binding evidence is incomplete or inconsistent")


class DirectusWriteAdapter(Protocol):
    def ensure_synced(self, event: dict) -> VerifiedDirectusState:
        """Idempotently write then re-read and verify the bound scan and result."""


def build_directus_v2_result_contract_v1(result: dict, directus_scan_id: str) -> dict:
    """Map the displayable V2 indicator into the established Directus result row."""
    indicator=result.get("drowsiness_indicator") if isinstance(result,dict) else None
    risk=indicator.get("risk_level") if isinstance(indicator,dict) else None
    allowed={"stable","low_focus","elevated_fatigue","high_risk"}
    if risk not in allowed:
        # An insufficient-quality scan still needs a displayable, non-risk result.
        risk="stable"
    contributors=indicator.get("contributing_indicators",[])
    message=result.get("message","")
    explanation="; ".join([*(str(item) for item in contributors if isinstance(item,str)),
                            message if isinstance(message,str) else ""])
    baseline=indicator.get("baseline") if isinstance(indicator,dict) else None
    raw=indicator.get("raw_measurements") if isinstance(indicator,dict) else None
    return {"scan_id":directus_scan_id,"risk_level":risk,"confidence":indicator.get("confidence_level"),
            "explanation":explanation or "Indicative, non-diagnostic result.",
            "suggested_action":{"stable":"Continue routine monitoring.","low_focus":"Consider a short break.",
                "elevated_fatigue":"Take a break before continuing.","high_risk":"Stop the activity and rest."}[risk],
            "ai_model_version":indicator.get("model_version"),
            "baseline_used":bool(baseline.get("used")) if isinstance(baseline,dict) else False,
            "face_metrics":raw if isinstance(raw,dict) else None,"readiness_score":None}


def _validate_safe_result(event: dict) -> bool:
    result=event.get("result_json")
    if not isinstance(result,dict) or result.get("scan_id")!=event.get("scan_id"):
        return False
    indicator=result.get("drowsiness_indicator")
    return isinstance(indicator,dict) and isinstance(result.get("message"),str)


class DirectusOutboxPublisher:
    """Leased, at-least-once publisher core. No network client is configured here."""
    def __init__(self, store, adapter: DirectusWriteAdapter, *, max_attempts: int=8,
                 retry_base_seconds: float=1.0, lease_seconds: float=30.0):
        if not 1<=max_attempts<=12 or not 0<retry_base_seconds<=60 or not 0<lease_seconds<=600:
            raise ValueError("Directus publisher limits are invalid")
        self.store,self.adapter=store,adapter
        self.max_attempts,self.retry_base_seconds=max_attempts,retry_base_seconds
        self.lease_seconds=lease_seconds

    def run_once(self, publisher_id: str) -> str:
        event=self.store.claim_directus_outbox(publisher_id,lease_seconds=self.lease_seconds,
                                               max_attempts=self.max_attempts)
        if event is None:
            return "idle"
        safe_event=(event["event_type"]=="scan.completed" and _validate_safe_result(event)) or (
            event["event_type"]=="scan.failed" and event.get("result_json") is None)
        if not safe_event:
            self.store.fail_directus_outbox(event["event_id"],publisher_id,event["fencing_token"],
                "unsafe_result_contract",max_attempts=self.max_attempts,
                retry_base_seconds=self.retry_base_seconds)
            return "retry_scheduled"
        sync_event={key:value for key,value in event.items() if key!="result_json"}
        sync_event["v2_result_payload"]=(build_directus_v2_result_contract_v1(event["result_json"],event["directus_scan_id"])
            if event["event_type"]=="scan.completed" else None)
        try:
            remote=self.adapter.ensure_synced(sync_event)
        except Exception as exc:
            category=getattr(exc,"error_code",None)
            safe_codes={"directus_unavailable","directus_timeout","directus_forbidden",
                        "directus_schema_mismatch","remote_state_mismatch","unsafe_result_contract"}
            code=category if category in safe_codes else "directus_sync_failed"
            retried=self.store.fail_directus_outbox(event["event_id"],publisher_id,
                event["fencing_token"],code,max_attempts=self.max_attempts,
                retry_base_seconds=self.retry_base_seconds)
            return "retry_scheduled" if retried else "stale_lease"
        if not isinstance(remote,VerifiedDirectusState):
            remote=VerifiedDirectusState("","","invalid")
        expected_status="completed" if event["event_type"]=="scan.completed" else "failed"
        expected_result=event["directus_scan_id"] if event["event_type"]=="scan.completed" else None
        if remote.scan_id!=event["directus_scan_id"] or remote.status!=expected_status or remote.result_scan_id!=expected_result:
            retried=self.store.fail_directus_outbox(event["event_id"],publisher_id,
                event["fencing_token"],"remote_state_mismatch",max_attempts=self.max_attempts,
                retry_base_seconds=self.retry_base_seconds)
            return "retry_scheduled" if retried else "stale_lease"
        delivered=self.store.ack_directus_outbox(event["event_id"],publisher_id,
            event["fencing_token"],remote.scan_id,remote.status,remote.result_scan_id)
        return "delivered" if delivered else "verification_failed"
