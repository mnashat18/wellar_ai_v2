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
    options=dict(max_total_bytes=settings.max_media_total_bytes,max_tenant_bytes=settings.max_media_per_tenant_bytes,
        max_concurrent_uploads=settings.max_concurrent_uploads,scratch_sweep_max_entries=settings.media_scratch_sweep_max_entries)
    if settings.media_backend=="s3":
        return S3ObjectMediaStore(settings.media_root,store,caps,bucket=settings.s3_bucket,region=settings.s3_region,
            endpoint_url=settings.s3_endpoint_url or None,delete_connect_timeout_seconds=settings.media_delete_connect_timeout_seconds,
            delete_read_timeout_seconds=settings.media_delete_read_timeout_seconds,
            delete_operation_timeout_seconds=settings.media_delete_operation_timeout_seconds,**options)
    return LocalMediaStore(settings.media_root,store,caps,settings.max_media_total_bytes,settings.max_media_per_tenant_bytes,
        settings.max_concurrent_uploads,delete_timeout_seconds=settings.media_delete_operation_timeout_seconds,**{
            "scratch_sweep_max_entries":settings.media_scratch_sweep_max_entries})


def create_analysis_processor(settings,media_store):
    """Build the configured feature processor, loading only guarded local releases."""
    from app.media.analysis import MultimodalFeatureProcessor
    from app.ml.registry import load_predictors

    enabled=settings.model_inference_enabled
    predictors=load_predictors(enabled=enabled,artifact_dir=settings.model_artifact_dir)
    return MultimodalFeatureProcessor(media_store,max_video_seconds=settings.max_video_seconds,
        max_audio_seconds=settings.max_audio_seconds,max_image_pixels=settings.max_image_pixels,
        max_video_frames=settings.max_video_frames,video_sample_hz=settings.video_sample_hz,
        decode_timeout_seconds=settings.decode_timeout_seconds,
        decode_memory_limit_mb=settings.decode_memory_limit_mb,
        analysis_timeout_seconds=settings.worker_drain_seconds,predictors=predictors,
        prediction_enabled=enabled)
