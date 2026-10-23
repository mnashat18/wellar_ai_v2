from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.security import HTTPAuthorizationCredentials

from app.core.errors import WellarError
from app.core.logging import event
from app.media.storage import MediaInputError
from app.scans.schemas import ScanResult, ScanStatus, ScanSubmission
from app.scans.service import ScanService
from app.security.auth import AuthenticationProviderUnavailable, Principal, bearer

logger=logging.getLogger("wellar.api")
router=APIRouter(prefix="/api/v1")


def get_principal(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Principal:
    if not credentials:
        raise HTTPException(401,detail="authentication required",headers={"WWW-Authenticate":"Bearer"})
    try:
        principal=request.app.state.authenticator.authenticate(credentials.credentials)
    except AuthenticationProviderUnavailable:
        raise HTTPException(503,detail={"code":"identity_provider_unavailable","message":"Identity service is temporarily unavailable","retryable":True}) from None
    if not principal:
        raise HTTPException(401,detail="invalid credentials",headers={"WWW-Authenticate":"Bearer"})
    return principal


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
