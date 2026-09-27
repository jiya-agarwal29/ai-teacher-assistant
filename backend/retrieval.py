import json

import numpy as np
from sqlalchemy.orm import Session

from embeddings import create_embedding
from models import Page

_embedding_matrix = None
_page_ids = None


def invalidate_cache():
    global _embedding_matrix, _page_ids
    _embedding_matrix = None
    _page_ids = None


def _ensure_cache(db: Session):
    global _embedding_matrix, _page_ids

    if _embedding_matrix is not None:
        return

    vectors = []
    ids = []

    for page in db.query(Page).filter(Page.embedding.isnot(None)).all():
        try:
            vectors.append(json.loads(page.embedding))
            ids.append(page.id)
        except (TypeError, ValueError):
            continue

    _embedding_matrix = np.array(vectors) if vectors else np.empty((0, 0))
    _page_ids = ids


def retrieve(
    db: Session,
    query: str,
    top_k: int = 3,
    apply_threshold: bool = True
) -> list[tuple[float, Page]]:
    _ensure_cache(db)

    if _embedding_matrix.shape[0] == 0:
        return []

    query_vector = create_embedding(query)
    scores = _embedding_matrix @ query_vector

    order = np.argsort(scores)[::-1]
    ranked = [(float(scores[i]), _page_ids[i]) for i in order]

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
