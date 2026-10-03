"""
Swappable AI model layer.

LLM_PROVIDER selects the active backend:
  - "gemini" (default): Google's hosted Gemini API. Requires GEMINI_API_KEY.
  - "local": the in-process Flan-T5 model already used elsewhere in rag.py,
    so the app keeps working fully offline / without an API key.

The rest of the app should only ever call generate() / generate_json() /
provider_status() from this module — never import the Gemini SDK or rag's
Flan-T5 internals directly for generation, so the provider stays swappable.

get_client() exposes the shared, cached Gemini client so embeddings.py can
reuse the same client/API key when EMBEDDING_PROVIDER=gemini, instead of
building a second one.
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
            "GEMINI_API_KEY is not set, but it's required for Gemini-based "
            "features (LLM_PROVIDER=gemini and/or EMBEDDING_PROVIDER=gemini). "
            "Set GEMINI_API_KEY in backend/.env, or switch those to a "
            "different provider (LLM_PROVIDER=local, "
            "EMBEDDING_PROVIDER=voyage/local)."
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


def get_client():
    """
    Returns the shared Gemini client, building it if needed. Public so
    embeddings.py can reuse the same client/API key for Gemini embeddings
    instead of constructing a second one.
    """
    return _get_gemini_client()


def _is_retryable_gemini_error(exc: Exception) -> bool:
    from google.genai import errors as genai_errors
    return isinstance(exc, genai_errors.APIError) and (
        exc.code == 429 or (exc.code is not None and exc.code >= 500)
    )


def _is_gemini_rate_limited(exc: Exception) -> bool:
    from google.genai import errors as genai_errors
    return isinstance(exc, genai_errors.APIError) and exc.code == 429


def _gemini_retry_after_seconds(exc: Exception):
    """
    Reads a Retry-After header off the failed response, if present. Small,
    deliberate duplicate of embeddings.py's identical helper -- llm.py must
    not import embeddings.py (which itself lazily imports llm.py to reuse
    the Gemini client, see _embed_gemini), so this stays self-contained
    rather than risk a circular import.
    """
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    value = headers.get("retry-after") or headers.get("Retry-After")
    if not value:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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


OCR_INSTRUCTION = (
    "Transcribe ALL text on this page exactly as written, including handwriting. "
    "Keep the original order, headings, bullet points and line breaks. Write "
    "equations in plain text. Do not summarise, explain or add anything. If a "
    "word is unreadable write [illegible]. If the page has no text, return an "
    "empty string."
)

_OCR_RATE_LIMIT_DEFAULT_WAIT_SECONDS = 60.0
_OCR_MAX_WAIT_DEFAULT_SECONDS = 300.0


def _read_image_gemini(image_bytes: bytes, mime_type: str, max_wait_seconds: float) -> str:
    """
    Paces through Gemini rate limits the same way embeddings.py's
    _embed_gemini does (wait out a 429 up to max_wait_seconds total,
    rather than giving up on the first one) since jobs.py's OCR step is a
    background job with no user request blocked on it, just like a bulk
    embedding call. Non-rate-limit errors (5xx, etc.) use the same bounded
    attempt/backoff retry as generate().
    """
    from google.genai import types

    client = _get_gemini_client()
    config = types.GenerateContentConfig(temperature=0.0)

    def call():
        response = client.models.generate_content(
            model=_gemini_model(),
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                OCR_INSTRUCTION,
            ],
            config=config,
        )
        return response.text or ""

    description = "Gemini read_image"
    total_wait = 0.0
    backoff = _BASE_BACKOFF_SECONDS
    attempt = 0

    while True:
        attempt += 1
        try:
            return call()
        except Exception as exc:
            if _is_gemini_rate_limited(exc):
                wait_seconds = _gemini_retry_after_seconds(exc) or _OCR_RATE_LIMIT_DEFAULT_WAIT_SECONDS
                if total_wait + wait_seconds > max_wait_seconds:
                    logger.error(
                        "%s still rate-limited after waiting %.0fs total (limit %.0fs); giving up",
                        description, total_wait, max_wait_seconds
                    )
                    raise LLMUnavailableError(
                        f"{description} is still rate-limited after waiting "
                        f"{total_wait:.0f}s (limit {max_wait_seconds:.0f}s)"
                    ) from exc

                total_wait += wait_seconds
                logger.info("%s rate-limited, waiting %ds", description, int(round(wait_seconds)))
                time.sleep(wait_seconds)
                attempt = 0  # a rate-limit wait doesn't count against the bounded retry budget below
                continue

            if _is_retryable_gemini_error(exc) and attempt < MAX_ATTEMPTS:
                logger.warning(
                    "%s failed on attempt %d/%d (%s); retrying in %.1fs",
                    description, attempt, MAX_ATTEMPTS, type(exc).__name__, backoff
                )
                time.sleep(backoff)
                backoff *= 2
                continue

            logger.error(
                "%s failed on attempt %d (%s); giving up",
                description, attempt, type(exc).__name__
            )
            raise LLMUnavailableError(
                f"{description} failed after {attempt} attempt(s)"
            ) from exc


def read_image(image_bytes: bytes, mime_type: str, max_wait_seconds: float = _OCR_MAX_WAIT_DEFAULT_SECONDS) -> str:
    """
    OCRs a single page image (a rendered scanned PDF page, or an uploaded
    photo of handwritten/printed notes) via Gemini vision, transcribing all
    text verbatim -- see OCR_INSTRUCTION. Used only by jobs.py's background
    document-processing pipeline, which passes its own (much longer)
    rate-limit wait budget (EMBED_JOB_MAX_WAIT_SECONDS).

    Raises NotImplementedError for LLM_PROVIDER=local (no vision model
    available offline) and LLMUnavailableError if the provider stays
    unavailable/rate-limited past max_wait_seconds.
    """
    if _provider() == "local":
        raise NotImplementedError(
            "Reading scanned or handwritten pages needs the Gemini provider."
        )
    return _read_image_gemini(image_bytes, mime_type, max_wait_seconds)


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
