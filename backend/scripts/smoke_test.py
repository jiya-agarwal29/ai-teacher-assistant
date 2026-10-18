"""
End-to-end smoke test against a REAL running deployment (local or
production) -- walks the actual HTTP API like a real user would, so it
catches the kind of thing only a live deployment can expose (a
misconfigured env var, a broken CORS setting, a provider that's actually
unreachable) that the mocked test suite never touches.

Usage:
    python scripts/smoke_test.py --api https://your-backend.onrender.com
    python scripts/smoke_test.py --api http://127.0.0.1:8000

Uses ONE fixed account ("smoke_test_bot") -- logs in, and only registers it
if it doesn't exist yet -- instead of creating a fresh throwaway account on
every run. Its password comes from the SMOKE_PASSWORD environment
variable (never a default, never a CLI argument -- it would show up in
shell history); the script exits with a clear message if it's unset.
Never prints the password or any JWT token.

Steps (each timed, PASS/FAIL): GET /ping (generous timeout -- the free
plan's first request after a while asleep can take up to ~60s to even
start responding), GET /health, login, upload a tiny generated .txt
document, poll /books/{id}/status until "ready", one /chat question about
it, a 3-question quiz, GET /analytics/summary, then DELETE the uploaded
document -- always, even if an earlier step failed, so a bad run never
leaves junk behind. No paper generation here; it costs far more AI calls
than a routine check should.

Exit code 0 only if every step passed.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

SMOKE_USERNAME = "smoke_test_bot"
PING_TIMEOUT_SECONDS = 120
DEFAULT_TIMEOUT_SECONDS = 30
POLL_READY_TIMEOUT_SECONDS = 180
POLL_INTERVAL_SECONDS = 3


class StepFailed(Exception):
    """Raised by a step function to fail that step with a specific, readable reason."""


def http_request(method: str, url: str, *, headers: dict | None = None,
                  json_body=None, data: bytes | None = None, timeout: float = DEFAULT_TIMEOUT_SECONDS):
    """
    A minimal JSON-aware HTTP client using only the standard library --
    deliberately no `requests`/`httpx` dependency for a script this small.
    Returns (status_code, parsed_body_or_raw_text) for both a normal
    response and an HTTPError (so a 4xx/5xx is handled the same way as a
    2xx, not as an exception the caller has to catch separately).
    """
    headers = dict(headers or {})
    body = data
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return response.status, _parse_json_or_raw(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, _parse_json_or_raw(raw)


def _parse_json_or_raw(raw: bytes):
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw.decode("utf-8", "replace")}


def build_multipart_body(fields: dict, files: dict) -> tuple[bytes, str]:
    """
    Hand-built multipart/form-data body (see http_request's own docstring
    for why no third-party library) -- `fields` are plain form fields,
    `files` maps a form field name to (filename, content_bytes, content_type).
    """
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        parts.append(f"{value}\r\n".encode())
    for name, (filename, content, content_type) in files.items():
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n".encode()
        )
        parts.append(content)
        parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


class SmokeTest:
    def __init__(self, api_base: str, password: str):
        self.api_base = api_base.rstrip("/")
        self.password = password
        self.token = None
        self.book_id = None
        self.any_failed = False

    def auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def run_step(self, name: str, fn, *args, **kwargs):
        start = time.monotonic()
        try:
            result = fn(*args, **kwargs)
        except StepFailed as exc:
            self._report(name, False, time.monotonic() - start, str(exc))
            return None
        except Exception as exc:  # noqa: BLE001 -- any unexpected error still fails the step cleanly
            self._report(name, False, time.monotonic() - start, f"unexpected error: {exc}")
            return None
        else:
            self._report(name, True, time.monotonic() - start, None)
            return result

    def _report(self, name: str, passed: bool, elapsed: float, reason: str | None):
        status = "PASS" if passed else "FAIL"
        line = f"{status}  {name:<32} {elapsed:6.2f}s"
        if reason:
            line += f"  -- {reason}"
        print(line)
        if not passed:
            self.any_failed = True

    # --- individual steps ---

    def step_ping(self):
        status, _ = http_request("GET", f"{self.api_base}/ping", timeout=PING_TIMEOUT_SECONDS)
        if status != 200:
            raise StepFailed(f"GET /ping returned {status}")

    def step_health(self):
        status, body = http_request("GET", f"{self.api_base}/health")
        if status != 200:
            raise StepFailed(f"GET /health returned {status}")
        if body.get("database_reachable") is not True:
            raise StepFailed(f"database_reachable={body.get('database_reachable')!r}, expected true")
        if body.get("storage_backend") != "s3":
            raise StepFailed(f"storage_backend={body.get('storage_backend')!r}, expected 's3'")

    def step_login(self):
        status, body = self._try_login()
        if status == 401:
            self._register()
            status, body = self._try_login()
        if status != 200:
            raise StepFailed(f"login failed with status {status}")
        token = body.get("access_token")
        if not token:
            raise StepFailed("login response had no access_token")
        self.token = token

    def _try_login(self):
        data = f"username={SMOKE_USERNAME}&password={self.password}".encode()
        return http_request(
            "POST", f"{self.api_base}/login", data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    def _register(self):
        status, body = http_request(
            "POST", f"{self.api_base}/register",
            json_body={"username": SMOKE_USERNAME, "password": self.password},
        )
        # 400 "Username already exists" is fine -- another run (or a race
        # with one) may have just registered it; anything else is a real
        # failure that should surface as a readable reason.
        if status not in (200, 400):
            raise StepFailed(f"register failed with status {status}: {body}")

    def step_upload(self):
        content = (
            b"Smoke test document.\n\n"
            b"Photosynthesis is the process by which plants convert light energy, "
            b"water, and carbon dioxide into glucose and oxygen. It takes place "
            b"mainly in the chloroplasts of plant cells, using the green pigment "
            b"chlorophyll to capture light energy.\n"
        )
        body, content_type = build_multipart_body(
            {"review_ocr": "false"},
            {"file": ("smoke_test.txt", content, "text/plain")},
        )
        status, body_json = http_request(
            "POST", f"{self.api_base}/upload-book", data=body,
            headers={**self.auth_headers(), "Content-Type": content_type},
        )
        if status != 202:
            raise StepFailed(f"upload returned {status}: {body_json}")
        book_id = body_json.get("book_id")
        if not book_id:
            raise StepFailed("upload response had no book_id")
        self.book_id = book_id

    def step_wait_until_ready(self):
        deadline = time.monotonic() + POLL_READY_TIMEOUT_SECONDS
        last_status = None
        while time.monotonic() < deadline:
            status, body = http_request(
                "GET", f"{self.api_base}/books/{self.book_id}/status",
                headers=self.auth_headers(),
            )
            if status != 200:
                raise StepFailed(f"GET status returned {status}")
            last_status = body.get("status")
            if last_status == "ready":
                return
            if last_status == "failed":
                raise StepFailed(f"document processing failed: {body.get('error')}")
            time.sleep(POLL_INTERVAL_SECONDS)
        raise StepFailed(f"document never reached 'ready' within {POLL_READY_TIMEOUT_SECONDS}s (last status: {last_status})")

    def step_chat(self):
        status, body = http_request(
            "POST", f"{self.api_base}/chat",
            json_body={"question": "What is photosynthesis?", "conversation_id": None},
            headers=self.auth_headers(),
        )
        if status != 200:
            raise StepFailed(f"POST /chat returned {status}: {body}")
        if "answer" not in body and "message" not in body:
            raise StepFailed(f"response had neither 'answer' nor 'message': {body}")

    def step_quiz(self):
        status, body = http_request(
            "GET", f"{self.api_base}/generate-quiz?topic=photosynthesis&count=3",
            headers=self.auth_headers(),
        )
        if status != 200:
            raise StepFailed(f"GET /generate-quiz returned {status}: {body}")
        if "questions" not in body:
            raise StepFailed(f"response had no 'questions' field: {body}")

    def step_analytics(self):
        status, body = http_request("GET", f"{self.api_base}/analytics/summary", headers=self.auth_headers())
        if status != 200:
            raise StepFailed(f"GET /analytics/summary returned {status}: {body}")

    def step_delete_document(self):
        if not self.book_id:
            raise StepFailed("no book_id to delete (upload never succeeded)")
        status, body = http_request(
            "DELETE", f"{self.api_base}/books/{self.book_id}", headers=self.auth_headers()
        )
        if status != 200:
            raise StepFailed(f"DELETE returned {status}: {body}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", required=True, help="Backend base URL, e.g. https://your-backend.onrender.com")
    args = parser.parse_args()

    password = os.environ.get("SMOKE_PASSWORD")
    if not password:
        print("SMOKE_PASSWORD is not set. Set it to the smoke_test_bot account's password and re-run:")
        print("  SMOKE_PASSWORD=... python scripts/smoke_test.py --api " + args.api)
        sys.exit(1)

    test = SmokeTest(args.api, password)

    test.run_step("ping", test.step_ping)
    test.run_step("health", test.step_health)
    test.run_step("login", test.step_login)

    # Everything from here on needs a token -- if login never produced one,
    # every remaining step would just fail noisily on an obvious 401.
    # Skipping straight to cleanup keeps the output focused on the real
    # cause instead of six repeats of "login failed with status ...".
    if test.token:
        test.run_step("upload document", test.step_upload)
        if test.book_id:
            test.run_step("wait until ready", test.step_wait_until_ready)
            test.run_step("chat", test.step_chat)
            test.run_step("quiz", test.step_quiz)
            test.run_step("analytics summary", test.step_analytics)

    # Always attempted, even if an earlier step failed -- a bad run should
    # never leave a smoke-test document sitting in the real account.
    if test.token and test.book_id:
        test.run_step("delete document", test.step_delete_document)

    sys.exit(1 if test.any_failed else 0)


if __name__ == "__main__":
    main()
