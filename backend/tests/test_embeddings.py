"""
Tests for the swappable embedding layer (embeddings.py) and how retrieval.py
uses it. The Voyage and Gemini clients are always mocked here -- no real
network calls.
"""
import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import embeddings
import llm as llm_module
import retrieval
from models import Base, Book, Page, User


class _FakeEmbeddingsResult:
    def __init__(self, vectors):
        self.embeddings = vectors
        self.total_tokens = sum(len(v) for v in vectors)


class _FakeVoyageClient:
    """Records every embed() call and returns distinct, non-zero vectors."""

    def __init__(self, dim=8):
        self.dim = dim
        self.calls = []  # list of (texts, model, input_type)

    def embed(self, texts, model=None, input_type=None):
        self.calls.append((list(texts), model, input_type))
        vectors = [
            [float((i % 5) + 1)] + [1.0] * (self.dim - 1)
            for i in range(len(texts))
        ]
        return _FakeEmbeddingsResult(vectors)


class _FakeGeminiEmbedding:
    def __init__(self, values):
        self.values = values


class _FakeGeminiEmbedResponse:
    def __init__(self, vectors):
        self.embeddings = [_FakeGeminiEmbedding(v) for v in vectors]


class _FakeGeminiModels:
    """Records every embed_content() call; side_effects is a queue of
    either an exception to raise or a response to return."""

    def __init__(self, side_effects, dim=8):
        self.dim = dim
        self._side_effects = list(side_effects)
        self.calls = []  # list of (texts, model, task_type)

    def embed_content(self, model=None, contents=None, config=None):
        self.calls.append((list(contents), model, config.task_type))
        effect = self._side_effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        vectors = [
            [float((i % 5) + 1)] + [1.0] * (self.dim - 1)
            for i in range(len(contents))
        ]
        return _FakeGeminiEmbedResponse(vectors)


class _FakeGeminiClient:
    def __init__(self, side_effects, dim=8):
        self.models = _FakeGeminiModels(side_effects, dim=dim)


class _FakeHttpResponse:
    def __init__(self, headers=None):
        self.headers = headers or {}


def _gemini_api_error(code, retry_after=None):
    from google.genai import errors as genai_errors
    response = _FakeHttpResponse({"retry-after": str(retry_after)}) if retry_after is not None else None
    return genai_errors.APIError(code=code, response_json={"message": "boom"}, response=response)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    # Keeps every retry/backoff test fast -- none of them need a real delay.
    monkeypatch.setattr(embeddings.time, "sleep", lambda seconds: None)


@pytest.fixture
def voyage_provider(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "voyage")
    monkeypatch.setenv("VOYAGE_API_KEY", "fake-key-for-tests")


@pytest.fixture
def fake_voyage(voyage_provider, monkeypatch):
    client = _FakeVoyageClient()
    monkeypatch.setattr(embeddings, "_get_voyage_client", lambda: client)
    return client


@pytest.fixture
def gemini_provider(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-tests")


@pytest.fixture
def fake_gemini(gemini_provider, monkeypatch):
    def _install(side_effects=(_FakeGeminiEmbedResponse([[1.0] * 8]),)):
        client = _FakeGeminiClient(list(side_effects))
        monkeypatch.setattr(llm_module, "get_client", lambda: client)
        return client
    return _install


def test_embed_documents_uses_document_input_type(fake_voyage):
    embeddings.embed_documents(["a", "b", "c"])
    assert fake_voyage.calls
    assert all(input_type == "document" for _, _, input_type in fake_voyage.calls)


def test_embed_query_uses_query_input_type(fake_voyage):
    embeddings.embed_query("what is x?")
    assert fake_voyage.calls
    assert all(input_type == "query" for _, _, input_type in fake_voyage.calls)


def test_embed_documents_batches_at_64(fake_voyage):
    texts = [f"chunk {i}" for i in range(150)]
    embeddings.embed_documents(texts)
    batch_sizes = [len(texts_in_call) for texts_in_call, _, _ in fake_voyage.calls]
    assert batch_sizes == [64, 64, 22]


def test_embed_documents_uses_active_voyage_model(fake_voyage, monkeypatch):
    monkeypatch.setenv("VOYAGE_MODEL", "voyage-custom")
    embeddings.embed_documents(["a"])
    assert fake_voyage.calls[0][1] == "voyage-custom"


def test_embeddings_are_l2_normalised(fake_voyage):
    matrix = embeddings.embed_documents(["a", "b", "c"])
    norms = np.linalg.norm(matrix, axis=1)
    assert np.allclose(norms, 1.0)

    query_vector = embeddings.embed_query("a")
    assert query_vector.ndim == 1
    assert np.isclose(np.linalg.norm(query_vector), 1.0)


def test_active_model_name_reflects_provider(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_EMBED_MODEL", "gemini-embedding-001")
    monkeypatch.setenv("GEMINI_EMBED_DIM", "768")
    assert embeddings.active_model_name() == "gemini:gemini-embedding-001:768"

    monkeypatch.setenv("EMBEDDING_PROVIDER", "voyage")
    monkeypatch.setenv("VOYAGE_MODEL", "voyage-4")
    assert embeddings.active_model_name() == "voyage:voyage-4"

    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
    assert embeddings.active_model_name() == "local:all-MiniLM-L6-v2"


def test_embed_documents_uses_gemini_retrieval_document_task_type(fake_gemini):
    client = fake_gemini()
    embeddings.embed_documents(["a", "b", "c"])
    assert client.models.calls
    assert all(task_type == "RETRIEVAL_DOCUMENT" for _, _, task_type in client.models.calls)


def test_embed_query_uses_gemini_retrieval_query_task_type(fake_gemini):
    client = fake_gemini()
    embeddings.embed_query("what is x?")
    assert client.models.calls
    assert all(task_type == "RETRIEVAL_QUERY" for _, _, task_type in client.models.calls)


def test_embed_documents_batches_gemini_at_configured_size(fake_gemini, monkeypatch):
    monkeypatch.setenv("GEMINI_EMBED_BATCH_SIZE", "20")
    client = fake_gemini([
        _FakeGeminiEmbedResponse([[1.0] * 8] * 20),
        _FakeGeminiEmbedResponse([[1.0] * 8] * 20),
        _FakeGeminiEmbedResponse([[1.0] * 8] * 5),
    ])
    texts = [f"chunk {i}" for i in range(45)]
    embeddings.embed_documents(texts)
    batch_sizes = [len(texts_in_call) for texts_in_call, _, _ in client.models.calls]
    assert batch_sizes == [20, 20, 5]


def test_gemini_embeddings_are_l2_normalised(fake_gemini):
    client = fake_gemini([_FakeGeminiEmbedResponse([[3.0, 4.0] + [0.0] * 6] * 2)])
    matrix = embeddings.embed_documents(["a", "b"])
    norms = np.linalg.norm(matrix, axis=1)
    assert np.allclose(norms, 1.0)


def test_gemini_embed_waits_out_mid_upload_rate_limit_and_finishes(fake_gemini, monkeypatch, caplog):
    monkeypatch.setenv("GEMINI_EMBED_BATCH_SIZE", "5")
    client = fake_gemini([
        _FakeGeminiEmbedResponse([[1.0] * 8] * 5),   # batch 1 succeeds
        _gemini_api_error(429, retry_after=30),       # batch 2, first attempt: rate limited
        _FakeGeminiEmbedResponse([[1.0] * 8] * 5),   # batch 2, retried: succeeds
    ])
    texts = [f"chunk {i}" for i in range(10)]

    with caplog.at_level("INFO", logger="embeddings"):
        matrix = embeddings.embed_documents(texts)

    # All 10 chunks made it through -- the 429 paused and resumed the
    # upload rather than failing it.
    assert matrix.shape[0] == 10
    assert len(client.models.calls) == 3
    norms = np.linalg.norm(matrix, axis=1)
    assert np.allclose(norms, 1.0)

    assert any(
        "embedded 5/10 chunks, waiting 30s for rate limit" in record.message
        for record in caplog.records
    )


def test_gemini_embed_exceeds_total_wait_budget_raises_service_busy(fake_gemini, monkeypatch):
    monkeypatch.setenv("EMBED_MAX_WAIT_SECONDS", "50")
    client = fake_gemini([
        _gemini_api_error(429, retry_after=20),
        _gemini_api_error(429, retry_after=20),
        _gemini_api_error(429, retry_after=20),
    ])
    with pytest.raises(embeddings.EmbeddingServiceBusyError):
        embeddings.embed_documents(["a"])
    # First two 429s are waited out (running total 20s, then 40s, both
    # within the 50s budget); the third would push the total to 60s, over
    # budget, so it raises instead of waiting (or calling) a 4th time.
    assert len(client.models.calls) == 3


def test_gemini_embed_non_rate_limit_error_still_uses_bounded_retry(fake_gemini):
    client = fake_gemini([
        _gemini_api_error(503),
        _gemini_api_error(503),
        _gemini_api_error(503),
    ])
    with pytest.raises(RuntimeError):
        embeddings.embed_documents(["a"])
    # Bounded attempts (_MAX_ATTEMPTS), not the unbounded rate-limit wait.
    assert len(client.models.calls) == embeddings._MAX_ATTEMPTS


def test_embed_query_uses_its_own_shorter_wait_budget(fake_gemini, monkeypatch):
    # embed_query() is on the interactive request path, so it gets a much
    # shorter rate-limit wait budget than embed_documents() (document
    # upload) -- a single 20s wait already exceeds the 10s query budget,
    # so it should raise immediately without a second call.
    monkeypatch.setenv("EMBED_QUERY_MAX_WAIT_SECONDS", "10")
    query_client = fake_gemini([_gemini_api_error(429, retry_after=20)])
    with pytest.raises(embeddings.EmbeddingServiceBusyError):
        embeddings.embed_query("hello")
    assert len(query_client.models.calls) == 1

    # The same 20s wait is comfortably within the much larger (default)
    # document-upload budget, so embed_documents() waits it out and
    # succeeds instead.
    doc_client = fake_gemini([
        _gemini_api_error(429, retry_after=20),
        _FakeGeminiEmbedResponse([[1.0] * 8]),
    ])
    embeddings.embed_documents(["a"])
    assert len(doc_client.models.calls) == 2


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _make_user_with_book(db_session, username):
    user = User(username=username, password="hashed")
    db_session.add(user)
    db_session.commit()
    # retrieval.py only scores pages belonging to a "ready" book -- these
    # tests are about embedding-model matching, not processing status.
    book = Book(name="book.txt", user_id=user.id, status="ready")
    db_session.add(book)
    db_session.commit()
    return user, book


def test_retrieve_ignores_pages_from_a_different_embedding_model(db_session, fake_voyage):
    retrieval.invalidate_cache()
    user, book = _make_user_with_book(db_session, "retrieval_test_user")

    current_model = embeddings.active_model_name()  # "voyage:voyage-4"

    matching_page = Page(
        book_id=book.id, page_number=1, chunk_number=1,
        content="bees pollinate flowers",
        embedding="[1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]",
        embedding_model=current_model
    )
    stale_page = Page(
        book_id=book.id, page_number=1, chunk_number=2,
        content="this page is from the old local model",
        embedding=str([0.1] * 384),
        embedding_model="local:all-MiniLM-L6-v2"
    )
    db_session.add_all([matching_page, stale_page])
    db_session.commit()

    results = retrieval.retrieve(db_session, user.id, "bees", top_k=5, apply_threshold=False)

    result_contents = [page.content for _, page in results]
    assert matching_page.content in result_contents
    assert stale_page.content not in result_contents

    retrieval.invalidate_cache()


def test_needs_reembedding_true_when_no_pages_match_active_model(db_session):
    retrieval.invalidate_cache()
    user, book = _make_user_with_book(db_session, "needs_reembed_user")

    stale_page = Page(
        book_id=book.id, page_number=1, chunk_number=1,
        content="old content",
        embedding=str([0.1] * 384),
        embedding_model="local:all-MiniLM-L6-v2"
    )
    db_session.add(stale_page)
    db_session.commit()

    assert retrieval.needs_reembedding(db_session, user.id) is True

    matching_page = Page(
        book_id=book.id, page_number=1, chunk_number=2,
        content="new content",
        embedding=str([0.1] * 8),
        embedding_model=embeddings.active_model_name()
    )
    db_session.add(matching_page)
    db_session.commit()

    assert retrieval.needs_reembedding(db_session, user.id) is False
    retrieval.invalidate_cache()


def test_needs_reembedding_false_when_user_has_no_documents(db_session):
    retrieval.invalidate_cache()
    user = User(username="no_docs_user", password="hashed")
    db_session.add(user)
    db_session.commit()

    assert retrieval.needs_reembedding(db_session, user.id) is False
