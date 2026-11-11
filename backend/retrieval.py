import json

import numpy as np
from sqlalchemy.orm import Session

import embeddings
from models import Book, Page

# Per-user cache: user_id -> (embedding_matrix, page_ids)
_cache_by_user = {}


def invalidate_cache(user_id: int = None):
    global _cache_by_user
    if user_id is None:
        _cache_by_user = {}
    else:
        _cache_by_user.pop(user_id, None)


def user_has_documents(db: Session, user_id: int) -> bool:
    # Only books that finished processing are searchable -- a "processing"
    # or "failed" book has no usable (or no) pages yet.
    return db.query(Page.id).join(
        Book, Page.book_id == Book.id
    ).filter(Book.user_id == user_id, Book.status == "ready").first() is not None


def user_has_any_books(db: Session, user_id: int) -> bool:
    """
    True if the user has uploaded anything at all, regardless of status --
    used to tell "never uploaded anything" apart from "uploaded, but not
    searchable yet" (still processing, awaiting OCR review, or failed) so
    the AI routes can give a more accurate message than a flat "no
    documents uploaded" in the latter case.
    """
    return db.query(Book.id).filter(Book.user_id == user_id).first() is not None


def needs_reembedding(db: Session, user_id: int) -> bool:
    """
    True when the user has documents, but none of them are embedded with
    the currently active embedding model (e.g. after switching
    EMBEDDING_PROVIDER without re-running scripts/reembed.py yet) --
    otherwise retrieve() would silently return no results, indistinguishable
    from "no documents uploaded".
    """
    if not user_has_documents(db, user_id):
        return False

    has_current = db.query(Page.id).join(
        Book, Page.book_id == Book.id
    ).filter(
        Book.user_id == user_id,
        Book.status == "ready",
        Page.embedding_model == embeddings.active_model_name()
    ).first() is not None

    return not has_current


def _ensure_cache(db: Session, user_id: int):
    if user_id in _cache_by_user:
        return

    vectors = []
    ids = []

    pages = db.query(Page).join(
        Book, Page.book_id == Book.id
    ).filter(
        Book.user_id == user_id,
        Book.status == "ready",
        Page.embedding.isnot(None),
        Page.embedding_model == embeddings.active_model_name()
    ).all()

    for page in pages:
        try:
            vectors.append(json.loads(page.embedding))
            ids.append(page.id)
        except (TypeError, ValueError):
            continue

    matrix = np.array(vectors) if vectors else np.empty((0, 0))
    _cache_by_user[user_id] = (matrix, ids)


def retrieve(
    db: Session,
    user_id: int,
    query: str,
    top_k: int = 3,
    apply_threshold: bool = True
) -> list[tuple[float, Page]]:
    _ensure_cache(db, user_id)

    embedding_matrix, page_ids = _cache_by_user[user_id]

    if embedding_matrix.shape[0] == 0:
        return []

    query_vector = embeddings.embed_query(query)
    scores = embedding_matrix @ query_vector

    order = np.argsort(scores)[::-1]
    ranked = [(float(scores[i]), page_ids[i]) for i in order]

    if not apply_threshold:
        selected = ranked[:top_k]
    else:
        # Filter weak matches and apply relative retrieval thresholding
        selected = []
        top_score = ranked[0][0]
        for score, page_id in ranked:
            if score > 0.22:
                if top_score > 0.40:
                    if score >= top_score - 0.18:
                        selected.append((score, page_id))
                else:
                    selected.append((score, page_id))

    if not selected:
        return []

    pages_by_id = {
        page.id: page
        for page in db.query(Page).filter(
            Page.id.in_([page_id for _, page_id in selected])
        ).all()
    }

    if not apply_threshold:
        return [
            (score, pages_by_id[page_id])
            for score, page_id in selected
            if page_id in pages_by_id
        ]

    # Deduplicate retrieved chunks (Jaccard similarity > 0.5 is considered duplicate)
    top_pages = []
    seen_contents = []
    for score, page_id in selected:
        page = pages_by_id.get(page_id)
        if not page:
            continue

        page_words = set(page.content.lower().split())
        if not page_words:
            continue

        is_duplicate = False
        for seen_text in seen_contents:
            seen_words = set(seen_text.lower().split())
            if seen_words:
                intersection = page_words.intersection(seen_words)
                union = page_words.union(seen_words)
                overlap = len(intersection) / len(union)
                if overlap > 0.5:
                    is_duplicate = True
                    break

        if not is_duplicate:
            top_pages.append((score, page))
            seen_contents.append(page.content)
            if len(top_pages) >= top_k:
                break

    return top_pages


def expand_with_neighbor_chunks(
    db: Session, top_pages: list[tuple[float, Page]]
) -> list[tuple[float, Page]]:
    """
    Fix round 2, item 8: the single best-matching chunk sometimes only
    covers part of the answer because the document's own text continues
    into the next chunk (e.g. "...placed just behind the perimeter
    firewall" ends one chunk, and the SPAN/mirror-port detail the retrieval
    actually scored on starts the next). Pulls in the chunk immediately
    before and after the TOP hit only -- not every retrieved chunk, so the
    added context stays small -- by chunk_number within the same book
    (chunk_number is assigned in document order when a book is indexed,
    see jobs.py's _chunk_embed_and_save). A higher top_k was the other
    option considered, but that pulls in more *unrelated* chunks too
    (worsening item 9's "unrelated sources" problem); expanding only the
    neighbors of the best hit targets the actual failure instead.

    Neighbors are given a score just under the top hit's own score, so
    they still sort below genuine matches. Already-selected pages (by id)
    are never duplicated.
    """
    if not top_pages:
        return top_pages

    top_score, top_page = top_pages[0]
    existing_ids = {page.id for _, page in top_pages}

    neighbors = db.query(Page).filter(
        Page.book_id == top_page.book_id,
        Page.chunk_number.in_([top_page.chunk_number - 1, top_page.chunk_number + 1])
    ).all()

    expanded = list(top_pages)
    for neighbor in neighbors:
        if neighbor.id in existing_ids:
            continue
        expanded.append((top_score - 0.01, neighbor))
        existing_ids.add(neighbor.id)

    return expanded
