"""
Swappable AI model layer.

LLM_PROVIDER selects the active backend:
  - "gemini" (default): Google's hosted Gemini API. Requires GEMINI_API_KEY.
  - "local": the in-process Flan-T5 model already used elsewhere in rag.py,
    so the app keeps working fully offline / without an API key.

The rest of the app should only ever call generate() / generate_json() /
provider_status() from this module — never import the Gemini SDK or rag's
Flan-T5 internals directly for generation, so the provider stays swappable.
"""
import json
import logging
import os
import time

logger = logging.getLogger(__name__)

GEMINI_TIMEOUT_MS = 60_000
MAX_ATTEMPTS = 3
_BASE_BACKOFF_SECONDS = 1.0

_gemini_client = None


class LLMUnavailableError(Exception):
    """Raised when the active provider fails to produce a response."""


def _provider() -> str:
    return os.getenv("LLM_PROVIDER", "gemini").strip().lower()


def _gemini_model() -> str:
    return os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip()


def _get_gemini_client():
    """
    Builds the Gemini client once and caches it. Raises RuntimeError
    immediately if GEMINI_API_KEY is missing, so a misconfigured deployment
    fails loudly (see init(), called from the FastAPI lifespan at startup)
    instead of on the first real request.
    """
    global _gemini_client
    if _gemini_client is not None:
        return _gemini_client

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "LLM_PROVIDER=gemini but GEMINI_API_KEY is not set. Set "
            "GEMINI_API_KEY in backend/.env, or set LLM_PROVIDER=local to "
            "use the offline Flan-T5 model instead."
        )

    from google import genai
    from google.genai import types

    _gemini_client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=GEMINI_TIMEOUT_MS,
            # We implement our own retry/backoff in _call_with_retry so
            # retry behaviour is predictable and testable; disable the
            # SDK's built-in retries (default 5 attempts).
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )
    logger.info("Gemini client initialized (model=%s)", _gemini_model())
    return _gemini_client


def _is_retryable_gemini_error(exc: Exception) -> bool:
    from google.genai import errors as genai_errors
    return isinstance(exc, genai_errors.APIError) and (
        exc.code == 429 or (exc.code is not None and exc.code >= 500)
    )


def _call_with_retry(call_fn, description: str):
    """
    Calls call_fn() with up to MAX_ATTEMPTS tries, retrying with exponential
    backoff only on rate-limit (429) or server (5xx) errors. Any other
    error, or exhausting all attempts, raises LLMUnavailableError instead of
    leaking the raw provider exception. Never logs prompt/response content —
    only the error type and HTTP status.
    """
    delay = _BASE_BACKOFF_SECONDS
    last_exc = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return call_fn()
        except Exception as exc:
            last_exc = exc
            if not _is_retryable_gemini_error(exc) or attempt == MAX_ATTEMPTS:
                logger.error(
                    "%s failed on attempt %d/%d (%s); giving up",
                    description, attempt, MAX_ATTEMPTS, type(exc).__name__
                )
                break
            logger.warning(
                "%s failed on attempt %d/%d (%s); retrying in %.1fs",
                description, attempt, MAX_ATTEMPTS, type(exc).__name__, delay
            )
            time.sleep(delay)
            delay *= 2

    raise LLMUnavailableError(
        f"{description} failed after {MAX_ATTEMPTS} attempt(s)"
    ) from last_exc


def _generate_gemini(prompt, system, temperature, max_tokens):
    from google.genai import types

    client = _get_gemini_client()
    config = types.GenerateContentConfig(
        system_instruction=system,
        temperature=temperature,
        max_output_tokens=max_tokens,
    )

    def call():
        response = client.models.generate_content(
            model=_gemini_model(),
            contents=prompt,
            config=config,
        )
        return response.text or ""

    return _call_with_retry(call, "Gemini generate_content")


def _generate_json_gemini(prompt, schema, system):
    from google.genai import types

    client = _get_gemini_client()
    config = types.GenerateContentConfig(
        system_instruction=system,
        response_mime_type="application/json",
        response_json_schema=schema,
    )

    def call():
        response = client.models.generate_content(
            model=_gemini_model(),
            contents=prompt,
            config=config,
        )
        try:
            return json.loads(response.text)
        except (TypeError, json.JSONDecodeError) as exc:
            raise LLMUnavailableError(
                "Gemini returned a response that was not valid JSON"
            ) from exc

    return _call_with_retry(call, "Gemini generate_content (JSON)")


def _generate_local(prompt: str, system: str | None, max_tokens: int) -> str:
    import rag
    instruction = system or "Respond to the instructions above."
    return rag.generate_focused_answer(prompt, instruction, max_tokens=max_tokens)


def generate(
    prompt: str,
    system: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 1024,
) -> str:
    if _provider() == "local":
        return _generate_local(prompt, system, max_tokens)
    return _generate_gemini(prompt, system, temperature, max_tokens)


def generate_json(prompt: str, schema: dict, system: str | None = None) -> dict:
    if _provider() == "local":
        raise NotImplementedError(
            "generate_json is not supported for LLM_PROVIDER=local "
            "(Flan-T5 has no structured JSON output)."
        )
    return _generate_json_gemini(prompt, schema, system)


def provider_status() -> dict:
    provider = _provider()

    if provider == "local":
        import rag
        return {
            "provider": "local",
            "model": rag.model_name,
            "configured": True,
            "ready": rag.model is not None,
        }

    return {
        "provider": "gemini",
        "model": _gemini_model(),
        "configured": bool(os.getenv("GEMINI_API_KEY")),
        "ready": _gemini_client is not None,
    }


def init():
    """
    Called once from the FastAPI lifespan at startup. Eagerly prepares the
    active provider so a missing GEMINI_API_KEY (or an unknown provider
    name) fails loudly at boot instead of on the first real request. Only
    loads the local Flan-T5 model when LLM_PROVIDER=local — with Gemini
    active, Flan-T5 is never loaded at startup.
    """
    provider = _provider()

    if provider == "local":
        import rag
        rag.load_model()
    elif provider == "gemini":
        _get_gemini_client()
    else:
        raise RuntimeError(
            f"Unknown LLM_PROVIDER '{provider}'. Use 'gemini' or 'local'."
        )
