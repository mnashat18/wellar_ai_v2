from __future__ import annotations

import asyncio
import contextvars
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import hashlib
import hmac
import logging
import os
import tempfile
import time
from pathlib import Path
from uuid import uuid4


from app.core.errors import WellarError
from app.core.logging import event
from app.core.errors import WellarError
from app.media.storage import MediaInputError, _MIME, _signature_ok
from app.media.deletion_process import run_bounded_storage_action
from app.observability.metrics import worker_metrics

logger=logging.getLogger("wellar.media.s3")


class S3ObjectMediaStore:
    """Private S3-compatible object store. Never generates public or signed URLs."""
    def __init__(self,root,queue_store,caps,*,bucket,region=None,endpoint_url=None,
                 max_total_bytes=1073741824,max_tenant_bytes=104857600,max_concurrent_uploads=4,
                 client=None,temporary_root=None,delete_connect_timeout_seconds:float=2.0,
                 delete_read_timeout_seconds:float=3.0,delete_operation_timeout_seconds:float=5.0,
                 scratch_sweep_max_entries:int=100):
        if not bucket: raise ValueError("object storage bucket is required")
        self.root=Path(temporary_root or root).resolve(); self.root.mkdir(parents=True,exist_ok=True)
        try:os.chmod(self.root,0o700)
        except OSError:pass
        self._workdir=Path(tempfile.mkdtemp(prefix="wellar-s3-",dir=self.root))
        try:os.chmod(self._workdir,0o700)
        except OSError:pass
        self.queue_store,self.caps,self.bucket=queue_store,caps,bucket
        self.max_total_bytes,self.max_tenant_bytes=max_total_bytes,max_tenant_bytes
        self.delete_connect_timeout_seconds=float(delete_connect_timeout_seconds)
        self.delete_read_timeout_seconds=float(delete_read_timeout_seconds)
        self.delete_operation_timeout_seconds=float(delete_operation_timeout_seconds)
        self.scratch_sweep_max_entries=max(1,min(int(scratch_sweep_max_entries),1000))
        import threading
        upload_workers=max(1,int(max_concurrent_uploads))
        self._slots=threading.BoundedSemaphore(max_concurrent_uploads)
        # Admission occurs before submission, so this executor's internal work
        # queue can never hold more than the admitted upload limit.
        self._upload_executor=ThreadPoolExecutor(max_workers=upload_workers,thread_name_prefix="wellar-s3-upload")
        self._background_upload_cleanups=set()
        self._active_upload_operations=set()
        self._health_lock=threading.Lock();self._health_checked=0.0;self._health_ok=False
        self._test_double_client=client is not None and not hasattr(client,"meta")
        self._deletion_credentials=None
        if client is not None and not self._test_double_client:
            credentials=getattr(getattr(client,"_request_signer",None),"_credentials",None)
            if credentials is None or hasattr(credentials,"_refresh_using"):
                raise ValueError("injected S3 SDK client must use static credentials for bounded deletion")
            frozen=credentials.get_frozen_credentials()
            self._deletion_credentials={"access_key":frozen.access_key,"secret_key":frozen.secret_key,
                "session_token":frozen.token}
        if client is None:
            import boto3
            from botocore.config import Config
            client=boto3.client("s3",region_name=region,endpoint_url=endpoint_url,
                config=Config(connect_timeout=self.delete_connect_timeout_seconds,read_timeout=self.delete_read_timeout_seconds,
                              max_pool_connections=16,retries={"total_max_attempts":1,"mode":"standard"}))
        self._upload_transfer_config=None
        if not self._test_double_client:
            from boto3.s3.transfer import TransferConfig
            # Keep each upload serial within one bounded executor thread.
            self._upload_transfer_config=TransferConfig(max_concurrency=1,use_threads=False)
        self.client=client

    def _upload_object(self,path,key,normalized,hexdigest,tenant_id):
        options={"ExtraArgs":{"ContentType":normalized,"Metadata":{"sha256":hexdigest,"tenant_ref":hashlib.sha256(tenant_id.encode()).hexdigest()[:16]}}}
        if self._upload_transfer_config is not None: options["Config"]=self._upload_transfer_config
        self.client.upload_file(path,self.bucket,key,**options)

    def _register_uploaded_media(self,*,media_id,tenant_id,subject_id,modality,content_type,size_bytes,sha256,relative_path):
        self.queue_store.register_media(media_id=media_id,tenant_id=tenant_id,subject_id=subject_id,
            modality=modality,content_type=content_type,size_bytes=size_bytes,sha256=sha256,
            relative_path=relative_path,max_total_bytes=self.max_total_bytes,max_tenant_bytes=self.max_tenant_bytes)

    async def _run_upload_blocking(self,fn,*args):
        context=contextvars.copy_context()
        return await asyncio.get_running_loop().run_in_executor(
            self._upload_executor,context.run,partial(fn,*args))

    async def _upload_and_register(self,*,path,key,normalized,hexdigest,tenant_id,subject_id,media_id,modality,size,started):
        try:
            await self._run_upload_blocking(self._upload_object,path,key,normalized,hexdigest,tenant_id)
        except Exception as exc:
            # The outcome can be ambiguous if the remote accepted bytes before
            # the connection failed. Delete by key before releasing admission.
            await self._run_upload_blocking(self._cleanup_unregistered_object,key)
            event(logger,"media.object.upload_failed",logging.ERROR,processing_stage="object_upload",modality=modality,error_code="object_storage_unavailable",exception_type=type(exc).__name__)
            raise WellarError("object_storage_unavailable","Object storage is temporarily unavailable",503,True) from None
        try:
            await self._run_upload_blocking(partial(self._register_uploaded_media,
                media_id=media_id,tenant_id=tenant_id,subject_id=subject_id,modality=modality,
                content_type=normalized,size_bytes=size,sha256=hexdigest,relative_path=key))
        except ValueError:
            await self._run_upload_blocking(self._cleanup_unregistered_object,key)
            raise MediaInputError("media_storage_quota","Media storage quota is full",429) from None
        except Exception:
            await self._run_upload_blocking(self._cleanup_unregistered_object,key)
            raise WellarError("media_metadata_unavailable","Media metadata could not be stored",503,True) from None
        event(logger,"media.object.stored",processing_stage="object_upload",modality=modality,size_bytes=size,
              tenant_ref=hashlib.sha256(tenant_id.encode()).hexdigest()[:16],
              duration_ms=round((time.perf_counter()-started)*1000,3))
        worker_metrics.increment("storage","stored")
        return {"media_ref":f"media://{hashlib.sha256(tenant_id.encode()).hexdigest()[:24]}/{media_id}",
                "modality":modality,"content_type":normalized,"size_bytes":size}

    async def _cleanup_cancelled_upload(self,operation,*,tenant_id,subject_id,path):
        try:
            try: result=await asyncio.shield(operation)
            except BaseException: result=None
            if isinstance(result,dict) and isinstance(result.get("media_ref"),str):
                try:
                    await self._run_upload_blocking(self.delete,result["media_ref"],tenant_id,subject_id)
                except Exception as exc:
                    # A registered reference remains tenant-scoped and can be
                    # removed by the existing retention/reconciliation path.
                    event(logger,"media.object.cancel_cleanup_failed",logging.ERROR,processing_stage="object_cleanup",
                          error_code="registered_media_cleanup_failed",exception_type=type(exc).__name__)
            worker_metrics.increment("storage","upload_cancelled")
        finally:
            Path(path).unlink(missing_ok=True)
            self._slots.release()

    def _track_background_cleanup(self,task):
        self._background_upload_cleanups.add(task)
        def finished(done):
            self._background_upload_cleanups.discard(done)
            if not done.cancelled():
                try: done.exception()
                except Exception: pass
        task.add_done_callback(finished)

    def _track_upload_operation(self,task):
        self._active_upload_operations.add(task)
        task.add_done_callback(self._active_upload_operations.discard)

    def _cleanup_unregistered_object(self, key: str) -> bool:
        """Compensate for an upload that could not be registered in queue metadata."""
        try:
            self._delete_object(key)
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
        transfer_cleanup=False
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
                operation=asyncio.create_task(self._upload_and_register(path=path,key=key,normalized=normalized,
                    hexdigest=hexdigest,tenant_id=tenant_id,subject_id=subject_id,media_id=media_id,
                    modality=modality,size=size,started=started))
                self._track_upload_operation(operation)
                try:
                    response=await asyncio.shield(operation)
                except asyncio.CancelledError:
                    # shield keeps the SDK call alive; a retained cleanup task
                    # owns the slot and tempfile until the ambiguous operation
                    # completes and any registered object is compensated.
                    cleanup=asyncio.create_task(self._cleanup_cancelled_upload(operation,
                        tenant_id=tenant_id,subject_id=subject_id,path=path))
                    self._track_background_cleanup(cleanup)
                    transfer_cleanup=True
                    raise
                return response
            finally:
                if not transfer_cleanup:Path(path).unlink(missing_ok=True)
        finally:
            if not transfer_cleanup:self._slots.release()

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
        try:self._delete_object(asset["relative_path"])
        except Exception:
            raise WellarError("object_storage_unavailable","Object storage is temporarily unavailable",503,True) from None
        self.queue_store.delete_media_record(asset["media_id"],tenant_id,subject_id)
        worker_metrics.increment("storage","deleted")
        return True

    def delete_for_reconciliation(self,reference,tenant_id,subject_id,*,timeout_seconds=None):
        """Delete object bytes only; lease-fenced queue cleanup owns metadata writes."""
        asset=self.queue_store.get_media_by_ref(reference,tenant_id,subject_id)
        if not asset:return False
        try:self._delete_object(asset["relative_path"],timeout_seconds=timeout_seconds)
        except Exception:
            raise WellarError("object_storage_unavailable","Object storage is temporarily unavailable",503,True) from None
        return True

    def _delete_object(self,key,timeout_seconds=None):
        if self._test_double_client:
            # Test doubles are synchronous and cannot own a production network socket.
            self.client.delete_object(Bucket=self.bucket,Key=key)
            return
        run_bounded_storage_action({"action":"s3_delete","bucket":self.bucket,"key":key,
            "region":self.client.meta.region_name,"endpoint_url":self.client.meta.endpoint_url,
            "connect_timeout":self.delete_connect_timeout_seconds,"read_timeout":self.delete_read_timeout_seconds,
            "credentials":self._deletion_credentials},
            min(self.delete_operation_timeout_seconds,float(timeout_seconds)) if timeout_seconds is not None else self.delete_operation_timeout_seconds)

    def purge_expired(self,cutoff_epoch):
        removed=0
        for asset in self.queue_store.list_expired_media(cutoff_epoch):
            try:self._delete_object(asset["relative_path"])
            except Exception:
                raise WellarError("object_storage_unavailable","Object storage is temporarily unavailable",503,True) from None
            if self.queue_store.delete_media_record(asset["media_id"],asset["tenant_id"],asset["subject_id"]):removed+=1
        worker_metrics.increment("storage","retention_deleted")
        return removed

    def sweep_stale_scratch(self,older_than_seconds=3600):
        cutoff=time.time()-max(60.0,float(older_than_seconds))
        try:return int(run_bounded_storage_action({"action":"sweep_s3_scratch","root":str(self.root),"current":str(self._workdir),
            "cutoff":cutoff,"maximum":self.scratch_sweep_max_entries},self.delete_operation_timeout_seconds).get("removed",0))
        except Exception:
            worker_metrics.increment("storage","scratch_delete_failed")
            raise

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
        self._upload_executor.shutdown(wait=True,cancel_futures=True)
        try:
            run_bounded_storage_action({"action":"remove_s3_workdir","root":str(self.root),"target":str(self._workdir)},
                                      self.delete_operation_timeout_seconds)
        except Exception as exc:
            worker_metrics.increment("storage","scratch_delete_failed")
            event(logger,"media.scratch.cleanup_failed",logging.ERROR,processing_stage="object_cleanup",
                  error_code="scratch_cleanup_failed",exception_type=type(exc).__name__)

    async def aclose(self):
        # The application lifespan calls this after request shutdown; waiting
        # here keeps cancellation compensation and executor work owned/reaped.
        while self._active_upload_operations or self._background_upload_cleanups:
            pending=tuple(self._active_upload_operations|self._background_upload_cleanups)
            if pending:await asyncio.gather(*pending,return_exceptions=True)
        self.close()
