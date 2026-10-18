from __future__ import annotations

import logging
import time
import os

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.security import HTTPAuthorizationCredentials

from app.core.errors import WellarError
from app.core.logging import event
from app.media.storage import MediaInputError
from app.scans.schemas import ScanResult, ScanStatus, ScanSubmission
from app.scans.labels import KSSLabelSubmission
from app.scans.measurements import ClientMeasurements
from app.drowsiness_indicator import classify as classify_drowsiness, load_thresholds, personal_closure_deviation, fresh_baseline_values
from app.scans.service import ScanService
from app.security.auth import (AuthenticationProviderUnavailable, DirectusPermissionDenied,
                               DirectusRequestRejected, TenantAuthorizationDenied, Principal, bearer)
from app.compat.directus_facade import DirectusProcessRequest

logger=logging.getLogger("wellar.api")
router=APIRouter(prefix="/api/v1")


def get_principal(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Principal:
    token_present=bool(credentials and credentials.credentials)
    event(logger,"auth.token_present",error_code="present" if token_present else "missing")
    if not credentials:
        event(logger,"auth.rejected",error_code="missing_bearer_token")
        raise HTTPException(401,detail="authentication required",headers={"WWW-Authenticate":"Bearer"})
    try:
        principal=request.app.state.authenticator.authenticate(credentials.credentials)
    except TenantAuthorizationDenied:
        event(logger,"auth.rejected",error_code="tenant_access_denied")
        raise HTTPException(403,detail={"code":"tenant_access_denied","message":"Active workspace access could not be verified","retryable":False}) from None
    except DirectusPermissionDenied as exc:
        event(logger,"auth.rejected",error_code=exc.error_code,processing_stage=exc.stage)
        raise HTTPException(502,detail={"code":exc.error_code,"message":"Identity provider permissions do not allow this verification","retryable":False}) from None
    except DirectusRequestRejected as exc:
        event(logger,"auth.rejected",error_code=exc.error_code,processing_stage=exc.stage)
        raise HTTPException(502,detail={"code":exc.error_code,"message":"Identity provider rejected the verification request","retryable":False}) from None
    except AuthenticationProviderUnavailable:
        event(logger,"auth.rejected",error_code="identity_provider_unavailable")
        raise HTTPException(503,detail={"code":"identity_provider_unavailable","message":"Identity service is temporarily unavailable","retryable":True}) from None
    if not principal:
        event(logger,"auth.rejected",error_code="invalid_or_expired_token")
        raise HTTPException(401,detail="invalid credentials",headers={"WWW-Authenticate":"Bearer"})
    event(logger,"auth.accepted")
    limiter=getattr(request.app.state,"rate_limiter",None)
    if limiter is not None:
        allowed,retry_after=limiter.consume(principal.tenant_id)
        if not allowed:
            raise HTTPException(429,detail={"code":"tenant_rate_limited","message":"Tenant request limit reached","retryable":True},headers={"Retry-After":str(retry_after)})
    return principal


@router.post("/process",status_code=202)
def directus_process(payload: DirectusProcessRequest, response: Response, request: Request,
                     principal: Principal=Depends(get_principal)):
    if not request.app.state.directus_pull_enabled:
        raise HTTPException(503,detail={"code":"directus_pull_disabled","message":"Directus processing is not enabled","retryable":False})
    if principal.subject_id != payload.user_id:
        raise HTTPException(403,detail={"code":"scan_owner_mismatch","message":"Authenticated user does not own this scan","retryable":False})
    record,created=ScanService(request.app.state.store).submit(principal,payload.scan_id,payload.queue_payload())
    response.headers["Location"]=f"/api/v1/scans/{record['scan_id']}"
    return {"scan_id":payload.scan_id,"status":"accepted" if created else record["status"],"created":created}


@router.post("/media/{modality}",status_code=201)
async def upload_media(modality: str,request: Request,principal: Principal=Depends(get_principal)):
    content_type=request.headers.get("Content-Type","")
    try:
        return await request.app.state.media_store.save_stream(modality=modality,content_type=content_type,content_length=request.headers.get("Content-Length"),chunks=request.stream(),tenant_id=principal.tenant_id,subject_id=principal.subject_id)
    except MediaInputError:
        raise


@router.delete("/media",status_code=204)
def delete_media(request: Request, media_ref: str=Query(...,max_length=256),
                 principal: Principal=Depends(get_principal)):
    deleted=request.app.state.media_store.delete(media_ref,principal.tenant_id,principal.subject_id)
    if not deleted:
        raise HTTPException(404,detail={"code":"media_not_found","message":"Media reference is unavailable"})
    return Response(status_code=204)


@router.post("/scans",status_code=202)
def submit_scan(payload: ScanSubmission, response: Response, request: Request,
                idempotency_key: str | None=Header(None,alias="Idempotency-Key"),
                principal: Principal=Depends(get_principal)):
    if not idempotency_key:
        raise HTTPException(400,detail={"code":"idempotency_key_required","message":"Idempotency-Key is required"})
    modalities=[]; manifest=[]
    for reference in payload.media_refs:
        asset=request.app.state.media_store.resolve(reference,principal.tenant_id,principal.subject_id)
        if asset is None or not asset.get("available"):
            raise HTTPException(404,detail={"code":"media_not_found","message":"Media reference is unavailable"})
        modalities.append(asset["modality"])
        manifest.append({"media_ref":reference,"modality":asset["modality"]})
    if len(set(modalities))!=len(modalities):
        raise HTTPException(422,detail={"code":"duplicate_media_modality","message":"At most one asset per modality is accepted per scan"})
    try:
        scan_payload=payload.model_dump(mode="json")
        scan_payload["_media_manifest"]=manifest
        if getattr(request.app.state.store,"shared_monotonic_clock",False):
            scan_payload["_accepted_monotonic"]=time.perf_counter()
        traceparent=request.headers.get("traceparent","")
        if len(traceparent)<=256 and "\r" not in traceparent and "\n" not in traceparent:
            scan_payload["_traceparent"]=traceparent
        queue_submit_started=time.perf_counter()
        record,created=ScanService(request.app.state.store).submit(principal,idempotency_key,scan_payload)
        event(logger,"scan.queue.accepted",logging.INFO,processing_stage="queue_acceptance",scan_id=record["scan_id"],duration_ms=round((time.perf_counter()-queue_submit_started)*1000,3),status_code=202)
    except ValueError as exc:
        raise HTTPException(400,detail={"code":"invalid_request","message":str(exc)}) from None
    except WellarError:
        raise
    response.headers["Location"]=f"/api/v1/scans/{record['scan_id']}"
    request.app.state.metrics.increment("stage","accepted")
    return {"scan_id":record["scan_id"],"status":record["status"],"created":created}


@router.get("/scans/{scan_id}",response_model=ScanStatus)
def get_status(scan_id: str, request: Request, principal: Principal=Depends(get_principal)):
    record=request.app.state.store.get_scan(scan_id,principal.tenant_id,principal.subject_id)
    if not record: raise HTTPException(404,detail={"code":"scan_not_found","message":"Scan not found"})
    return {"scan_id":record["scan_id"],"status":record["status"],"created_at":record["created_at"],"updated_at":record["updated_at"],"retryable":record["status"]=="queued"}


@router.get("/scans/{scan_id}/result",response_model=ScanResult)
def get_result(scan_id: str, request: Request, principal: Principal=Depends(get_principal)):
    result=request.app.state.store.get_result(scan_id,principal.tenant_id,principal.subject_id)
    if not result: raise HTTPException(404,detail={"code":"result_unavailable","message":"Result is not available"})
    return result


@router.post("/scans/measurements")
def submit_client_measurements(payload: ClientMeasurements, request: Request,
                               principal: Principal=Depends(get_principal)):
    thresholds=load_thresholds()
    values=payload.model_dump(exclude={"audio_quality_status"})
    history_reader=getattr(getattr(request.app.state.media_store,"queue_store",None),"get_indicator_history",None)
    history=[]
    if callable(history_reader):
        try: history=history_reader(principal.tenant_id,principal.subject_id)
        except Exception: history=[]
    prior,baseline_reason=fresh_baseline_values(history,int(thresholds["baseline_freshness_window_days"]))
    minimum=int(thresholds["baseline_minimum_prior_scans"])
    z_score,count=personal_closure_deviation(values["short_clip_eye_closure_fraction"],prior,minimum)
    indicator=classify_drowsiness(values,thresholds,audio_quality_status=payload.audio_quality_status,
        baseline_z_score=z_score,minimum_prior_scans=minimum,prior_scans=count)
    if "personal_baseline" not in indicator:
        indicator["baseline_reason_code"]=baseline_reason or "baseline_not_ready"
    return {"source":"client_measurements","measurement_provenance":"client-supplied, unverified",
        "drowsiness_indicator":indicator,"label":"indicative, non-diagnostic, not a validated classifier"}


@router.post("/scans/{scan_id}/labels/kss",status_code=202)
def attach_kss_label(scan_id: str, payload: KSSLabelSubmission, request: Request,
                     principal: Principal=Depends(get_principal)):
    store=request.app.state.store
    scan=store.get_scan(scan_id,principal.tenant_id,principal.subject_id)
    if not scan or scan.get("status")!="completed":
        raise HTTPException(404,detail={"code":"completed_scan_not_found","message":"Completed scan not found"})
    if not payload.training_consent:
        return {"scan_id":scan_id,"label_status":"not_retained","training_consent":False}
    try:
        retention_days=int(os.environ.get("WELLAR_LABEL_RETENTION_DAYS", ""))
    except ValueError:
        retention_days=0
    if retention_days < 1:
        raise HTTPException(503,detail={"code":"label_retention_unconfigured","message":"Label retention must be configured before consented labels can be stored"})
    result=store.get_result(scan_id,principal.tenant_id,principal.subject_id)
    evidence=result.get("evidence",{}) if isinstance(result,dict) else {}
    modalities=evidence.get("modalities",{}) if isinstance(evidence,dict) else {}
    features={}
    allowed={"short_clip_eye_closure_fraction","blink_rate_per_minute","closure_duration_mean_seconds","closure_duration_max_seconds","face_coverage","mean_sampled_luminance","mean_blur_variance","eye_aspect_ratio","laplacian_variance","gray_mean"}
    for modality in ("video","image"):
        envelope=modalities.get(modality,{})
        values=envelope.get("features",{}) if isinstance(envelope,dict) else {}
        for name,value in values.items() if isinstance(values,dict) else ():
            if name in allowed and isinstance(value,(int,float)) and not isinstance(value,bool):
                features[f"{modality}.{name}"]=value
    store.store_training_label(scan_id,principal.tenant_id,principal.subject_id,payload.kss,features,
        fatigue=payload.fatigue,focus=payload.focus,shift=payload.shift,time_of_day=payload.time_of_day,
        expires_at=time.time()+retention_days*86400,consent_text_version="wellar-v2-training-consent-v1")
    return {"scan_id":scan_id,"label_status":"retained_features_only","training_consent":True,"feature_count":len(features)}


@router.delete("/scans/{scan_id}/labels",status_code=204)
def withdraw_scan_label(scan_id: str, request: Request, principal: Principal=Depends(get_principal)):
    request.app.state.store.delete_training_label(scan_id,principal.tenant_id,principal.subject_id)


@router.delete("/me/labels",status_code=204)
def withdraw_all_labels(request: Request, principal: Principal=Depends(get_principal)):
    request.app.state.store.delete_training_labels(principal.tenant_id,principal.subject_id)
