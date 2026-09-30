"""
Swappable embedding layer.

EMBEDDING_PROVIDER selects the active backend:
  - "gemini" (default): Google's hosted Gemini embedding API, via the same
    GEMINI_API_KEY and client used by llm.py. Free tier. 768-dimensional
    vectors by default (GEMINI_EMBED_DIM).
  - "voyage": Voyage AI's hosted embedding API. Requires VOYAGE_API_KEY.
    1024-dimensional vectors. Without a payment method on the Voyage
    account, this is capped at ~10K tokens/minute -- too slow for large
    uploads; kept as an optional provider.
  - "local": the in-process all-MiniLM-L6-v2 model (sentence-transformers),
    loaded lazily and only when actually used, so the app keeps working
    fully offline / without an API key. 384-dimensional vectors.

Vectors from different providers/dimensions are not comparable. Every page
stores the embedding_model that produced its vector (see
models.Page.embedding_model); retrieval.py only ever loads and scores pages
whose stored embedding_model matches active_model_name().
"""
import logging
import os
import time

import numpy as np

logger = logging.getLogger(__name__)

_LOCAL_MODEL_NAME = "all-MiniLM-L6-v2"

_VOYAGE_DEFAULT_MODEL = "voyage-4"
_VOYAGE_BATCH_SIZE = 64
# Voyage's rate limits reset per-minute (not per-second like a typical burst
# limit), so a short backoff just wastes all its retries inside the same
# rate-limit window. Starting at 20s gives a 429 a real chance to clear.
_VOYAGE_BASE_BACKOFF_SECONDS = 20.0

_GEMINI_EMBED_DEFAULT_MODEL = "gemini-embedding-001"
_GEMINI_EMBED_DEFAULT_DIM = 768
_GEMINI_EMBED_DEFAULT_BATCH_SIZE = 50
_GEMINI_BASE_BACKOFF_SECONDS = 1.0
# Gemini's free tier embed_content quota resets roughly per-minute. When a
# 429 doesn't carry a Retry-After header, this is how long we wait before
# retrying the same batch.
_GEMINI_RATE_LIMIT_DEFAULT_WAIT_SECONDS = 60.0
_EMBED_MAX_WAIT_DEFAULT_SECONDS = 300.0

_MAX_ATTEMPTS = 3

_local_model = None
_voyage_client = None


class EmbeddingServiceBusyError(Exception):
    """
    Raised when the active embedding provider keeps rate-limiting us even
    after retries. Routes map this to a 503 ("try again shortly") instead
    of a generic 500, and must not save anything when it's raised.
    """


def _provider() -> str:
    return os.getenv("EMBEDDING_PROVIDER", "gemini").strip().lower()


def _voyage_model() -> str:
    return os.getenv("VOYAGE_MODEL", _VOYAGE_DEFAULT_MODEL).strip()


def _gemini_embed_model() -> str:
    return os.getenv("GEMINI_EMBED_MODEL", _GEMINI_EMBED_DEFAULT_MODEL).strip()


def _gemini_embed_dim() -> int:
    try:
        return int(os.getenv("GEMINI_EMBED_DIM", str(_GEMINI_EMBED_DEFAULT_DIM)))
    except ValueError:
        return _GEMINI_EMBED_DEFAULT_DIM


def _gemini_embed_batch_size() -> int:
    try:
        return int(os.getenv("GEMINI_EMBED_BATCH_SIZE", str(_GEMINI_EMBED_DEFAULT_BATCH_SIZE)))
    except ValueError:
        return _GEMINI_EMBED_DEFAULT_BATCH_SIZE


def _embed_max_wait_seconds() -> float:
    try:
        return float(os.getenv("EMBED_MAX_WAIT_SECONDS", str(_EMBED_MAX_WAIT_DEFAULT_SECONDS)))
    except ValueError:
        return _EMBED_MAX_WAIT_DEFAULT_SECONDS


def active_model_name() -> str:
    """
    Identifies which provider+model(+dimension) produced (or will produce)
    a vector, e.g. "gemini:gemini-embedding-001:768", "voyage:voyage-4", or
    "local:all-MiniLM-L6-v2". Stored on every Page so retrieval can tell
    incompatible vectors apart.
    """
    provider = _provider()
    if provider == "local":
        return f"local:{_LOCAL_MODEL_NAME}"
    if provider == "gemini":
        return f"gemini:{_gemini_embed_model()}:{_gemini_embed_dim()}"
    return f"voyage:{_voyage_model()}"


def _load_local_model():
    global _local_model
    if _local_model is not None:
        return _local_model
    from sentence_transformers import SentenceTransformer
    _local_model = SentenceTransformer(_LOCAL_MODEL_NAME)
    logger.info("Loaded local embedding model '%s'", _LOCAL_MODEL_NAME)
    return _local_model


def _get_voyage_client():
    global _voyage_client
    if _voyage_client is not None:
        return _voyage_client

    api_key = os.getenv("VOYAGE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "EMBEDDING_PROVIDER=voyage but VOYAGE_API_KEY is not set. Set "
            "VOYAGE_API_KEY in backend/.env, or set EMBEDDING_PROVIDER=gemini "
            "(the default) or EMBEDDING_PROVIDER=local instead."
        )

    import voyageai

    # We implement our own retry/backoff in _call_with_retry so retry
    # behaviour is predictable and testable; disable the SDK's own retries.
    _voyage_client = voyageai.Client(api_key=api_key, max_retries=0)
    logger.info("Voyage client initialized (model=%s)", _voyage_model())
    return _voyage_client


def init():
    """
    Called once from the FastAPI lifespan at startup. Eagerly prepares the
    active provider so a missing API key (or an unknown provider name)
    fails loudly at boot instead of on the first request. Only loads the
    local MiniLM model when EMBEDDING_PROVIDER=local -- with Gemini or
    Voyage active, MiniLM is never loaded at startup.
    """
    provider = _provider()

    if provider == "local":
        _load_local_model()
    elif provider == "gemini":
        import llm
        llm.get_client()
    elif provider == "voyage":
        _get_voyage_client()
    else:
        raise RuntimeError(
            f"Unknown EMBEDDING_PROVIDER '{provider}'. Use 'gemini', 'voyage', or 'local'."
        )


def _is_retryable_voyage_error(exc: Exception) -> bool:
    import voyageai.error as verror
    return isinstance(exc, (
        verror.RateLimitError,
        verror.ServerError,
        verror.ServiceUnavailableError,
        verror.Timeout,
        verror.TryAgain,
        verror.APIConnectionError,
    ))


def _is_voyage_rate_limited(exc: Exception) -> bool:
    import voyageai.error as verror
    return isinstance(exc, verror.RateLimitError)


def _is_retryable_gemini_embed_error(exc: Exception) -> bool:
    from google.genai import errors as genai_errors
    return isinstance(exc, genai_errors.APIError) and (
        exc.code == 429 or (exc.code is not None and exc.code >= 500)
    )


def _is_gemini_rate_limited(exc: Exception) -> bool:
    from google.genai import errors as genai_errors
    return isinstance(exc, genai_errors.APIError) and exc.code == 429


def _gemini_retry_after_seconds(exc: Exception):
    """Reads a Retry-After header off the failed response, if present."""
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


def _call_with_retry(call_fn, description: str, is_retryable, is_rate_limited, base_backoff_seconds: float):
    """
    Calls call_fn() with up to _MAX_ATTEMPTS tries, retrying with
    exponential backoff only on rate-limit/server errors. Exhausting
    retries on a rate-limit error raises EmbeddingServiceBusyError; any
    other failure raises RuntimeError instead of leaking the raw provider
    exception. Never logs the texts being embedded.

    Used by the Voyage path. The Gemini path has its own pacing logic (see
    _embed_gemini) that waits out rate limits instead of giving up on them.
    """
    delay = base_backoff_seconds
    last_exc = None

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return call_fn()
        except Exception as exc:
            last_exc = exc
            if not is_retryable(exc) or attempt == _MAX_ATTEMPTS:
                logger.error(
                    "%s failed on attempt %d/%d (%s); giving up",
                    description, attempt, _MAX_ATTEMPTS, type(exc).__name__
                )
                break

            logger.warning(
                "%s failed on attempt %d/%d (%s); retrying in %.1fs",
                description, attempt, _MAX_ATTEMPTS, type(exc).__name__, delay
            )
            time.sleep(delay)
            delay *= 2

    if is_rate_limited(last_exc):
        raise EmbeddingServiceBusyError(
            f"{description} is still rate-limited after {_MAX_ATTEMPTS} attempt(s)"
        ) from last_exc

    raise RuntimeError(
        f"{description} failed after {_MAX_ATTEMPTS} attempt(s)"
    ) from last_exc


def _l2_normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def _embed_voyage(texts: list[str], input_type: str) -> np.ndarray:
    client = _get_voyage_client()
    model = _voyage_model()
    all_vectors = []

    for start in range(0, len(texts), _VOYAGE_BATCH_SIZE):
        batch = texts[start:start + _VOYAGE_BATCH_SIZE]

        def call(batch=batch):
            return client.embed(batch, model=model, input_type=input_type)

        result = _call_with_retry(
            call, f"Voyage embed ({input_type})",
            is_retryable=_is_retryable_voyage_error,
            is_rate_limited=_is_voyage_rate_limited,
            base_backoff_seconds=_VOYAGE_BASE_BACKOFF_SECONDS,
        )
        all_vectors.extend(result.embeddings)

    matrix = np.asarray(all_vectors, dtype=np.float32)
    return _l2_normalize(matrix)


def _embed_gemini(texts: list[str], task_type: str) -> np.ndarray:
    """
    Embeds `texts` in batches via Gemini. On a 429 (free-tier quota), waits
    for the quota to reset (Retry-After if given, otherwise
    _GEMINI_RATE_LIMIT_DEFAULT_WAIT_SECONDS) and retries the *same* batch,
    rather than giving up -- large uploads finish on their own, just more
    slowly, instead of failing outright. The total time spent waiting
    across the whole call is capped at EMBED_MAX_WAIT_SECONDS; only once
    that's exceeded do we raise EmbeddingServiceBusyError. Non-rate-limit
    errors (5xx, etc.) still use the bounded attempt/backoff retry.
    """
    import llm
    from google.genai import types

    client = llm.get_client()
    model = _gemini_embed_model()
    dim = _gemini_embed_dim()
    batch_size = _gemini_embed_batch_size()
    max_wait = _embed_max_wait_seconds()

    all_vectors = []
    total_wait = 0.0
    embedded_count = 0
    total_count = len(texts)

    for start in range(0, total_count, batch_size):
        batch = texts[start:start + batch_size]

        def call(batch=batch):
            response = client.models.embed_content(
                model=model,
                contents=batch,
                config=types.EmbedContentConfig(
                    task_type=task_type,
                    output_dimensionality=dim,
                ),
            )
            return [embedding.values for embedding in response.embeddings]

        description = f"Gemini embed_content ({task_type})"
        backoff = _GEMINI_BASE_BACKOFF_SECONDS
        attempt = 0
        vectors = None

        while vectors is None:
            attempt += 1
            try:
                vectors = call()
            except Exception as exc:
                if _is_gemini_rate_limited(exc):
                    wait_seconds = _gemini_retry_after_seconds(exc) or _GEMINI_RATE_LIMIT_DEFAULT_WAIT_SECONDS
                    if total_wait + wait_seconds > max_wait:
                        logger.error(
                            "%s still rate-limited after waiting %.0fs total "
                            "(limit %.0fs); giving up",
                            description, total_wait, max_wait
                        )
                        raise EmbeddingServiceBusyError(
                            f"{description} is still rate-limited after waiting "
                            f"{total_wait:.0f}s (limit {max_wait:.0f}s)"
                        ) from exc

                    total_wait += wait_seconds
                    logger.info(
                        "embedded %d/%d chunks, waiting %ds for rate limit",
                        embedded_count, total_count, int(round(wait_seconds))
                    )
                    time.sleep(wait_seconds)
                    # A rate-limit wait isn't a failed "attempt" at getting
                    # this batch through -- don't count it against the
                    # bounded retry budget below.
                    attempt = 0
                    continue

                if _is_retryable_gemini_embed_error(exc) and attempt < _MAX_ATTEMPTS:
                    logger.warning(
                        "%s failed on attempt %d/%d (%s); retrying in %.1fs",
                        description, attempt, _MAX_ATTEMPTS, type(exc).__name__, backoff
                    )
                    time.sleep(backoff)
                    backoff *= 2
                    continue

                logger.error(
                    "%s failed on attempt %d (%s); giving up",
                    description, attempt, type(exc).__name__
                )
                raise RuntimeError(
                    f"{description} failed after {attempt} attempt(s)"
                ) from exc

        all_vectors.extend(vectors)
        embedded_count += len(batch)

    matrix = np.asarray(all_vectors, dtype=np.float32)
    # gemini-embedding-001 does not L2-normalise its output when
    # output_dimensionality is reduced below the model's native size --
    # always normalise ourselves so every provider's vectors are comparable.
    return _l2_normalize(matrix)


def embed_documents(texts: list[str]) -> np.ndarray:
    """
    Embeds document chunks for storage (upload). Returns an (N, dim)
    L2-normalised matrix.
    """
    if not texts:
        return np.empty((0, 0), dtype=np.float32)

    provider = _provider()

    if provider == "local":
        vectors = np.asarray(_load_local_model().encode(texts, batch_size=32), dtype=np.float32)
        return _l2_normalize(vectors)

    if provider == "gemini":
        return _embed_gemini(texts, task_type="RETRIEVAL_DOCUMENT")

    return _embed_voyage(texts, input_type="document")


def embed_query(text: str) -> np.ndarray:
    """
    Embeds a single question/search query. Returns an L2-normalised
    (dim,) vector.
    """
    provider = _provider()

    if provider == "local":
        vector = np.asarray(_load_local_model().encode([text])[0], dtype=np.float32)
        return _l2_normalize(vector.reshape(1, -1))[0]

    if provider == "gemini":
        return _embed_gemini([text], task_type="RETRIEVAL_QUERY")[0]

    return _embed_voyage([text], input_type="query")[0]
