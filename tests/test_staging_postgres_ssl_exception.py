import pytest

from app.core.config import Settings


def _staging_settings(
    dsn: str,
    *,
    ack: bool = False,
    environment: str = "staging",
    s3_endpoint_url: str = "https://s3.staging.invalid",
) -> Settings:
    return Settings(
        environment=environment,
        queue_backend="postgres",
        postgres_dsn=dsn,
        staging_postgres_ssl_disable_ack=ack,
        media_backend="s3",
        s3_bucket="private-test-bucket",
        s3_endpoint_url=s3_endpoint_url,
        auth_mode="oidc",
        oidc_issuer="https://identity.staging.invalid/",
        oidc_audience="wellar-staging",
        oidc_jwks_uri="https://identity.staging.invalid/jwks",
    )


def test_staging_ssl_disable_is_allowed_only_for_exact_internal_host_with_ack():
    settings = _staging_settings(
        "postgresql://wellar_v2_user:placeholder@conntinuity_wellar-v2-postgres:5432/wellar_v2?sslmode=disable",
        ack=True,
    )

    settings.validate()


def test_staging_ssl_disable_without_ack_is_rejected():
    settings = _staging_settings(
        "postgresql://wellar_v2_user:placeholder@conntinuity_wellar-v2-postgres:5432/wellar_v2?sslmode=disable"
    )

    with pytest.raises(ValueError, match="WELLAR_STAGING_POSTGRES_SSL_DISABLE_ACK=true"):
        settings.validate()


def test_staging_ssl_disable_for_unknown_host_is_rejected_even_with_ack():
    settings = _staging_settings(
        "postgresql://wellar_v2_user:placeholder@public-db.invalid:5432/wellar_v2?sslmode=disable",
        ack=True,
    )

    with pytest.raises(ValueError, match="approved internal database host"):
        settings.validate()


def test_production_ssl_disable_is_rejected_even_with_staging_ack():
    settings = _staging_settings(
        "postgresql://wellar_v2_user:placeholder@conntinuity_wellar-v2-postgres:5432/wellar_v2?sslmode=disable",
        ack=True,
        environment="production",
    )

    with pytest.raises(ValueError, match="sslmode=verify-full"):
        settings.validate()


def test_staging_exception_does_not_weaken_s3_https_requirement():
    settings = _staging_settings(
        "postgresql://wellar_v2_user:placeholder@conntinuity_wellar-v2-postgres:5432/wellar_v2?sslmode=disable",
        ack=True,
        s3_endpoint_url="http://s3.staging.invalid",
    )

    with pytest.raises(ValueError, match="object storage endpoint must use HTTPS"):
        settings.validate()


def test_staging_ssl_disable_ack_is_explicit_environment_flag(monkeypatch):
    monkeypatch.setenv("WELLAR_STAGING_POSTGRES_SSL_DISABLE_ACK", "true")

    assert Settings.from_env().staging_postgres_ssl_disable_ack is True
