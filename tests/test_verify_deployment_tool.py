from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "verify_deployment.py"
SPEC = importlib.util.spec_from_file_location("v2_verify_deployment", SCRIPT)
verify = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(verify)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8000", "http://localhost:8000/", "http://[::1]:8000",
])
def test_verifier_accepts_loopback_only_over_http(url):
    assert verify.validate_target(url, allow_staging=False, allowed_host="")


def test_verifier_requires_explicit_exact_https_staging_allowlist():
    assert verify.validate_target("https://staging.example.test", allow_staging=True,
                                  allowed_host="staging.example.test")
    with pytest.raises(ValueError):
        verify.validate_target("https://staging.example.test", allow_staging=False,
                              allowed_host="staging.example.test")
    with pytest.raises(ValueError):
        verify.validate_target("https://staging.example.test", allow_staging=True,
                              allowed_host="other.example.test")


@pytest.mark.parametrize("url", [
    "http://example.test", "https://example.test", "https://user:pass@example.test",
    "https://staging.example.test/path", "https://staging.example.test?x=1",
])
def test_verifier_rejects_unsafe_or_ambiguous_targets(url):
    with pytest.raises(ValueError):
        verify.validate_target(url, allow_staging=True, allowed_host="staging.example.test")
