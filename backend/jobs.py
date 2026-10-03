"""
Background document-processing worker.

/upload-book only validates the file(s), saves them to disk, and creates a
Book row with status="processing" before returning 202 -- everything slow
(parsing/OCR, chunking, embedding, saving pages) runs here, off the request
thread, in a small pool of worker threads. Each job opens its own DB session
(SQLAlchemy sessions aren't safe to share across threads) and is responsible
for leaving the book in a terminal status ("ready"/"failed") or "needs_review"
no matter how it exits.

Per-page extraction always produces DocumentPage rows first (method="text"
or "ocr", review_status "auto_approved"/"needs_review") -- Page rows (the
searchable chunked index) are only ever built from DocumentPage.extracted_text,
and only once every page for that book is approved.

Two more job types live here for the review flow (main.py's /books/{id}/...
pages endpoints): submit_indexing_job() does the chunk+embed+save step on
its own, for when a book's pages only just finished being approved (or
re-approved after a post-approval edit, in which case it rebuilds -- see
_chunk_embed_and_save) -- and submit_reread_job() re-runs OCR for a single
already-saved page image.
"""
import logging
import os
from concurrent.futures import ThreadPoolExecutor

import database
import document_parsers
import embeddings
import json
import llm
import ocr
from document_parsers import chunk_parsed_document
from models import Book, DocumentPage, Page
from retrieval import invalidate_cache

logger = logging.getLogger(__name__)

JOB_WORKERS = int(os.getenv("JOB_WORKERS", "2"))
# Background jobs have no user waiting on a response, so they get a much
# longer Gemini rate-limit wait budget than the interactive upload path
# used to have -- a large upload just takes longer instead of failing.
EMBED_JOB_MAX_WAIT_SECONDS = float(os.getenv("EMBED_JOB_MAX_WAIT_SECONDS", "1800"))

# A pdfplumber page with fewer than this many real (post-cleanup) characters
# is treated as having no usable text layer and gets OCR'd instead.
OCR_MIN_CHARS = int(os.getenv("OCR_MIN_CHARS", "40"))
# Caps both how many low-text PDF pages one document may send to OCR, and
# how many images a multi-image upload may contain -- OCR is a per-page
# Gemini vision call, so this bounds cost/time on one document.
MAX_OCR_PAGES = int(os.getenv("MAX_OCR_PAGES", "50"))

NO_READABLE_TEXT_ERROR = (
    "No readable text found. This looks like a scanned document — OCR support is coming soon."
)
EMBEDDING_BUSY_ERROR = "Search service is busy, please retry."
OCR_BUSY_ERROR = "Search service is busy, please retry."
GENERIC_PROCESSING_ERROR = "Something went wrong while processing this document. Please try again."

_executor = ThreadPoolExecutor(max_workers=JOB_WORKERS, thread_name_prefix="doc-job")

# Tests monkeypatch this to True so uploads finish (ready/failed/needs_review)
# before the request assertion runs, instead of racing a real background thread.
JOBS_SYNC = os.getenv("JOBS_SYNC", "false").strip().lower() == "true"


def submit_processing_job(book_id: int, kind: str, file_paths: list, filename: str, review_ocr: bool = True):
    """
    Queues background processing for an already-saved upload. Returns
    immediately. `kind` is "document" (file_paths has exactly one path: a
    pdf/docx/pptx/doc/ppt/txt/md file) or "images" (one or more page image
    paths, in reading order).
    """
    if JOBS_SYNC:
        _process_book(book_id, kind, file_paths, filename, review_ocr)
    else:
        _executor.submit(_process_book, book_id, kind, file_paths, filename, review_ocr)


def _mark_failed(db, book_id: int, error: str):
    book = db.query(Book).filter(Book.id == book_id).first()
    if not book:
        return
    book.status = "failed"
    book.error = error
    db.commit()


def _save_page_image(book_dir, page_number: int, image_bytes: bytes) -> str:
    pages_dir = os.path.join(book_dir, "pages")
    os.makedirs(pages_dir, exist_ok=True)
    path = os.path.join(pages_dir, f"{page_number:04d}.jpg")
    with open(path, "wb") as f:
        f.write(image_bytes)
    return path


def _extract_pdf_as_pages(db, book, file_path: str, book_dir: str, review_ocr: bool) -> list:
    """
    Per-page decision for a PDF: pdfplumber text if the page has at least
    OCR_MIN_CHARS real (post-cleanup) characters, otherwise render that page
    with pypdfium2 and OCR it with Gemini vision. Returns a list of page
    records (page_number/extracted_text/method/review_status/image_path).
    """
    with open(file_path, "rb") as f:
        pages_data = document_parsers.parse_pdf(f)

    ocr_page_numbers = [
        p["page_number"] for p in pages_data if len(p["content"].strip()) < OCR_MIN_CHARS
    ]
    if len(ocr_page_numbers) > MAX_OCR_PAGES:
        raise ValueError(
            f"This document has {len(ocr_page_numbers)} scanned or low-text pages, "
            f"over the {MAX_OCR_PAGES}-page OCR limit."
        )

    # Drives the "Reading page N of M" progress shown while book.source_type
    # is still unset (see jobs._run_pipeline / the frontend status poll).
    book.pages_total = len(ocr_page_numbers)
    book.pages_done = 0
    db.commit()

    records = []
    ocr_done = 0

    for page in pages_data:
        content = page["content"]
        page_number = page["page_number"]

        if len(content.strip()) >= OCR_MIN_CHARS:
            records.append({
                "page_number": page_number,
                "extracted_text": content,
                "method": "text",
                "review_status": "auto_approved",
                "image_path": None,
            })
            continue

        rendered = ocr.render_pdf_page_to_png(file_path, page_index=page_number - 1)
        prepared = ocr.prepare_image_for_ocr(rendered)
        image_path = _save_page_image(book_dir, page_number, prepared)

        extracted_text = llm.read_image(
            prepared, mime_type="image/jpeg", max_wait_seconds=EMBED_JOB_MAX_WAIT_SECONDS
        )

        records.append({
            "page_number": page_number,
            "extracted_text": extracted_text,
            "method": "ocr",
            "review_status": "needs_review" if review_ocr else "auto_approved",
            "image_path": image_path,
        })

        ocr_done += 1
        book.pages_done = ocr_done
        db.commit()

    return records


def _extract_images_as_pages(db, book, file_paths: list, book_dir: str, review_ocr: bool) -> list:
    """Every image is its own page, always OCR'd, in the order given."""
    if len(file_paths) > MAX_OCR_PAGES:
        raise ValueError(
            f"This document has {len(file_paths)} images, over the {MAX_OCR_PAGES}-page OCR limit."
        )

    book.pages_total = len(file_paths)
    book.pages_done = 0
    db.commit()

    records = []

    for index, path in enumerate(file_paths, start=1):
        with open(path, "rb") as f:
            raw_bytes = f.read()

        prepared = ocr.prepare_image_for_ocr(raw_bytes)
        image_path = _save_page_image(book_dir, index, prepared)

        extracted_text = llm.read_image(
            prepared, mime_type="image/jpeg", max_wait_seconds=EMBED_JOB_MAX_WAIT_SECONDS
        )

        records.append({
            "page_number": index,
            "extracted_text": extracted_text,
            "method": "ocr",
            "review_status": "needs_review" if review_ocr else "auto_approved",
            "image_path": image_path,
        })

        book.pages_done = index
        db.commit()

    return records


def _extract_document_as_pages(db, book, file_path: str, filename: str, book_dir: str, review_ocr: bool) -> list:
    """PDF gets the per-page OCR-aware treatment above; everything else is unchanged from Phase 3A (always method="text")."""
    ext = os.path.splitext(filename)[1].lower()

    if ext == ".pdf":
        return _extract_pdf_as_pages(db, book, file_path, book_dir, review_ocr)

    with open(file_path, "rb") as f:
        pages_data = document_parsers.parse_document(f, filename)

    return [
        {
            "page_number": p["page_number"],
            "extracted_text": p["content"],
            "method": "text",
            "review_status": "auto_approved",
            "image_path": None,
        }
        for p in pages_data
    ]


def _process_book(book_id: int, kind: str, file_paths: list, filename: str, review_ocr: bool):
    # Looked up on the database module at call time (not imported by name)
    # so tests can redirect it to an isolated test database -- see
    # tests/test_api.py's `database.SessionLocal = TestSessionLocal`.
    db = database.SessionLocal()
    try:
        _run_pipeline(db, book_id, kind, file_paths, filename, review_ocr)
    except Exception:
        logger.exception("Unhandled error while processing book_id=%d ('%s')", book_id, filename)
        try:
            _mark_failed(db, book_id, GENERIC_PROCESSING_ERROR)
        except Exception:
            logger.exception("Also failed to mark book_id=%d as failed", book_id)
    finally:
        db.close()


def _run_pipeline(db, book_id: int, kind: str, file_paths: list, filename: str, review_ocr: bool):
    book = db.query(Book).filter(Book.id == book_id).first()
    if not book:
        logger.error("Background job: book_id=%d no longer exists, skipping", book_id)
        return

    # A retry re-extracts from scratch (simple and correct, if not the
    # cheapest possible option for a book that already got partway through
    # OCR before failing) -- clear any DocumentPage rows left over from a
    # previous attempt so this run's rows aren't mixed in with stale ones.
    db.query(DocumentPage).filter(DocumentPage.book_id == book_id).delete()
    db.commit()

    book_dir = os.path.dirname(file_paths[0]) if kind == "document" else os.path.dirname(os.path.dirname(file_paths[0]))

    try:
        if kind == "images":
            page_records = _extract_images_as_pages(db, book, file_paths, book_dir, review_ocr)
        else:
            page_records = _extract_document_as_pages(db, book, file_paths[0], filename, book_dir, review_ocr)
    except NotImplementedError as e:
        _mark_failed(db, book_id, str(e))
        return
    except llm.LLMUnavailableError:
        logger.warning("OCR service busy while processing book_id=%d ('%s')", book_id, filename)
        _mark_failed(db, book_id, OCR_BUSY_ERROR)
        return
    except ValueError as e:
        _mark_failed(db, book_id, str(e))
        return
    except Exception:
        logger.exception("Failed to extract pages for book_id=%d ('%s')", book_id, filename)
        _mark_failed(db, book_id, GENERIC_PROCESSING_ERROR)
        return

    if not page_records:
        _mark_failed(db, book_id, NO_READABLE_TEXT_ERROR)
        return

    methods = {p["method"] for p in page_records}
    if methods == {"text"}:
        book.source_type = "text"
    elif methods == {"ocr"}:
        book.source_type = "scanned"
    else:
        book.source_type = "mixed"

    for p in page_records:
        db.add(DocumentPage(
            book_id=book_id,
            page_number=p["page_number"],
            image_path=p["image_path"],
            extracted_text=p["extracted_text"],
            method=p["method"],
            review_status=p["review_status"],
        ))
    db.commit()

    if any(p["review_status"] == "needs_review" for p in page_records):
        # Nothing is chunked/embedded yet -- the uploaded/rendered pages and
        # their transcriptions are saved, but Page rows (the searchable
        # index) are only ever built once every page is approved. No
        # review/approve endpoint exists yet; a later phase adds one.
        book.status = "needs_review"
        db.commit()
        return

    _chunk_embed_and_save(db, book, book_id, page_records, filename)


def _chunk_embed_and_save(db, book, book_id: int, page_records: list, filename: str):
    # Idempotent rebuild: a first-time build has nothing to delete here, but
    # re-indexing after a post-approval edit (see submit_indexing_job) must
    # replace the old chunks rather than append duplicates alongside them.
    db.query(Page).filter(Page.book_id == book_id).delete()
    db.commit()

    pages_data = [
        {"page_number": p["page_number"], "content": p["extracted_text"] or ""}
        for p in page_records
    ]
    chunks = chunk_parsed_document(pages_data)
    book.pages_total = len(chunks)
    book.pages_done = 0
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


def submit_indexing_job(book_id: int):
    """
    Queues the chunk+embed+save step for a book whose pages are all
    approved -- called once the last "needs_review" page is approved (see
    main.py's _finalize_if_fully_approved). Also used to rebuild the index
    after a post-ready edit: _chunk_embed_and_save replaces any existing
    Page rows rather than appending to them.
    """
    if JOBS_SYNC:
        _run_indexing_job(book_id)
    else:
        _executor.submit(_run_indexing_job, book_id)


def _run_indexing_job(book_id: int):
    db = database.SessionLocal()
    try:
        book = db.query(Book).filter(Book.id == book_id).first()
        if not book:
            logger.error("Indexing job: book_id=%d no longer exists, skipping", book_id)
            return

        doc_pages = db.query(DocumentPage).filter(
            DocumentPage.book_id == book_id
        ).order_by(DocumentPage.page_number).all()

        page_records = [
            {"page_number": p.page_number, "extracted_text": p.extracted_text or ""}
            for p in doc_pages
        ]

        _chunk_embed_and_save(db, book, book_id, page_records, book.name)
    except Exception:
        logger.exception("Unhandled error while indexing book_id=%d", book_id)
        try:
            _mark_failed(db, book_id, GENERIC_PROCESSING_ERROR)
        except Exception:
            logger.exception("Also failed to mark book_id=%d as failed", book_id)
    finally:
        db.close()


def submit_reread_job(book_id: int, page_number: int):
    """
    Queues a single page's OCR to run again against its already-saved
    (already-prepared) image -- no re-render/re-upload needed. The route
    sets review_status="needs_review" synchronously before calling this;
    this job only needs to update the transcription once it's done.
    """
    if JOBS_SYNC:
        _reread_page(book_id, page_number)
    else:
        _executor.submit(_reread_page, book_id, page_number)


def _reread_page(book_id: int, page_number: int):
    db = database.SessionLocal()
    try:
        page = db.query(DocumentPage).filter(
            DocumentPage.book_id == book_id,
            DocumentPage.page_number == page_number
        ).first()
        if not page or not page.image_path:
            logger.error(
                "Re-read job: no page/image for book_id=%d page=%d, skipping", book_id, page_number
            )
            return

        try:
            with open(page.image_path, "rb") as f:
                image_bytes = f.read()
            page.extracted_text = llm.read_image(
                image_bytes, mime_type="image/jpeg", max_wait_seconds=EMBED_JOB_MAX_WAIT_SECONDS
            )
            db.commit()
        except Exception:
            # Best-effort: the page already sits at review_status="needs_review"
            # (set by the route before this job ran) with its prior text intact,
            # so a failed re-read just means "try again" rather than losing anything.
            logger.exception("Failed to re-read book_id=%d page=%d", book_id, page_number)
    finally:
        db.close()
