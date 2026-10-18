"""
Unit tests for scripts/smoke_test.py's own helpers -- the pure parsing/
body-building functions, and http_request()'s handling of both a normal
response and an HTTPError, each tested against a fake urlopen() so none of
this ever makes a real network call. The script's end-to-end steps
themselves are deliberately NOT run here or anywhere in CI -- they're only
ever meant to be run by hand against a real local or deployed server (see
the script's own docstring).
"""
import io
import json
import urllib.error

import pytest

from scripts.smoke_test import (
    SmokeTest,
    StepFailed,
    build_multipart_body,
    http_request,
)


# --- build_multipart_body ---

def test_build_multipart_body_includes_field_and_file():
    body, content_type = build_multipart_body(
        {"review_ocr": "false"},
        {"file": ("smoke_test.txt", b"hello world", "text/plain")},
    )
    boundary = content_type.split("boundary=")[1]
    text = body.decode()

    assert content_type.startswith("multipart/form-data; boundary=")
    assert f"--{boundary}" in text
    assert 'name="review_ocr"' in text
    assert "false" in text
    assert 'name="file"; filename="smoke_test.txt"' in text
    assert "Content-Type: text/plain" in text
    assert "hello world" in text
    assert text.rstrip().endswith(f"--{boundary}--")


def test_build_multipart_body_each_call_gets_a_distinct_boundary():
    _, content_type_1 = build_multipart_body({}, {"file": ("a.txt", b"x", "text/plain")})
    _, content_type_2 = build_multipart_body({}, {"file": ("a.txt", b"x", "text/plain")})
    assert content_type_1 != content_type_2


# --- http_request ---

class _FakeResponse:
    def __init__(self, status, body_bytes):
        self.status = status
        self._body = body_bytes

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def test_http_request_returns_parsed_json_on_success(monkeypatch):
    def fake_urlopen(request, timeout):
        return _FakeResponse(200, json.dumps({"ok": True}).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    status, body = http_request("GET", "https://example.invalid/ping")
    assert status == 200
    assert body == {"ok": True}


def test_http_request_sends_json_body_and_content_type_header(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        return _FakeResponse(200, b"{}")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    http_request("POST", "https://example.invalid/login", json_body={"a": 1})

    sent_request = captured["request"]
    assert sent_request.get_header("Content-type") == "application/json"
    assert json.loads(sent_request.data) == {"a": 1}


def test_http_request_returns_status_and_body_on_http_error(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url, 401, "Unauthorized", {}, io.BytesIO(json.dumps({"detail": "nope"}).encode())
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    status, body = http_request("POST", "https://example.invalid/login")
    assert status == 401
    assert body == {"detail": "nope"}


def test_http_request_handles_non_json_error_body(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url, 500, "Internal Server Error", {}, io.BytesIO(b"not json")
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    status, body = http_request("GET", "https://example.invalid/health")
    assert status == 500
    assert body == {"_raw": "not json"}


def test_http_request_empty_body_returns_empty_dict(monkeypatch):
    def fake_urlopen(request, timeout):
        return _FakeResponse(200, b"")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    status, body = http_request("DELETE", "https://example.invalid/books/1")
    assert status == 200
    assert body == {}


# --- SmokeTest.run_step bookkeeping ---

def test_run_step_marks_failure_on_step_failed_without_raising():
    test = SmokeTest("https://example.invalid", "password")
    test.run_step("broken step", lambda: (_ for _ in ()).throw(StepFailed("explicit reason")))
    assert test.any_failed is True


def test_run_step_marks_failure_on_unexpected_exception():
    test = SmokeTest("https://example.invalid", "password")

    def boom():
        raise ValueError("surprise")

    test.run_step("broken step", boom)
    assert test.any_failed is True


def test_run_step_does_not_mark_failure_on_success():
    test = SmokeTest("https://example.invalid", "password")
    test.run_step("fine step", lambda: None)
    assert test.any_failed is False


def test_password_is_never_in_auth_headers_as_plaintext():
    """auth_headers() carries the bearer token, never the raw password -- a sanity check against a future refactor accidentally logging/sending it."""
    test = SmokeTest("https://example.invalid", "super-secret-password")
    test.token = "a.jwt.token"
    headers = test.auth_headers()
    assert "super-secret-password" not in str(headers)
