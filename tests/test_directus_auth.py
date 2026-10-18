from __future__ import annotations

import json
from io import BytesIO
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
import pytest

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.security.auth import (
    AuthenticationProviderUnavailable,
    DirectusAuthenticator,
    DirectusPermissionDenied,
    Principal,
    TenantAuthorizationDenied,
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


def directus_error(url, status, code):
    body = json.dumps({"errors": [{"extensions": {"code": code}}]}).encode()
    return HTTPError(url, status, "Directus error", {}, BytesIO(body))


def user(subject="employee-1", tenant="company-1"):
    return {"data": {"id": subject, "status": "active"}}


def membership(subject="employee-1", tenant="company-1", status="active"):
    return {"data": [{"id": "membership-1", "user": subject,
                      "business_profile": tenant, "status": status}]}


def test_directus_authenticates_opaque_session_and_checks_membership_each_request():
    opener = FakeOpener([user(), membership(), user(), {"data": []}])
    auth = DirectusAuthenticator("https://directus.example", opener=opener)
    assert auth.authenticate("opaque-session-token") == Principal("employee-1", "company-1")
    # Membership removal is observed on the next call: no cached authorization.
    with pytest.raises(TenantAuthorizationDenied):
        auth.authenticate("opaque-session-token")
    assert len(opener.requests) == 4
    assert all(req.get_header("Authorization") == "Bearer opaque-session-token" for req, _ in opener.requests)
    assert all(timeout == 3.0 for _, timeout in opener.requests)
    user_me_url=urlparse(opener.requests[0][0].full_url)
    assert user_me_url.path=="/users/me"
    assert parse_qs(user_me_url.query)["fields"]==["id,status"]
    membership_url=urlparse(opener.requests[1][0].full_url)
    assert membership_url.path=="/items/business_profile_members"
    query=parse_qs(membership_url.query)
    assert query["fields"]==["id,user,business_profile,status"]
    assert query["filter[user][_eq]"]==["employee-1"]
    assert query["filter[status][_eq]"]==["active"]


def test_directus_rejects_inactive_user_mismatched_or_ambiguous_membership():
    auth = DirectusAuthenticator("https://directus.example", opener=FakeOpener([
        {"data": {"id": "employee-1", "status": "suspended", "active_business_profile": "company-1"}},
        user(), membership(subject="another-user"),
        user(), {"data": [{}, {}]},
    ]))
    with pytest.raises(TenantAuthorizationDenied):
        auth.authenticate("opaque")
    with pytest.raises(TenantAuthorizationDenied):
        auth.authenticate("opaque")
    with pytest.raises(TenantAuthorizationDenied):
        auth.authenticate("opaque")


def test_directus_denies_multiple_active_workspaces_without_choosing_one():
    auth = DirectusAuthenticator("https://directus.example", opener=FakeOpener([
        user(),
        {"data": [
            {"id": "membership-1", "user": "employee-1",
             "business_profile": "company-1", "status": "active"},
            {"id": "membership-2", "user": "employee-1",
             "business_profile": "company-2", "status": "active"},
        ]},
    ]))
    with pytest.raises(TenantAuthorizationDenied):
        auth.authenticate("opaque")


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
    try:
        auth.authenticate("opaque")
    except DirectusPermissionDenied:
        pass
    else:
        raise AssertionError("membership permission denial must remain a tenant authorization error")


def test_auth_diagnostics_report_stages_without_sensitive_values(caplog):
    caplog.set_level("INFO")
    secret_values = ("opaque-secret-token", "employee-private", "tenant-private", "private@example.test")
    auth = DirectusAuthenticator("https://directus.example", opener=FakeOpener([
        user(subject=secret_values[1], tenant=secret_values[2]),
        membership(subject=secret_values[1], tenant=secret_values[2]),
    ]))
    assert auth.authenticate(secret_values[0]) == Principal(secret_values[1], secret_values[2])
    text = " ".join(record.getMessage() + str(getattr(record, "event_fields", {})) for record in caplog.records)
    assert "auth.directus_users_me_request" in text
    assert "auth.directus_users_me_status" in text
    assert "auth.user_status_validation" in text
    assert "auth.membership_query_status" in text
    assert "auth.membership_validation" in text
    assert all(value not in text for value in secret_values)


def test_directus_rejection_stages_are_distinguished_in_safe_logs(caplog):
    caplog.set_level("INFO")
    cases = [
        ([HTTPError("https://directus.example/users/me", 401, "unauthorized", {}, None)], "auth.directus_users_me_status"),
        ([{"data": {"id": "employee-1", "status": "suspended", "active_business_profile": "company-1"}}], "auth.user_status_validation"),
        ([{"data": {"id": "employee-1"}}], "auth.user_status_validation"),
        ([user(), membership(status="inactive")], "auth.membership_validation"),
        ([user(), membership(subject="another-user")], "auth.membership_validation"),
    ]
    for responses, expected_event in cases:
        caplog.clear()
        try:
            DirectusAuthenticator("https://directus.example", opener=FakeOpener(responses)).authenticate("safe-test-token")
        except TenantAuthorizationDenied:
            pass
        except AuthenticationProviderUnavailable:
            pass
        assert any(expected_event in record.getMessage() for record in caplog.records)
        assert "safe-test-token" not in caplog.text


def test_membership_permission_failure_maps_to_nonretryable_gateway_error():
    settings = Settings(environment="test", db_path=":memory:", auth_mode="directus",
                        directus_url="https://directus.example")
    client = TestClient(create_app(settings=settings, authenticator=DirectusAuthenticator(
        "https://directus.example", opener=FakeOpener([
            user(), HTTPError("https://directus.example/items/business_profile_members", 403, "forbidden", {}, None)
        ]))))
    response = client.get("/api/v1/scans/any", headers={"Authorization": "Bearer opaque"})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "directus_membership_permission_denied"
    assert "opaque" not in response.text


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


def test_directus_invalid_token_code_maps_403_to_normal_auth_rejection(capsys):
    settings = Settings(environment="test", db_path=":memory:", auth_mode="directus",
                        directus_url="https://directus.example")
    client = TestClient(create_app(settings=settings, authenticator=DirectusAuthenticator(
        "https://directus.example", opener=FakeOpener([
            directus_error("https://directus.example/users/me", 403, "INVALID_TOKEN"),
        ]))))
    response = client.get("/api/v1/scans/any", headers={"Authorization": "Bearer fake-token"})
    logs = capsys.readouterr().err
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"
    assert "fake-token" not in response.text + logs
    assert '"processing_stage":"auth.directus_users_me_status"' in logs
    assert '"directus_error_code":"INVALID_TOKEN"' in logs
    assert '"error_code":"invalid_or_expired_token"' in logs


@pytest.mark.parametrize("stage,url", [
    ("auth.directus_users_me_status", "https://directus.example/users/me"),
    ("auth.membership_query_status", "https://directus.example/items/business_profile_members"),
])
def test_directus_forbidden_is_permission_failure_not_invalid_token(stage, url):
    results = ([directus_error(url, 403, "FORBIDDEN")] if stage == "auth.directus_users_me_status"
               else [user(), directus_error(url, 403, "FORBIDDEN")])
    auth = DirectusAuthenticator("https://directus.example", opener=FakeOpener(results))
    with pytest.raises(DirectusPermissionDenied):
        auth.authenticate("synthetic-token")


def test_directus_forbidden_permission_failure_maps_to_nonretryable_gateway_error(capsys):
    settings = Settings(environment="test", db_path=":memory:", auth_mode="directus",
                        directus_url="https://directus.example")
    client = TestClient(create_app(settings=settings, authenticator=DirectusAuthenticator(
        "https://directus.example", opener=FakeOpener([
            directus_error("https://directus.example/users/me", 403, "FORBIDDEN"),
        ]))))
    response = client.get("/api/v1/scans/any", headers={"Authorization": "Bearer synthetic-token"})
    logs = capsys.readouterr().err
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "directus_identity_permission_denied"
    assert response.json()["error"]["retryable"] is False
    assert "synthetic-token" not in logs + response.text
    assert "Directus error" not in logs + response.text
    assert '"directus_error_code":"FORBIDDEN"' in logs
    assert '"status_code":403' in logs


def test_directus_invalid_token_at_membership_stage_maps_to_401():
    settings = Settings(environment="test", db_path=":memory:", auth_mode="directus",
                        directus_url="https://directus.example")
    client = TestClient(create_app(settings=settings, authenticator=DirectusAuthenticator(
        "https://directus.example", opener=FakeOpener([
            user(), directus_error("https://directus.example/items/business_profile_members", 403, "INVALID_TOKEN"),
        ]))))
    response = client.get("/api/v1/scans/any", headers={"Authorization": "Bearer fake-token"})
    assert response.status_code == 401


def test_directus_invalid_payload_and_oversized_token_fail_closed():
    auth = DirectusAuthenticator("https://directus.example", opener=FakeOpener([
        {"unexpected": "shape"}
    ]))
    with pytest.raises(AuthenticationProviderUnavailable):
        auth.authenticate("opaque")
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


@pytest.mark.parametrize("responses,expected_status", [
    ([user(), membership()], 404),
    ([HTTPError("https://directus.example/users/me", 401, "unauthorized", {}, None)], 401),
    ([{"data": {"id": "employee-1", "status": "suspended", "active_business_profile": "company-1"}}], 403),
    ([{"data": {"id": "employee-1", "active_business_profile": "company-1"}}], 503),
    ([{"data": {"id": "employee-1", "status": "active"}}, {"data": []}], 403),
    ([user(), membership(subject="another-user")], 403),
    ([user(), membership(tenant="another-company")], 404),
    ([user(), membership(status="inactive")], 403),
    ([user(), HTTPError("https://directus.example/items/business_profile_members", 403, "forbidden", {}, None)], 502),
    ([user(), {"data": [{"id": "membership-1", "status": "active"}]}], 503),
    ([directus_error("https://directus.example/users/me", 403, "INVALID_TOKEN")], 401),
    ([directus_error("https://directus.example/users/me", 403, "FORBIDDEN")], 502),
    ([HTTPError("https://directus.example/users/me", 403, "forbidden", {}, None)], 502),
    ([directus_error("https://directus.example/users/me", 503, "INVALID_TOKEN")], 503),
    ([HTTPError("https://directus.example/users/me", 429, "limited", {}, None)], 503),
    ([HTTPError("https://directus.example/users/me", 503, "unavailable", {}, None)], 503),
    ([URLError("provider unavailable")], 503),
])
def test_directus_auth_status_mapping_and_safe_request_events(responses,expected_status,capsys):
    token="synthetic-auth-token"
    settings=Settings(environment="test",db_path=":memory:",auth_mode="directus",
                       directus_url="https://directus.example")
    app=create_app(settings=settings,authenticator=DirectusAuthenticator(
        "https://directus.example",opener=FakeOpener(responses)))
    with TestClient(app) as client:
        response=client.get("/api/v1/scans/not-present",headers={"Authorization":f"Bearer {token}"})
    log_text=capsys.readouterr().err
    assert response.status_code==expected_status
    assert token not in log_text and token not in response.text
    assert "auth.token_present" in log_text
    if expected_status==404:
        assert "auth.accepted" in log_text
    elif expected_status in {401,403,503}:
        assert "auth.rejected" in log_text
    if expected_status==401:
        assert response.headers.get("www-authenticate")=="Bearer"
    if expected_status==403:
        assert response.json()["error"]["code"]=="tenant_access_denied"
    if expected_status==503:
        assert response.json()["error"]["code"]=="identity_provider_unavailable"
