from __future__ import annotations

import logging
import time

from app.core.adapters import create_media_store,create_queue_store
from app.core.config import Settings
from app.core.logging import configure_logging,event


def main():
    settings=Settings.from_env();settings.validate();configure_logging(settings.log_level,settings.environment)
    store=create_queue_store(settings);media=create_media_store(settings,store)
    started=time.perf_counter()
    try:
        cutoff=time.time()-settings.media_retention_days*86400
        removed=media.purge_expired(cutoff)
        label_purge=getattr(store,"purge_expired_training_labels",None)
        expired_labels=label_purge() if callable(label_purge) else 0
        event(logging.getLogger("wellar.retention"),"media.retention.completed",processing_stage="retention",duration_ms=round((time.perf_counter()-started)*1000,3),item_count=removed,label_count=expired_labels)
    finally:
        if hasattr(media,"close"):media.close()
        store.close()


if __name__=="__main__":main()
