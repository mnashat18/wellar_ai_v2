from fastapi.testclient import TestClient

from app.main import create_app
from tests.media_fixtures import image_bytes


def test_self_test_page_is_loopback_test_only_and_no_store(settings, store):
    app = create_app(settings, store=store)
    with TestClient(app, client=("127.0.0.1", 32100)) as local:
        page = local.get("/self-test")
        assert page.status_code == 200
        assert page.headers["cache-control"] == "no-store, max-age=0"
        assert "connect-src 'self'" in page.headers["content-security-policy"]
        assert "not a validated PVT" in page.text
        assert "insufficient_evidence" in page.text
        assert "media_devices" not in page.text  # no simulated browser capability
        assert "fetch(\"http" not in page.text and "fetch('http" not in page.text
    with TestClient(app, client=("192.0.2.10", 32100)) as remote:
        assert remote.get("/self-test").status_code == 404


def test_self_test_page_is_disabled_outside_test_environment(settings, store):
    from dataclasses import replace
    app = create_app(replace(settings, environment="development"), store=store)
    with TestClient(app, client=("127.0.0.1", 32101)) as client:
        assert client.get("/self-test").status_code == 404


def test_authenticated_media_deletion_enforces_subject_tenant_and_is_idempotently_missing(client, auth):
    response = client.post("/api/v1/media/image", headers={**auth, "Content-Type": "image/jpeg"}, content=image_bytes())
    assert response.status_code == 201
    reference = response.json()["media_ref"]
    asset = client.app.state.media_store.resolve(reference, "tenant-a", "user-a")
    assert asset and asset["path"].is_file()

    assert client.delete("/api/v1/media", params={"media_ref": reference}).status_code == 401
    other_tenant = {"Authorization": "Bearer token-other"}
    assert client.delete("/api/v1/media", params={"media_ref": reference}, headers=other_tenant).status_code == 404
    assert asset["path"].is_file()
    assert client.delete("/api/v1/media", params={"media_ref": reference}, headers=auth).status_code == 204
    assert not asset["path"].exists()
    assert client.app.state.media_store.resolve(reference, "tenant-a", "user-a") is None
    assert client.delete("/api/v1/media", params={"media_ref": reference}, headers=auth).status_code == 404


def test_failed_capture_upload_leaves_no_partial_or_registered_media(client, auth):
    response = client.post("/api/v1/media/image", headers={**auth, "Content-Type": "image/jpeg"}, content=b"not-an-image")
    assert response.status_code == 422
    assert client.app.state.store.db.execute("SELECT COUNT(*) FROM media_assets").fetchone()[0] == 0
    assert not list(client.app.state.media_store.root.rglob("*.partial"))
