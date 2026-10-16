from app.media.storage import LocalMediaStore
from app.media.s3_storage import S3ObjectMediaStore
from app.queue.postgres_store import PostgresQueueStore
from app.queue.sqlite_store import SQLiteQueueStore


def create_queue_store(settings):
    if settings.queue_backend=="postgres":
        return PostgresQueueStore(settings.postgres_dsn,capacity=settings.queue_capacity,max_attempts=settings.max_attempts,
            lease_seconds=settings.lease_seconds,retry_base_seconds=settings.retry_base_seconds,
            min_pool_size=settings.postgres_pool_min,max_pool_size=settings.postgres_pool_max,
            pool_timeout=settings.postgres_pool_timeout,connect_timeout=settings.postgres_connect_timeout,
            statement_timeout_ms=settings.postgres_statement_timeout_ms,migrate=settings.postgres_auto_migrate)
    return SQLiteQueueStore(settings.db_path,settings.queue_capacity,settings.max_attempts,settings.lease_seconds,settings.retry_base_seconds)


def create_media_store(settings,store):
    caps={"video":settings.max_video_bytes,"image":settings.max_image_bytes,"audio":settings.max_audio_bytes}
    options=dict(max_total_bytes=settings.max_media_total_bytes,max_tenant_bytes=settings.max_media_per_tenant_bytes,max_concurrent_uploads=settings.max_concurrent_uploads)
    if settings.media_backend=="s3":
        return S3ObjectMediaStore(settings.media_root,store,caps,bucket=settings.s3_bucket,region=settings.s3_region,endpoint_url=settings.s3_endpoint_url or None,**options)
    return LocalMediaStore(settings.media_root,store,caps,settings.max_media_total_bytes,settings.max_media_per_tenant_bytes,settings.max_concurrent_uploads)
