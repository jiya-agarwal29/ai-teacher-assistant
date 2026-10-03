import glob
import logging
import os
import re
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException, Depends
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

import embeddings
import jobs
import llm
from rag import (
    generate_quiz,
    format_quiz_text,
    check_context_relevance,
    summarize_text_in_bullets,
    extract_definitions_and_statements,
    clean_pdf_text_for_quiz,
    clean_extracted_text
)
from retrieval import retrieve, invalidate_cache, user_has_documents, needs_reembedding

import database
from database import engine, get_db
from models import Base, Book, DocumentPage, Page, User

from auth import (
    create_access_token,
    verify_password,
    hash_password,
    get_current_user,
    verify_token
)

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# Readiness flags exposed via /health — set once the FastAPI lifespan below
# has warmed the AI models and prepared the database.
app_state = {"models_ready": False, "database_ready": False}


def _fail_interrupted_books(db: Session) -> int:
    """
    We don't resume jobs across a restart -- any book still "processing"
    belonged to a worker thread that no longer exists, so it can never reach
    "ready" on its own. Fail it now with a clear, re-uploadable error instead
    of leaving it stuck "processing" forever. Returns the number marked
    failed (also directly unit-testable without a real server restart).
    """
    interrupted = db.query(Book).filter(Book.status == "processing").all()
    for book in interrupted:
        book.status = "failed"
        book.error = "Processing was interrupted. Please re-upload."
    if interrupted:
        db.commit()
    return len(interrupted)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up: loading AI models and preparing database")

    # Each init() loads the local model only when its provider env var is
    # "local"; otherwise it eagerly builds the hosted client so a missing
    # API key fails startup here rather than on first request.
    embeddings.init()
    llm.init()
    app_state["models_ready"] = True

    Base.metadata.create_all(bind=engine)
    app_state["database_ready"] = True

    # Looked up on the database module at call time (not imported by name)
    # so tests can redirect it to an isolated test database.
    db = database.SessionLocal()
    try:
        failed_count = _fail_interrupted_books(db)
        if failed_count:
            logger.warning("Marked %d interrupted upload(s) as failed on startup", failed_count)
    finally:
        db.close()

    logger.info("Startup complete")
    yield
    logger.info("Shutting down")


AI_RATE_LIMIT_DETAIL = "Too many requests. Please wait a minute."


def _user_rate_limit_key(request: Request) -> str:
    """
    Rate-limits AI routes per logged-in user instead of per IP, so one
    user can't exhaust another's budget (or a shared/NATed IP's budget)
    and vice versa. Decodes the username straight out of the bearer token;
    falls back to the client IP if there's no usable token -- the route's
    own get_current_user dependency rejects those requests anyway.
    """
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        username = verify_token(auth_header[7:])
        if username:
            return f"user:{username}"
    return get_remote_address(request)


limiter = Limiter(key_func=get_remote_address)

app = FastAPI(lifespan=lifespan)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    # /login and /register keep slowapi's own default {"error": "..."}
    # response shape (already documented in the README); the per-user AI
    # route limits below use a plainer, friendlier message instead.
    if request.url.path in ("/login", "/register"):
        return _rate_limit_exceeded_handler(request, exc)
    return JSONResponse(status_code=429, content={"detail": AI_RATE_LIMIT_DETAIL})


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    # Catches anything not already handled as an HTTPException elsewhere.
    # Never send exception text to the client — only the stack trace goes
    # to the log.
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


DEFAULT_CORS_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
    "http://localhost:5175",
    "http://127.0.0.1:5175",
    "http://localhost:3000",
    "http://127.0.0.1:3000"
]
cors_origins_env = os.getenv("CORS_ORIGINS")
allow_origins = (
    [origin.strip() for origin in cors_origins_env.split(",") if origin.strip()]
    if cors_origins_env
    else DEFAULT_CORS_ORIGINS
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")

ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".docx", ".pptx", ".doc", ".ppt", ".txt", ".md"}
MAX_UPLOAD_MB = float(os.getenv("MAX_UPLOAD_MB", "60"))
MAX_UPLOAD_BYTES = int(MAX_UPLOAD_MB * 1024 * 1024)

# Scanned/photographed pages (jobs.py OCRs these with Gemini vision -- see
# ocr.py). Sent via the separate "files" field, one or more at a time, never
# mixed with the single-document "file" field above.
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
MAX_IMAGE_MB = 15.0
MAX_IMAGE_BYTES = int(MAX_IMAGE_MB * 1024 * 1024)

# Absolute path next to this file by default, not a "./"-relative one, for
# the same reason as database.py's DEFAULT_DB_PATH -- it must not depend on
# the working directory the server happens to be launched from.
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR") or (Path(__file__).resolve().parent / "uploads"))


def _book_upload_dir(user_id: int, book_id: int) -> Path:
    return UPLOAD_DIR / str(user_id) / str(book_id)


def _find_original_sources(user_id: int, book_id: int):
    """
    Locates whatever was saved for /books/{id}/retry to re-process: either
    a single "original.<ext>" document, or one or more images under
    "original_images/" (in upload order). Returns (kind, file_paths) or
    None if nothing is saved (e.g. the upload folder was lost).
    """
    book_dir = _book_upload_dir(user_id, book_id)

    doc_matches = sorted(glob.glob(str(book_dir / "original.*")))
    if doc_matches:
        return "document", doc_matches

    image_matches = sorted(glob.glob(str(book_dir / "original_images" / "*")))
    if image_matches:
        return "images", image_matches

    return None


MAX_OCR_PAGES = int(os.getenv("MAX_OCR_PAGES", "50"))

MAX_SUMMARIZE_CHARS = 20000

AI_BUSY_DETAIL = "AI service is busy. Please try again in a minute."

CHAT_SYSTEM_INSTRUCTION = (
    "You are a helpful teaching assistant. Answer ONLY from the provided sources. "
    "Cite sources inline like [1]. If the sources do not contain the answer, reply "
    "exactly: 'The uploaded documents do not contain enough information for this "
    "question.' Use short headings and bullet points where helpful. Explain simply "
    "for students."
)

SUMMARIZE_SCHEMA = {
    "type": "object",
    "properties": {
        "bullets": {
            "type": "array",
            "items": {"type": "string"}
        }
    },
    "required": ["bullets"]
}

FLASHCARDS_SCHEMA = {
    "type": "object",
    "properties": {
        "cards": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "term": {"type": "string"},
                    "definition": {"type": "string"},
                    "source_index": {"type": "integer"}
                },
                "required": ["term", "definition", "source_index"]
            }
        }
    },
    "required": ["cards"]
}

QUIZ_QUESTION_TYPES = [
    "mcq", "true_false", "fill_blank", "short_answer",
    "long_answer", "scenario", "viva", "interview"
]

QUIZ_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "type": {"type": "string", "enum": QUIZ_QUESTION_TYPES},
                    "question": {"type": "string"},
                    "options": {"type": "array", "items": {"type": "string"}},
                    "correctAnswer": {"type": "string"},
                    "explanation": {"type": "string"},
                    "source_index": {"type": "integer"}
                },
                "required": ["id", "type", "question", "correctAnswer", "explanation", "source_index"]
            }
        }
    },
    "required": ["questions"]
}

QUIZ_PROMPT_RULES = (
    "Use ONLY the provided numbered sources -- never invent facts. Generate exactly "
    "one question of each type: mcq, true_false, fill_blank, short_answer, "
    "long_answer, scenario, viva, interview. No question's text may reveal or hint "
    "at its own correct answer. For type mcq, provide exactly 4 distinct, plausible "
    "options and set correctAnswer to the letter (A, B, C, or D) of the correct one. "
    "For type true_false, set options to [\"True\", \"False\"] and correctAnswer to "
    "\"A\" or \"B\" -- mix true and false answers across the quiz rather than always "
    "picking the same one. For every other type, correctAnswer is the actual answer "
    "text (not a letter) and options is omitted. Every question must test a "
    "different concept from the sources, and every question needs a source_index: "
    "the number of the source chunk it's grounded in."
)


class RegisterRequest(BaseModel):
    username: str
    password: str


class SummarizeRequest(BaseModel):
    text: str


class FlashcardsRequest(BaseModel):
    topic: str


class TutorRequest(BaseModel):
    question: str


# -----------------------------
# ROOT ROUTE
# -----------------------------
@app.get("/")
def read_root():

    return {
        "message": "AI Teacher Assistant Backend Running"
    }


# -----------------------------
# HEALTH CHECK
# -----------------------------
@app.get("/health")
def health_check():

    ready = app_state["models_ready"] and app_state["database_ready"]

    return {
        "status": "Server is healthy" if ready else "Server is starting up",
        "models_ready": app_state["models_ready"],
        "database_ready": app_state["database_ready"],
        "llm": llm.provider_status()
    }


# -----------------------------
# REGISTER
# -----------------------------
@app.post("/register")
@limiter.limit("10/minute")
def register(request: Request, payload: RegisterRequest, db: Session = Depends(get_db)):

    if not USERNAME_PATTERN.fullmatch(payload.username):

        raise HTTPException(
            status_code=400,
            detail="Username must be 3-32 characters and contain only letters, digits, underscores, dots, or hyphens."
        )

    if len(payload.password) < 8:

        raise HTTPException(
            status_code=400,
            detail="Password must be at least 8 characters long."
        )

    existing_user = db.query(User).filter(
        User.username == payload.username
    ).first()

    if existing_user:

        raise HTTPException(
            status_code=400,
            detail="Username already exists"
        )

    hashed_password = hash_password(payload.password)

    new_user = User(
        username=payload.username,
        password=hashed_password
    )

    db.add(new_user)
    db.commit()

    return {
        "message": "User registered successfully"
    }


# -----------------------------
# LOGIN
# -----------------------------
@app.post("/login")
@limiter.limit("10/minute")
def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):

    user = db.query(User).filter(
        User.username == form_data.username
    ).first()

    if not user or not verify_password(form_data.password, user.password):

        raise HTTPException(
            status_code=401,
            detail="Invalid username or password"
        )

    token = create_access_token(
        data={"sub": user.username}
    )

    return {
        "access_token": token,
        "token_type": "bearer"
    }


# -----------------------------
# REFRESH TOKEN
# -----------------------------
@app.post("/refresh-token")
@limiter.limit("10/minute", key_func=_user_rate_limit_key)
def refresh_token(
    request: Request,
    current_user: User = Depends(get_current_user)
):
    # get_current_user already rejects a missing/invalid/expired token with
    # a 401 before this body ever runs -- "Stay logged in" simply can't
    # extend a session that's already gone.
    token = create_access_token(
        data={"sub": current_user.username}
    )

    return {
        "access_token": token,
        "token_type": "bearer"
    }


# -----------------------------
# DOCUMENT UPLOAD (+ OCR)
# -----------------------------
def _validate_document_upload(file: UploadFile) -> str:
    """Returns the validated extension, or raises an HTTPException."""
    ext = os.path.splitext(file.filename or "")[1].lower()

    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file extension '{ext}'. Allowed: {', '.join(sorted(ALLOWED_UPLOAD_EXTENSIONS))}"
        )

    file.file.seek(0, os.SEEK_END)
    file_size = file.file.tell()
    file.file.seek(0)

    if file_size > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum allowed size of {MAX_UPLOAD_MB:g} MB"
        )

    return ext


def _validate_image_uploads(image_files: list) -> None:
    if len(image_files) > MAX_OCR_PAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Too many images ({len(image_files)}); the limit is {MAX_OCR_PAGES} per document."
        )

    for f in image_files:
        ext = os.path.splitext(f.filename or "")[1].lower()
        if ext not in ALLOWED_IMAGE_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported image extension '{ext}'. Allowed: {', '.join(sorted(ALLOWED_IMAGE_EXTENSIONS))}"
            )

        f.file.seek(0, os.SEEK_END)
        size = f.file.tell()
        f.file.seek(0)
        if size > MAX_IMAGE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Image '{f.filename}' exceeds the maximum allowed size of {MAX_IMAGE_MB:g} MB"
            )


@app.post("/upload-book", status_code=202)
def upload_book(
    file: UploadFile = File(None),
    files: list[UploadFile] = File(None),
    review_ocr: bool = Form(True),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # A single traditional document goes in "file"; one or more page images
    # (a scan, phone photos of handwritten notes, etc. -- OCR'd as one
    # document, in the order sent) go in "files". Exactly one of the two.
    image_files = [f for f in (files or []) if f and f.filename]
    has_document = bool(file and file.filename)

    if has_document and image_files:
        raise HTTPException(status_code=400, detail="Send either 'file' or 'files', not both.")

    if image_files:
        kind = "images"
        _validate_image_uploads(image_files)
        book_name = image_files[0].filename
    elif has_document:
        kind = "document"
        book_name = file.filename
        ext = _validate_document_upload(file)
    else:
        raise HTTPException(
            status_code=400,
            detail="No file provided. Send a document as 'file' or one or more images as 'files'."
        )

    new_book = Book(name=book_name, user_id=current_user.id, status="processing", review_ocr=review_ocr)
    db.add(new_book)
    db.flush()  # assigns new_book.id, needed for the upload folder name below

    book_dir = _book_upload_dir(current_user.id, new_book.id)

    try:
        book_dir.mkdir(parents=True, exist_ok=True)

        if kind == "images":
            images_dir = book_dir / "original_images"
            images_dir.mkdir(parents=True, exist_ok=True)
            saved_paths = []
            for index, f in enumerate(image_files, start=1):
                image_ext = os.path.splitext(f.filename or "")[1].lower()
                dest = images_dir / f"{index:04d}{image_ext}"
                f.file.seek(0)
                with open(dest, "wb") as out:
                    shutil.copyfileobj(f.file, out)
                saved_paths.append(str(dest))
        else:
            dest = book_dir / f"original{ext}"
            file.file.seek(0)
            with open(dest, "wb") as out:
                shutil.copyfileobj(file.file, out)
            saved_paths = [str(dest)]
    except Exception:
        db.rollback()
        logger.exception("Failed to save uploaded file(s) for '%s' to disk", book_name)
        raise HTTPException(status_code=500, detail="Failed to save the uploaded document.")

    db.commit()
    db.refresh(new_book)

    jobs.submit_processing_job(new_book.id, kind, saved_paths, book_name, review_ocr)

    # In production this job runs on a worker thread and is still
    # "processing" by the time we get here. In tests (jobs.JOBS_SYNC=True)
    # it just ran inline on a separate session -- refresh so the response
    # (and tests asserting against it) reflect the real outcome either way.
    db.refresh(new_book)

    return {"book_id": new_book.id, "status": new_book.status}


# -----------------------------
# GET ALL BOOKS
# -----------------------------
def _book_summary(book: Book) -> dict:
    return {
        "id": book.id,
        "name": book.name,
        "status": book.status,
        "error": book.error,
        "source_type": book.source_type,
        "pages_total": book.pages_total,
        "pages_done": book.pages_done,
        "created_at": book.created_at.isoformat() if book.created_at else None
    }


@app.get("/books")
def get_books(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):

    books = db.query(Book).filter(Book.user_id == current_user.id).all()

    return [_book_summary(book) for book in books]


def _get_owned_book_or_404(db: Session, book_id: int, user_id: int) -> Book:
    book = db.query(Book).filter(
        Book.id == book_id,
        Book.user_id == user_id
    ).first()

    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    return book


# -----------------------------
# BOOK PROCESSING STATUS
# -----------------------------
@app.get("/books/{book_id}/status")
def get_book_status(
    book_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    book = _get_owned_book_or_404(db, book_id, current_user.id)

    return {
        "id": book.id,
        "name": book.name,
        "status": book.status,
        "error": book.error,
        "pages_total": book.pages_total,
        "pages_done": book.pages_done,
        "source_type": book.source_type
    }


# -----------------------------
# RETRY FAILED PROCESSING
# -----------------------------
@app.post("/books/{book_id}/retry", status_code=202)
def retry_book(
    book_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    book = _get_owned_book_or_404(db, book_id, current_user.id)

    if book.status != "failed":
        raise HTTPException(status_code=400, detail="Only a failed document can be retried.")

    sources = _find_original_sources(current_user.id, book_id)
    if not sources:
        raise HTTPException(
            status_code=400,
            detail="The original uploaded file is no longer available. Please upload it again."
        )
    kind, file_paths = sources

    book.status = "processing"
    book.error = None
    book.pages_total = 0
    book.pages_done = 0
    # Reset so the frontend's "reading pages" vs "processing chunks" phase
    # detection (source_type is still null while OCR/extraction runs) isn't
    # confused by a stale value left over from the failed attempt.
    book.source_type = None
    db.commit()

    jobs.submit_processing_job(book.id, kind, file_paths, book.name, book.review_ocr)
    db.refresh(book)

    return {"book_id": book.id, "status": book.status}


MAX_PAGE_TEXT_CHARS = 20000
# review_status values that don't block indexing -- a page needing a human
# look is "needs_review"; everything else (never needed OCR, or a human
# already signed off on it) is fine to chunk/embed as-is.
_APPROVED_REVIEW_STATUSES = ("auto_approved", "approved")


class PageTextUpdateRequest(BaseModel):
    extracted_text: str


def _get_document_page_or_404(db: Session, book_id: int, page_number: int) -> DocumentPage:
    page = db.query(DocumentPage).filter(
        DocumentPage.book_id == book_id,
        DocumentPage.page_number == page_number
    ).first()

    if not page:
        raise HTTPException(status_code=404, detail="Page not found")

    return page


def _reopen_book_if_ready(book: Book):
    """A previously-finished book whose text just changed needs a fresh look before it's trusted again."""
    if book.status == "ready":
        book.status = "needs_review"


def _finalize_if_fully_approved(db: Session, book: Book) -> dict:
    """
    Call after any action that approves a page. If no page is left
    "needs_review", kicks off the background chunk+embed job (status
    "processing" -> "ready") -- this both builds the index for the first
    time and rebuilds it (replacing old Page rows) after a post-ready edit
    reopened the book for review. Returns the response body for the caller.
    """
    remaining = db.query(DocumentPage).filter(
        DocumentPage.book_id == book.id,
        DocumentPage.review_status == "needs_review"
    ).count()

    if remaining == 0:
        book.status = "processing"
        book.pages_total = 0
        book.pages_done = 0
        db.commit()
        jobs.submit_indexing_job(book.id)
        db.refresh(book)

    return {"book_id": book.id, "status": book.status, "pages_remaining": remaining}


# -----------------------------
# OCR REVIEW
# -----------------------------
@app.get("/books/{book_id}/pages")
def get_book_pages(
    book_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    _get_owned_book_or_404(db, book_id, current_user.id)

    pages = db.query(DocumentPage).filter(
        DocumentPage.book_id == book_id
    ).order_by(DocumentPage.page_number).all()

    return [
        {
            "page_number": p.page_number,
            "method": p.method,
            "review_status": p.review_status,
            "extracted_text": p.extracted_text,
            "has_image": bool(p.image_path)
        }
        for p in pages
    ]


@app.get("/books/{book_id}/pages/{page_number}/image")
def get_page_image(
    book_id: int,
    page_number: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    _get_owned_book_or_404(db, book_id, current_user.id)
    page = _get_document_page_or_404(db, book_id, page_number)

    if not page.image_path:
        raise HTTPException(status_code=404, detail="This page has no saved image.")

    # Defense in depth: only ever serve a file that's actually inside this
    # book's own upload folder, regardless of what's stored in image_path.
    book_dir = _book_upload_dir(current_user.id, book_id).resolve()
    image_path = Path(page.image_path).resolve()
    try:
        image_path.relative_to(book_dir)
    except ValueError:
        logger.error(
            "Refusing to serve page image outside its book's upload folder (book_id=%d, page=%d)",
            book_id, page_number
        )
        raise HTTPException(status_code=404, detail="Image not found")

    if not image_path.is_file():
        raise HTTPException(status_code=404, detail="Image not found")

    return FileResponse(image_path, media_type="image/jpeg")


@app.put("/books/{book_id}/pages/{page_number}")
def update_page_text(
    book_id: int,
    page_number: int,
    payload: PageTextUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    book = _get_owned_book_or_404(db, book_id, current_user.id)
    page = _get_document_page_or_404(db, book_id, page_number)

    if len(payload.extracted_text) > MAX_PAGE_TEXT_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"Text is too long ({len(payload.extracted_text)} characters). Maximum is {MAX_PAGE_TEXT_CHARS} characters."
        )

    page.extracted_text = payload.extracted_text

    # A page that was already signed off on needs a fresh look since its
    # text just changed; one still mid-review just keeps its status as is.
    if page.review_status in _APPROVED_REVIEW_STATUSES:
        page.review_status = "needs_review"
        _reopen_book_if_ready(book)

    db.commit()

    return {"page_number": page.page_number, "review_status": page.review_status, "status": book.status}


@app.post("/books/{book_id}/pages/{page_number}/approve")
def approve_page(
    book_id: int,
    page_number: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    book = _get_owned_book_or_404(db, book_id, current_user.id)
    page = _get_document_page_or_404(db, book_id, page_number)

    page.review_status = "approved"
    db.commit()

    return _finalize_if_fully_approved(db, book)


@app.post("/books/{book_id}/approve-all")
def approve_all_pages(
    book_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    book = _get_owned_book_or_404(db, book_id, current_user.id)

    db.query(DocumentPage).filter(
        DocumentPage.book_id == book_id,
        DocumentPage.review_status == "needs_review"
    ).update({"review_status": "approved"})
    db.commit()

    return _finalize_if_fully_approved(db, book)


@app.post("/books/{book_id}/pages/{page_number}/reread", status_code=202)
def reread_page(
    book_id: int,
    page_number: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    book = _get_owned_book_or_404(db, book_id, current_user.id)
    page = _get_document_page_or_404(db, book_id, page_number)

    if not page.image_path:
        raise HTTPException(status_code=400, detail="This page has no saved image to re-read.")

    page.review_status = "needs_review"
    _reopen_book_if_ready(book)
    db.commit()

    jobs.submit_reread_job(book_id, page_number)

    return {"book_id": book_id, "page_number": page_number, "review_status": page.review_status, "status": book.status}


# -----------------------------
# DELETE BOOK
# -----------------------------
@app.delete("/books/{book_id}")
def delete_book(
    book_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):

    book = _get_owned_book_or_404(db, book_id, current_user.id)

    # Cascades to the book's pages via the Book.pages relationship
    db.delete(book)
    db.commit()
    invalidate_cache(current_user.id)

    shutil.rmtree(_book_upload_dir(current_user.id, book_id), ignore_errors=True)

    return {
        "status": "Book and associated chunks deleted successfully"
    }


# -----------------------------
# NORMAL SEARCH
# -----------------------------
def _escape_like(value: str) -> str:
    """
    Escapes LIKE/ILIKE wildcard characters in user-supplied search text, so
    a literal "%" or "_" in the query matches itself instead of acting as a
    wildcard. Must be paired with escape="\\" on the ilike() call.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@app.get("/search")
def search_content(
    query: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):

    results = db.query(Page).join(
        Book, Page.book_id == Book.id
    ).filter(
        Book.user_id == current_user.id,
        Book.status == "ready",
        Page.content.ilike(f"%{_escape_like(query)}%", escape="\\")
    ).all()

    if not results:

        return {
            "message": "No matching content found"
        }

    return [
        {
            "book_id": page.book_id,
            "page_number": page.page_number,
            "chunk_number": page.chunk_number,
            "content": page.content[:500]
        }

        for page in results
    ]


def _retrieve_or_503(db: Session, user_id: int, query: str, top_k: int, apply_threshold: bool):
    """
    Wraps retrieve() so a Gemini embed_query() rate limit that's still busy
    after EMBED_QUERY_MAX_WAIT_SECONDS surfaces as a 503 to the client,
    instead of a generic 500 or an unhandled exception.
    """
    try:
        return retrieve(db, user_id, query, top_k=top_k, apply_threshold=apply_threshold)
    except embeddings.EmbeddingServiceBusyError:
        raise HTTPException(status_code=503, detail=AI_BUSY_DETAIL)


# -----------------------------
# SEMANTIC SEARCH
# -----------------------------
@app.get("/semantic-search")
def semantic_search(
    query: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):

    if not user_has_documents(db, current_user.id):

        return {
            "message": "No documents available"
        }

    if needs_reembedding(db, current_user.id):

        return {
            "message": "Your documents need re-indexing"
        }

    top_pages = _retrieve_or_503(db, current_user.id, query, top_k=3, apply_threshold=False)

    return [
        {
            "book_id": page.book_id,
            "page_number": page.page_number,
            "chunk_number": page.chunk_number,
            "content": page.content[:500],
            "similarity_score": round(float(score), 4)
        }

        for score, page in top_pages
    ]


# -----------------------------
# AI CHAT
# -----------------------------
def _answer_question(question: str, current_user: User, db: Session):
    """
    Shared retrieval + grounded-answer pipeline used by both /chat and
    /tools/tutor — same behaviour, just two different entry points for the
    same plain question (no prefix is added anywhere in this pipeline).
    """
    if not user_has_documents(db, current_user.id):
        return {
            "message": "No documents uploaded"
        }

    if needs_reembedding(db, current_user.id):
        return {
            "message": "Your documents need re-indexing"
        }

    top_pages = _retrieve_or_503(db, current_user.id, question, top_k=5, apply_threshold=True)

    # Relevance checking using both cosine similarity and lexical overlap
    temp_context = " ".join([page.content for _, page in top_pages])
    if not top_pages or not check_context_relevance(question, temp_context, top_pages[0][0]):
        return {
            "question": question,
            "answer": "The uploaded documents do not contain enough information for this question.",
            "sources": []
        }

    # Build the numbered source list ([1], [2], ...) the LLM cites inline,
    # and the sources array the frontend renders -- same top_pages, so the
    # citation numbers line up with their position in "sources".
    sources = []
    numbered_sources = []

    for idx, (score, page) in enumerate(top_pages, start=1):

        book = db.query(Book).filter(
            Book.id == page.book_id
        ).first()

        if not book:
            continue

        sources.append({
            "book_name": book.name,
            "page_number": page.page_number,
            "chunk_number": page.chunk_number,
            "similarity_score": round(float(score), 4),
            "page_id": page.id,
            "content": page.content
        })
        numbered_sources.append(f"[{idx}] ({book.name}, page {page.page_number}): {page.content}")

    prompt = f"Question: {question}\n\nSources:\n" + "\n\n".join(numbered_sources)

    try:
        answer = llm.generate(prompt, system=CHAT_SYSTEM_INSTRUCTION)
    except llm.LLMUnavailableError:
        raise HTTPException(status_code=503, detail=AI_BUSY_DETAIL)

    if not answer or "do not contain enough information" in answer.lower():
        return {
            "question": question,
            "answer": "The uploaded documents do not contain enough information for this question.",
            "sources": []
        }

    return {
        "question": question,
        "answer": answer,
        "sources": sources
    }


@app.get("/chat")
@limiter.limit("20/minute", key_func=_user_rate_limit_key)
def chat_with_pdf(
    request: Request,
    question: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    return _answer_question(question, current_user, db)


# -----------------------------
# AI TOOLS: SUMMARIZER, FLASHCARDS, TUTOR
# -----------------------------
@app.post("/tools/summarize")
@limiter.limit("10/minute", key_func=_user_rate_limit_key)
def summarize_notes(
    request: Request,
    payload: SummarizeRequest,
    current_user: User = Depends(get_current_user)
):
    text = payload.text.strip()

    if not text:
        raise HTTPException(status_code=400, detail="Please provide some text to summarize.")

    if len(text) > MAX_SUMMARIZE_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"Text is too long ({len(text)} characters). Maximum is {MAX_SUMMARIZE_CHARS} characters."
        )

    try:
        result = llm.generate_json(
            "Summarize the following text in 5 to 10 clear, concise bullet points. "
            "Use ONLY information from this text -- do not add anything that isn't "
            f"there.\n\nText:\n{text}",
            schema=SUMMARIZE_SCHEMA
        )
        bullets = result.get("bullets", []) if isinstance(result, dict) else []
    except NotImplementedError:
        # LLM_PROVIDER=local has no structured JSON output -- fall back to
        # the local Flan-T5 bullet-by-bullet summarizer.
        bullets = summarize_text_in_bullets(text)
    except llm.LLMUnavailableError:
        raise HTTPException(status_code=503, detail=AI_BUSY_DETAIL)

    return {"bullets": bullets}


@app.post("/tools/flashcards")
@limiter.limit("10/minute", key_func=_user_rate_limit_key)
def generate_flashcards(
    request: Request,
    payload: FlashcardsRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    topic = payload.topic.strip()

    if not topic:
        raise HTTPException(status_code=400, detail="Please provide a topic.")

    if not user_has_documents(db, current_user.id):
        return {"cards": [], "message": "No documents uploaded yet."}

    if needs_reembedding(db, current_user.id):
        return {"cards": [], "message": "Your documents need re-indexing"}

    top_pages = _retrieve_or_503(db, current_user.id, topic, top_k=5, apply_threshold=True)

    if not top_pages:
        return {"cards": [], "message": "No relevant content found in your documents for this topic."}

    try:
        numbered_sources = "\n\n".join(
            f"[{idx}] {page.content}" for idx, (_, page) in enumerate(top_pages, start=1)
        )
        result = llm.generate_json(
            f"Based ONLY on the numbered source chunks below about '{topic}', create up "
            "to 10 flashcards. Each flashcard needs a short 'term', a 'definition' "
            "grounded only in the sources, and a 'source_index' -- the number of the "
            f"chunk the definition came from.\n\nSources:\n{numbered_sources}",
            schema=FLASHCARDS_SCHEMA
        )
        raw_cards = result.get("cards", []) if isinstance(result, dict) else []

        cards = []
        for card in raw_cards[:10]:
            source_index = card.get("source_index")
            if not isinstance(source_index, int) or not (1 <= source_index <= len(top_pages)):
                continue  # drop cards with an invalid source_index

            _, page = top_pages[source_index - 1]
            book = db.query(Book).filter(Book.id == page.book_id).first()
            source_label = f"{book.name}, page {page.page_number}" if book else f"page {page.page_number}"

            cards.append({
                "term": card.get("term", ""),
                "definition": card.get("definition", ""),
                "source": source_label
            })

    except NotImplementedError:
        # LLM_PROVIDER=local has no structured JSON output -- fall back to
        # the extraction-based flashcard generation.
        combined_text = "\n".join(page.content for _, page in top_pages)
        cleaned = clean_pdf_text_for_quiz(combined_text)
        definitions, statements = extract_definitions_and_statements(cleaned)

        cards = []
        seen_terms = set()

        for d in definitions:
            term = clean_extracted_text(d["term"])
            key = term.lower()
            if key in seen_terms:
                continue
            cards.append({
                "term": term,
                "definition": clean_extracted_text(d["explanation"]),
                "source": clean_extracted_text(d["raw"])
            })
            seen_terms.add(key)
            if len(cards) >= 10:
                break

        if len(cards) < 10:
            for s in statements:
                if len(cards) >= 10:
                    break
                words = s.split()
                term = " ".join(words[:3]) if len(words) > 3 else s
                key = term.lower()
                if key in seen_terms:
                    continue
                cards.append({
                    "term": term,
                    "definition": clean_extracted_text(s),
                    "source": clean_extracted_text(s)
                })
                seen_terms.add(key)

    except llm.LLMUnavailableError:
        raise HTTPException(status_code=503, detail=AI_BUSY_DETAIL)

    if not cards:
        return {"cards": [], "message": "No relevant content found in your documents for this topic."}

    return {"cards": cards}


@app.post("/tools/tutor")
@limiter.limit("20/minute", key_func=_user_rate_limit_key)
def ai_tutor(
    request: Request,
    payload: TutorRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    return _answer_question(payload.question, current_user, db)


def _validate_quiz_questions(raw_questions, num_sources: int):
    """
    Validates and cleans Gemini's raw quiz question list: correct option
    counts, a valid correctAnswer, no question leaking its own answer (for
    types where correctAnswer is real answer text rather than a letter),
    and no duplicate questions. Invalid questions are dropped rather than
    failing the whole quiz. Returns cleaned question dicts -- id/type/
    question/options?/correctAnswer/explanation, matching the existing
    response shape (source_index is internal-only and stripped here).
    """
    if not isinstance(raw_questions, list):
        return []

    valid = []
    seen_question_text = set()

    for q in raw_questions:
        if not isinstance(q, dict):
            continue

        q_type = q.get("type")
        question_text = q.get("question")
        correct_answer = q.get("correctAnswer")
        explanation = q.get("explanation")
        source_index = q.get("source_index")
        options = q.get("options")

        if q_type not in QUIZ_QUESTION_TYPES:
            continue
        if not isinstance(question_text, str) or not question_text.strip():
            continue
        if not isinstance(correct_answer, str) or not correct_answer.strip():
            continue
        if not isinstance(explanation, str) or not explanation.strip():
            continue
        if not isinstance(source_index, int) or not (1 <= source_index <= num_sources):
            continue

        cleaned_options = None

        if q_type == "mcq":
            if not isinstance(options, list) or len(options) != 4:
                continue
            # Gemini sometimes prefixes its own option text with a letter
            # (e.g. "A) The atmospheric cycle") even though it also
            # returns a separate lettered correctAnswer -- strip a leading
            # "A) "/"A. "/"A: " style prefix so the option isn't rendered
            # double-lettered (e.g. "A. A) ...").
            option_texts = [
                re.sub(r'^[A-Da-d][\.\):-]\s*', '', o.strip())
                for o in options if isinstance(o, str) and o.strip()
            ]
            if len(option_texts) != 4:
                continue
            if len({o.lower() for o in option_texts}) != 4:
                continue  # options must be distinct
            if correct_answer not in ("A", "B", "C", "D"):
                continue
            cleaned_options = option_texts

        elif q_type == "true_false":
            if not isinstance(options, list) or [str(o).strip() for o in options] != ["True", "False"]:
                continue
            if correct_answer not in ("A", "B"):
                continue
            cleaned_options = ["True", "False"]

        else:
            # correctAnswer is real answer text here, not a letter -- the
            # question text must not give it away.
            if correct_answer.strip().lower() in question_text.lower():
                continue

        normalized = re.sub(r'\s+', ' ', question_text.strip().lower())
        if normalized in seen_question_text:
            continue  # duplicate question
        seen_question_text.add(normalized)

        cleaned = {
            "id": len(valid) + 1,
            "type": q_type,
            "question": question_text.strip(),
            "correctAnswer": correct_answer.strip(),
            "explanation": explanation.strip()
        }
        if cleaned_options is not None:
            cleaned["options"] = cleaned_options
        valid.append(cleaned)

    return valid


def _generate_quiz_with_gemini(topic: str, top_pages):
    """
    Builds the numbered-source prompt, asks Gemini for a full quiz as
    JSON, and validates/cleans the result. Returns (quiz_text, questions)
    on success, or None if fewer than 3 valid questions remain even after
    one retry -- the caller falls back to generate_quiz() (the local
    hybrid generator) in that case. Raises NotImplementedError for
    LLM_PROVIDER=local and llm.LLMUnavailableError on a persistently
    unavailable provider, same as llm.generate_json() itself.
    """
    numbered_sources = "\n\n".join(
        f"[{idx}] {page.content}" for idx, (_, page) in enumerate(top_pages, start=1)
    )
    prompt = (
        f"Create a quiz about '{topic}' based on the numbered sources below.\n\n"
        f"{QUIZ_PROMPT_RULES}\n\nSources:\n{numbered_sources}"
    )

    for _attempt in range(2):  # one retry if the first pass yields too few valid questions
        result = llm.generate_json(prompt, schema=QUIZ_SCHEMA)
        raw_questions = result.get("questions", []) if isinstance(result, dict) else []
        questions = _validate_quiz_questions(raw_questions, len(top_pages))
        if len(questions) >= 3:
            return format_quiz_text(questions), questions

    return None


@app.get("/generate-quiz")
@limiter.limit("5/minute", key_func=_user_rate_limit_key)
def generate_ai_quiz(
    request: Request,
    topic: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):

    if not user_has_documents(db, current_user.id):
        return {
            "topic": topic,
            "quiz": "Not enough information found in uploaded documents.",
            "questions": []
        }

    if needs_reembedding(db, current_user.id):
        return {
            "topic": topic,
            "quiz": "Your documents need re-indexing",
            "questions": []
        }

    top_pages = _retrieve_or_503(db, current_user.id, topic, top_k=5, apply_threshold=True)

    # Relevance checking using both cosine similarity and lexical overlap
    temp_context = " ".join([page.content for _, page in top_pages])
    if not top_pages or not check_context_relevance(topic, temp_context, top_pages[0][0]):
        return {
            "topic": topic,
            "quiz": "Not enough information found in uploaded documents.",
            "questions": []
        }

    try:
        result = _generate_quiz_with_gemini(topic, top_pages)
    except NotImplementedError:
        # LLM_PROVIDER=local has no structured JSON output -- go straight
        # to the local hybrid quiz generator below.
        result = None
    except llm.LLMUnavailableError:
        raise HTTPException(status_code=503, detail=AI_BUSY_DETAIL)

    if result is None:
        context = "\n\n".join(page.content for _, page in top_pages)
        quiz_text, questions = generate_quiz(context, topic)
    else:
        quiz_text, questions = result

    return {
        "topic": topic,
        "quiz": quiz_text,
        "questions": questions
    }
