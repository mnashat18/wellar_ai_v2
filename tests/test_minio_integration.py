"""Opt-in MinIO checks for the explicitly isolated Sprint 08 bucket only."""

from __future__ import annotations

import os
import time
import hashlib
from uuid import uuid4
import asyncio
from urllib.parse import urlparse

import pytest

from app.media.s3_storage import S3ObjectMediaStore
from tests.media_fixtures import wav_bytes


def report_only_orphans(client, bucket, prefix, registered_keys, *, max_keys=16):
    """Bounded inventory for one unique synthetic tenant prefix; never mutates S3."""
    if max_keys < 1 or max_keys > 64:
        raise ValueError("orphan report limit is out of range")
    page=client.list_objects_v2(Bucket=bucket,Prefix=prefix,MaxKeys=max_keys)
    if page.get("IsTruncated"):
        raise AssertionError("synthetic orphan report exceeded its bounded page")
    return sorted(item["Key"] for item in page.get("Contents",[]) if item["Key"] not in registered_keys)


def test_report_only_orphan_helper_is_bounded_and_never_mutates_objects():
    class InventoryClient:
        def __init__(self,truncated=False):self.truncated=truncated;self.deleted=[];self.request=None
        def list_objects_v2(self,**kwargs):
            self.request=kwargs
            return {"IsTruncated":self.truncated,"Contents":[{"Key":"synthetic/present"},{"Key":"synthetic/orphan"}]}
        def delete_object(self,**kwargs):self.deleted.append(kwargs)
    client=InventoryClient()
    result=report_only_orphans(client,"test-bucket","synthetic/",{"synthetic/present"},max_keys=4)
    assert result==["synthetic/orphan"]
    assert client.request["MaxKeys"]==4 and client.request["Prefix"]=="synthetic/"
    assert client.deleted==[]
    with pytest.raises(ValueError):report_only_orphans(client,"test-bucket","synthetic/",set(),max_keys=1000)
    client.truncated=True
    with pytest.raises(AssertionError):report_only_orphans(client,"test-bucket","synthetic/",set())
    assert client.deleted==[]


@pytest.fixture(scope="module")
def live_minio():
    endpoint = os.getenv("WELLAR_TEST_S3_ENDPOINT")
    if not endpoint:
        pytest.skip("WELLAR_TEST_S3_ENDPOINT not configured; live MinIO integration not run")

    from dotenv import dotenv_values

    from pathlib import Path

    project_root = Path(__file__).resolve().parents[1]
    local_credentials = dotenv_values(project_root / ".env.sprint08")
    parsed = urlparse(endpoint)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.port != 19000:
        pytest.fail("refusing S3 integration unless endpoint is loopback port 19000")
    bucket = os.getenv("WELLAR_TEST_S3_BUCKET")
    if bucket != "wellar-v2-sprint08-test":
        pytest.fail("refusing S3 integration unless the exact Sprint 08 test bucket is selected")
    if local_credentials.get("WELLAR_SPRINT08_MINIO_ROOT_USER") != "wellar_sprint08" or not local_credentials.get("WELLAR_SPRINT08_MINIO_ROOT_PASSWORD"):
        pytest.fail("Sprint 08 test-only MinIO credentials are missing")

    import boto3

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name="us-east-1",
        aws_access_key_id=local_credentials["WELLAR_SPRINT08_MINIO_ROOT_USER"],
        aws_secret_access_key=local_credentials["WELLAR_SPRINT08_MINIO_ROOT_PASSWORD"],
    )
    client.head_bucket(Bucket=bucket)
    acl = client.get_bucket_acl(Bucket=bucket)
    public_uris = {
        "http://acs.amazonaws.com/groups/global/AllUsers",
        "http://acs.amazonaws.com/groups/global/AuthenticatedUsers",
    }
    assert not any(
        grant.get("Grantee", {}).get("Type") == "Group"
        and grant.get("Grantee", {}).get("URI") in public_uris
        for grant in acl.get("Grants", [])
    )
    try:
        import json

        policy = json.loads(client.get_bucket_policy(Bucket=bucket)["Policy"])
    except Exception as exc:
        code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
        if code not in {"NoSuchBucketPolicy", "NoSuchPolicy"}:
            pytest.fail("could not verify the test bucket policy")
    else:
        statements = policy.get("Statement", [])
        if isinstance(statements, dict):
            statements = [statements]
        assert not any(
            statement.get("Effect", "Allow").lower() == "allow"
            and (statement.get("Principal") == "*" or (
                isinstance(statement.get("Principal"), dict)
                and any("*" in (value if isinstance(value, list) else [value]) for value in statement["Principal"].values())
            ))
            for statement in statements
        )
    yield client, bucket


def test_minio_media_integrity_tenant_isolation_delete_and_retention(live_minio, store, tmp_path):
    client, bucket = live_minio
    storage = S3ObjectMediaStore(
        str(tmp_path), store, {"audio": 100_000, "video": 100_000, "image": 100_000},
        bucket=bucket, region="us-east-1", endpoint_url=os.environ["WELLAR_TEST_S3_ENDPOINT"], client=client,
    )
    try:
        data = wav_bytes()

        async def chunks():
            yield data[:19]
            yield data[19:]

        uploaded = asyncio.run(storage.save_stream(
            modality="audio", content_type="audio/wav", content_length=str(len(data)), chunks=chunks(),
            tenant_id="synthetic-sprint08-tenant", subject_id="synthetic-sprint08-user",
        ))
        ref = uploaded["media_ref"]
        assert client.head_object(Bucket=bucket, Key=store.get_media_by_ref(ref, "synthetic-sprint08-tenant", "synthetic-sprint08-user")["relative_path"])["ContentLength"] == len(data)
        assert storage.resolve(ref, "other-synthetic-tenant", "synthetic-sprint08-user", verify_integrity=True) is None
        resolved = storage.resolve(ref, "synthetic-sprint08-tenant", "synthetic-sprint08-user", verify_integrity=True)
        assert resolved["integrity_ok"] and resolved["path"].read_bytes() == data
        storage.release(resolved)
        assert storage.delete(ref, "synthetic-sprint08-tenant", "synthetic-sprint08-user")

        async def second_chunks():
            yield data

        retained = asyncio.run(storage.save_stream(
            modality="audio", content_type="audio/wav", content_length=str(len(data)), chunks=second_chunks(),
            tenant_id="synthetic-sprint08-tenant", subject_id="synthetic-sprint08-user",
        ))
        assert storage.purge_expired(time.time() + 1) == 1
        assert storage.resolve(retained["media_ref"], "synthetic-sprint08-tenant", "synthetic-sprint08-user") is None
    finally:
        storage.close()


def test_minio_delete_retry_and_report_only_orphan_detection(live_minio, store, tmp_path, monkeypatch):
    client,bucket=live_minio
    namespace=uuid4().hex
    tenant_id="synthetic-lifecycle-tenant-"+namespace
    subject_id="synthetic-lifecycle-subject-"+namespace
    other_subject="synthetic-other-subject-"+namespace
    tenant_prefix=hashlib.sha256(tenant_id.encode()).hexdigest()[:24]+"/"
    orphan_key=tenant_prefix+uuid4().hex+".wav"
    storage=S3ObjectMediaStore(
        str(tmp_path),store,{"audio":100_000,"video":100_000,"image":100_000},
        bucket=bucket,region="us-east-1",endpoint_url=os.environ["WELLAR_TEST_S3_ENDPOINT"],client=client,
    )
    reference=None
    data=wav_bytes()
    async def chunks():yield data
    try:
        uploaded=asyncio.run(storage.save_stream(
            modality="audio",content_type="audio/wav",content_length=str(len(data)),chunks=chunks(),
            tenant_id=tenant_id,subject_id=subject_id,
        ))
        reference=uploaded["media_ref"]
        asset=store.get_media_by_ref(reference,tenant_id,subject_id)
        assert asset and asset["relative_path"].startswith(tenant_prefix)
        assert not storage.delete(reference,"other-tenant-"+namespace,subject_id)
        assert not storage.delete(reference,tenant_id,other_subject)

        import app.media.s3_storage as s3_module
        real_delete=s3_module.run_bounded_storage_action
        state={"calls":0}
        def fail_once_after_delete(request,timeout_seconds):
            if request.get("action")=="s3_delete":
                state["calls"]+=1
                result=real_delete(request,timeout_seconds)
                if state["calls"]==1:raise RuntimeError("synthetic lost delete response")
                return result
            return real_delete(request,timeout_seconds)
        monkeypatch.setattr(s3_module,"run_bounded_storage_action",fail_once_after_delete)
        from app.core.errors import WellarError
        with pytest.raises(WellarError) as failed:
            storage.delete(reference,tenant_id,subject_id)
        assert failed.value.code=="object_storage_unavailable"
        assert store.get_media_by_ref(reference,tenant_id,subject_id) is not None
        with pytest.raises(WellarError):storage.resolve(reference,tenant_id,subject_id)
        monkeypatch.setattr(s3_module,"run_bounded_storage_action",real_delete)
        assert storage.delete(reference,tenant_id,subject_id)
        assert not storage.delete(reference,tenant_id,subject_id)
        assert store.get_media_by_ref(reference,tenant_id,subject_id) is None

        # Create exactly one unregistered object under a new synthetic tenant prefix.
        # The report is read-only and bounded; it must leave the object untouched.
        client.put_object(Bucket=bucket,Key=orphan_key,Body=b"synthetic orphan fixture")
        registered={row[0] for row in store.db.execute(
            "SELECT relative_path FROM media_assets WHERE tenant_id=?",(tenant_id,)
        ).fetchall()}
        reported=report_only_orphans(client,bucket,tenant_prefix,registered,max_keys=8)
        assert reported==[orphan_key]
        assert client.head_object(Bucket=bucket,Key=orphan_key)["ContentLength"]==len(b"synthetic orphan fixture")
    finally:
        # Cleanup targets only this test's generated keys and own metadata reference.
        try:
            client.delete_object(Bucket=bucket,Key=orphan_key)
        except Exception:
            pass
        if reference and store.get_media_by_ref(reference,tenant_id,subject_id):
            storage.delete(reference,tenant_id,subject_id)
        storage.close()
