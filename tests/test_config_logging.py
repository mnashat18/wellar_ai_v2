import json
import logging

import pytest

from app.core.config import Settings
from app.core.logging import JsonFormatter


def test_sqlite_refused_for_staging():
    with pytest.raises(ValueError,match="single-host"):
        Settings(environment="staging").validate()


def test_dev_auth_tokens_refused_outside_development():
    with pytest.raises(ValueError,match="forbidden"):
        Settings(environment="production",dev_auth_tokens={"x":{"sub":"s","tenant_id":"t"}}).validate()


def test_staging_requires_all_three_distributed_adapters_and_https_oidc():
    settings=Settings(environment="staging",queue_backend="postgres",postgres_dsn="postgresql://redacted@db/test?sslmode=verify-full",media_backend="s3",s3_bucket="private-test",auth_mode="oidc",oidc_issuer="https://identity.invalid/",oidc_audience="wellar-staging",oidc_jwks_uri="https://identity.invalid/jwks")
    settings.validate()
    with pytest.raises(ValueError,match="HTTPS"):
        Settings(environment="staging",queue_backend="postgres",postgres_dsn="postgresql://db/test?sslmode=verify-full",media_backend="s3",s3_bucket="private-test",auth_mode="oidc",oidc_issuer="http://identity.invalid/",oidc_audience="api",oidc_jwks_uri="http://identity.invalid/jwks").validate()


def test_log_formatter_redacts_secrets_and_exception_messages():
    record=logging.LogRecord("x",logging.ERROR,"file.py",10,"request failed",(),(ValueError,ValueError("Bearer abc-secret person@example.com"),None))
    record.event_fields={"scan_id":"opaque-id","error_code":"processing_error"}
    output=JsonFormatter().format(record)
    parsed=json.loads(output)
    assert "abc-secret" not in output and "person@example.com" not in output
    assert parsed["exception_type"]=="ValueError" and parsed["scan_id"]!="opaque-id"


def test_media_contracts_are_versioned_and_keep_target_provenance():
    from app.ml.contracts import ResearchInference, TargetEstimate
    from app.media.contracts import ModalityQuality
    quality=ModalityQuality(modality="audio",status="insufficient_quality",coverage=0.1,missingness=0.9)
    assert quality.schema_version=="1.0"
    estimate=TargetEstimate(target="vigilance_impairment",status="abstained",calibrated=False,provenance="research_only")
    assert ResearchInference(sleepiness=estimate,vigilance=estimate).decision_use=="disabled"


def test_directus_mock_never_contacts_external_service():
    from app.integrations.directus import MockDirectusAdapter
    mock=MockDirectusAdapter()
    assert mock.health()
    assert mock.assert_accessible("tenant-a","media://tenant-a/12345678-1234-4234-9234-123456789abc")
    assert not mock.assert_accessible("tenant-b","media://tenant-a/12345678-1234-4234-9234-123456789abc")


def test_production_directus_auth_requires_https_and_valid_base_url():
    Settings(environment="production",queue_backend="postgres",postgres_dsn="postgresql://db/test?sslmode=verify-full",media_backend="s3",s3_bucket="private-test",auth_mode="directus",directus_url="https://identity.invalid").validate()
    with pytest.raises(ValueError,match="HTTPS"):
        Settings(environment="production",queue_backend="postgres",postgres_dsn="postgresql://db/test?sslmode=verify-full",media_backend="s3",s3_bucket="private-test",auth_mode="directus",directus_url="http://identity.invalid").validate()
    with pytest.raises(ValueError):
        Settings(environment="test",auth_mode="directus",directus_url="https://user:password@identity.invalid").validate()
