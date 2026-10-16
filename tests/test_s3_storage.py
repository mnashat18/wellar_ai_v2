import asyncio
import hashlib
import pytest

from app.media.s3_storage import S3ObjectMediaStore
from tests.media_fixtures import wav_bytes


class FakeS3:
    def __init__(self):self.objects={}
    def upload_file(self,path,bucket,key,ExtraArgs):
        self.objects[(bucket,key)]=open(path,"rb").read()
    def head_object(self,*,Bucket,Key):
        data=self.objects[(Bucket,Key)]
        return {"ContentLength":len(data)}
    def download_file(self,bucket,key,path):open(path,"wb").write(self.objects[(bucket,key)])
    def delete_object(self,*,Bucket,Key):self.objects.pop((Bucket,Key),None)


class BrokenS3(FakeS3):
    def upload_file(self,path,bucket,key,ExtraArgs):raise RuntimeError("access_key=SECRET-AWS-TOKEN")


class BrokenDeleteS3(FakeS3):
    def delete_object(self,*,Bucket,Key):raise RuntimeError("secret=PRIVATE-OBJECT-CREDENTIAL")


class RecoveringDeleteS3(FakeS3):
    def __init__(self): super().__init__(); self.fail_deletes=True
    def delete_object(self,*,Bucket,Key):
        if self.fail_deletes: raise RuntimeError("synthetic delete outage")
        super().delete_object(Bucket=Bucket,Key=Key)


class QuotaStore:
    def register_media(self,**kwargs):raise ValueError("media storage quota exceeded")


def test_s3_storage_tenant_scope_integrity_and_delete(store,tmp_path):
    client=FakeS3(); storage=S3ObjectMediaStore(str(tmp_path),store,{"audio":100000,"video":100000,"image":100000},bucket="private-test",client=client)
    data=wav_bytes()
    async def chunks():yield data[:20];yield data[20:]
    response=asyncio.run(storage.save_stream(modality="audio",content_type="audio/wav",content_length=str(len(data)),chunks=chunks(),tenant_id="tenant-a",subject_id="user-a"))
    ref=response["media_ref"]
    assert response["size_bytes"]==len(data)
    assert storage.resolve(ref,"tenant-b","user-a",verify_integrity=True) is None
    resolved=storage.resolve(ref,"tenant-a","user-a",verify_integrity=True)
    assert resolved["integrity_ok"] and resolved["path"].read_bytes()==data
    storage.release(resolved)
    key=next(iter(client.objects));client.objects[key]=b"tampered"
    corrupted=storage.resolve(ref,"tenant-a","user-a",verify_integrity=True)
    assert not corrupted["integrity_ok"]
    storage.release(corrupted)
    assert not storage.delete(ref,"tenant-b","user-a")
    assert storage.delete(ref,"tenant-a","user-a") and not client.objects


def test_s3_storage_rejects_mime_signature_mismatch(store,tmp_path):
    storage=S3ObjectMediaStore(str(tmp_path),store,{"audio":1000,"video":1000,"image":1000},bucket="private",client=FakeS3())
    async def chunks():yield b"not-a-wave"
    try:asyncio.run(storage.save_stream(modality="audio",content_type="audio/wav",content_length="10",chunks=chunks(),tenant_id="t",subject_id="s"))
    except Exception as exc:assert getattr(exc,"code",None)=="invalid_media_signature"
    else:assert False,"invalid audio signature accepted"


def test_s3_error_contract_and_logs_do_not_expose_credentials(store,tmp_path,caplog):
    from app.core.errors import WellarError
    storage=S3ObjectMediaStore(str(tmp_path),store,{"audio":100000,"video":100000,"image":100000},bucket="private-test",client=BrokenS3())
    data=wav_bytes()
    async def chunks():yield data
    with pytest.raises(WellarError) as caught:
        asyncio.run(storage.save_stream(modality="audio",content_type="audio/wav",content_length=str(len(data)),chunks=chunks(),tenant_id="tenant",subject_id="subject"))
    assert caught.value.code=="object_storage_unavailable"
    leaked_marker_present = "SECRET-AWS-TOKEN" in caplog.text
    assert leaked_marker_present is False, "sensitive content was present in logs"
    storage.close()


def test_s3_quota_compensation_failure_is_observable_and_sanitized(tmp_path,caplog):
    import io
    import logging
    from app.core.logging import JsonFormatter
    from app.media.storage import MediaInputError
    stream=io.StringIO();handler=logging.StreamHandler(stream);handler.setFormatter(JsonFormatter());root=logging.getLogger();root.addHandler(handler)
    storage=S3ObjectMediaStore(str(tmp_path),QuotaStore(),{"audio":100000,"video":100000,"image":100000},bucket="private-test",client=BrokenDeleteS3())
    data=wav_bytes()
    async def chunks():yield data
    try:
        with pytest.raises(MediaInputError) as caught:
            asyncio.run(storage.save_stream(modality="audio",content_type="audio/wav",content_length=str(len(data)),chunks=chunks(),tenant_id="tenant-synthetic",subject_id="subject-synthetic"))
    finally:
        root.removeHandler(handler)
    assert caught.value.status_code==429
    assert "media.object.compensation_failed" in stream.getvalue()
    assert "orphan_object_possible" in stream.getvalue()
    assert "PRIVATE-OBJECT-CREDENTIAL" not in stream.getvalue()
    assert "tenant-synthetic" not in stream.getvalue()
    storage.close()


def test_s3_delete_failure_preserves_metadata_for_retry(store,tmp_path):
    from app.core.errors import WellarError
    client=RecoveringDeleteS3()
    storage=S3ObjectMediaStore(str(tmp_path),store,{"audio":100000,"video":100000,"image":100000},bucket="private-test",client=client)
    data=wav_bytes()
    async def chunks(): yield data
    response=asyncio.run(storage.save_stream(modality="audio",content_type="audio/wav",content_length=str(len(data)),chunks=chunks(),tenant_id="tenant-a",subject_id="user-a"))
    ref=response["media_ref"]
    with pytest.raises(WellarError) as failed:
        storage.delete(ref,"tenant-a","user-a")
    assert failed.value.code=="object_storage_unavailable"
    assert storage.resolve(ref,"tenant-a","user-a") is not None
    client.fail_deletes=False
    assert storage.delete(ref,"tenant-a","user-a")
    assert storage.resolve(ref,"tenant-a","user-a") is None
    storage.close()
