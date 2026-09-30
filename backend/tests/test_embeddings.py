"""
Tests for the swappable embedding layer (embeddings.py) and how retrieval.py
uses it. The Voyage client is always mocked here -- no real network calls.
"""
import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import embeddings
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


@pytest.fixture(autouse=True)
def voyage_provider(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "voyage")
    monkeypatch.setenv("VOYAGE_API_KEY", "fake-key-for-tests")


@pytest.fixture
def fake_voyage(monkeypatch):
    client = _FakeVoyageClient()
    monkeypatch.setattr(embeddings, "_get_voyage_client", lambda: client)
    return client


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
    monkeypatch.setenv("VOYAGE_MODEL", "voyage-4")
    assert embeddings.active_model_name() == "voyage:voyage-4"

    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
    assert embeddings.active_model_name() == "local:all-MiniLM-L6-v2"


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
    book = Book(name="book.txt", user_id=user.id)
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
