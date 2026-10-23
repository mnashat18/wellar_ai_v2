"""Create and verify only the private bucket reserved for Sprint 08 tests."""

from __future__ import annotations

import json
import os
import time


EXPECTED_ENDPOINT = "http://minio:9000"
EXPECTED_BUCKET = "wellar-v2-sprint08-test"


def ensure_private_bucket(client, bucket: str) -> None:
    if bucket != EXPECTED_BUCKET:
        raise ValueError("refusing to initialize an unapproved bucket name")
    try:
        client.head_bucket(Bucket=bucket)
    except Exception as exc:
        response = getattr(exc, "response", {})
        error = response.get("Error", {})
        status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        code = str(error.get("Code", ""))
        if status == 403 or code in {"403", "AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch"}:
            raise RuntimeError("test object store is unavailable or access was denied") from None
        if status != 404 and code not in {"404", "NoSuchBucket", "NotFound"}:
            raise
        client.create_bucket(Bucket=bucket)

    acl = client.get_bucket_acl(Bucket=bucket)
    public_groups = {
        "http://acs.amazonaws.com/groups/global/AllUsers",
        "http://acs.amazonaws.com/groups/global/AuthenticatedUsers",
    }
    for grant in acl.get("Grants", []):
        grantee = grant.get("Grantee", {})
        if grantee.get("Type") == "Group" and grantee.get("URI") in public_groups:
            raise RuntimeError("test bucket has a public ACL; refusing to continue")
    try:
        policy = json.loads(client.get_bucket_policy(Bucket=bucket)["Policy"])
    except Exception as exc:
        response = getattr(exc, "response", {})
        error = response.get("Error", {})
        status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        code = str(error.get("Code", ""))
        if status == 404 or code in {"NoSuchBucketPolicy", "NoSuchPolicy"}:
            return
        raise RuntimeError("could not verify the test bucket policy") from None
    statements = policy.get("Statement", [])
    if isinstance(statements, dict):
        statements = [statements]
    for statement in statements:
        principal = statement.get("Principal")
        if statement.get("Effect", "Allow").lower() != "allow":
            continue
        if principal == "*" or (
            isinstance(principal, dict)
            and any("*" in (value if isinstance(value, list) else [value]) for value in principal.values())
        ):
            raise RuntimeError("test bucket has a public allow policy; refusing to continue")


def main() -> None:
    endpoint = os.getenv("WELLAR_S3_ENDPOINT_URL", "")
    bucket = os.getenv("WELLAR_S3_BUCKET", "")
    if endpoint != EXPECTED_ENDPOINT or bucket != EXPECTED_BUCKET:
        raise SystemExit("refusing any endpoint or bucket outside the Sprint 08 Compose stack")

    import boto3

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=os.getenv("WELLAR_S3_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    )
    last_error = None
    for attempt in range(30):
        try:
            ensure_private_bucket(client, bucket)
            print("Sprint 08 private test bucket is ready.")
            return
        except RuntimeError:
            raise
        except Exception as exc:
            last_error = type(exc).__name__
            if attempt < 29:
                time.sleep(2)
    raise SystemExit(f"MinIO did not become ready after 60 seconds ({last_error or 'unknown error'}).")


if __name__ == "__main__":
    main()
