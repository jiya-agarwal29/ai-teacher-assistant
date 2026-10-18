"""
Background document-processing worker.

/upload-book only validates the file(s), saves them via storage.py, and creates a
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
import io
import logging
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor

import database
import document_parsers
import embeddings
import json
import llm
import ocr
import storage
from document_parsers import chunk_parsed_document
from env_utils import get_env
from models import Book, DocumentPage, Page
from retrieval import invalidate_cache

logger = logging.getLogger(__name__)

JOB_WORKERS = int(get_env("JOB_WORKERS", "2"))
# Background jobs have no user waiting on a response, so they get a much
# longer Gemini rate-limit wait budget than the interactive upload path
# used to have -- a large upload just takes longer instead of failing.
EMBED_JOB_MAX_WAIT_SECONDS = float(get_env("EMBED_JOB_MAX_WAIT_SECONDS", "1800"))

# A pdfplumber page with fewer than this many real (post-cleanup) characters
# is treated as having no usable text layer and gets OCR'd instead.
OCR_MIN_CHARS = int(get_env("OCR_MIN_CHARS", "40"))
# Caps both how many low-text PDF pages one document may send to OCR, and
# how many images a multi-image upload may contain -- OCR is a per-page
# Gemini vision call, so this bounds cost/time on one document.
MAX_OCR_PAGES = int(get_env("MAX_OCR_PAGES", "50"))

NO_READABLE_TEXT_ERROR = (
    "No readable text found. This looks like a scanned document — OCR support is coming soon."
)
EMBEDDING_BUSY_ERROR = "Search service is busy, please retry."
OCR_BUSY_ERROR = "Search service is busy, please retry."
GENERIC_PROCESSING_ERROR = "Something went wrong while processing this document. Please try again."

_executor = ThreadPoolExecutor(max_workers=JOB_WORKERS, thread_name_prefix="doc-job")

# Tests monkeypatch this to True so uploads finish (ready/failed/needs_review)
# before the request assertion runs, instead of racing a real background thread.
JOBS_SYNC = (get_env("JOBS_SYNC", "false") or "false").lower() == "true"


def submit_processing_job(book_id: int, kind: str, storage_keys: list, filename: str, review_ocr: bool = True):
    """
    Queues background processing for an already-saved upload. Returns
    immediately. `kind` is "document" (storage_keys has exactly one key: a
    pdf/docx/pptx/doc/ppt/txt/md file) or "images" (one or more page image
    keys, in reading order). The original bytes are read from storage.py's
    storage backend, not straight off disk.
    """
    if JOBS_SYNC:
        _process_book(book_id, kind, storage_keys, filename, review_ocr)
    else:
        _executor.submit(_process_book, book_id, kind, storage_keys, filename, review_ocr)


def _mark_failed(db, book_id: int, error: str):
    book = db.query(Book).filter(Book.id == book_id).first()
    if not book:
        return
    book.status = "failed"
    book.error = error
    db.commit()


def _save_page_image(user_id: int, book_id: int, page_number: int, image_bytes: bytes) -> str:
    key = f"users/{user_id}/books/{book_id}/pages/{page_number:04d}.jpg"
    storage.storage.save(key, image_bytes, content_type="image/jpeg")
    return key


def _extract_pdf_as_pages(db, book, pdf_bytes: bytes, review_ocr: bool) -> list:
    """
    Per-page decision for a PDF: pdfplumber text if the page has at least
    OCR_MIN_CHARS real (post-cleanup) characters, otherwise render that page
    with pypdfium2 and OCR it with Gemini vision. Returns a list of page
    records (page_number/extracted_text/method/review_status/image_path).

    Writes `pdf_bytes` to one temp file and has both pdfplumber and
    pypdfium2 read from that path rather than from Python-held bytes --
    passing bytes directly makes each library copy the whole file into
    its own buffer, so a large scanned PDF would otherwise sit in memory
    multiple times over (Python's own pdf_bytes, plus pdfplumber's and
    pypdfium2's own copies) instead of letting both C libraries read the
    file themselves. pypdfium2 re-opens (and reparses) the file fresh for
    each OCR'd page rather than keeping one document handle open across
    the whole loop -- measured empirically (Phase 7C item 6): keeping a
    single document open let pdfium's own per-page caches (decoded
    images, content streams) accumulate for the document's entire
    lifetime, peaking *higher* over a many-page scan than the repeated
    open/close despite its extra reparsing. Each page's rendered/prepared
    image is OCR'd, saved, and dropped (`del`) before the next page is
    even rendered.
    """
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
    try:
        with os.fdopen(tmp_fd, "wb") as f:
            f.write(pdf_bytes)
        del pdf_bytes

        pages_data = document_parsers.parse_pdf(tmp_path)

        ocr_page_numbers = [
            p["page_number"] for p in pages_data if len(p["content"].strip()) < OCR_MIN_CHARS
        ]
        if len(ocr_page_numbers) > MAX_OCR_PAGES:
            raise ValueError(
                f"This document has {len(ocr_page_numbers)} scanned or low-text pages, "
                f"over the {MAX_OCR_PAGES}-page OCR limit."
            )

        # Drives the "Reading page N of M" progress shown while
        # book.source_type is still unset (see jobs._run_pipeline / the
        # frontend status poll).
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

            # Opened and closed fresh for each page rather than kept open
            # across the loop -- measured empirically (Phase 7C item 6):
            # keeping one pdfium document open for every page let its own
            # internal per-page caches (decoded images, content streams)
            # accumulate for the document's whole lifetime, peaking
            # *higher* than reopening per page despite the repeated parse.
            # tmp_path (not pdf_bytes) so pdfium reads the file itself
            # rather than copying it into its own buffer from Python bytes.
            rendered = ocr.render_pdf_page_to_png(tmp_path, page_index=page_number - 1)
            prepared = ocr.prepare_image_for_ocr(rendered)
            del rendered
            image_path = _save_page_image(book.user_id, book.id, page_number, prepared)

            extracted_text = llm.read_image(
                prepared, mime_type="image/jpeg", max_wait_seconds=EMBED_JOB_MAX_WAIT_SECONDS
            )
            del prepared

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
    finally:
        os.remove(tmp_path)

    return records


def _extract_images_as_pages(db, book, image_keys: list, review_ocr: bool) -> list:
    """Every image is its own page, always OCR'd, in the order given."""
    if len(image_keys) > MAX_OCR_PAGES:
        raise ValueError(
            f"This document has {len(image_keys)} images, over the {MAX_OCR_PAGES}-page OCR limit."
        )

    book.pages_total = len(image_keys)
    book.pages_done = 0
    db.commit()

    records = []

    for index, key in enumerate(image_keys, start=1):
        raw_bytes = storage.storage.read(key)

        prepared = ocr.prepare_image_for_ocr(raw_bytes)
        image_path = _save_page_image(book.user_id, book.id, index, prepared)

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


def _extract_document_as_pages(db, book, document_bytes: bytes, filename: str, review_ocr: bool) -> list:
    """PDF gets the per-page OCR-aware treatment above; everything else is unchanged from Phase 3A (always method="text")."""
    ext = os.path.splitext(filename)[1].lower()

    if ext == ".pdf":
        return _extract_pdf_as_pages(db, book, document_bytes, review_ocr)

    pages_data = document_parsers.parse_document(io.BytesIO(document_bytes), filename)

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


def _process_book(book_id: int, kind: str, storage_keys: list, filename: str, review_ocr: bool):
    # Looked up on the database module at call time (not imported by name)
    # so tests can redirect it to an isolated test database -- see
    # tests/test_api.py's `database.SessionLocal = TestSessionLocal`.
    db = database.SessionLocal()
    try:
        _run_pipeline(db, book_id, kind, storage_keys, filename, review_ocr)
    except Exception:
        logger.exception("Unhandled error while processing book_id=%d ('%s')", book_id, filename)
        try:
            _mark_failed(db, book_id, GENERIC_PROCESSING_ERROR)
        except Exception:
            logger.exception("Also failed to mark book_id=%d as failed", book_id)
    finally:
        db.close()


def _run_pipeline(db, book_id: int, kind: str, storage_keys: list, filename: str, review_ocr: bool):
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

    try:
        if kind == "images":
            page_records = _extract_images_as_pages(db, book, storage_keys, review_ocr)
        else:
            document_bytes = storage.storage.read(storage_keys[0])
            page_records = _extract_document_as_pages(db, book, document_bytes, filename, review_ocr)
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


def submit_paper_generation_job(paper_id: int):
    """
    Queues Phase 5B exam-paper generation for an already-planned Paper
    (status set to "generating" by the route before this runs). Returns
    immediately -- the job opens its own DB session and is responsible for
    leaving the paper "ready" or "failed" no matter how it exits.
    """
    if JOBS_SYNC:
        _run_paper_generation_job(paper_id)
    else:
        _executor.submit(_run_paper_generation_job, paper_id)


def _fail_paper(db, paper_model, paper_id: int, error: str):
    try:
        paper_row = db.query(paper_model).filter(paper_model.id == paper_id).first()
        if paper_row:
            paper_row.status = "failed"
            paper_row.error = error
            db.commit()
    except Exception:
        logger.exception("Also failed to mark paper_id=%d as failed", paper_id)


def _run_paper_generation_job(paper_id: int):
    # Lazy import: main.py imports jobs at its own module top level, so
    # importing main back at jobs.py's top level would be circular -- see
    # llm.py's _generate_local for the same pattern already used elsewhere.
    import main

    db = database.SessionLocal()
    try:
        paper_row = db.query(main.Paper).filter(main.Paper.id == paper_id).first()
        if not paper_row:
            logger.error("Paper generation job: paper_id=%d no longer exists, skipping", paper_id)
            return
        main.generate_paper(db, paper_row)
    except llm.LLMUnavailableError:
        logger.warning("AI service busy while generating paper_id=%d", paper_id)
        db.rollback()
        _fail_paper(db, main.Paper, paper_id, "AI service is busy, click Generate to continue")
    except Exception:
        logger.exception("Unhandled error while generating paper_id=%d", paper_id)
        db.rollback()
        _fail_paper(db, main.Paper, paper_id, "Something went wrong while generating this paper. Please try again.")
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
            image_bytes = storage.storage.read(page.image_path)
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
