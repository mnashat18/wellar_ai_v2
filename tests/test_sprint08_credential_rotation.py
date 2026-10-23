from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

from deployment.sprint08.run_integration_tests import (
    build_test_postgres_dsn,
    pytest_args_for_case,
    redact_integration_output,
    run_pytest_captured,
)


def test_test_dsn_is_fixed_to_sprint08_and_url_encodes_password():
    dsn = build_test_postgres_dsn("wellar_sprint08", "synthetic/credential?value")
    assert dsn == "postgresql://wellar_sprint08:synthetic%2Fcredential%3Fvalue@127.0.0.1:55432/wellar_v2_sprint08_test"
    with pytest.raises(ValueError):
        build_test_postgres_dsn("other_role", "synthetic")


def test_integration_traceback_output_redacts_raw_and_uri_encoded_password():
    password = "synthetic/credential?value"
    output = (
        f"raw={password} encoded=synthetic%2Fcredential%3Fvalue "
        f"dsn=postgresql://wellar_sprint08:{password}@127.0.0.1:55432/wellar_v2_sprint08_test"
    )
    redacted = redact_integration_output(output, password)
    assert password not in redacted
    assert "synthetic%2Fcredential%3Fvalue" not in redacted
    assert "postgresql://wellar_sprint08:[REDACTED]@127.0.0.1:55432/wellar_v2_sprint08_test" in redacted


def test_runner_captures_and_redacts_mocked_pytest_traceback():
    password = "mock-password-only"
    output = io.StringIO()
    seen_args = []

    def fake_pytest(args):
        seen_args.extend(args)
        print(f"OperationalError: postgresql://wellar_sprint08:{password}@127.0.0.1:55432/db", file=sys.stderr)
        return 1

    assert run_pytest_captured(fake_pytest, ["-q", "synthetic-test"], password, output) == 1
    assert seen_args == ["-q", "synthetic-test"]
    assert password not in output.getvalue()
    assert "postgresql://wellar_sprint08:[REDACTED]@127.0.0.1:55432/db" in output.getvalue()


def test_owner_rotation_helper_is_project_scoped_and_non_destructive():
    root = Path(__file__).resolve().parents[1]
    script = (root / "deployment/sprint08/rotate_postgres_credential.ps1").read_text(encoding="utf-8")
    assert "$projectName = 'wellar-v2-sprint08'" in script
    assert "$role = 'wellar_sprint08'" in script
    assert "$database = 'wellar_v2_sprint08_test'" in script
    assert "Read-Host 'Type ROTATE SPRINT08 TEST ROLE to proceed'" in script
    assert "StandardInput.Write($StandardInput)" in script
    assert "--volumes" not in script and "volume rm" not in script and " prune" not in script
    assert "Write-Host $newPassword" not in script


def test_targeted_live_case_selects_only_new_recovery_and_lifecycle_checks():
    args=pytest_args_for_case("recovery-lifecycle")
    assert args==[
        "-q",
        "tests/test_postgres_integration.py::test_postgres_worker_crash_lease_recovery_and_fenced_completion",
        "tests/test_minio_integration.py::test_minio_delete_retry_and_report_only_orphan_detection",
    ]
    with pytest.raises(ValueError):
        pytest_args_for_case("arbitrary-test-selector")
