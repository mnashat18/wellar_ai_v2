from pathlib import Path

import pytest

from app.queue.postgres_store import PostgresQueueStore


def test_postgres_pool_is_bounded_before_any_connection_is_opened():
    with pytest.raises(ValueError,match="pool size"):
        PostgresQueueStore("postgresql://invalid",max_pool_size=65,migrate=False)


def test_postgres_migrations_are_versioned_and_have_reverse_sql():
    root=Path(__file__).resolve().parents[1]/"deployment"/"migrations"
    up=(root/"001_initial.up.sql").read_text().lower()
    down=(root/"001_initial.down.sql").read_text().lower()
    for table in ("scans","jobs","results","outbox","media_assets"):
        assert f"create table {table}" in up
        assert f"drop table if exists {table}" in down
    assert "unique(scan_id,event_type)" in up
    assert "lease_until" in up and "fencing_token" in up


def test_directus_sync_migration_is_additive_and_has_unique_tenant_binding_and_delivery_leases():
    root=Path(__file__).resolve().parents[1]/"deployment"/"migrations"
    up=(root/"002_directus_sync.up.sql").read_text().lower()
    down=(root/"002_directus_sync.down.sql").read_text().lower()
    assert "create table directus_scan_bindings" in up
    assert "directus_scan_id text not null unique" in up
    assert "foreign key (scan_id, tenant_id, subject_id)" in up
    assert "delivery_status" in up and "fencing_token" in up and "lease_until" in up
    assert "for update" in (Path(__file__).resolve().parents[1]/"app"/"queue"/"postgres_store.py").read_text().lower()
    assert "drop table if exists directus_scan_bindings" in down
    assert "drop column if exists delivery_status" in down
