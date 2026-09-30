"""
Swappable embedding layer.

EMBEDDING_PROVIDER selects the active backend:
  - "voyage" (default): Voyage AI's hosted embedding API. Requires
    VOYAGE_API_KEY. 1024-dimensional vectors.
  - "local": the in-process all-MiniLM-L6-v2 model (sentence-transformers),
    loaded lazily and only when actually used, so the app keeps working
    fully offline / without an API key. 384-dimensional vectors.

Voyage and local vectors have different dimensions and are not comparable.
Every page stores the embedding_model that produced its vector (see
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
_MAX_ATTEMPTS = 3
# Voyage's rate limits reset per-minute (not per-second like a typical burst
# limit), so a short backoff just wastes all its retries inside the same
# rate-limit window. Starting at 20s gives a 429 a real chance to clear.
_BASE_BACKOFF_SECONDS = 20.0

_local_model = None
_voyage_client = None


def _provider() -> str:
    return os.getenv("EMBEDDING_PROVIDER", "voyage").strip().lower()


def _voyage_model() -> str:
    return os.getenv("VOYAGE_MODEL", _VOYAGE_DEFAULT_MODEL).strip()


def active_model_name() -> str:
    """
    Identifies which provider+model produced (or will produce) a vector,
    e.g. "voyage:voyage-4" or "local:all-MiniLM-L6-v2". Stored on every
    Page so retrieval can tell incompatible vectors apart.
    """
    if _provider() == "local":
        return f"local:{_LOCAL_MODEL_NAME}"
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
            "VOYAGE_API_KEY in backend/.env, or set EMBEDDING_PROVIDER=local "
            "to use the offline MiniLM model instead."
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
    active provider so a missing VOYAGE_API_KEY (or an unknown provider
    name) fails loudly at boot instead of on the first request. Only loads
    the local MiniLM model when EMBEDDING_PROVIDER=local -- with Voyage
    active, MiniLM is never loaded at startup.
    """
    provider = _provider()

    if provider == "local":
        _load_local_model()
    elif provider == "voyage":
        _get_voyage_client()
    else:
        raise RuntimeError(
            f"Unknown EMBEDDING_PROVIDER '{provider}'. Use 'voyage' or 'local'."
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


def _call_with_retry(call_fn, description: str):
    """
    Calls call_fn() with up to _MAX_ATTEMPTS tries, retrying with
    exponential backoff only on rate-limit/server errors. Any other error,
    or exhausting all attempts, raises RuntimeError instead of leaking the
    raw provider exception. Never logs the texts being embedded.
    """
    delay = _BASE_BACKOFF_SECONDS
    last_exc = None

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return call_fn()
        except Exception as exc:
            last_exc = exc
            if not _is_retryable_voyage_error(exc) or attempt == _MAX_ATTEMPTS:
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

        result = _call_with_retry(call, f"Voyage embed ({input_type})")
        all_vectors.extend(result.embeddings)

    matrix = np.asarray(all_vectors, dtype=np.float32)
    return _l2_normalize(matrix)


def embed_documents(texts: list[str]) -> np.ndarray:
    """
    Embeds document chunks for storage (upload). Returns an (N, dim)
    L2-normalised matrix.
    """
    if not texts:
        return np.empty((0, 0), dtype=np.float32)

    if _provider() == "local":
        vectors = np.asarray(_load_local_model().encode(texts, batch_size=32), dtype=np.float32)
        return _l2_normalize(vectors)

    return _embed_voyage(texts, input_type="document")


def embed_query(text: str) -> np.ndarray:
    """
    Embeds a single question/search query. Returns an L2-normalised
    (dim,) vector.
    """
    if _provider() == "local":
        vector = np.asarray(_load_local_model().encode([text])[0], dtype=np.float32)
        return _l2_normalize(vector.reshape(1, -1))[0]

    return _embed_voyage([text], input_type="query")[0]
