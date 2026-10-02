"""
Shared pytest configuration for the backend test suite.

- Makes `backend/` importable as top-level modules (rag, models, main, ...)
  regardless of where pytest is invoked from.
- Sets dummy SECRET_KEY / GEMINI_API_KEY before any test module imports
  auth.py or llm.py, so the full suite runs with no real backend/.env file
  and without ever needing a real API key.
- Replaces embeddings.embed_documents()/embed_query() with deterministic
  fake vectors for every test file except test_embeddings.py (which tests
  those functions' own real behaviour against lower-level client mocks)
  and any individual test marked @pytest.mark.real_embeddings_path (which
  deliberately exercises the real embeddings.py pipeline against a mocked
  Gemini client, e.g. to test rate-limit handling). This keeps the whole
  suite network-free -- no test should ever make a real API call.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# setdefault() so a real configured value (e.g. from backend/.env) always
# wins -- these exist purely so the suite runs with no .env at all, and
# since conftest.py loads before any test module, python-dotenv's own
# load_dotenv() (override=False by default) won't clobber them later.
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-pytest-only")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-api-key-for-pytest-only")

import numpy as np
import pytest

import embeddings as _embeddings_module

# Captured once, before anything below ever monkeypatches embeddings.py's
# module attributes -- lets a test temporarily undo the autouse fake for
# just one of the two functions (see real_embed_query below) without
# needing the whole real_embeddings_path marker (which would also disable
# the fake for e.g. an upload step earlier in the same test).
_REAL_EMBED_DOCUMENTS = _embeddings_module.embed_documents
_REAL_EMBED_QUERY = _embeddings_module.embed_query

_FAKE_EMBED_DIM = 8
_FAKE_VECTOR_VALUE = float(1.0 / np.sqrt(_FAKE_EMBED_DIM))


def _fake_embed_documents(texts):
    return np.full((len(texts), _FAKE_EMBED_DIM), _FAKE_VECTOR_VALUE, dtype=np.float32)


def _fake_embed_query(text):
    return np.full(_FAKE_EMBED_DIM, _FAKE_VECTOR_VALUE, dtype=np.float32)


@pytest.fixture
def real_embed_query():
    """
    The real (unmocked) embeddings.embed_query, for a test that needs to
    exercise it directly (e.g. against a mocked low-level Gemini client)
    even though the autouse fake is active by default -- without having to
    disable the fake for the whole test via @pytest.mark.real_embeddings_path.
    """
    return _REAL_EMBED_QUERY


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_embeddings_path: exercises the real embeddings.py pipeline "
        "against a mocked low-level client (Gemini/Voyage) instead of the "
        "autouse fake-embeddings fixture -- still makes no real network call."
    )


@pytest.fixture(autouse=True)
def _deterministic_embeddings(request, monkeypatch):
    if request.module.__name__ == "test_embeddings":
        yield
        return
    if request.node.get_closest_marker("real_embeddings_path"):
        yield
        return

    import embeddings
    monkeypatch.setattr(embeddings, "embed_documents", _fake_embed_documents)
    monkeypatch.setattr(embeddings, "embed_query", _fake_embed_query)
    yield
