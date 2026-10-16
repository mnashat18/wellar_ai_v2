"""Directus pull facade. All remote media access is by configured origin plus file ID."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import socket
import time
from typing import Any, Protocol
from urllib.parse import quote, urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.compat.legacy_projection import project_v1
from app.core.logging import event
from app.media.reconciliation import delete_with_retry
from app.media.extractors import InvalidMediaError
from app.media.storage import MediaInputError
from app.observability.metrics import worker_metrics

logger = logging.getLogger("wellar.directus")


class DirectusProcessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scan_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    user_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    video_file_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    audio_file_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    image_file_id: str | None = Field(default=None, pattern=r"^[0-9a-fA-F-]{36}$")
    task_metrics: dict[str, Any] = Field(default_factory=dict, max_length=32)
    device_info: dict[str, Any] = Field(default_factory=dict, max_length=32)
    environment_info: dict[str, Any] = Field(default_factory=dict, max_length=32)

    @model_validator(mode="after")
    def metadata_bounded(self):
        import json
        if len(json.dumps([self.task_metrics,self.device_info,self.environment_info],separators=(",",":"),default=str).encode()) > 16_384:
            raise ValueError("Directus process metadata exceeds limit")
        return self

    def queue_payload(self) -> dict:
        return {"_directus_pull": self.model_dump(mode="json"), "media_refs": []}


class DirectusClientProtocol(Protocol):
    def get_scan(self, scan_id: str) -> dict: ...
    def get_scan_media(self, scan_id: str) -> dict: ...
    def fetch_file(self, file_id: str, maximum_bytes: int) -> tuple[bytes, str]: ...
    def existing_result(self, scan_id: str) -> dict | None: ...
    def write_result(self, scan_id: str, projection: dict, deletion_proven: bool) -> None: ...
    def set_scan_status(self, scan_id: str, status: str) -> None: ...
    def delete_file(self, file_id: str) -> bool: ...


class DirectusHTTPClient:
    """HTTP client with a fixed Directus origin, no redirects, and public-IP checks."""
    def __init__(self, base_url: str, service_token: str, *, timeout: float = 3.0,
                 transport: httpx.BaseTransport | None = None):
        parsed = urlparse(base_url)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Directus origin is invalid")
        if parsed.scheme != "https" and transport is None:
            raise ValueError("Directus HTTP requires TLS")
        self.base_url = base_url.rstrip("/")
        self.host = parsed.hostname
        self.token = service_token
        self.timeout = timeout
        self.client = httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False, transport=transport)
        self._test_transport = transport is not None

    def _assert_public_origin(self) -> None:
        if self._test_transport:
            return
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(self.host, None, type=socket.SOCK_STREAM)}
        except OSError:
            raise DirectusUnavailable("directus_unavailable") from None
        if not addresses or any((lambda ip: ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified)(ipaddress.ip_address(addr)) for addr in addresses):
            raise DirectusUnavailable("directus_private_address_rejected")

    def _request(self, method: str, path: str, **kwargs):
        self._assert_public_origin()
        try:
            response = self.client.request(method, self.base_url + path,
                headers={"Authorization": "Bearer " + self.token, "Accept": "application/json", **kwargs.pop("headers", {})}, **kwargs)
        except httpx.TimeoutException:
            raise DirectusUnavailable("directus_timeout") from None
        except httpx.HTTPError:
            raise DirectusUnavailable("directus_unavailable") from None
        if response.status_code < 200 or response.status_code >= 300:
            _log_directus_response(response, path)
        if response.is_redirect:
            raise DirectusUnavailable("directus_redirect_rejected")
        if response.status_code >= 500 or response.status_code == 429:
            raise DirectusUnavailable("directus_unavailable")
        return response

    def get_scan(self, scan_id: str) -> dict:
        response = self._request("GET", f"/items/wellness_scans/{quote(scan_id, safe='')}", params={"fields": "id,user,status,business_profile"})
        if response.status_code != 200: _raise_response(response,"directus_scan_not_found",allow_not_found=False)
        return response.json().get("data", {})

    def get_scan_media(self, scan_id: str) -> dict:
        response = self._request("GET", "/items/scan_media", params={"filter[scan_id][_eq]": scan_id, "fields": "scan_id,video_file,audio_file,image_file", "limit": "2"})
        if response.status_code != 200: _raise_response(response,"directus_schema_mismatch",allow_not_found=False)
        rows=response.json().get("data", [])
        if len(rows) != 1: raise DirectusPermanentFailure("directus_invalid_media")
        return rows[0]

    def fetch_file(self, file_id: str, maximum_bytes: int) -> tuple[bytes, str]:
        self._assert_public_origin()
        try:
            with self.client.stream("GET",self.base_url+f"/assets/{quote(file_id,safe='')}",headers={"Authorization":"Bearer "+self.token,"Accept":"application/octet-stream"}) as response:
                if response.status_code!=200:
                    _log_directus_response(response,"/assets")
                    if response.is_redirect: raise DirectusUnavailable("directus_redirect_rejected")
                    _raise_response(response,"directus_invalid_media",allow_not_found=False)
                declared=response.headers.get("content-length")
                if declared and int(declared)>maximum_bytes: raise ValueError("directus_file_too_large")
                chunks=[]; size=0
                for chunk in response.iter_bytes():
                    size+=len(chunk)
                    if size>maximum_bytes: raise ValueError("directus_file_too_large")
                    chunks.append(chunk)
                if not size: raise ValueError("directus_file_empty")
                return b"".join(chunks),response.headers.get("content-type","").split(";",1)[0].lower()
        except httpx.TimeoutException:
            raise DirectusUnavailable("directus_timeout") from None
        except httpx.HTTPError:
            raise DirectusUnavailable("directus_unavailable") from None

    def existing_result(self, scan_id: str) -> dict | None:
        response=self._request("GET","/items/scan_results",params={"filter[scan_id][_eq]":scan_id,"limit":"1"})
        if response.status_code != 200: _raise_response(response,"directus_schema_mismatch",allow_not_found=False)
        rows=response.json().get("data",[])
        return rows[0] if rows else None

    def write_result(self, scan_id: str, projection: dict, deletion_proven: bool) -> None:
        existing=self.existing_result(scan_id)
        internal=projection.get("internal_analysis")
        if isinstance(internal,str):
            try: internal=json.loads(internal)
            except (TypeError,ValueError): internal={"internal_analysis":internal}
        details=dict(internal) if isinstance(internal,dict) else {"projection":projection}
        details["deletion_proven"]=bool(deletion_proven)
        explanation=projection.get("explanation")
        if not isinstance(explanation,str) or not explanation.strip():
            explanation="indicative, non-diagnostic, not a validated classifier"
        body={"scan_id":scan_id,
              "risk_level":projection.get("risk_level"),
              "readiness_score":None,
              "confidence":None,
              "ai_model_version":projection.get("ai_model_version"),
              "baseline_used":bool(projection.get("baseline_used",False)),
              "explanation":explanation,
              "internal_analysis":json.dumps(details,sort_keys=True,separators=(",",":"),allow_nan=False)}
        if existing and existing.get("id") is not None:
            response=self._request("PATCH",f"/items/scan_results/{quote(str(existing['id']),safe='')}",json=body)
        else:
            response=self._request("POST","/items/scan_results",json=body)
        if response.status_code not in {200,201}: _raise_response(response,"directus_schema_mismatch",allow_not_found=False)

    def set_scan_status(self, scan_id: str, status: str) -> None:
        response=self._request("PATCH",f"/items/wellness_scans/{quote(scan_id,safe='')}",json={"status":status})
        if response.status_code not in {200,201}: _raise_response(response,"directus_schema_mismatch",allow_not_found=False)

    def delete_file(self, file_id: str) -> bool:
        response=self._request("DELETE",f"/files/{quote(file_id,safe='')}")
        return response.status_code in {200,204,404}

    def close(self) -> None:
        self.client.close()


class DirectusUnavailable(RuntimeError):
    def __init__(self, error_code: str):
        super().__init__(error_code)
        self.error_code=error_code
        self.retryable=True


class DirectusPermanentFailure(DirectusUnavailable):
    def __init__(self, error_code: str):
        super().__init__(error_code)
        self.retryable=False


def _endpoint_name(path: str) -> str:
    parts=[part for part in path.strip("/").split("/") if part]
    if not parts:return "root"
    if parts[0]=="items" and len(parts)>1:return "items."+parts[1][:80]
    return parts[0][:80]


def _directus_error_details(response: httpx.Response) -> tuple[str,str]:
    try: body=response.json()
    except (ValueError,UnicodeDecodeError): return "http_error",""
    error_code="http_error"; fields=set()
    errors=body.get("errors",[]) if isinstance(body,dict) else []
    if not isinstance(errors,list):errors=[]
    for item in errors[:16]:
        extensions=item.get("extensions",{}) if isinstance(item,dict) else {}
        if not isinstance(extensions,dict):continue
        code=extensions.get("code")
        if error_code=="http_error" and isinstance(code,str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}",code):
            error_code=code
        for key in ("field","fields"):
            value=extensions.get(key)
            candidates=value if isinstance(value,list) else [value]
            for candidate in candidates:
                if isinstance(candidate,str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,79}",candidate):fields.add(candidate)
    return error_code,",".join(sorted(fields))


def _log_directus_response(response: httpx.Response, path: str) -> None:
    directus_code,field_names=_directus_error_details(response)
    error_code="directus_validation_error" if response.status_code in {400,422} else "directus_http_error"
    event(logger,"directus.http.error",logging.WARNING,status_code=response.status_code,
          endpoint_name=_endpoint_name(path),error_code=error_code,
          directus_error_code=directus_code,field_names=field_names)


def _raise_response(response: httpx.Response, fallback: str, *, allow_not_found: bool) -> None:
    status=response.status_code
    if status in {400,422}:raise DirectusPermanentFailure("directus_validation_error")
    if status in {401,403}:raise DirectusPermanentFailure("directus_forbidden")
    if status==404 and not allow_not_found:raise DirectusPermanentFailure(fallback)
    if status>=500 or status==429:raise DirectusUnavailable("directus_unavailable")
    if status==404:raise DirectusUnavailable(fallback)
    raise DirectusUnavailable("directus_http_error")


class DirectusPullProcessor:
    """Fetch by ID, validate ownership/attachment, run the regular processor, project and delete."""
    CAPS={"video":20*1024*1024,"image":5*1024*1024,"audio":8*1024*1024}
    MIME={"video":"video/mp4","image":"image/jpeg","audio":"audio/wav"}

    def __init__(self, client: DirectusClientProtocol, media_store, delegate):
        self.client,self.media_store,self.delegate=client,media_store,delegate
        self.media_store=media_store
        self.deletion_store=getattr(media_store,"queue_store",None)

    def __call__(self, payload: dict) -> dict:
        directus=payload.get("_directus_pull")
        if not directus:
            return self.delegate(payload)
        context=payload.get("_worker_context",{})
        scan_id=directus["scan_id"]
        ids={"video":directus["video_file_id"],"audio":directus["audio_file_id"]}
        if directus.get("image_file_id"): ids["image"]=directus["image_file_id"]
        try:
            return self._process_scan(payload,directus,context,scan_id,ids)
        except DirectusPermanentFailure as exc:
            self._terminal_failure(scan_id,ids.values(),context,exc.error_code)
            raise
        except (InvalidMediaError,MediaInputError) as exc:
            failure=DirectusPermanentFailure("directus_invalid_media")
            self._terminal_failure(scan_id,ids.values(),context,failure.error_code)
            raise failure from exc

    @staticmethod
    def _internal_analysis(value) -> dict:
        if isinstance(value,dict):return value
        if isinstance(value,str):
            try:
                decoded=json.loads(value)
                return decoded if isinstance(decoded,dict) else {}
            except (TypeError,ValueError):return {}
        return {}

    def _process_scan(self,payload,directus,context,scan_id,ids):
        remote_result=self.client.existing_result(scan_id)
        if remote_result:
            internal=self._internal_analysis(remote_result.get("internal_analysis"))
            self._delete_remote(ids.values())
            existing_projection=internal.get("projection",{})
            self.client.write_result(scan_id,{**existing_projection,"internal_analysis":internal,"risk_level":remote_result.get("risk_level"),"ai_model_version":remote_result.get("ai_model_version"),"baseline_used":remote_result.get("baseline_used",False)},True)
            remote_state=existing_projection.get("status")
            self.client.set_scan_status(scan_id,"retake_required" if remote_state=="retake_required" else "completed")
            return {"scan_id":scan_id,"status":"insufficient_evidence","drowsiness_indicator":internal.get("drowsiness_indicator",{}),"legacy_projection":existing_projection,"deletion_proven":True,"source":"directus_pull"}
        scan=self.client.get_scan(scan_id)
        if scan.get("id")!=scan_id or scan.get("user")!=directus["user_id"] or scan.get("business_profile")!=context.get("tenant_id") or scan.get("status")!="media_ready":
            raise DirectusPermanentFailure("remote_state_mismatch")
        relation=self.client.get_scan_media(scan_id)
        for modality,file_id in ids.items():
            if relation.get(f"{modality}_file")!=file_id:
                raise DirectusPermanentFailure("directus_invalid_media")
        refs=[]
        try:
            for modality,file_id in ids.items():
                try:blob,mime=self.client.fetch_file(file_id,self.CAPS[modality])
                except ValueError as exc:raise DirectusPermanentFailure("directus_invalid_media") from exc
                if len(blob)>self.CAPS[modality]: raise DirectusPermanentFailure("directus_invalid_media")
                if mime!=self.MIME[modality]: raise DirectusPermanentFailure("directus_invalid_media")
                saved=asyncio.run(self.media_store.save_stream(modality=modality,content_type=mime,content_length=str(len(blob)),chunks=_chunks(blob),tenant_id=context["tenant_id"],subject_id=context["subject_id"]))
                refs.append(saved["media_ref"])
                payload["media_refs"]=list(refs)
            try:
                result=self.delegate({"media_refs":refs,"consent":{"media_processing":True,"research_use":False},"task_metrics":directus.get("task_metrics",{}),"device_info":directus.get("device_info",{}),"environment_info":directus.get("environment_info",{}),"_worker_context":context})
            except InvalidMediaError as exc:
                raise DirectusPermanentFailure("directus_invalid_media") from exc
            projection=project_v1(result)
            status="retake_required" if projection["status"]=="retake_required" else "completed"
            self.client.write_result(scan_id,{**projection,"internal_analysis":{"projection":projection,"drowsiness_indicator":result.get("drowsiness_indicator"),"source":"v2_rule_based_indicator"}},False)
            self._delete_remote(ids.values())
            for ref in refs:
                if not delete_with_retry(self.media_store,ref,context.get("tenant_id",""),context.get("subject_id","")):
                    raise DirectusUnavailable("media_delete_failed")
            self.client.write_result(scan_id,{**projection,"internal_analysis":{"projection":projection,"drowsiness_indicator":result.get("drowsiness_indicator"),"source":"v2_rule_based_indicator"}},True)
            self.client.set_scan_status(scan_id,status)
            result["legacy_projection"]=projection
            result["deletion_proven"]=True
            result["source"]="directus_pull"
            return result
        finally:
            # The worker's failure cleanup sees payload.media_refs if local deletion fails.
            for ref in refs:
                if self.media_store.resolve(ref,context.get("tenant_id",""),context.get("subject_id","")):
                    if not delete_with_retry(self.media_store,ref,context.get("tenant_id",""),context.get("subject_id","")):
                        raise DirectusUnavailable("media_delete_failed")

    def _record_deletion_state(self,scan_id,state):
        recorder=getattr(self.deletion_store,"set_directus_deletion_state",None)
        if callable(recorder) and recorder(scan_id,state) is not True:
            raise RuntimeError("Directus deletion state was not persisted")

    def _terminal_failure(self,scan_id,file_ids,context,error_code):
        ids=list(dict.fromkeys(file_id for file_id in file_ids if isinstance(file_id,str) and file_id))
        try:self._record_deletion_state(scan_id,"pending")
        except Exception:
            worker_metrics.increment("storage","directus_deletion_failed")
            event(logger,"directus.deletion_state.persist_failed",logging.ERROR,error_code="directus_deletion_state_failed",exception_type="StorageError")
        try:self.client.set_scan_status(scan_id,"failed")
        except Exception as exc:
            event(logger,"directus.scan.failed_status_write",logging.ERROR,error_code="directus_status_write_failed",exception_type=type(exc).__name__)
        try:
            self._delete_remote(ids)
            self._record_deletion_state(scan_id,"proven")
        except Exception as exc:
            worker_metrics.increment("storage","directus_deletion_failed")
            event(logger,"directus.files.deletion_failed",logging.ERROR,error_code="directus_deletion_failed",exception_type=type(exc).__name__,attempt_count=3)

    def reconcile_pending_deletions(self):
        reader=getattr(self.deletion_store,"list_pending_directus_deletions",None)
        if not callable(reader):return 0
        completed=0
        for item in reader(limit=100):
            directus=item.get("directus_pull",{})
            file_ids=[directus.get(key) for key in ("video_file_id","audio_file_id","image_file_id")]
            try:
                self._delete_remote(file_ids)
                self._record_deletion_state(item["scan_id"],"proven")
                completed+=1
            except Exception as exc:
                worker_metrics.increment("storage","directus_deletion_failed")
                event(logger,"directus.files.deletion_failed",logging.ERROR,error_code="directus_deletion_failed",exception_type=type(exc).__name__,attempt_count=3)
        return completed

    def close(self):
        close=getattr(self.client,"close",None)
        if callable(close): close()
        close=getattr(self.delegate,"close",None)
        if callable(close): close()

    def _delete_remote(self, file_ids):
        failures=[]
        for file_id in file_ids:
            deleted=False
            for attempt in range(3):
                try:
                    if self.client.delete_file(file_id): deleted=True; break
                except Exception:
                    pass
                time.sleep(0.05*(2**attempt))
            if not deleted: failures.append(file_id)
        if failures:
            raise DirectusUnavailable("directus_delete_failed")


def _chunks(data: bytes):
    async def stream():
        yield data
    return stream()
