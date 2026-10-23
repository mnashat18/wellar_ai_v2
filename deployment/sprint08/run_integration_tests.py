"""Run only the opt-in live checks against the exact Sprint 08 loopback stack."""

from __future__ import annotations

import os
import re
import sys
import argparse
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from urllib.parse import quote

from dotenv import dotenv_values


_POSTGRES_DSN = re.compile(r"(?i)(postgres(?:ql)?://[^:\s/@]+:)[^@\s/]+(@)")
_TEST_CASES = {
    "all": ["tests/test_postgres_integration.py", "tests/test_minio_integration.py"],
    "recovery-lifecycle": [
        "tests/test_postgres_integration.py::test_postgres_worker_crash_lease_recovery_and_fenced_completion",
        "tests/test_minio_integration.py::test_minio_delete_retry_and_report_only_orphan_detection",
    ],
}


def pytest_args_for_case(case: str) -> list[str]:
    if case not in _TEST_CASES:
        raise ValueError("unknown isolated integration test case")
    return ["-q", *_TEST_CASES[case]]


def build_test_postgres_dsn(username: str, password: str) -> str:
    if username != "wellar_sprint08" or not password:
        raise ValueError("Sprint 08 test-only PostgreSQL credentials are missing")
    return f"postgresql://{username}:{quote(password, safe='')}@127.0.0.1:55432/wellar_v2_sprint08_test"


def redact_integration_output(output: str, password: str) -> str:
    # Drivers and pytest plugins can include connection details in exceptions.
    safe = output.replace(password, "[REDACTED]") if password else output
    if password:
        safe = safe.replace(quote(password, safe=""), "[REDACTED]")
    return _POSTGRES_DSN.sub(r"\1[REDACTED]\2", safe)


def run_pytest_captured(pytest_main, args: list[str], password: str, output) -> int:
    captured = StringIO()
    try:
        with redirect_stdout(captured), redirect_stderr(captured):
            code = int(pytest_main(args))
    except Exception:
        captured.write("Integration tests stopped with an error; details were suppressed.\n")
        code = 1
    output.write(redact_integration_output(captured.getvalue(), password))
    return code


def main() -> None:
    parser=argparse.ArgumentParser(description="Run isolated Sprint 08 PostgreSQL/MinIO checks")
    parser.add_argument("--case",choices=tuple(_TEST_CASES),default="all",
                        help="select the full suite or only the worker-recovery/media-lifecycle checks")
    args=parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    config = dotenv_values(root / ".env.sprint08")
    username = config.get("WELLAR_SPRINT08_POSTGRES_USER")
    password = config.get("WELLAR_SPRINT08_POSTGRES_PASSWORD")
    try:
        dsn = build_test_postgres_dsn(username, password)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    os.environ["WELLAR_TEST_POSTGRES_DSN"] = dsn
    os.environ["WELLAR_TEST_S3_ENDPOINT"] = "http://127.0.0.1:19000"
    os.environ["WELLAR_TEST_S3_BUCKET"] = "wellar-v2-sprint08-test"
    import pytest

    raise SystemExit(run_pytest_captured(
        pytest.main,
        pytest_args_for_case(args.case),
        password,
        sys.stdout,
    ))


if __name__ == "__main__":
    main()
