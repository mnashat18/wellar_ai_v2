from datetime import datetime,timezone,timedelta

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.exceptions import PyJWKClientConnectionError

from app.security.auth import AuthenticationProviderUnavailable, JWTAuthenticator


class Key:
    def __init__(self,key): self.key=key


class LocalJwks:
    def __init__(self,key):self.key=key
    def get_signing_key_from_jwt(self,token):return Key(self.key)


def test_oidc_jwt_verifies_signature_issuer_audience_expiry_and_tenant_claim():
    private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    now=datetime.now(timezone.utc)
    claims={"iss":"https://issuer.invalid","aud":"wellar-api","sub":"subject-7","tenant_id":"tenant-9","iat":now,"exp":now+timedelta(minutes=2)}
    verifier=JWTAuthenticator(issuer=claims["iss"],audience=claims["aud"],jwks_uri="https://issuer.invalid/keys",jwks_client=LocalJwks(private.public_key()))
    token=jwt.encode(claims,private,algorithm="RS256",headers={"kid":"test-key"})
    principal=verifier.authenticate(token)
    assert (principal.subject_id,principal.tenant_id)==("subject-7","tenant-9")
    assert verifier.authenticate(jwt.encode({**claims,"aud":"other"},private,algorithm="RS256",headers={"kid":"test-key"})) is None
    assert verifier.authenticate(jwt.encode({**claims,"exp":now-timedelta(seconds=1)},private,algorithm="RS256",headers={"kid":"test-key"})) is None


def test_oidc_rejects_hmac_and_missing_kid():
    private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    verifier=JWTAuthenticator(issuer="issuer",audience="aud",jwks_uri="https://issuer/keys",jwks_client=LocalJwks(private.public_key()))
    hmac_token=jwt.encode({"iss":"issuer","aud":"aud","sub":"s","tenant_id":"t","exp":9999999999,"iat":1},"secret",algorithm="HS256",headers={"kid":"test"})
    assert verifier.authenticate(hmac_token) is None
    token=jwt.encode({"iss":"issuer","aud":"aud","sub":"s","tenant_id":"t","exp":9999999999,"iat":1},private,algorithm="RS256",headers={"kid":"test"})
    no_kid=jwt.encode({"iss":"issuer","aud":"aud","sub":"s","tenant_id":"t","exp":9999999999,"iat":1},private,algorithm="RS256")
    assert verifier.authenticate(no_kid) is None


def test_oidc_fails_closed_for_forged_signature_issuer_missing_claim_and_malformed_token():
    trusted=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    forged=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    verifier=JWTAuthenticator(issuer="https://trusted.invalid",audience="wellar-api",jwks_uri="https://trusted.invalid/keys",jwks_client=LocalJwks(trusted.public_key()))
    now=datetime.now(timezone.utc)
    claims={"iss":"https://trusted.invalid","aud":"wellar-api","sub":"subject","tenant_id":"tenant","iat":now,"exp":now+timedelta(minutes=1)}
    assert verifier.authenticate(jwt.encode(claims,forged,algorithm="RS256",headers={"kid":"key"})) is None
    assert verifier.authenticate(jwt.encode({**claims,"iss":"https://attacker.invalid"},trusted,algorithm="RS256",headers={"kid":"key"})) is None
    assert verifier.authenticate(jwt.encode({key:value for key,value in claims.items() if key!="tenant_id"},trusted,algorithm="RS256",headers={"kid":"key"})) is None
    assert verifier.authenticate("not-a-jwt") is None


def test_oidc_rejects_not_yet_valid_future_issued_and_missing_expiry_tokens():
    trusted=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    verifier=JWTAuthenticator(issuer="https://issuer.invalid",audience="wellar-api",jwks_uri="https://issuer.invalid/keys",jwks_client=LocalJwks(trusted.public_key()))
    now=datetime.now(timezone.utc)
    claims={"iss":"https://issuer.invalid","aud":"wellar-api","sub":"subject","tenant_id":"tenant","iat":now,"exp":now+timedelta(minutes=2)}
    not_yet_valid=jwt.encode({**claims,"nbf":now+timedelta(minutes=1)},trusted,algorithm="RS256",headers={"kid":"key"})
    future_issued=jwt.encode({**claims,"iat":now+timedelta(minutes=1)},trusted,algorithm="RS256",headers={"kid":"key"})
    no_expiry=jwt.encode({key:value for key,value in claims.items() if key!="exp"},trusted,algorithm="RS256",headers={"kid":"key"})
    assert verifier.authenticate(not_yet_valid) is None
    assert verifier.authenticate(future_issued) is None
    assert verifier.authenticate(no_expiry) is None


def test_oidc_classifies_jwks_transport_outage_as_provider_unavailable():
    trusted=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    class UnavailableJwks:
        def get_signing_key_from_jwt(self,token): raise PyJWKClientConnectionError("provider details must not escape")
    unavailable=JWTAuthenticator(issuer="issuer",audience="aud",jwks_uri="https://issuer/keys",jwks_client=UnavailableJwks())
    token=jwt.encode({"iss":"issuer","aud":"aud","sub":"subject","tenant_id":"tenant","exp":9999999999,"iat":1},trusted,algorithm="RS256",headers={"kid":"test"})
    with pytest.raises(AuthenticationProviderUnavailable):
        unavailable.authenticate(token)


def test_oidc_rejects_malformed_identity_claim_types():
    trusted=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    now=datetime.now(timezone.utc)
    verifier=JWTAuthenticator(issuer="issuer",audience="aud",jwks_uri="https://issuer/keys",jwks_client=LocalJwks(trusted.public_key()))
    for claim,value in (("sub",["subject"]),("tenant_id",{"tenant":"tenant"}),("tenant_id","")):
        claims={"iss":"issuer","aud":"aud","sub":"subject","tenant_id":"tenant","iat":now,"exp":now+timedelta(minutes=1),claim:value}
        token=jwt.encode(claims,trusted,algorithm="RS256",headers={"kid":"key"})
        assert verifier.authenticate(token) is None


import pytest
