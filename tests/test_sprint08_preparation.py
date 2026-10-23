from __future__ import annotations

import json
import os
import subprocess

import pytest
from dotenv import dotenv_values
from deployment.sprint08 import prepare_env

from deployment.sprint08.init_minio import EXPECTED_BUCKET, ensure_private_bucket
from deployment.sprint08.prepare_env import write_environment


class BucketMissing(Exception):
    response = {
        "Error": {"Code": "NoSuchBucket"},
        "ResponseMetadata": {"HTTPStatusCode": 404},
    }


class NoBucketPolicy(Exception):
    response = {"Error": {"Code": "NoSuchBucketPolicy"}, "ResponseMetadata": {"HTTPStatusCode": 404}}


class PrivateBucket:
    def __init__(self):
        self.objects = set()

    def head_bucket(self, *, Bucket):
        if Bucket not in self.objects:
            raise BucketMissing()

    def create_bucket(self, *, Bucket):
        self.objects.add(Bucket)

    def get_bucket_acl(self, *, Bucket):
        assert Bucket in self.objects
        return {"Grants": [{"Grantee": {"Type": "CanonicalUser"}, "Permission": "FULL_CONTROL"}]}

    def get_bucket_policy(self, *, Bucket):
        raise NoBucketPolicy()


def test_bucket_bootstrap_creates_only_private_named_test_bucket():
    client = PrivateBucket()
    ensure_private_bucket(client, EXPECTED_BUCKET)
    assert client.objects == {EXPECTED_BUCKET}


def test_bucket_bootstrap_rejects_other_bucket_names():
    with pytest.raises(ValueError):
        ensure_private_bucket(PrivateBucket(), "customer-bucket")


def test_bucket_bootstrap_rejects_public_grants():
    class PublicBucket(PrivateBucket):
        def head_bucket(self, *, Bucket):
            self.objects.add(Bucket)

        def get_bucket_acl(self, *, Bucket):
            return {"Grants": [{"Grantee": {"Type": "Group", "URI": "http://acs.amazonaws.com/groups/global/AllUsers"}}]}

    with pytest.raises(RuntimeError, match="public ACL"):
        ensure_private_bucket(PublicBucket(), EXPECTED_BUCKET)


def test_bucket_bootstrap_rejects_wildcard_allow_policy():
    class PublicPolicy(PrivateBucket):
        def head_bucket(self, *, Bucket):
            self.objects.add(Bucket)

        def get_bucket_policy(self, *, Bucket):
            return {"Policy": json.dumps({"Statement": [{"Effect": "Allow", "Principal": "*", "Action": "s3:GetObject", "Resource": "*"}]})}

    with pytest.raises(RuntimeError, match="public allow policy"):
        ensure_private_bucket(PublicPolicy(), EXPECTED_BUCKET)


def test_environment_generator_writes_unique_synthetic_credentials_and_never_overwrites(tmp_path, monkeypatch):
    root = tmp_path / "isolated-v2"
    root.mkdir()
    target = root / ".env.sprint08"
    acl_calls = []

    def fake_run(command, **kwargs):
        acl_calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="synthetic-user\n", stderr="")

    monkeypatch.setattr(prepare_env.subprocess, "run", fake_run)
    write_environment(target, project_root=root)
    values = target.read_text(encoding="utf-8")
    assert "synthetic-sprint08-user" in values and "wellar-v2-sprint08-test" in values
    parsed = dotenv_values(target)
    tokens = json.loads(parsed["WELLAR_DEV_AUTH_TOKENS_JSON"])
    assert len(tokens) == 1
    assert next(iter(tokens.values())) == {"sub": "synthetic-sprint08-user", "tenant_id": "synthetic-sprint08-tenant"}
    assert parsed["WELLAR_POSTGRES_DSN"].endswith("@postgres:5432/wellar_v2_sprint08_test")
    if os.name == "nt":
        assert len(acl_calls) == 2 and acl_calls[1][0] == "icacls"
    with pytest.raises(FileExistsError):
        write_environment(target, project_root=root)


def test_environment_generator_removes_empty_file_when_acl_cannot_be_restricted(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows ACL failure behavior is platform-specific")
    root = tmp_path / "isolated-v2"
    root.mkdir()
    target = root / ".env.sprint08"

    def fail_acl(command, **kwargs):
        if command[0] == "icacls":
            raise subprocess.CalledProcessError(5, command)
        return subprocess.CompletedProcess(command, 0, stdout="synthetic-user\n", stderr="")

    monkeypatch.setattr(prepare_env.subprocess, "run", fail_acl)
    with pytest.raises(RuntimeError, match="restrict access"):
        write_environment(target, project_root=root)
    assert not target.exists()
