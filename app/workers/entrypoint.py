from __future__ import annotations

import logging
import signal
import threading
import uuid

from app.core.config import Settings
from app.core.logging import configure_logging,event
from app.media.analysis import MultimodalFeatureProcessor
from app.ml.registry import load_predictors
from app.ml.research_image_drowsiness import ResearchImageDrowsinessClassifier
from app.core.adapters import create_media_store,create_queue_store
from app.workers.worker import Worker
from app.compat.directus_facade import DirectusHTTPClient,DirectusPullProcessor


def main() -> None:
    settings=Settings.from_env()
    configure_logging(settings.log_level,settings.environment)
    # Extractors lazily use NumPy; initialize its native runtime before the worker
    # starts heartbeat threads so first-import latency is outside a claimed lease.
    import numpy  # noqa: F401
    store=create_queue_store(settings)
    media_store=create_media_store(settings,store)
    predictors=load_predictors(enabled=settings.model_inference_enabled,artifact_dir=settings.model_artifact_dir)
    research_image_classifier=ResearchImageDrowsinessClassifier.load(
        settings.research_image_drowsiness_artifact_dir or "",
        enabled=settings.research_image_drowsiness_enabled,
        non_production=settings.environment in {"development", "test"},
    )
    processor=MultimodalFeatureProcessor(media_store,max_video_seconds=settings.max_video_seconds,max_audio_seconds=settings.max_audio_seconds,max_image_pixels=settings.max_image_pixels,max_video_frames=settings.max_video_frames,video_sample_hz=settings.video_sample_hz,decode_timeout_seconds=settings.decode_timeout_seconds,decode_memory_limit_mb=settings.decode_memory_limit_mb,analysis_timeout_seconds=settings.worker_drain_seconds,predictors=predictors,research_image_classifier=research_image_classifier)
    if settings.directus_pull_enabled:
        processor=DirectusPullProcessor(DirectusHTTPClient(settings.directus_url,settings.directus_service_token,timeout=settings.directus_timeout_seconds),media_store,processor)
    event(logging.getLogger("wellar.worker"),"worker.started",processing_stage="worker_startup")
    stop=threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM): signal.signal(sig,lambda *_: stop.set())
    try: Worker(store,f"worker-{uuid.uuid4()}",processor).run_forever(stop)
    finally:
        event(logging.getLogger("wellar.worker"),"worker.stopped",processing_stage="shutdown")
        processor.close(); media_store.close(); store.close()


if __name__=="__main__": main()
