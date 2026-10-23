from __future__ import annotations

import logging
import os
from pathlib import Path
import signal
import tempfile
import threading
import time
import uuid

from app.core.config import Settings
from app.core.logging import configure_logging,event
from app.media.analysis import MultimodalFeatureProcessor
from app.core.adapters import create_media_store,create_queue_store
from app.workers.worker import Worker
from app.compat.directus_facade import DirectusHTTPClient,DirectusPullProcessor


def readiness_marker_path(pid: int | None=None) -> Path:
    return Path(tempfile.gettempdir())/f"wellar-worker-ready-{pid or os.getpid()}"


def main() -> None:
    settings=Settings.from_env()
    configure_logging(settings.log_level,settings.environment)
    logger=logging.getLogger("wellar.worker")
    stop=threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM): signal.signal(sig,lambda *_: stop.set())

    store=media_store=processor=None
    worker_id=f"worker-{uuid.uuid4()}"
    ready_path=readiness_marker_path()
    ready_path.unlink(missing_ok=True)
    started=time.perf_counter()
    try:
        # Import native runtimes before Worker starts lease-heartbeat threads.
        import numpy  # noqa: F401
        store=create_queue_store(settings)
        media_store=create_media_store(settings,store)
        # Stage 1 runs media feature extraction only; no prediction artifacts load.
        processor=MultimodalFeatureProcessor(media_store,max_video_seconds=settings.max_video_seconds,max_audio_seconds=settings.max_audio_seconds,max_image_pixels=settings.max_image_pixels,max_video_frames=settings.max_video_frames,video_sample_hz=settings.video_sample_hz,decode_timeout_seconds=settings.decode_timeout_seconds,decode_memory_limit_mb=settings.decode_memory_limit_mb,analysis_timeout_seconds=settings.worker_drain_seconds)
        processor.warmup()
        if settings.directus_pull_enabled:
            processor=DirectusPullProcessor(DirectusHTTPClient(settings.directus_url,settings.directus_service_token,timeout=settings.directus_timeout_seconds),media_store,processor)
        worker=Worker(store,worker_id,processor,
            reconciliation_page_size=settings.media_reconciliation_page_size,
            reconciliation_max_pages=settings.media_reconciliation_max_pages,
            reconciliation_max_references=settings.media_reconciliation_max_references,
            reconciliation_lease_seconds=settings.media_reconciliation_lease_seconds,
            reconciliation_delete_attempts=settings.media_delete_retry_attempts,
            reconciliation_delete_retry_deadline_seconds=settings.media_delete_retry_deadline_seconds,
            reconciliation_retry_initial_seconds=settings.media_reconciliation_retry_initial_seconds,
            reconciliation_retry_multiplier=settings.media_reconciliation_retry_multiplier,
            reconciliation_retry_max_seconds=settings.media_reconciliation_retry_max_seconds,
            reconciliation_retry_jitter=settings.media_reconciliation_retry_jitter,
            reconciliation_stop_event=stop)
        ready_path.write_text(f"{worker_id}\n",encoding="ascii")
        event(logger,"worker.ready",processing_stage="worker_readiness",worker_id=worker_id,
            worker_count=settings.worker_count,duration_ms=round((time.perf_counter()-started)*1000,3))
        worker.run_forever(stop)
    except Exception as exc:
        event(logger,"worker.process.failed",logging.ERROR,processing_stage="worker_startup" if not ready_path.exists() else "worker_runtime",
            error_code="worker_process_failed",exception_type=type(exc).__name__,worker_id=worker_id)
        raise
    finally:
        ready_path.unlink(missing_ok=True)
        event(logger,"worker.stopped",processing_stage="shutdown",worker_id=worker_id)
        if processor is not None:
            try:processor.close()
            except Exception as exc:event(logger,"worker.processor.close_failed",logging.ERROR,processing_stage="shutdown",error_code="worker_close_failed",exception_type=type(exc).__name__)
        if media_store is not None:
            try:media_store.close()
            except Exception as exc:event(logger,"worker.media_store.close_failed",logging.ERROR,processing_stage="shutdown",error_code="worker_close_failed",exception_type=type(exc).__name__)
        if store is not None:
            try:store.close()
            except Exception as exc:event(logger,"worker.queue_store.close_failed",logging.ERROR,processing_stage="shutdown",error_code="worker_close_failed",exception_type=type(exc).__name__)


if __name__=="__main__":main()
