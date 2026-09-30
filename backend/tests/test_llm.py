"""
Tests for the swappable LLM layer (llm.py). The Gemini client is always
mocked here — none of these tests make a real network call.
"""
import pytest
from google.genai import errors as genai_errors

import llm


class _FakeResponse:
    def __init__(self, text):
        self.text = text


class _FakeModels:
    def __init__(self, side_effects):
        self._side_effects = list(side_effects)
        self.call_count = 0

    def generate_content(self, **kwargs):
        self.call_count += 1
        effect = self._side_effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return effect


class _FakeClient:
    def __init__(self, side_effects):
        self.models = _FakeModels(side_effects)


def _api_error(code):
    return genai_errors.APIError(code=code, response_json={"message": "boom"}, response=None)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    # Keeps the retry/backoff tests fast — they don't need a real delay.
    monkeypatch.setattr(llm.time, "sleep", lambda seconds: None)


@pytest.fixture(autouse=True)
def gemini_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-tests")


@pytest.fixture
def fake_client(monkeypatch):
    def _install(side_effects):
        client = _FakeClient(side_effects)
        monkeypatch.setattr(llm, "_get_gemini_client", lambda: client)
        return client
    return _install


def test_generate_success(fake_client):
    fake_client([_FakeResponse("OK")])
    assert llm.generate("Say OK") == "OK"


def test_generate_retries_then_succeeds(fake_client):
    client = fake_client([_api_error(429), _FakeResponse("OK after retry")])
    result = llm.generate("Say OK")
    assert result == "OK after retry"
    assert client.models.call_count == 2


def test_generate_retries_exhausted_raises_llm_unavailable(fake_client):
    client = fake_client([_api_error(503), _api_error(503), _api_error(503)])
    with pytest.raises(llm.LLMUnavailableError):
        llm.generate("Say OK")
    assert client.models.call_count == llm.MAX_ATTEMPTS


def test_generate_non_retryable_error_fails_after_one_attempt(fake_client):
    client = fake_client([_api_error(400)])
    with pytest.raises(llm.LLMUnavailableError):
        llm.generate("Say OK")
    assert client.models.call_count == 1


def test_generate_json_parses_response(fake_client):
    fake_client([_FakeResponse('{"answer": "42"}')])
    result = llm.generate_json("What is the answer?", schema={"type": "object"})
    assert result == {"answer": "42"}


def test_generate_json_invalid_json_raises_llm_unavailable(fake_client):
    fake_client([_FakeResponse("not valid json")])
    with pytest.raises(llm.LLMUnavailableError):
        llm.generate_json("What is the answer?", schema={"type": "object"})


def test_generate_json_not_implemented_for_local(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "local")
    with pytest.raises(NotImplementedError):
        llm.generate_json("x", schema={})


def test_missing_api_key_fails_fast_at_init(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(llm, "_gemini_client", None)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        llm.init()


def test_unknown_provider_fails_fast_at_init(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "not-a-real-provider")
    with pytest.raises(RuntimeError, match="Unknown LLM_PROVIDER"):
        llm.init()


def test_provider_status_gemini_configured(monkeypatch):
    status = llm.provider_status()
    assert status["provider"] == "gemini"
    assert status["configured"] is True
    assert status["model"]


def test_provider_status_gemini_not_configured(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    status = llm.provider_status()
    assert status["configured"] is False
