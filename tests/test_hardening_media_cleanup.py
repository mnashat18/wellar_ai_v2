from __future__ import annotations

import pytest

from app.media.analysis import MultimodalFeatureProcessor


class ResolveFailureStore:
    def __init__(self, delete_error: Exception | None = None):
        self.delete_calls = []
        self.delete_error = delete_error

    def resolve(self, *args, **kwargs):
        raise OSError("synthetic resolve failure")

    def delete(self, reference, tenant_id, subject_id):
        self.delete_calls.append((reference, tenant_id, subject_id))
        if self.delete_error:
            raise self.delete_error
        return True


def processor(store):
    return MultimodalFeatureProcessor(
        store,
        max_video_seconds=5,
        max_audio_seconds=5,
        max_image_pixels=100_000,
        max_video_frames=150,
        video_sample_hz=3,
    )


def test_resolve_exception_preserves_media_for_worker_retry():
    store = ResolveFailureStore()
    with pytest.raises(OSError, match="synthetic resolve failure"):
        processor(store)({
            "media_refs": ["media://tenant/ref"],
            "_worker_context": {"scan_id": "scan-test", "tenant_id": "tenant", "subject_id": "subject"},
        })
    assert store.delete_calls == []


def test_resolve_exception_does_not_delete_even_when_storage_cleanup_would_fail():
    store = ResolveFailureStore(delete_error=OSError("synthetic delete failure"))
    with pytest.raises(OSError, match="synthetic resolve failure"):
        processor(store)({
            "media_refs": ["media://tenant/ref"],
            "_worker_context": {"scan_id": "scan-test", "tenant_id": "tenant", "subject_id": "subject"},
        })
    assert store.delete_calls == []
