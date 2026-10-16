from __future__ import annotations

from typing import Protocol


class MediaReferenceProvider(Protocol):
    """Resolve an authorized opaque media reference; no raw media is queued."""
    def assert_accessible(self, tenant_id: str, reference: str) -> bool: ...


class DirectusAdapter(Protocol):
    """Future external data-provider boundary; deliberately unconfigured in Sprint 01."""
    def health(self) -> bool: ...


class MockDirectusAdapter:
    """In-memory test double; never contacts a Directus server."""
    def __init__(self, available: bool = True): self.available=available
    def health(self) -> bool: return self.available
    def assert_accessible(self, tenant_id: str, reference: str) -> bool:
        return self.available and reference.startswith(f"media://{tenant_id}/")
