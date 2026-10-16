import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.queue.sqlite_store import SQLiteQueueStore


@pytest.fixture
def store(tmp_path):
    db=SQLiteQueueStore(str(tmp_path/"test.sqlite"),capacity=20,max_attempts=2,lease_seconds=1,retry_base_seconds=0.001)
    yield db
    db.close()


@pytest.fixture
def settings(tmp_path):
    return Settings(environment="test",db_path=str(tmp_path/"app.sqlite"),media_root=str(tmp_path/"media"),queue_capacity=20,max_attempts=2,lease_seconds=1,retry_base_seconds=0.001,dev_auth_tokens={"token-a":{"sub":"user-a","tenant_id":"tenant-a"},"token-b":{"sub":"user-b","tenant_id":"tenant-b"},"token-other":{"sub":"user-a","tenant_id":"tenant-b"},"token-c":{"sub":"user-c","tenant_id":"tenant-a"}})


@pytest.fixture
def client(settings,store):
    app=create_app(settings,store=store)
    with TestClient(app) as c: yield c


@pytest.fixture
def auth(): return {"Authorization":"Bearer token-a"}
