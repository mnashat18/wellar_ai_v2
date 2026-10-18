from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from app.core.logging import event

import jwt
from jwt import PyJWKClient

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


@dataclass(frozen=True)
class Principal:
    subject_id: str
    tenant_id: str


class Authenticator(Protocol):
    def authenticate(self, token: str) -> Principal | None: ...


class AuthenticationProviderUnavailable(Exception):
    """An identity provider could not establish the caller's current session."""


class TenantAuthorizationDenied(Exception):
    """A valid session does not have a verifiable active tenant membership."""


_auth_logger = logging.getLogger("wellar.auth")


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class DirectusAuthenticator:
    """Validate a Directus session and active membership using the caller token.

    No token parsing, service credential, permission cache, or privileged fallback is used.
    """
    _MAX_RESPONSE_BYTES = 64 * 1024

    def __init__(self, base_url: str, timeout: float = 3.0, opener=None):
        if not base_url or timeout <= 0:
            raise ValueError("Directus URL and positive timeout are required")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._opener = opener or build_opener(_NoRedirectHandler())

    def _get_json(self, path: str, token: str, *, stage: str) -> dict | None:
        request = Request(
            self.base_url + path,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            method="GET",
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                event(_auth_logger, stage, status_code=response.status)
                if response.status != 200:
                    if response.status == 401:
                        return None
                    raise AuthenticationProviderUnavailable
                raw = response.read(self._MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            exc.close()
            event(_auth_logger, stage, status_code=exc.code)
            if stage == "auth.membership_query_status" and exc.code == 403:
                raise TenantAuthorizationDenied from None
            if exc.code == 401:
                return None
            if exc.code == 403:
                raise AuthenticationProviderUnavailable from None
            if exc.code == 429 or exc.code >= 500:
                raise AuthenticationProviderUnavailable from None
            if 400 <= exc.code < 500:
                # Endpoint/schema/configuration failures are not evidence of a bad session.
                raise AuthenticationProviderUnavailable from None
            raise AuthenticationProviderUnavailable from None
        except (URLError, TimeoutError, OSError):
            raise AuthenticationProviderUnavailable from None
        if len(raw) > self._MAX_RESPONSE_BYTES:
            raise AuthenticationProviderUnavailable
        try:
            decoded = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise AuthenticationProviderUnavailable from None
        if not isinstance(decoded, dict):
            raise AuthenticationProviderUnavailable
        return decoded

    @staticmethod
    def _relation_id(value) -> str | None:
        if isinstance(value, dict):
            value = value.get("id")
        return value if isinstance(value, str) and value else None

    def authenticate(self, token: str) -> Principal | None:
        if (not token or len(token) > 16_384
                or any(ord(char) < 33 or ord(char) == 127 for char in token)):
            return None
        event(_auth_logger, "auth.directus_users_me_request")
        user_response = self._get_json("/users/me?fields=id,status,active_business_profile", token,
                                       stage="auth.directus_users_me_status")
        if user_response is None:
            return None
        user = user_response.get("data")
        if not isinstance(user, dict):
            event(_auth_logger, "auth.user_status_validation", error_code="response_unavailable")
            raise AuthenticationProviderUnavailable
        user_status = user.get("status")
        if not isinstance(user_status, str) or not user_status:
            event(_auth_logger, "auth.user_status_validation", error_code="field_unavailable")
            raise AuthenticationProviderUnavailable
        if user_status != "active":
            event(_auth_logger, "auth.user_status_validation", error_code="inactive")
            raise TenantAuthorizationDenied
        event(_auth_logger, "auth.user_status_validation", error_code="active")
        subject_id = user.get("id")
        tenant_id = self._relation_id(user.get("active_business_profile"))
        if not isinstance(subject_id, str) or not subject_id or len(subject_id) > 256:
            event(_auth_logger, "auth.user_status_validation", error_code="identity_field_unavailable")
            raise AuthenticationProviderUnavailable
        if "active_business_profile" not in user:
            event(_auth_logger, "auth.active_business_profile_validation", error_code="field_unavailable")
            raise AuthenticationProviderUnavailable
        if not tenant_id or len(tenant_id) > 256:
            event(_auth_logger, "auth.active_business_profile_validation", error_code="missing_or_invalid")
            raise TenantAuthorizationDenied
        event(_auth_logger, "auth.active_business_profile_validation", error_code="valid")

        query = urlencode({
            "fields": "id,user,business_profile,status",
            "filter[user][_eq]": subject_id,
            "filter[business_profile][_eq]": tenant_id,
            "filter[status][_eq]": "active",
            "limit": "2",
        })
        memberships_response = self._get_json("/items/business_profile_members?" + query, token,
                                              stage="auth.membership_query_status")
        rows = memberships_response.get("data") if memberships_response else None
        if not isinstance(rows, list):
            event(_auth_logger, "auth.membership_validation", error_code="response_unavailable")
            raise AuthenticationProviderUnavailable
        if len(rows) != 1:
            event(_auth_logger, "auth.membership_validation", error_code="missing_or_ambiguous")
            raise TenantAuthorizationDenied
        membership = rows[0]
        if not isinstance(membership, dict) or any(
                field not in membership for field in ("user", "business_profile", "status")):
            event(_auth_logger, "auth.membership_validation", error_code="field_unavailable")
            raise AuthenticationProviderUnavailable
        if (membership.get("status") != "active"
                or self._relation_id(membership.get("user")) != subject_id
                or self._relation_id(membership.get("business_profile")) != tenant_id):
            event(_auth_logger, "auth.membership_validation", error_code="inactive_or_mismatched")
            raise TenantAuthorizationDenied
        event(_auth_logger, "auth.membership_validation", error_code="valid")
        return Principal(subject_id=subject_id, tenant_id=tenant_id)


class DevelopmentAuthenticator:
    """Local-only token map; replace with a verified identity provider adapter."""
    def __init__(self, tokens: dict[str, dict[str, str]]):
        self._tokens = tokens

    def authenticate(self, token: str) -> Principal | None:
        entry = self._tokens.get(token)
        if not entry:
            return None
        return Principal(subject_id=entry["sub"], tenant_id=entry["tenant_id"])


class JWTAuthenticator:
    """OIDC/JWKS verifier. Identity is accepted only after signature and claim validation."""
    def __init__(self,*,issuer:str,audience:str,jwks_uri:str,tenant_claim:str="tenant_id",
                 algorithms:tuple[str,...]=( "RS256",),jwks_timeout:float=3.0,jwks_client=None):
        if not issuer or not audience or not jwks_uri or not tenant_claim: raise ValueError("OIDC issuer, audience, JWKS URI and tenant claim are required")
        if not algorithms or any(alg not in {"RS256","RS384","RS512","ES256","ES384","EdDSA"} for alg in algorithms):
            raise ValueError("only configured asymmetric JWT algorithms are allowed")
        self.issuer,self.audience,self.tenant_claim=issuer,audience,tenant_claim
        self.algorithms=tuple(algorithms)
        self.jwks=jwks_client or PyJWKClient(jwks_uri,timeout=jwks_timeout,cache_jwk_set=True, lifespan=300)

    def authenticate(self,token:str)->Principal|None:
        try:
            header=jwt.get_unverified_header(token)
            if header.get("alg") not in self.algorithms or not header.get("kid"):return None
            key=self.jwks.get_signing_key_from_jwt(token).key
            claims=jwt.decode(token,key,algorithms=list(self.algorithms),issuer=self.issuer,audience=self.audience,
                              options={"require":["exp","iat","sub",self.tenant_claim],"verify_signature":True},leeway=0)
            subject,tenant=claims.get("sub"),claims.get(self.tenant_claim)
            if not isinstance(subject,str) or not subject or not isinstance(tenant,str) or not tenant:return None
            return Principal(subject_id=subject,tenant_id=tenant)
        except Exception:
            # JWKS transport/client failures must fail closed just like invalid tokens.
            # Provider exception messages are intentionally neither returned nor logged.
            return None


bearer = HTTPBearer(auto_error=False)


def principal_dependency(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Principal:
    # The app factory replaces this dependency with an app-bound authenticator.
    raise HTTPException(status_code=401, detail="authentication required", headers={"WWW-Authenticate": "Bearer"})
