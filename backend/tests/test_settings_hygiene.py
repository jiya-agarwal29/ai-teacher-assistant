"""
Phase 7D item 1: env_utils.get_env()'s whitespace stripping, and main.py's
production-only startup validation (DATABASE_URL / S3_ENDPOINT_URL /
CORS_ORIGINS).

The validation itself runs once, at `import main` time -- and main.py is
already imported (under this test session's own dev-mode settings) long
before this file runs, so re-importing it in-process with different env
vars wouldn't actually re-run that top-level code a second time. Every
"does production startup actually fail/succeed" case here instead spawns a
fresh `python -c "import main"` subprocess with its own env -- slower per
test, but the only way to really exercise this import-time behaviour. The
pure validator functions themselves (_is_valid_production_cors_origin,
storage._is_valid_https_url) are also tested directly, in-process, for
faster/more thorough edge-case coverage.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from env_utils import get_env
from main import _is_valid_production_cors_origin
from storage import _is_valid_https_url

BACKEND_DIR = Path(__file__).resolve().parent.parent

# A fake, deliberately secret-looking value baked into the deliberately-bad
# setting in each subprocess test below -- every such test asserts this
# exact string never appears in the subprocess's stdout/stderr, proving the
# validation error message never echoes the value it rejected.
_LEAK_CANARY = "leaked-s3cr3t-canary-9f3a2b"


def _subprocess_env(**overrides) -> dict:
    """A full, valid "production, everything set correctly" env, as a base for one deliberately-broken override per test."""
    base = {
        **os.environ,
        "ENV": "production",
        "SECRET_KEY": "x" * 40,
        "GEMINI_API_KEY": "dummy-key-for-subprocess-import-check",
        "DATABASE_URL": "postgresql://user:pass@host.invalid/db",
        "STORAGE_BACKEND": "s3",
        "S3_ENDPOINT_URL": "https://example.supabase.co/storage/v1/s3",
        "S3_REGION": "us-east-1",
        "S3_BUCKET": "uploads",
        "S3_ACCESS_KEY_ID": "dummy-access-key-id",
        "S3_SECRET_ACCESS_KEY": "dummy-secret-access-key",
        "CORS_ORIGINS": "https://app.example.com",
    }
    base.update(overrides)
    return base


def _run_import_main(env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", "import main"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


# --- env_utils.get_env() itself ---

def test_get_env_strips_leading_and_trailing_whitespace(monkeypatch):
    monkeypatch.setenv("SOME_SETTING", "  value-with-padding  ")
    assert get_env("SOME_SETTING") == "value-with-padding"


def test_get_env_strips_trailing_newline(monkeypatch):
    monkeypatch.setenv("SOME_SETTING", "value-with-newline\n")
    assert get_env("SOME_SETTING") == "value-with-newline"


def test_get_env_returns_default_when_unset(monkeypatch):
    monkeypatch.delenv("SOME_SETTING", raising=False)
    assert get_env("SOME_SETTING", "fallback") == "fallback"


def test_get_env_whitespace_only_value_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("SOME_SETTING", "   \n  ")
    assert get_env("SOME_SETTING", "fallback") == "fallback"


def test_get_env_no_default_and_unset_returns_none(monkeypatch):
    monkeypatch.delenv("SOME_SETTING", raising=False)
    assert get_env("SOME_SETTING") is None


# --- main._is_valid_production_cors_origin() ---

@pytest.mark.parametrize("origin", [
    "https://app.example.com",
    "https://app.example.com:8443",
])
def test_valid_cors_origin_accepted(origin):
    assert _is_valid_production_cors_origin(origin) is True


@pytest.mark.parametrize("origin", [
    "https://app.example.com/",
    "https://app.example.com/some/path",
    "http://app.example.com",
    "app.example.com",
    "*",
])
def test_invalid_cors_origin_rejected(origin):
    assert _is_valid_production_cors_origin(origin) is False


# --- storage._is_valid_https_url() ---

@pytest.mark.parametrize("url", [
    "https://example.supabase.co/storage/v1/s3",
    "https://s3.example-region.amazonaws.com",
])
def test_valid_s3_endpoint_url_accepted(url):
    assert _is_valid_https_url(url) is True


@pytest.mark.parametrize("url", [
    "http://example.supabase.co/storage/v1/s3",
    "example.supabase.co/storage/v1/s3",
    "not a url at all",
    "",
])
def test_invalid_s3_endpoint_url_rejected(url):
    assert _is_valid_https_url(url) is False


# --- End-to-end: production startup, via subprocess ---

def test_production_startup_succeeds_with_valid_settings():
    result = _run_import_main(_subprocess_env())
    assert result.returncode == 0, result.stderr


def test_production_startup_succeeds_with_whitespace_padded_settings():
    """A value with a trailing space/newline (e.g. pasted into Render's dashboard) still works -- env_utils.get_env() strips it first."""
    result = _run_import_main(_subprocess_env(
        DATABASE_URL="  postgresql://user:pass@host.invalid/db\n",
        S3_ENDPOINT_URL="https://example.supabase.co/storage/v1/s3\n",
        CORS_ORIGINS=" https://app.example.com \n",
    ))
    assert result.returncode == 0, result.stderr


def test_production_startup_fails_on_malformed_database_url():
    result = _run_import_main(_subprocess_env(
        DATABASE_URL=f"not-a-valid-url-{_LEAK_CANARY}",
    ))
    assert result.returncode != 0
    assert "DATABASE_URL looks malformed" in result.stderr
    assert _LEAK_CANARY not in result.stdout
    assert _LEAK_CANARY not in result.stderr


def test_production_startup_fails_on_sqlite_database_url():
    result = _run_import_main(_subprocess_env(DATABASE_URL="sqlite:///somewhere.db"))
    assert result.returncode != 0
    assert "DATABASE_URL looks malformed" in result.stderr


def test_production_startup_fails_on_malformed_s3_endpoint_url():
    result = _run_import_main(_subprocess_env(
        S3_ENDPOINT_URL=f"not-a-valid-url-{_LEAK_CANARY}",
    ))
    assert result.returncode != 0
    assert "S3_ENDPOINT_URL looks malformed" in result.stderr
    assert _LEAK_CANARY not in result.stdout
    assert _LEAK_CANARY not in result.stderr


def test_production_startup_fails_on_non_https_s3_endpoint_url():
    result = _run_import_main(_subprocess_env(
        S3_ENDPOINT_URL="http://example.supabase.co/storage/v1/s3",
    ))
    assert result.returncode != 0
    assert "S3_ENDPOINT_URL looks malformed" in result.stderr


def test_production_startup_fails_on_cors_origin_with_trailing_slash():
    result = _run_import_main(_subprocess_env(
        CORS_ORIGINS=f"https://{_LEAK_CANARY}.example.com/",
    ))
    assert result.returncode != 0
    assert "CORS_ORIGINS" in result.stderr
    assert _LEAK_CANARY not in result.stdout
    assert _LEAK_CANARY not in result.stderr


def test_production_startup_fails_on_cors_origin_with_path():
    result = _run_import_main(_subprocess_env(
        CORS_ORIGINS="https://app.example.com/some/path",
    ))
    assert result.returncode != 0
    assert "CORS_ORIGINS" in result.stderr


def test_production_startup_fails_on_non_https_cors_origin():
    result = _run_import_main(_subprocess_env(CORS_ORIGINS="http://app.example.com"))
    assert result.returncode != 0
    assert "CORS_ORIGINS" in result.stderr


def test_no_subprocess_output_ever_contains_any_secret_value():
    """
    Belt-and-suspenders over the per-case assertions above: run every
    failing case again and confirm none of the actual secret-ish setting
    values in _subprocess_env() (SECRET_KEY, the S3 keys) ever show up in
    either stream, regardless of which single setting that case broke.
    """
    secrets_to_never_leak = ["x" * 40, "dummy-access-key-id", "dummy-secret-access-key"]
    broken_cases = [
        {"DATABASE_URL": "not-a-valid-url"},
        {"S3_ENDPOINT_URL": "not-a-valid-url"},
        {"CORS_ORIGINS": "https://app.example.com/"},
    ]
    for overrides in broken_cases:
        result = _run_import_main(_subprocess_env(**overrides))
        assert result.returncode != 0
        for secret in secrets_to_never_leak:
            assert secret not in result.stdout
            assert secret not in result.stderr
