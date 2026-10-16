from __future__ import annotations

import json
from urllib.error import HTTPError, URLError

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.security.auth import (
    AuthenticationProviderUnavailable,
    DirectusAuthenticator,
    Principal,
)


class FakeResponse:
    status = 200

    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, size=-1):
        return self.payload[:size]


class FakeOpener:
    def __init__(self, results):
        self.results = list(results)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return FakeResponse(result)


def user(subject="employee-1", tenant="company-1"):
    return {"data": {"id": subject, "status": "active", "active_business_profile": tenant}}


def membership(subject="employee-1", tenant="company-1", status="active"):
    return {"data": [{"id": "membership-1", "user": subject,
                      "business_profile": tenant, "status": status}]}


def test_directus_authenticates_opaque_session_and_checks_membership_each_request():
    opener = FakeOpener([user(), membership(), user(), {"data": []}])
    auth = DirectusAuthenticator("https://directus.example", opener=opener)
    assert auth.authenticate("opaque-session-token") == Principal("employee-1", "company-1")
    # Membership removal is observed on the next call: no cached authorization.
    assert auth.authenticate("opaque-session-token") is None
    assert len(opener.requests) == 4
    assert all(req.get_header("Authorization") == "Bearer opaque-session-token" for req, _ in opener.requests)
    assert all(timeout == 3.0 for _, timeout in opener.requests)
    assert "company-1" in opener.requests[1][0].full_url


def test_directus_rejects_inactive_user_mismatched_or_ambiguous_membership():
    auth = DirectusAuthenticator("https://directus.example", opener=FakeOpener([
        {"data": {"id": "employee-1", "status": "suspended", "active_business_profile": "company-1"}},
        user(), membership(subject="another-user"),
        user(), {"data": [{}, {}]},
    ]))
    assert auth.authenticate("opaque") is None
    assert auth.authenticate("opaque") is None
    assert auth.authenticate("opaque") is None


def test_expired_or_revoked_directus_session_fails_closed():
    auth = DirectusAuthenticator("https://directus.example", opener=FakeOpener([
        HTTPError("https://directus.example/users/me", 401, "unauthorized", {}, None),
    ]))
    assert auth.authenticate("expired-or-revoked") is None


def test_changed_directus_permission_denies_membership_lookup():
    auth = DirectusAuthenticator("https://directus.example", opener=FakeOpener([
        user(),
        HTTPError("https://directus.example/items/business_profile_members", 403, "forbidden", {}, None),
    ]))
    assert auth.authenticate("opaque") is None


def test_directus_outage_is_distinguished_without_provider_details():
    auth = DirectusAuthenticator("https://directus.example", opener=FakeOpener([
        URLError("sensitive-provider-error")
    ]))
    try:
        auth.authenticate("opaque")
    except AuthenticationProviderUnavailable as exc:
        assert str(exc) == ""
    else:
        raise AssertionError("provider outage must fail closed as unavailable")


def test_directus_rate_limit_is_provider_unavailable_not_invalid_session():
    auth = DirectusAuthenticator("https://directus.example", opener=FakeOpener([
        HTTPError("https://directus.example/users/me", 429, "limited", {}, None),
    ]))
    try:
        auth.authenticate("opaque")
    except AuthenticationProviderUnavailable:
        pass
    else:
        raise AssertionError("rate limiting must not be mistaken for an invalid token")


def test_directus_invalid_payload_and_oversized_token_fail_closed():
    auth = DirectusAuthenticator("https://directus.example", opener=FakeOpener([
        {"unexpected": "shape"}
    ]))
    assert auth.authenticate("opaque") is None
    assert auth.authenticate("x" * 16_385) is None
    assert auth.authenticate("bad\r\nheader") is None


def test_directus_mode_uses_live_session_auth_and_keeps_scan_subject_private():
    auth = DirectusAuthenticator("https://directus.example", opener=FakeOpener([
        user(subject="owner", tenant="company-1"), membership(subject="owner"),
        user(subject="other", tenant="company-2"), membership(subject="other", tenant="company-2"),
    ]))
    settings = Settings(environment="test", db_path=":memory:", auth_mode="directus",
                        directus_url="https://directus.example")
    with TestClient(create_app(settings=settings, authenticator=auth)) as client:
        created = client.post("/api/v1/scans", headers={"Authorization": "Bearer session-a",
                                 "Idempotency-Key": "directus-private-1"}, json={"media_refs": []})
        assert created.status_code == 202
        scan_id = created.json()["scan_id"]
        status = client.get(f"/api/v1/scans/{scan_id}", headers={"Authorization": "Bearer session-b",
                                "X-User-Id": "owner", "X-Tenant-Id": "company-1",
                                "X-Role": "Admin", "X-Permissions": "scan.read.all"})
        assert status.status_code == 404


def test_directus_outage_returns_redacted_retryable_service_error():
    settings = Settings(environment="test", db_path=":memory:", auth_mode="directus",
                        directus_url="https://directus.example")
    client = TestClient(create_app(settings=settings, authenticator=DirectusAuthenticator(
        "https://directus.example", opener=FakeOpener([URLError("do-not-leak")]))))
    response = client.get("/api/v1/scans/any", headers={"Authorization": "Bearer opaque"})
    assert response.status_code == 503
    assert response.json()["error"]["retryable"] is True
    assert "do-not-leak" not in response.text
    assert "opaque" not in response.text
