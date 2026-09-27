import json

import numpy as np
from sqlalchemy.orm import Session

from embeddings import create_embedding
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
    return db.query(Page.id).join(
        Book, Page.book_id == Book.id
    ).filter(Book.user_id == user_id).first() is not None


def _ensure_cache(db: Session, user_id: int):
    if user_id in _cache_by_user:
        return

    vectors = []
    ids = []

    pages = db.query(Page).join(
        Book, Page.book_id == Book.id
    ).filter(
        Book.user_id == user_id,
        Page.embedding.isnot(None)
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

    query_vector = create_embedding(query)
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
