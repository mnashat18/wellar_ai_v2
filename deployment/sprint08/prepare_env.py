"""Create one-time local credentials for the isolated Sprint 08 Compose stack."""

from __future__ import annotations

import os
import secrets
import subprocess
from pathlib import Path


def write_environment(path: Path, *, project_root: Path | None = None) -> None:
    path = path.resolve()
    root = (project_root or Path(__file__).resolve().parents[2]).resolve()
    if path != root / ".env.sprint08":
        raise ValueError("credential file must be the V2 root .env.sprint08")

    db_password = secrets.token_hex(24)
    object_password = secrets.token_hex(24)
    dev_token = secrets.token_hex(32)
    content = "\n".join(
        [
            "WELLAR_SPRINT08_POSTGRES_USER=wellar_sprint08",
            f"WELLAR_SPRINT08_POSTGRES_PASSWORD={db_password}",
            "WELLAR_SPRINT08_MINIO_ROOT_USER=wellar_sprint08",
            f"WELLAR_SPRINT08_MINIO_ROOT_PASSWORD={object_password}",
            f"WELLAR_TEST_AUTH_TOKEN={dev_token}",
            f"WELLAR_DEV_AUTH_TOKENS_JSON='{{\"{dev_token}\":{{\"sub\":\"synthetic-sprint08-user\",\"tenant_id\":\"synthetic-sprint08-tenant\"}}}}'",
            "WELLAR_ENV=test",
            "WELLAR_LOG_LEVEL=INFO",
            "WELLAR_QUEUE_BACKEND=postgres",
            "WELLAR_POSTGRES_DSN=postgresql://wellar_sprint08:" + db_password + "@postgres:5432/wellar_v2_sprint08_test",
            "WELLAR_POSTGRES_AUTO_MIGRATE=false",
            "WELLAR_POSTGRES_POOL_MIN=1",
            "WELLAR_POSTGRES_POOL_MAX=5",
            "WELLAR_POSTGRES_POOL_TIMEOUT=5",
            "WELLAR_POSTGRES_CONNECT_TIMEOUT=5",
            "WELLAR_POSTGRES_STATEMENT_TIMEOUT_MS=5000",
            "WELLAR_QUEUE_CAPACITY=1000",
            "WELLAR_MAX_ATTEMPTS=3",
            "WELLAR_LEASE_SECONDS=30",
            "WELLAR_RETRY_BASE_SECONDS=1",
            "WELLAR_AUTH_MODE=development",
            "WELLAR_MEDIA_BACKEND=s3",
            "WELLAR_MEDIA_ROOT=/tmp/wellar-sprint08",
            "WELLAR_S3_BUCKET=wellar-v2-sprint08-test",
            "WELLAR_S3_REGION=us-east-1",
            "WELLAR_S3_ENDPOINT_URL=http://minio:9000",
            f"AWS_ACCESS_KEY_ID=wellar_sprint08",
            f"AWS_SECRET_ACCESS_KEY={object_password}",
            "AWS_DEFAULT_REGION=us-east-1",
            "WELLAR_MAX_MEDIA_TOTAL_BYTES=536870912",
            "WELLAR_MAX_MEDIA_PER_TENANT_BYTES=104857600",
            "WELLAR_MAX_CONCURRENT_UPLOADS=4",
            "WELLAR_MEDIA_RETENTION_DAYS=1",
            "WELLAR_MAX_VIDEO_BYTES=20971520",
            "WELLAR_MAX_IMAGE_BYTES=5242880",
            "WELLAR_MAX_AUDIO_BYTES=8388608",
        ]
    ) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        if os.name == "nt":
            identity = subprocess.run(["whoami"], check=True, capture_output=True, text=True).stdout.strip()
            subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", f"{identity}:F", "SYSTEM:F"],
                check=True, capture_output=True, text=True,
            )
        else:
            os.chmod(path, 0o600)
    except (OSError, subprocess.CalledProcessError):
        path.unlink(missing_ok=True)
        raise RuntimeError("could not restrict access to the generated credential file") from None
    try:
        path.write_text(content, encoding="utf-8", newline="\n")
    except OSError:
        path.unlink(missing_ok=True)
        raise RuntimeError("could not write the generated credential file") from None


def main() -> None:
    path = Path(__file__).resolve().parents[2] / ".env.sprint08"
    write_environment(path)
    print("Created gitignored Sprint 08 test credentials at .env.sprint08; values were not printed.")


if __name__ == "__main__":
    main()
