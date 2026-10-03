"""
Background document-processing worker.

/upload-book only validates the file, saves it to disk, and creates a Book
row with status="processing" before returning 202 -- the slow part (parse,
chunk, embed, save pages) runs here, off the request thread, in a small
pool of worker threads. Each job opens its own DB session (SQLAlchemy
sessions aren't safe to share across threads) and is responsible for
leaving the book in a terminal status ("ready" or "failed") no matter how
it exits.
"""
import logging
import os
from concurrent.futures import ThreadPoolExecutor

import database
import embeddings
import json
from document_parsers import parse_document, chunk_parsed_document
from models import Book, Page
from retrieval import invalidate_cache

logger = logging.getLogger(__name__)

JOB_WORKERS = int(os.getenv("JOB_WORKERS", "2"))
# Background jobs have no user waiting on a response, so they get a much
# longer Gemini rate-limit wait budget than the interactive upload path
# used to have -- a large upload just takes longer instead of failing.
EMBED_JOB_MAX_WAIT_SECONDS = float(os.getenv("EMBED_JOB_MAX_WAIT_SECONDS", "1800"))

NO_READABLE_TEXT_ERROR = (
    "No readable text found. This looks like a scanned document — OCR support is coming soon."
)
EMBEDDING_BUSY_ERROR = "Search service is busy, please retry."
GENERIC_PROCESSING_ERROR = "Something went wrong while processing this document. Please try again."

_executor = ThreadPoolExecutor(max_workers=JOB_WORKERS, thread_name_prefix="doc-job")

# Tests monkeypatch this to True so uploads finish (ready/failed) before the
# request assertion runs, instead of racing a real background thread.
JOBS_SYNC = os.getenv("JOBS_SYNC", "false").strip().lower() == "true"


def submit_processing_job(book_id: int, file_path: str, filename: str):
    """Queues background processing for an already-saved upload. Returns immediately."""
    if JOBS_SYNC:
        _process_book(book_id, file_path, filename)
    else:
        _executor.submit(_process_book, book_id, file_path, filename)


def _detect_source_type(pages_data: list) -> str:
    """
    No OCR yet, so this is purely descriptive: "text" when every page
    yielded extractable text, "scanned" when none did (that book then also
    fails below, since there's nothing to index), "mixed" otherwise.
    """
    if not pages_data:
        return "text"
    non_empty = sum(1 for p in pages_data if p["content"].strip())
    if non_empty == len(pages_data):
        return "text"
    if non_empty == 0:
        return "scanned"
    return "mixed"


def _mark_failed(db, book_id: int, error: str):
    book = db.query(Book).filter(Book.id == book_id).first()
    if not book:
        return
    book.status = "failed"
    book.error = error
    db.commit()


def _process_book(book_id: int, file_path: str, filename: str):
    # Looked up on the database module at call time (not imported by name)
    # so tests can redirect it to an isolated test database -- see
    # tests/test_api.py's `database.SessionLocal = TestSessionLocal`.
    db = database.SessionLocal()
    try:
        _run_pipeline(db, book_id, file_path, filename)
    except Exception:
        logger.exception("Unhandled error while processing book_id=%d ('%s')", book_id, filename)
        try:
            _mark_failed(db, book_id, GENERIC_PROCESSING_ERROR)
        except Exception:
            logger.exception("Also failed to mark book_id=%d as failed", book_id)
    finally:
        db.close()


def _run_pipeline(db, book_id: int, file_path: str, filename: str):
    book = db.query(Book).filter(Book.id == book_id).first()
    if not book:
        logger.error("Background job: book_id=%d no longer exists, skipping", book_id)
        return

    try:
        with open(file_path, "rb") as f:
            pages_data = parse_document(f, filename)
    except ValueError as e:
        _mark_failed(db, book_id, str(e))
        return
    except Exception:
        logger.exception("Failed to parse document for book_id=%d ('%s')", book_id, filename)
        _mark_failed(db, book_id, GENERIC_PROCESSING_ERROR)
        return

    book.source_type = _detect_source_type(pages_data)
    chunks = chunk_parsed_document(pages_data)
    book.pages_total = len(chunks)
    db.commit()

    if not chunks:
        _mark_failed(db, book_id, NO_READABLE_TEXT_ERROR)
        return

    texts = [chunk_item["content"] for chunk_item in chunks]

    def _report_progress(done_count):
        # Best-effort: a progress update failing shouldn't abort the job.
        try:
            book.pages_done = done_count
            db.commit()
        except Exception:
            logger.exception("Failed to update progress for book_id=%d", book_id)

    try:
        embedding_vectors = embeddings.embed_documents(
            texts, max_wait_seconds=EMBED_JOB_MAX_WAIT_SECONDS, on_batch_done=_report_progress
        )
    except embeddings.EmbeddingServiceBusyError:
        logger.warning("Embedding service busy while embedding book_id=%d ('%s')", book_id, filename)
        _mark_failed(db, book_id, EMBEDDING_BUSY_ERROR)
        return
    except Exception:
        logger.exception("Failed to embed chunks for book_id=%d ('%s')", book_id, filename)
        _mark_failed(db, book_id, GENERIC_PROCESSING_ERROR)
        return

    if len(embedding_vectors) != len(texts):
        logger.error(
            "Embedding count mismatch for book_id=%d: expected %d chunks, got %d embeddings",
            book_id, len(texts), len(embedding_vectors)
        )
        _mark_failed(db, book_id, GENERIC_PROCESSING_ERROR)
        return

    embedding_model = embeddings.active_model_name()

    try:
        for chunk_index, (chunk_item, embedding) in enumerate(zip(chunks, embedding_vectors)):
            db.add(Page(
                book_id=book_id,
                page_number=chunk_item["page_number"],
                chunk_number=chunk_index + 1,
                content=chunk_item["content"],
                embedding=json.dumps(embedding.tolist()),
                embedding_model=embedding_model
            ))

        book.pages_done = len(chunks)
        book.status = "ready"
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to save pages for book_id=%d ('%s')", book_id, filename)
        _mark_failed(db, book_id, GENERIC_PROCESSING_ERROR)
        return

    invalidate_cache(book.user_id)
