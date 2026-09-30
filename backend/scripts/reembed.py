"""
Re-embeds stored pages with the currently active embedding provider
(EMBEDDING_PROVIDER), in batches, showing progress.

Voyage and local MiniLM vectors have different dimensions and can't be
mixed -- a page keeps its old embedding + embedding_model until this script
re-embeds it, and retrieval.py ignores any page whose embedding_model
doesn't match the active one. Run this after switching EMBEDDING_PROVIDER
(or VOYAGE_MODEL) so existing documents become searchable again.

Usage:
    python scripts/reembed.py                    # every page, all users
    python scripts/reembed.py --username alice    # only alice's pages
"""
import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

import embeddings
from database import SessionLocal
from models import Book, Page, User
from retrieval import invalidate_cache

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(message)s",
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

BATCH_SIZE = 64


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", help="Only re-embed pages belonging to this user.")
    args = parser.parse_args()

    # Fails loudly here if the active provider is misconfigured (e.g. a
    # missing VOYAGE_API_KEY), before touching any data.
    embeddings.init()
    target_model = embeddings.active_model_name()
    logger.info("Re-embedding with active model: %s", target_model)

    db = SessionLocal()
    try:
        query = db.query(Page).join(Book, Page.book_id == Book.id)

        if args.username:
            user = db.query(User).filter(User.username == args.username).first()
            if not user:
                logger.error("No user named '%s' found.", args.username)
                sys.exit(1)
            query = query.filter(Book.user_id == user.id)

        pages = query.all()
        total = len(pages)

        if total == 0:
            logger.info("No pages found to re-embed.")
            return

        logger.info("Re-embedding %d page(s)...", total)
        updated = 0

        for start in range(0, total, BATCH_SIZE):
            batch = pages[start:start + BATCH_SIZE]
            texts = [page.content for page in batch]
            vectors = embeddings.embed_documents(texts)

            for page, vector in zip(batch, vectors):
                page.embedding = json.dumps(vector.tolist())
                page.embedding_model = target_model
                updated += 1

            db.commit()
            logger.info("  %d/%d done", min(start + BATCH_SIZE, total), total)

        invalidate_cache()
        logger.info("Re-embedded %d page(s) with '%s'.", updated, target_model)
    finally:
        db.close()


if __name__ == "__main__":
    main()
