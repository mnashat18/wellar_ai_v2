from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import re
import time
import threading
from pathlib import Path
from uuid import uuid4

from app.core.errors import WellarError
from app.core.logging import event
from app.observability.metrics import worker_metrics

logger=logging.getLogger("wellar.media.intake")
_MIME={
    "video":{"video/mp4":(".mp4","mp4"),"video/x-msvideo":(".avi","avi")},
    "image":{"image/jpeg":(".jpg","jpeg"),"image/png":(".png","png")},
    "audio":{"audio/wav":(".wav","wav"),"audio/x-wav":(".wav","wav")},
}


class MediaInputError(WellarError):
    def __init__(self, code: str, message: str, status: int=422):
        super().__init__(code,message,status,False)


def _signature_ok(modality: str, content_type: str, head: bytes) -> bool:
    if modality=="image": return head.startswith(b"\xff\xd8\xff") if content_type=="image/jpeg" else head.startswith(b"\x89PNG\r\n\x1a\n")
    if modality=="audio": return len(head)>=12 and head[:4]==b"RIFF" and head[8:12]==b"WAVE"
    if content_type=="video/mp4": return len(head)>=12 and head[4:8]==b"ftyp"
    return len(head)>=12 and head[:4]==b"RIFF" and head[8:12]==b"AVI "


class LocalMediaStore:
    """Single-host development object store. References disclose no filesystem path."""
    def __init__(self, root: str, queue_store, caps: dict[str,int], max_total_bytes:int=1024*1024*1024,max_tenant_bytes:int=100*1024*1024,max_concurrent_uploads:int=4):
        self.root=Path(root).resolve()
        self.root.mkdir(parents=True,exist_ok=True)
        try: os.chmod(self.root,0o700)
        except OSError: pass
        self.queue_store=queue_store
        self.caps=caps
        self.max_total_bytes=max_total_bytes; self.max_tenant_bytes=max_tenant_bytes
        self._upload_slots=threading.BoundedSemaphore(max_concurrent_uploads)

    def _tenant_dir(self, tenant_id: str) -> tuple[str,Path]:
        tenant_hash=hashlib.sha256(tenant_id.encode()).hexdigest()[:24]
        path=self.root/tenant_hash
        path.mkdir(parents=True,exist_ok=True)
        try: os.chmod(path,0o700)
        except OSError: pass
        return tenant_hash,path

    async def save_stream(self, *, modality: str, content_type: str, content_length: str | None,
                          chunks, tenant_id: str, subject_id: str) -> dict:
        if not self._upload_slots.acquire(blocking=False):
            raise MediaInputError("upload_capacity_exceeded","Media intake is temporarily at capacity",429)
        try:
            return await self._save_stream_inner(modality=modality,content_type=content_type,content_length=content_length,chunks=chunks,tenant_id=tenant_id,subject_id=subject_id)
        finally:
            self._upload_slots.release()

    async def _save_stream_inner(self, *, modality: str, content_type: str, content_length: str | None,
                          chunks, tenant_id: str, subject_id: str) -> dict:
        if modality not in _MIME: raise MediaInputError("unsupported_modality","Supported media modalities are video, image and audio",415)
        normalized=content_type.split(";",1)[0].strip().lower()
        if normalized not in _MIME[modality]: raise MediaInputError("unsupported_media_type","Content-Type is not allowed for this modality",415)
        cap=self.caps[modality]
        if content_length:
            try: declared=int(content_length)
            except ValueError: raise MediaInputError("invalid_content_length","Content-Length is invalid",400) from None
            if declared<1: raise MediaInputError("empty_media","Media body must not be empty")
            if declared>cap: raise MediaInputError("media_too_large","Media exceeds the configured size limit",413)
        tenant_hash,directory=self._tenant_dir(tenant_id)
        media_id=uuid4().hex
        suffix,_=_MIME[modality][normalized]
        partial=directory/("."+media_id+".partial")
        final=directory/(media_id+suffix)
        fd=os.open(partial,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        digest=hashlib.sha256(); size=0; head=bytearray(); started=time.perf_counter()
        try:
            with os.fdopen(fd,"wb") as target:
                async for chunk in chunks:
                    if not chunk: continue
                    size+=len(chunk)
                    if size>cap: raise MediaInputError("media_too_large","Media exceeds the configured size limit",413)
                    if len(head)<32: head.extend(chunk[:32-len(head)])
                    digest.update(chunk)
                    await asyncio.to_thread(target.write,chunk)
                await asyncio.to_thread(target.flush)
                await asyncio.to_thread(os.fsync,target.fileno())
            if not size: raise MediaInputError("empty_media","Media body must not be empty")
            if content_length and size!=declared:raise MediaInputError("content_length_mismatch","Media body length does not match Content-Length",400)
            if not _signature_ok(modality,normalized,bytes(head)):
                raise MediaInputError("invalid_media_signature","Media bytes do not match the declared content type")
            os.replace(partial,final)
            relative=final.relative_to(self.root).as_posix()
            try:
                self.queue_store.register_media(media_id=media_id,tenant_id=tenant_id,subject_id=subject_id,modality=modality,content_type=normalized,size_bytes=size,sha256=digest.hexdigest(),relative_path=relative,max_total_bytes=self.max_total_bytes,max_tenant_bytes=self.max_tenant_bytes)
            except ValueError as exc:
                final.unlink(missing_ok=True)
                raise MediaInputError("media_storage_quota","Media storage quota is full",429) from None
            except Exception:
                final.unlink(missing_ok=True)
                raise
            reference=f"media://{tenant_hash}/{media_id}"
            tenant_ref=hashlib.sha256(tenant_id.encode()).hexdigest()[:16]
            event(logger,"media.upload.stored",processing_stage="media_intake",duration_ms=round((time.perf_counter()-started)*1000,3),modality=modality,size_bytes=size,tenant_ref=tenant_ref)
            worker_metrics.increment("storage","stored")
            return {"media_ref":reference,"modality":modality,"content_type":normalized,"size_bytes":size}
        except BaseException:
            partial.unlink(missing_ok=True)
            raise

    def resolve(self, reference: str, tenant_id: str, subject_id: str, *, verify_integrity: bool=False) -> dict | None:
        item=self.queue_store.get_media_by_ref(reference,tenant_id,subject_id)
        if not item: return None
        relative=Path(item["relative_path"])
        candidate=self.root/relative
        if candidate.is_symlink(): return None
        path=candidate.resolve()
        if self.root not in path.parents: return None
        item["path"]=path
        item["available"]=path.is_file()
        if verify_integrity and item["available"]:
            digest=hashlib.sha256(); size=0
            with path.open("rb") as source:
                for chunk in iter(lambda:source.read(1024*1024),b""):
                    digest.update(chunk); size+=len(chunk)
            item["integrity_ok"]=size==item["size_bytes"] and hmac.compare_digest(digest.hexdigest(),item["sha256"])
        return item

    def delete(self,reference: str,tenant_id: str,subject_id: str)->bool:
        item=self.queue_store.get_media_by_ref(reference,tenant_id,subject_id)
        if not item:return False
        path=self.root/item["relative_path"]
        path.unlink(missing_ok=True)
        record=self.queue_store.delete_media_record(item["media_id"],tenant_id,subject_id)
        if record:worker_metrics.increment("storage","deleted")
        return bool(record)

    def purge_expired(self,cutoff_epoch:float)->int:
        removed=0
        for item in self.queue_store.list_expired_media(cutoff_epoch):
            if self.delete(f"media://{hashlib.sha256(item['tenant_id'].encode()).hexdigest()[:24]}/{item['media_id']}",item["tenant_id"],item["subject_id"]):removed+=1
        worker_metrics.increment("storage","retention_deleted")
        return removed

    def sweep_stale_scratch(self,older_than_seconds:float=3600)->int:
        cutoff=time.time()-max(60.0,float(older_than_seconds));removed=0
        for path in self.root.rglob("*.partial"):
            try:
                if path.is_file() and path.stat().st_mtime<cutoff:
                    path.unlink(missing_ok=True);removed+=1
            except OSError:
                worker_metrics.increment("storage","scratch_delete_failed")
        return removed

    def close(self)->None: pass

    def release(self,asset:dict)->None: pass

    def readiness(self)->bool:
        return self.root.is_dir() and os.access(self.root,os.W_OK)
