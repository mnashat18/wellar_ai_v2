from __future__ import annotations

import hashlib
import hmac
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from uuid import uuid4


from app.core.errors import WellarError
from app.core.logging import event
from app.core.errors import WellarError
from app.media.storage import MediaInputError, _MIME, _signature_ok
from app.observability.metrics import worker_metrics

logger=logging.getLogger("wellar.media.s3")


class S3ObjectMediaStore:
    """Private S3-compatible object store. Never generates public or signed URLs."""
    def __init__(self,root,queue_store,caps,*,bucket,region=None,endpoint_url=None,
                 max_total_bytes=1073741824,max_tenant_bytes=104857600,max_concurrent_uploads=4,
                 client=None,temporary_root=None):
        if not bucket: raise ValueError("object storage bucket is required")
        self.root=Path(temporary_root or root).resolve(); self.root.mkdir(parents=True,exist_ok=True)
        try:os.chmod(self.root,0o700)
        except OSError:pass
        self._workdir=Path(tempfile.mkdtemp(prefix="wellar-s3-",dir=self.root))
        try:os.chmod(self._workdir,0o700)
        except OSError:pass
        self.queue_store,self.caps,self.bucket=queue_store,caps,bucket
        self.max_total_bytes,self.max_tenant_bytes=max_total_bytes,max_tenant_bytes
        import threading
        self._slots=threading.BoundedSemaphore(max_concurrent_uploads)
        self._health_lock=threading.Lock();self._health_checked=0.0;self._health_ok=False
        if client is None:
            import boto3
            from botocore.config import Config
            client=boto3.client("s3",region_name=region,endpoint_url=endpoint_url,
                config=Config(connect_timeout=3,read_timeout=20,max_pool_connections=16,retries={"max_attempts":2}))
        self.client=client

    def _cleanup_unregistered_object(self, key: str) -> bool:
        """Compensate for an upload that could not be registered in queue metadata."""
        try:
            self.client.delete_object(Bucket=self.bucket,Key=key)
            return True
        except Exception as exc:
            # Do not hide orphan risk, but never log the SDK message, key, or tenant.
            event(logger,"media.object.compensation_failed",logging.ERROR,
                  processing_stage="object_cleanup",error_code="orphan_object_possible",
                  exception_type=type(exc).__name__)
            worker_metrics.increment("storage","compensation_failed")
            return False

    async def save_stream(self,*,modality,content_type,content_length,chunks,tenant_id,subject_id):
        if not self._slots.acquire(blocking=False): raise MediaInputError("upload_capacity_exceeded","Media intake is temporarily at capacity",429)
        started=time.perf_counter()
        try:
            if modality not in _MIME: raise MediaInputError("unsupported_modality","Supported media modalities are video, image and audio",415)
            normalized=content_type.split(";",1)[0].strip().lower()
            if normalized not in _MIME[modality]: raise MediaInputError("unsupported_media_type","Content-Type is not allowed for this modality",415)
            cap=self.caps[modality]
            if content_length:
                try: declared=int(content_length)
                except ValueError: raise MediaInputError("invalid_content_length","Content-Length is invalid",400) from None
                if declared<1: raise MediaInputError("empty_media","Media body must not be empty")
                if declared>cap: raise MediaInputError("media_too_large","Media exceeds the configured size limit",413)
            tenant_hash=hashlib.sha256(tenant_id.encode()).hexdigest()[:24]
            media_id=uuid4().hex; suffix=_MIME[modality][normalized][0]
            key=f"{tenant_hash}/{media_id}{suffix}"; digest=hashlib.sha256(); head=bytearray(); size=0
            fd,path=tempfile.mkstemp(prefix="upload-",dir=self._workdir); os.chmod(path,0o600)
            try:
                with os.fdopen(fd,"wb") as output:
                    async for chunk in chunks:
                        if not chunk: continue
                        size+=len(chunk)
                        if size>cap: raise MediaInputError("media_too_large","Media exceeds the configured size limit",413)
                        if len(head)<32: head.extend(chunk[:32-len(head)])
                        digest.update(chunk); output.write(chunk)
                    output.flush(); os.fsync(output.fileno())
                if not size: raise MediaInputError("empty_media","Media body must not be empty")
                if content_length and size!=declared:raise MediaInputError("content_length_mismatch","Media body length does not match Content-Length",400)
                if not _signature_ok(modality,normalized,bytes(head)): raise MediaInputError("invalid_media_signature","Media bytes do not match the declared content type")
                hexdigest=digest.hexdigest()
                try:
                    self.client.upload_file(path,self.bucket,key,ExtraArgs={"ContentType":normalized,"Metadata":{"sha256":hexdigest,"tenant_ref":hashlib.sha256(tenant_id.encode()).hexdigest()[:16]},"ServerSideEncryption":"AES256"})
                except Exception as exc:
                    event(logger,"media.object.upload_failed",logging.ERROR,processing_stage="object_upload",modality=modality,error_code="object_storage_unavailable",exception_type=type(exc).__name__)
                    raise WellarError("object_storage_unavailable","Object storage is temporarily unavailable",503,True) from None
                try:
                    self.queue_store.register_media(media_id=media_id,tenant_id=tenant_id,subject_id=subject_id,modality=modality,content_type=normalized,size_bytes=size,sha256=hexdigest,relative_path=key,max_total_bytes=self.max_total_bytes,max_tenant_bytes=self.max_tenant_bytes)
                except ValueError:
                    self._cleanup_unregistered_object(key)
                    raise MediaInputError("media_storage_quota","Media storage quota is full",429) from None
                except Exception:
                    self._cleanup_unregistered_object(key)
                    raise WellarError("media_metadata_unavailable","Media metadata could not be stored",503,True) from None
                event(logger,"media.object.stored",processing_stage="object_upload",modality=modality,size_bytes=size,tenant_ref=hashlib.sha256(tenant_id.encode()).hexdigest()[:16],duration_ms=round((time.perf_counter()-started)*1000,3))
                worker_metrics.increment("storage","stored")
                return {"media_ref":f"media://{tenant_hash}/{media_id}","modality":modality,"content_type":normalized,"size_bytes":size}
            finally: Path(path).unlink(missing_ok=True)
        finally: self._slots.release()

    def resolve(self,reference,tenant_id,subject_id,*,verify_integrity=False):
        started=time.perf_counter()
        asset=self.queue_store.get_media_by_ref(reference,tenant_id,subject_id)
        if not asset:return None
        try:
            head=self.client.head_object(Bucket=self.bucket,Key=asset["relative_path"])
            asset["available"]=int(head.get("ContentLength",-1))==int(asset["size_bytes"])
            asset["path"]=None; asset["integrity_ok"]=asset["available"]
            if verify_integrity and asset["available"]:
                fd,path=tempfile.mkstemp(prefix="object-",dir=self._workdir); os.close(fd); os.chmod(path,0o600)
                try:
                    self.client.download_file(self.bucket,asset["relative_path"],path)
                    digest=hashlib.sha256()
                    with open(path,"rb") as source:
                        for chunk in iter(lambda:source.read(1024*1024),b""): digest.update(chunk)
                    asset["integrity_ok"]=hmac.compare_digest(digest.hexdigest(),asset["sha256"])
                    asset["path"]=Path(path)
                    asset["_temporary_path"]=path
                except Exception:
                    Path(path).unlink(missing_ok=True); raise
            event(logger,"media.object.resolved",processing_stage="object_download" if verify_integrity else "object_head",modality=asset.get("modality"),size_bytes=asset.get("size_bytes"),tenant_ref=hashlib.sha256(tenant_id.encode()).hexdigest()[:16],duration_ms=round((time.perf_counter()-started)*1000,3))
            return asset
        except Exception:
            event(logger,"media.object.resolve_failed",logging.WARNING,processing_stage="object_download",modality=asset.get("modality"),error_code="object_unavailable")
            worker_metrics.increment("storage","resolve_failed")
            raise WellarError("object_storage_unavailable","Object storage is temporarily unavailable",503,True) from None

    def release(self,asset):
        temporary=asset.get("_temporary_path")
        if temporary: Path(temporary).unlink(missing_ok=True)

    def delete(self,reference,tenant_id,subject_id):
        asset=self.queue_store.get_media_by_ref(reference,tenant_id,subject_id)
        if not asset:return False
        try:self.client.delete_object(Bucket=self.bucket,Key=asset["relative_path"])
        except Exception:
            raise WellarError("object_storage_unavailable","Object storage is temporarily unavailable",503,True) from None
        self.queue_store.delete_media_record(asset["media_id"],tenant_id,subject_id)
        worker_metrics.increment("storage","deleted")
        return True

    def purge_expired(self,cutoff_epoch):
        removed=0
        for asset in self.queue_store.list_expired_media(cutoff_epoch):
            try:self.client.delete_object(Bucket=self.bucket,Key=asset["relative_path"])
            except Exception:
                raise WellarError("object_storage_unavailable","Object storage is temporarily unavailable",503,True) from None
            if self.queue_store.delete_media_record(asset["media_id"],asset["tenant_id"],asset["subject_id"]):removed+=1
        worker_metrics.increment("storage","retention_deleted")
        return removed

    def sweep_stale_scratch(self,older_than_seconds=3600):
        cutoff=time.time()-max(60.0,float(older_than_seconds));removed=0
        for path in self.root.iterdir():
            try:
                if path==self._workdir or path.name.startswith("wellar-s3-") is False or path.stat().st_mtime>=cutoff:
                    continue
                if path.is_dir():
                    shutil.rmtree(path);removed+=1
                elif path.is_file():
                    path.unlink(missing_ok=True);removed+=1
            except OSError:
                worker_metrics.increment("storage","scratch_delete_failed")
        return removed

    def readiness(self):
        with self._health_lock:
            if time.monotonic()-self._health_checked<5:return self._health_ok
            self._health_checked=time.monotonic()
            try:self.client.head_bucket(Bucket=self.bucket);self._health_ok=True
            except Exception:
                self._health_ok=False
                event(logger,"media.object.health_failed",logging.WARNING,processing_stage="object_health",error_code="object_storage_unavailable")
            return self._health_ok

    def close(self):
        shutil.rmtree(self._workdir,ignore_errors=True)
