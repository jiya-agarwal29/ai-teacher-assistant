"""
Tests for retrieval.py's neighbor-chunk expansion (Fix round 2, item 8):
"Where should the IDS be placed?" used to only surface the chunk with the
SPAN/mirror-port detail, missing "just behind the perimeter firewall" from
the immediately adjacent chunk of the same document.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

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
