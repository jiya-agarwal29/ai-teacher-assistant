"""
Tests for retrieval.py's neighbor-chunk expansion (Fix round 2, item 8):
"Where should the IDS be placed?" used to only surface the chunk with the
SPAN/mirror-port detail, missing "just behind the perimeter firewall" from
the immediately adjacent chunk of the same document. Also covers
book-scoped retrieval (Fix round 4, item 1 -- Quiz Builder's "Choose
document(s)").
"""
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import embeddings
import retrieval
from models import Base, Book, Page, User


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _make_user_with_book(db_session, username, book_name="book.txt"):
    user = User(username=username, password="hashed")
    db_session.add(user)
    db_session.commit()
    book = Book(name=book_name, user_id=user.id, status="ready")
    db_session.add(book)
    db_session.commit()
    return user, book


def test_expand_with_neighbor_chunks_adds_both_adjacent_chunks(db_session):
    _, book = _make_user_with_book(db_session, "neighbor_test_user")

    chunk5 = Page(book_id=book.id, page_number=3, chunk_number=5, content="chunk five")
    chunk6 = Page(
        book_id=book.id, page_number=3, chunk_number=6,
        content="the IDS should be placed just behind the perimeter firewall"
    )
    chunk7 = Page(
        book_id=book.id, page_number=4, chunk_number=7,
        content="using a SPAN or mirror port on the switch"
    )
    db_session.add_all([chunk5, chunk6, chunk7])
    db_session.commit()

    expanded = retrieval.expand_with_neighbor_chunks(db_session, [(0.9, chunk6)])

    ids = {page.id for _, page in expanded}
    assert ids == {chunk5.id, chunk6.id, chunk7.id}

    scores_by_id = {page.id: score for score, page in expanded}
    assert scores_by_id[chunk6.id] == 0.9
    assert scores_by_id[chunk5.id] < 0.9
    assert scores_by_id[chunk7.id] < 0.9


def test_expand_with_neighbor_chunks_skips_chunks_from_another_book(db_session):
    user, book_a = _make_user_with_book(db_session, "neighbor_book_a_user", "a.txt")
    book_b = Book(name="b.txt", user_id=user.id, status="ready")
    db_session.add(book_b)
    db_session.commit()

    top_hit = Page(book_id=book_a.id, page_number=1, chunk_number=2, content="top hit")
    other_book_chunk1 = Page(book_id=book_b.id, page_number=1, chunk_number=1, content="unrelated book, chunk 1")
    other_book_chunk3 = Page(book_id=book_b.id, page_number=1, chunk_number=3, content="unrelated book, chunk 3")
    db_session.add_all([top_hit, other_book_chunk1, other_book_chunk3])
    db_session.commit()

    expanded = retrieval.expand_with_neighbor_chunks(db_session, [(0.9, top_hit)])

    assert [page.id for _, page in expanded] == [top_hit.id]


def test_expand_with_neighbor_chunks_never_duplicates_an_already_selected_page(db_session):
    _, book = _make_user_with_book(db_session, "neighbor_dup_user")

    chunk1 = Page(book_id=book.id, page_number=1, chunk_number=1, content="first chunk")
    chunk2 = Page(book_id=book.id, page_number=1, chunk_number=2, content="second chunk, already selected")
    db_session.add_all([chunk1, chunk2])
    db_session.commit()

    # chunk2 is the top hit; chunk1 is already present too (e.g. a
    # separate real retrieval match) -- expansion must not add either
    # again as a "neighbor" of the other.
    expanded = retrieval.expand_with_neighbor_chunks(db_session, [(0.9, chunk2), (0.5, chunk1)])

    ids = [page.id for _, page in expanded]
    assert ids.count(chunk1.id) == 1
    assert ids.count(chunk2.id) == 1
    assert len(ids) == 2


def test_expand_with_neighbor_chunks_returns_empty_list_unchanged(db_session):
    assert retrieval.expand_with_neighbor_chunks(db_session, []) == []


# --- Resume round 3, item 2: a deleted/missing chunk must never crash
# the whole chat/tutor request. ---
def test_expand_with_neighbor_chunks_skips_a_none_page_instead_of_crashing(db_session):
    _, book = _make_user_with_book(db_session, "neighbor_none_page_user")
    real_page = Page(book_id=book.id, page_number=1, chunk_number=1, content="a real chunk")
    db_session.add(real_page)
    db_session.commit()

    # (score, None) simulates a chunk whose Page row was deleted (e.g. by
    # a re-index) between being looked up and being used here.
    expanded = retrieval.expand_with_neighbor_chunks(db_session, [(0.9, real_page), (0.8, None)])

    assert [page.id for _, page in expanded] == [real_page.id]


def test_expand_with_neighbor_chunks_returns_empty_when_only_a_none_page_is_given(db_session):
    assert retrieval.expand_with_neighbor_chunks(db_session, [(0.9, None)]) == []


def _add_page_pair(db_session, user, active_model, suffix):
    book_a = Book(name=f"a{suffix}.txt", user_id=user.id, status="ready")
    book_b = Book(name=f"b{suffix}.txt", user_id=user.id, status="ready")
    db_session.add_all([book_a, book_b])
    db_session.commit()

    page_a = Page(
        book_id=book_a.id, page_number=1, chunk_number=1, content="content from book A",
        embedding=json.dumps([1.0] + [0.0] * 7), embedding_model=active_model
    )
    page_b = Page(
        book_id=book_b.id, page_number=1, chunk_number=1, content="content from book B",
        embedding=json.dumps([1.0] + [0.0] * 7), embedding_model=active_model
    )
    db_session.add_all([page_a, page_b])
    db_session.commit()
    return book_a, book_b


# --- Fix round 4, item 1: Quiz Builder's "Choose document(s)" -- retrieval
# scoped to only the chosen documents. ---
def test_retrieve_with_book_ids_only_searches_the_given_books(db_session):
    retrieval.invalidate_cache()
    user = User(username="book_ids_test_user", password="hashed")
    db_session.add(user)
    db_session.commit()
    book_a, book_b = _add_page_pair(db_session, user, embeddings.active_model_name(), "1")

    results = retrieval.retrieve(
        db_session, user.id, "anything", top_k=5, apply_threshold=False, book_ids=[book_a.id]
    )

    assert {page.book_id for _, page in results} == {book_a.id}
    retrieval.invalidate_cache()


def test_retrieve_with_book_ids_none_searches_every_ready_book(db_session):
    retrieval.invalidate_cache()
    user = User(username="book_ids_none_test_user", password="hashed")
    db_session.add(user)
    db_session.commit()
    book_a, book_b = _add_page_pair(db_session, user, embeddings.active_model_name(), "2")

    results = retrieval.retrieve(db_session, user.id, "anything", top_k=5, apply_threshold=False)

    assert {page.book_id for _, page in results} == {book_a.id, book_b.id}
    retrieval.invalidate_cache()


def test_retrieve_with_book_ids_does_not_poison_the_whole_user_cache(db_session):
    """
    A book-scoped call bypasses the cache entirely (see retrieve()'s
    docstring) -- an unscoped call right after a scoped one must still see
    every ready book, not just the ones the scoped call looked at.
    """
    retrieval.invalidate_cache()
    user = User(username="book_ids_cache_test_user", password="hashed")
    db_session.add(user)
    db_session.commit()
    book_a, book_b = _add_page_pair(db_session, user, embeddings.active_model_name(), "3")

    scoped = retrieval.retrieve(
        db_session, user.id, "anything", top_k=5, apply_threshold=False, book_ids=[book_a.id]
    )
    assert {page.book_id for _, page in scoped} == {book_a.id}

    unscoped = retrieval.retrieve(db_session, user.id, "anything", top_k=5, apply_threshold=False)
    assert {page.book_id for _, page in unscoped} == {book_a.id, book_b.id}

    retrieval.invalidate_cache()
