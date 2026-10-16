from fnmatch import fnmatchcase
from pathlib import Path


def test_ignore_policy_excludes_local_credentials_and_private_keys_but_keeps_template():
    root = Path(__file__).resolve().parents[1]
    rules = [line.strip() for line in (root / ".gitignore").read_text(encoding="utf-8").splitlines()]
    assert ".env.*" in rules
    assert "!.env.example" in rules
    assert "!.env.*.example" in rules

    def ignored(path):
        result = False
        for rule in rules:
            if not rule or rule.startswith("#"):
                continue
            negate = rule.startswith("!")
            pattern = rule[1:] if negate else rule
            if pattern.endswith("/"):
                matched = path == pattern[:-1] or path.startswith(pattern)
            else:
                matched = fnmatchcase(path, pattern) or ("/" not in pattern and any(fnmatchcase(part, pattern) for part in path.split("/")))
            if matched:
                result = not negate
        return result

    for path in (".env", ".env.local", ".env.sprint08", "nested/.env.production", ".aws/credentials", "secrets/service.json", "nested/private.pem", "tls/server.key", "release.p12"):
        assert ignored(path), path
    assert not ignored(".env.example")
    assert not ignored(".env.staging.example")
