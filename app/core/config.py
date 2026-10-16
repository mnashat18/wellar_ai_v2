from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse


STAGING_POSTGRES_SSL_DISABLE_ALLOWED_HOST = "conntinuity_wellar-v2-postgres"


def _env_bool(name:str,default:bool)->bool:
    raw=os.getenv(name)
    if raw is None:return default
    if raw.lower() in {"1","true","yes"}:return True
    if raw.lower() in {"0","false","no"}:return False
    raise ValueError(f"{name} must be true or false")
from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    environment: str = "development"
    log_level: str = "INFO"
    db_path: str = "./data/wellar-v2-dev.sqlite3"
    queue_capacity: int = 1000
    max_attempts: int = 3
    lease_seconds: int = 30
    retry_base_seconds: float = 1.0
    dev_auth_tokens: dict[str, dict[str, str]] | None = None
    media_root: str = "./data/media"
    max_video_bytes: int = 20 * 1024 * 1024
    max_image_bytes: int = 5 * 1024 * 1024
    max_audio_bytes: int = 8 * 1024 * 1024
    max_video_seconds: int = 30
    max_audio_seconds: int = 30
    max_image_pixels: int = 4_000_000
    max_video_frames: int = 1800
    video_sample_hz: float = 4.0
    decode_timeout_seconds: float = 12.0
    decode_memory_limit_mb: int = 768
    worker_drain_seconds: float = 40.0
    max_media_total_bytes: int = 1073741824
    max_media_per_tenant_bytes: int = 104857600
    media_retention_days: int = 30
    max_concurrent_uploads: int = 4
    queue_backend: str = "sqlite"
    postgres_dsn: str | None = None
    staging_postgres_ssl_disable_ack: bool = False
    postgres_pool_min: int = 1
    postgres_pool_max: int = 10
    postgres_pool_timeout: float = 5.0
    postgres_connect_timeout: int = 5
    postgres_statement_timeout_ms: int = 5000
    postgres_auto_migrate: bool = True
    auth_mode: str = "development"
    directus_url: str | None = None
    directus_service_token: str | None = None
    directus_pull_enabled: bool = False
    directus_timeout_seconds: float = 3.0
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_uri: str | None = None
    oidc_tenant_claim: str = "tenant_id"
    oidc_algorithms: tuple[str,...] = ("RS256",)
    media_backend: str = "local"
    s3_bucket: str | None = None
    s3_region: str | None = None
    s3_endpoint_url: str | None = None
    model_inference_enabled: bool = False
    model_artifact_dir: str | None = None
    research_image_drowsiness_enabled: bool = False
    research_image_drowsiness_artifact_dir: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(override=False)
        raw_tokens = os.getenv("WELLAR_DEV_AUTH_TOKENS_JSON", "{}")
        try:
            tokens = json.loads(raw_tokens)
        except json.JSONDecodeError as exc:
            raise ValueError("WELLAR_DEV_AUTH_TOKENS_JSON must be valid JSON") from exc
        if not isinstance(tokens, dict) or any(
            not isinstance(k, str) or not isinstance(v, dict)
            or not isinstance(v.get("sub"), str) or not isinstance(v.get("tenant_id"), str)
            for k, v in tokens.items()
        ):
            raise ValueError("development auth entries require sub and tenant_id strings")
        try:
            settings = cls(
                environment=os.getenv("WELLAR_ENV", "development").lower(),
                log_level=os.getenv("WELLAR_LOG_LEVEL", "INFO").upper(),
                db_path=os.getenv("WELLAR_DB_PATH", "./data/wellar-v2-dev.sqlite3"),
                queue_capacity=int(os.getenv("WELLAR_QUEUE_CAPACITY", "1000")),
                max_attempts=int(os.getenv("WELLAR_MAX_ATTEMPTS", "3")),
                lease_seconds=int(os.getenv("WELLAR_LEASE_SECONDS", "30")),
                retry_base_seconds=float(os.getenv("WELLAR_RETRY_BASE_SECONDS", "1")),
                dev_auth_tokens=tokens,
                media_root=os.getenv("WELLAR_MEDIA_ROOT", "./data/media"),
                max_video_bytes=int(os.getenv("WELLAR_MAX_VIDEO_BYTES", str(20*1024*1024))),
                max_image_bytes=int(os.getenv("WELLAR_MAX_IMAGE_BYTES", str(5*1024*1024))),
                max_audio_bytes=int(os.getenv("WELLAR_MAX_AUDIO_BYTES", str(8*1024*1024))),
                max_video_seconds=int(os.getenv("WELLAR_MAX_VIDEO_SECONDS", "30")),
                max_audio_seconds=int(os.getenv("WELLAR_MAX_AUDIO_SECONDS", "30")),
                max_image_pixels=int(os.getenv("WELLAR_MAX_IMAGE_PIXELS", "4000000")),
                max_video_frames=int(os.getenv("WELLAR_MAX_VIDEO_FRAMES", "1800")),
                video_sample_hz=float(os.getenv("WELLAR_VIDEO_SAMPLE_HZ", "4")),
                decode_timeout_seconds=float(os.getenv("WELLAR_DECODE_TIMEOUT_SECONDS","12")),
                decode_memory_limit_mb=int(os.getenv("WELLAR_DECODE_MEMORY_LIMIT_MB","768")),
                worker_drain_seconds=float(os.getenv("WELLAR_WORKER_DRAIN_SECONDS","40")),
                max_media_total_bytes=int(os.getenv("WELLAR_MAX_MEDIA_TOTAL_BYTES",str(1024*1024*1024))),
                max_media_per_tenant_bytes=int(os.getenv("WELLAR_MAX_MEDIA_PER_TENANT_BYTES",str(100*1024*1024))),
                media_retention_days=int(os.getenv("WELLAR_MEDIA_RETENTION_DAYS","30")),
                max_concurrent_uploads=int(os.getenv("WELLAR_MAX_CONCURRENT_UPLOADS", "4")),
                queue_backend=os.getenv("WELLAR_QUEUE_BACKEND","sqlite").lower(),
                postgres_dsn=os.getenv("WELLAR_POSTGRES_DSN"),
                staging_postgres_ssl_disable_ack=_env_bool("WELLAR_STAGING_POSTGRES_SSL_DISABLE_ACK", False),
                postgres_pool_min=int(os.getenv("WELLAR_POSTGRES_POOL_MIN","1")),
                postgres_pool_max=int(os.getenv("WELLAR_POSTGRES_POOL_MAX","10")),
                postgres_pool_timeout=float(os.getenv("WELLAR_POSTGRES_POOL_TIMEOUT","5")),
                postgres_connect_timeout=int(os.getenv("WELLAR_POSTGRES_CONNECT_TIMEOUT","5")),
                postgres_statement_timeout_ms=int(os.getenv("WELLAR_POSTGRES_STATEMENT_TIMEOUT_MS","5000")),
                postgres_auto_migrate=_env_bool("WELLAR_POSTGRES_AUTO_MIGRATE",os.getenv("WELLAR_ENV","development").lower() in {"development","test"}),
                auth_mode=os.getenv("WELLAR_AUTH_MODE","development").lower(),
                directus_url=os.getenv("WELLAR_DIRECTUS_URL"),
                directus_service_token=os.getenv("WELLAR_DIRECTUS_SERVICE_TOKEN"),
                directus_pull_enabled=_env_bool("WELLAR_DIRECTUS_PULL_ENABLED", False),
                directus_timeout_seconds=float(os.getenv("WELLAR_DIRECTUS_TIMEOUT_SECONDS", "3")),
                oidc_issuer=os.getenv("WELLAR_OIDC_ISSUER"),oidc_audience=os.getenv("WELLAR_OIDC_AUDIENCE"),
                oidc_jwks_uri=os.getenv("WELLAR_OIDC_JWKS_URI"),oidc_tenant_claim=os.getenv("WELLAR_OIDC_TENANT_CLAIM","tenant_id"),
                oidc_algorithms=tuple(x.strip() for x in os.getenv("WELLAR_OIDC_ALGORITHMS","RS256").split(",") if x.strip()),
                media_backend=os.getenv("WELLAR_MEDIA_BACKEND","local").lower(),
                s3_bucket=os.getenv("WELLAR_S3_BUCKET"),s3_region=os.getenv("WELLAR_S3_REGION"),s3_endpoint_url=os.getenv("WELLAR_S3_ENDPOINT_URL"),
                model_inference_enabled=_env_bool("WELLAR_MODEL_INFERENCE_ENABLED",False),
                model_artifact_dir=os.getenv("WELLAR_MODEL_ARTIFACT_DIR"),
                research_image_drowsiness_enabled=_env_bool("WELLAR_RESEARCH_IMAGE_DROWSINESS_ENABLED",False),
                research_image_drowsiness_artifact_dir=os.getenv("WELLAR_RESEARCH_IMAGE_DROWSINESS_ARTIFACT_DIR"),
            )
        except ValueError as exc:
            raise ValueError("invalid Wellar environment configuration") from exc
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.environment not in {"development", "test", "staging", "production"}:
            raise ValueError("WELLAR_ENV must be development, test, staging, or production")
        if self.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("WELLAR_LOG_LEVEL is invalid")
        if min(self.queue_capacity, self.max_attempts, self.lease_seconds) < 1 or self.retry_base_seconds <= 0:
            raise ValueError("queue capacity, attempts, lease and retry values must be positive")
        if min(self.max_video_bytes,self.max_image_bytes,self.max_audio_bytes,self.max_video_seconds,self.max_audio_seconds,self.max_image_pixels,self.max_video_frames,self.max_media_total_bytes,self.max_media_per_tenant_bytes,self.max_concurrent_uploads,self.media_retention_days) < 1 or self.video_sample_hz <= 0:
            raise ValueError("media limits and sample rate must be positive")
        if not 0.1<=self.decode_timeout_seconds<=120 or not 64<=self.decode_memory_limit_mb<=8192 or not 1<=self.worker_drain_seconds<=600:
            raise ValueError("decode deadline, memory cap, or worker drain setting is invalid")
        if self.environment not in {"development", "test"} and self.dev_auth_tokens:
            raise ValueError("development token authentication is forbidden outside development/test")
        if self.queue_backend not in {"sqlite","postgres"} or self.media_backend not in {"local","s3"} or self.auth_mode not in {"development","oidc","directus"}:
            raise ValueError("queue backend, media backend or auth mode is invalid")
        if self.directus_timeout_seconds <= 0:
            raise ValueError("Directus timeout must be positive")
        if not 1<=self.postgres_pool_min<=self.postgres_pool_max<=64 or min(self.postgres_connect_timeout,self.postgres_statement_timeout_ms)<1 or self.postgres_pool_timeout<=0:
            raise ValueError("PostgreSQL pool and timeout configuration is invalid")
        if self.queue_backend=="postgres" and not self.postgres_dsn: raise ValueError("WELLAR_POSTGRES_DSN is required for PostgreSQL")
        if self.media_backend=="s3" and not self.s3_bucket: raise ValueError("WELLAR_S3_BUCKET is required for S3-compatible storage")
        if self.model_inference_enabled and not self.model_artifact_dir:
            raise ValueError("WELLAR_MODEL_ARTIFACT_DIR is required when model inference is enabled")
        if self.research_image_drowsiness_enabled and self.environment not in {"development", "test"}:
            raise ValueError("the research image drowsiness model is restricted to development/test")
        if self.research_image_drowsiness_enabled and not self.research_image_drowsiness_artifact_dir:
            raise ValueError("WELLAR_RESEARCH_IMAGE_DROWSINESS_ARTIFACT_DIR is required when research image inference is enabled")
        if self.auth_mode=="oidc" and not all((self.oidc_issuer,self.oidc_audience,self.oidc_jwks_uri)):
            raise ValueError("OIDC issuer, audience and JWKS URI are required")
        if self.auth_mode=="directus":
            parsed=urlparse(self.directus_url or "")
            if parsed.scheme not in {"http","https"} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("WELLAR_DIRECTUS_URL must be an HTTP(S) origin/base path without credentials or query")
        if self.directus_pull_enabled and (not self.directus_url or not self.directus_service_token):
            raise ValueError("Directus pull requires WELLAR_DIRECTUS_URL and WELLAR_DIRECTUS_SERVICE_TOKEN")
        if not self.oidc_algorithms or any(alg not in {"RS256","RS384","RS512","ES256","ES384","EdDSA"} for alg in self.oidc_algorithms):raise ValueError("OIDC JWT algorithms must be asymmetric and explicitly allowed")
        if self.environment in {"staging","production"} and (self.queue_backend!="postgres" or self.media_backend!="s3" or self.auth_mode not in {"oidc", "directus"}):
            raise ValueError("SQLite is single-host only; staging/production require PostgreSQL, S3-compatible storage and verified identity authentication")
        if self.environment in {"staging","production"} and self.auth_mode=="oidc":
            if urlparse(self.oidc_issuer or "").scheme!="https" or urlparse(self.oidc_jwks_uri or "").scheme!="https":
                raise ValueError("staging/production OIDC issuer and JWKS URI must use HTTPS")
        if self.environment in {"staging","production"} and self.auth_mode=="directus" and urlparse(self.directus_url or "").scheme!="https":
            raise ValueError("staging/production Directus URL must use HTTPS")
        if self.environment in {"staging","production"} and self.queue_backend=="postgres":
            parsed_postgres_dsn = urlparse(self.postgres_dsn or "")
            ssl_modes = [mode.lower() for mode in parse_qs(parsed_postgres_dsn.query, keep_blank_values=True).get("sslmode", [])]
            if ssl_modes == ["verify-full"]:
                pass
            elif self.environment == "staging" and ssl_modes == ["disable"]:
                if not self.staging_postgres_ssl_disable_ack:
                    raise ValueError("staging PostgreSQL sslmode=disable requires WELLAR_STAGING_POSTGRES_SSL_DISABLE_ACK=true")
                if (parsed_postgres_dsn.hostname or "").lower().rstrip(".") != STAGING_POSTGRES_SSL_DISABLE_ALLOWED_HOST:
                    raise ValueError("staging PostgreSQL sslmode=disable is restricted to the approved internal database host")
            else:
                raise ValueError("staging/production PostgreSQL DSN must enable sslmode=verify-full")
        if self.environment in {"staging","production"} and self.s3_endpoint_url and urlparse(self.s3_endpoint_url).scheme!="https":
            raise ValueError("staging/production object storage endpoint must use HTTPS")
        if self.queue_backend=="sqlite" and self.db_path != ":memory:":
            path = Path(self.db_path)
            if not path.is_absolute():
                # Resolve against the process working directory; this is convenient locally,
                # while deployments should provide an explicit persistent path.
                path = path.resolve()
            if self.environment in {"staging", "production"}:
                raise ValueError("SQLite backend is single-host development only; configure a distributed backend")
