import glob
import json
import logging
import os
import re
import shutil
from collections import Counter
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

import bloom
import embeddings
import jobs
import llm
import ocr
from rag import (
    generate_quiz,
    format_quiz_text,
    check_context_relevance,
    summarize_text_in_bullets,
    extract_definitions_and_statements,
    clean_pdf_text_for_quiz,
    clean_extracted_text
)
from retrieval import retrieve, invalidate_cache, user_has_documents, user_has_any_books, needs_reembedding

import database
from database import engine, get_db
from models import Base, Book, DocumentPage, Page, Question, User

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

    logger.info("Upload limit: %g MB, OCR page limit: %d", MAX_UPLOAD_MB, MAX_OCR_PAGES)
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
MAX_UPLOAD_MB = float(os.getenv("MAX_UPLOAD_MB", "100"))
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

PENDING_DOCUMENTS_MESSAGE = "Your documents are still being processed or waiting for review."


def _pending_documents_message_or_none(db: Session, user_id: int):
    """
    None if the user has no documents at all (each AI route keeps its own
    existing wording for that case); PENDING_DOCUMENTS_MESSAGE if they have
    at least one book that just isn't "ready" yet (still processing,
    awaiting OCR review, or failed) -- so those routes don't claim "no
    documents uploaded" when the user has, in fact, uploaded some.
    """
    if user_has_any_books(db, user_id):
        return PENDING_DOCUMENTS_MESSAGE
    return None

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
                    "bloom_level": {"type": "string", "enum": bloom.level_keys()},
                    "question": {"type": "string"},
                    "options": {"type": "array", "items": {"type": "string"}},
                    "correctAnswer": {"type": "string"},
                    "explanation": {"type": "string"},
                    "source_index": {"type": "integer"}
                },
                "required": [
                    "id", "type", "bloom_level", "question",
                    "correctAnswer", "explanation", "source_index"
                ]
            }
        }
    },
    "required": ["questions"]
}

# The per-type/option-format rules shared by every question regardless of
# Bloom level; the level-specific breakdown (which levels, how many of each,
# their prompt_rules) is built separately per call by _bloom_level_prompt_block
# since it depends on what the caller asked for.
QUIZ_PROMPT_RULES = (
    "Use ONLY the provided numbered sources -- never invent facts. No question's "
    "text may reveal or hint at its own correct answer. For type mcq, provide "
    "exactly 4 distinct, plausible options and set correctAnswer to the letter "
    "(A, B, C, or D) of the correct one. For type true_false, set options to "
    "[\"True\", \"False\"] and correctAnswer to \"A\" or \"B\" -- mix true and false "
    "answers across the quiz rather than always picking the same one. For every "
    "other type, correctAnswer is the actual answer text (not a letter) and "
    "options is omitted. For long_answer, scenario, viva and interview questions, "
    "the explanation must list the KEY POINTS a good answer should include (a "
    "short marking guide), not just a single correct sentence. Every question "
    "must test a different concept from the sources, every question needs a "
    "source_index (the number of the source chunk it's grounded in) and a "
    "bloom_level matching exactly the level it was written for below."
)

# A question's Bloom level is chosen when it's written (here, by the prompt
# below) and only ever checked afterward -- never derived from its "type".
# See bloom.py's module docstring.
CLASSIFIER_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "level": {"type": "string", "enum": bloom.level_keys()}
                },
                "required": ["id", "level"]
            }
        }
    },
    "required": ["items"]
}


def _bloom_level_prompt_block(level_counts: dict) -> str:
    """One bullet per requested level with a non-zero count: its label,
    description, prompt_rules, allowed question types and how many to write."""
    lines = []
    for key, count in level_counts.items():
        if count <= 0:
            continue
        level = bloom.get_level(key)
        allowed = ", ".join(level.question_types)
        lines.append(
            f"- {level.label} -- write exactly {count} question(s) at this level. "
            f"{level.description} {level.prompt_rules} Allowed question types: {allowed}."
        )
    return "\n".join(lines)


def _build_quiz_prompt(topic: str, top_pages, level_counts: dict) -> str:
    numbered_sources = "\n\n".join(
        f"[{idx}] {page.content}" for idx, (_, page) in enumerate(top_pages, start=1)
    )
    return (
        f"Create a quiz about '{topic}' based on the numbered sources below.\n\n"
        f"{QUIZ_PROMPT_RULES}\n\n"
        f"Bloom's taxonomy levels to write:\n{_bloom_level_prompt_block(level_counts)}\n\n"
        f"Sources:\n{numbered_sources}"
    )


def _bloom_level_descriptions_block() -> str:
    return "\n".join(f"- {level.key}: {level.description}" for level in bloom.LEVELS)


def _classify_questions_with_gemini(question_texts: list):
    """
    Asks Gemini to classify each question's Bloom level independently, from
    the question text alone -- it is never told which level the question was
    written for, since this is a check on that choice, not a source of it.
    Returns a list of levels (or None entries) aligned by position with
    `question_texts`, or None if the whole response wasn't shaped as
    expected (treated the same as llm.LLMUnavailableError by the caller:
    leave the affected questions unchecked rather than fail the quiz).
    """
    numbered = "\n".join(f"{i}. {text}" for i, text in enumerate(question_texts, start=1))
    prompt = (
        "Classify each numbered question below into exactly one Bloom's "
        "taxonomy level, based only on what it asks the student to do. "
        f"The six levels are:\n{_bloom_level_descriptions_block()}\n\n"
        f"Questions:\n{numbered}"
    )
    result = llm.generate_json(prompt, schema=CLASSIFIER_SCHEMA)

    if not isinstance(result, dict):
        return None
    raw_items = result.get("items")
    if not isinstance(raw_items, list):
        return None

    detected_by_position = {}
    for entry in raw_items:
        if not isinstance(entry, dict):
            continue
        position, level = entry.get("id"), entry.get("level")
        if isinstance(position, int) and bloom.is_valid_level(level):
            detected_by_position[position] = level

    return [detected_by_position.get(i) for i in range(1, len(question_texts) + 1)]


def _check_and_fix_bloom_levels(
    questions: list, topic: str, top_pages, levels: list, drop_log: list = None
) -> list:
    """
    Runs the Bloom level check on `questions` (each already carrying the
    bloom_level it was generated for) and fixes up any disagreement by
    regenerating just those slots once. Mutates and returns `questions`
    with bloom_check/detected_level set on every item. Never raises -- an
    unavailable or malformed checker call just leaves the affected
    questions bloom_check="unchecked" instead of failing the whole quiz.
    """
    for q in questions:
        verb_level = bloom.classify_by_verbs(q["question"])["level"]
        if verb_level and verb_level != q["bloom_level"]:
            logger.debug(
                "classify_by_verbs disagrees with intended level (intended=%s, verb=%s): %r",
                q["bloom_level"], verb_level, q["question"][:80]
            )

    try:
        detected_levels = _classify_questions_with_gemini([q["question"] for q in questions])
    except llm.LLMUnavailableError:
        detected_levels = None

    if detected_levels is None:
        for q in questions:
            q["bloom_check"] = "unchecked"
            q["detected_level"] = None
        return questions

    mismatched_indices = []
    for i, q in enumerate(questions):
        detected = detected_levels[i]
        if detected == q["bloom_level"]:
            q["bloom_check"] = "verified"
            q["detected_level"] = None
        else:
            q["bloom_check"] = "flagged"
            q["detected_level"] = detected
            mismatched_indices.append(i)

    if not mismatched_indices:
        return questions

    regen_counts = {}
    for i in mismatched_indices:
        level = questions[i]["bloom_level"]
        regen_counts[level] = regen_counts.get(level, 0) + 1

    try:
        result = llm.generate_json(
            _build_quiz_prompt(topic, top_pages, regen_counts), schema=QUIZ_SCHEMA
        )
        raw_regenerated = result.get("questions", []) if isinstance(result, dict) else []
    except llm.LLMUnavailableError:
        raw_regenerated = []

    regenerated = _validate_quiz_questions(raw_regenerated, len(top_pages), list(regen_counts.keys()), drop_log)
    pool_by_level = {}
    for q in regenerated:
        pool_by_level.setdefault(q["bloom_level"], []).append(q)

    replaced_indices = []
    for i in mismatched_indices:
        pool = pool_by_level.get(questions[i]["bloom_level"]) or []
        if pool:
            replacement = pool.pop(0)
            replacement["id"] = questions[i]["id"]
            questions[i] = replacement
            replaced_indices.append(i)
        # else: no usable replacement came back -- keep the original, already flagged.

    if replaced_indices:
        try:
            recheck = _classify_questions_with_gemini([questions[i]["question"] for i in replaced_indices])
        except llm.LLMUnavailableError:
            recheck = None

        for position, i in enumerate(replaced_indices):
            if recheck is None:
                questions[i]["bloom_check"] = "unchecked"
                questions[i]["detected_level"] = None
                continue
            detected = recheck[position]
            if detected == questions[i]["bloom_level"]:
                questions[i]["bloom_check"] = "verified"
                questions[i]["detected_level"] = None
            else:
                questions[i]["bloom_check"] = "flagged"
                questions[i]["detected_level"] = detected

    return questions


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


ROTATE_DIRECTIONS = ("left", "right")


class RotatePageRequest(BaseModel):
    direction: str


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


def _resolve_page_image_path(user_id: int, book_id: int, page: DocumentPage) -> Path:
    """
    Validates and returns the on-disk path of a page's saved image, raising
    a 404 for anything that isn't an actual, existing file strictly inside
    this book's own upload folder. Used by both the image-serving route and
    the rotate route, which are the only two things that ever touch a page
    image file on disk -- defense in depth against a corrupted/tampered
    image_path, regardless of what's stored in the database.
    """
    if not page.image_path:
        raise HTTPException(status_code=404, detail="This page has no saved image.")

    book_dir = _book_upload_dir(user_id, book_id).resolve()
    image_path = Path(page.image_path).resolve()
    try:
        image_path.relative_to(book_dir)
    except ValueError:
        logger.error(
            "Refusing to touch a page image outside its book's upload folder (book_id=%d, page=%d)",
            book_id, page.page_number
        )
        raise HTTPException(status_code=404, detail="Image not found")

    if not image_path.is_file():
        raise HTTPException(status_code=404, detail="Image not found")

    return image_path


@app.get("/books/{book_id}/pages/{page_number}/image")
def get_page_image(
    book_id: int,
    page_number: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    _get_owned_book_or_404(db, book_id, current_user.id)
    page = _get_document_page_or_404(db, book_id, page_number)
    image_path = _resolve_page_image_path(current_user.id, book_id, page)

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


@app.post("/books/{book_id}/pages/{page_number}/rotate")
def rotate_page_image(
    book_id: int,
    page_number: int,
    payload: RotatePageRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    _get_owned_book_or_404(db, book_id, current_user.id)
    page = _get_document_page_or_404(db, book_id, page_number)

    if payload.direction not in ROTATE_DIRECTIONS:
        raise HTTPException(status_code=400, detail="direction must be 'left' or 'right'.")

    image_path = _resolve_page_image_path(current_user.id, book_id, page)

    try:
        rotated_bytes = ocr.rotate_image(image_path.read_bytes(), payload.direction)
        image_path.write_bytes(rotated_bytes)
    except Exception:
        logger.exception("Failed to rotate page image for book_id=%d page=%d", book_id, page_number)
        raise HTTPException(status_code=500, detail="Failed to rotate this page's image.")

    return {"book_id": book_id, "page_number": page_number, "direction": payload.direction}


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

    # Questions aren't a cascade relationship on Book (a separate concern
    # from its pages/chunks) -- delete them explicitly.
    db.query(Question).filter(Question.book_id == book_id).delete()

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
            "message": _pending_documents_message_or_none(db, current_user.id) or "No documents available"
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
            "message": _pending_documents_message_or_none(db, current_user.id) or "No documents uploaded"
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
        return {"cards": [], "message": _pending_documents_message_or_none(db, current_user.id) or "No documents uploaded yet."}

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


def _validate_quiz_questions(raw_questions, num_sources: int, requested_levels: list, drop_log: list = None):
    """
    Validates and cleans Gemini's raw quiz question list: correct option
    counts, a valid correctAnswer, no question leaking its own answer (for
    types where correctAnswer is real answer text rather than a letter),
    a bloom_level that's both one of `requested_levels` and a level this
    question's type is actually allowed at, and no duplicate questions.
    Invalid questions are dropped rather than failing the whole quiz.
    Returns cleaned question dicts -- id/type/bloom_level/question/options?/
    correctAnswer/explanation/source_index, matching the existing response
    shape plus the two Phase 4B fields the caller needs (bloom_level,
    source_index) before building the final API response.

    If `drop_log` is given, one short reason string (never the question's
    own text, or anything from the source documents) is appended to it for
    every dropped question, for the caller to log in aggregate.
    """
    if not isinstance(raw_questions, list):
        return []

    valid = []
    seen_question_text = set()

    def _drop(reason: str):
        if drop_log is not None:
            drop_log.append(reason)

    for q in raw_questions:
        if not isinstance(q, dict):
            _drop("entry is not an object")
            continue

        q_type = q.get("type")
        bloom_level = q.get("bloom_level")
        question_text = q.get("question")
        correct_answer = q.get("correctAnswer")
        explanation = q.get("explanation")
        source_index = q.get("source_index")
        options = q.get("options")

        if q_type not in QUIZ_QUESTION_TYPES:
            _drop(f"type '{q_type}' is not a recognized question type")
            continue
        if not bloom.is_valid_level(bloom_level) or bloom_level not in requested_levels:
            _drop(f"bloom_level '{bloom_level}' is not one of the requested levels")
            continue
        if q_type not in bloom.get_level(bloom_level).question_types:
            _drop(f"type '{q_type}' not allowed for '{bloom_level}'")
            continue
        if not isinstance(question_text, str) or not question_text.strip():
            _drop("missing or empty question text")
            continue
        if not isinstance(correct_answer, str) or not correct_answer.strip():
            _drop("missing or empty correct answer")
            continue
        if not isinstance(explanation, str) or not explanation.strip():
            _drop("missing or empty explanation")
            continue
        if not isinstance(source_index, int) or not (1 <= source_index <= num_sources):
            _drop("missing or out-of-range source_index")
            continue

        cleaned_options = None

        if q_type == "mcq":
            if not isinstance(options, list) or len(options) != 4:
                _drop("mcq does not have exactly 4 options")
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
                _drop("mcq options are empty or invalid after cleanup")
                continue
            if len({o.lower() for o in option_texts}) != 4:
                _drop("mcq options are not all distinct")
                continue
            if correct_answer not in ("A", "B", "C", "D"):
                _drop("mcq correctAnswer is not A-D")
                continue
            cleaned_options = option_texts

        elif q_type == "true_false":
            if not isinstance(options, list) or [str(o).strip() for o in options] != ["True", "False"]:
                _drop("true_false options are not ['True', 'False']")
                continue
            if correct_answer not in ("A", "B"):
                _drop("true_false correctAnswer is not A/B")
                continue
            cleaned_options = ["True", "False"]

        else:
            # correctAnswer is real answer text here, not a letter -- the
            # question text must not give it away.
            if correct_answer.strip().lower() in question_text.lower():
                _drop(f"question text leaks its own answer ({q_type})")
                continue

        normalized = re.sub(r'\s+', ' ', question_text.strip().lower())
        if normalized in seen_question_text:
            _drop("duplicate question text")
            continue
        seen_question_text.add(normalized)

        cleaned = {
            "id": len(valid) + 1,
            "type": q_type,
            "bloom_level": bloom_level,
            "question": question_text.strip(),
            "correctAnswer": correct_answer.strip(),
            "explanation": explanation.strip(),
            "source_index": source_index
        }
        if cleaned_options is not None:
            cleaned["options"] = cleaned_options
        valid.append(cleaned)

    return valid


def _generate_quiz_with_gemini(
    topic: str, top_pages, levels: list, level_counts: dict, drop_log: list = None
):
    """
    Builds the per-Bloom-level prompt, asks Gemini for a full quiz as JSON,
    and validates/cleans the result. Returns the cleaned question list on
    success, or None if fewer than 3 valid questions remain even after one
    retry -- the caller falls back to generate_quiz() (the local hybrid
    generator) in that case. Raises NotImplementedError for LLM_PROVIDER=local
    and llm.LLMUnavailableError on a persistently unavailable provider, same
    as llm.generate_json() itself. Does not run the Bloom level check or the
    missing-level top-up -- those are the caller's job (_check_and_fix_bloom_levels,
    _top_up_missing_levels), since they need the final assembled list, not
    whatever a single attempt happens to be.
    """
    prompt = _build_quiz_prompt(topic, top_pages, level_counts)

    for _attempt in range(2):  # one retry if the first pass yields too few valid questions
        result = llm.generate_json(prompt, schema=QUIZ_SCHEMA)
        raw_questions = result.get("questions", []) if isinstance(result, dict) else []
        questions = _validate_quiz_questions(raw_questions, len(top_pages), levels, drop_log)
        if len(questions) >= 3:
            return questions

    return None


def _top_up_missing_levels(
    questions: list, topic: str, top_pages, level_counts: dict, drop_log: list = None
) -> tuple:
    """
    Compares what actually survived validation/checking against what was
    asked for (bloom.distribute's level_counts) and, for every level that's
    short, makes ONE extra generate_json call for just those missing slots
    (reusing the same per-level prompt rules), then validates and
    Bloom-checks the result exactly like any other batch. A level still
    short after that is left as-is -- the caller gets a plain-English
    warning for it instead of a silently smaller quiz. Returns
    (questions, warnings); never raises (an unavailable top-up call just
    means that level stays short and gets a warning).
    """
    actual_counts = Counter(q["bloom_level"] for q in questions)
    missing_counts = {
        level: needed - actual_counts.get(level, 0)
        for level, needed in level_counts.items()
        if needed > actual_counts.get(level, 0)
    }

    if not missing_counts:
        return questions, []

    try:
        result = llm.generate_json(
            _build_quiz_prompt(topic, top_pages, missing_counts), schema=QUIZ_SCHEMA
        )
        raw_topped_up = result.get("questions", []) if isinstance(result, dict) else []
    except llm.LLMUnavailableError:
        raw_topped_up = []

    topped_up = _validate_quiz_questions(raw_topped_up, len(top_pages), list(missing_counts.keys()), drop_log)
    topped_up = _check_and_fix_bloom_levels(topped_up, topic, top_pages, list(missing_counts.keys()), drop_log)

    next_id = max((q["id"] for q in questions), default=0) + 1
    for q in topped_up:
        q["id"] = next_id
        next_id += 1

    combined = questions + topped_up
    final_counts = Counter(q["bloom_level"] for q in combined)

    warnings = []
    for level, needed in level_counts.items():
        if final_counts.get(level, 0) < needed:
            label = bloom.get_level(level).label
            warnings.append(f"Could not create enough {label} questions -- try again or pick other levels")

    return combined, warnings


QUESTION_CHECK_STATUSES = ("verified", "flagged", "unchecked", "teacher_set")
MIN_QUIZ_QUESTION_COUNT = 1
MAX_QUIZ_QUESTION_COUNT = 12


def _resolve_question_source(db: Session, top_pages, source_index):
    """
    Maps a validated question's source_index back to the actual Page/Book it
    was grounded in -- never trusting an id the model might have invented,
    only the position within the same top_pages list the prompt was built
    from. Returns (source_dict_or_None, book_id_or_None, page_id_or_None).
    """
    if not isinstance(source_index, int) or not (1 <= source_index <= len(top_pages)):
        return None, None, None

    _, page = top_pages[source_index - 1]
    book = db.query(Book).filter(Book.id == page.book_id).first()
    if not book:
        return None, page.book_id, page.id

    return {"book_name": book.name, "page_number": page.page_number}, page.book_id, page.id


def _save_generated_questions(db: Session, user_id: int, topic: str, questions: list, top_pages) -> list:
    """
    Persists every question in `questions` as its own Question row and
    returns the API response list for them -- the existing fields plus
    question_id, bloom_level/bloom_label/bloom_check/detected_level and
    source. `top_pages` resolves each question's source_index (absent
    entirely for local hybrid-fallback questions, which get a null source).
    """
    pending = []
    for q in questions:
        source, book_id, page_id = _resolve_question_source(db, top_pages, q.get("source_index"))
        db_question = Question(
            user_id=user_id,
            book_id=book_id,
            page_id=page_id,
            topic=topic,
            type=q["type"],
            bloom_level=q["bloom_level"],
            bloom_check=q["bloom_check"],
            detected_level=q.get("detected_level"),
            question=q["question"],
            options=json.dumps(q["options"]) if q.get("options") else None,
            correct_answer=q["correctAnswer"],
            explanation=q["explanation"],
        )
        db.add(db_question)
        pending.append((q, db_question, source))

    db.flush()

    response_questions = []
    for q, db_question, source in pending:
        level = bloom.get_level(q["bloom_level"])
        item = {
            "id": q["id"],
            "type": q["type"],
            "question": q["question"],
            "correctAnswer": q["correctAnswer"],
            "explanation": q["explanation"],
            "question_id": db_question.id,
            "bloom_level": q["bloom_level"],
            "bloom_label": level.label,
            "bloom_check": q["bloom_check"],
            "detected_level": q.get("detected_level"),
            "source": source,
        }
        if "options" in q:
            item["options"] = q["options"]
        response_questions.append(item)

    db.commit()
    return response_questions


@app.get("/generate-quiz")
@limiter.limit("5/minute", key_func=_user_rate_limit_key)
def generate_ai_quiz(
    request: Request,
    topic: str,
    levels: str | None = None,
    count: int = 6,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        requested_levels = bloom.parse_levels(levels)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if not (MIN_QUIZ_QUESTION_COUNT <= count <= MAX_QUIZ_QUESTION_COUNT):
        raise HTTPException(
            status_code=422,
            detail=f"count must be between {MIN_QUIZ_QUESTION_COUNT} and {MAX_QUIZ_QUESTION_COUNT}."
        )

    if not user_has_documents(db, current_user.id):
        return {
            "topic": topic,
            "quiz": _pending_documents_message_or_none(db, current_user.id) or "Not enough information found in uploaded documents.",
            "questions": [],
            "levels": requested_levels,
            "level_counts": {},
            "warnings": []
        }

    if needs_reembedding(db, current_user.id):
        return {
            "topic": topic,
            "quiz": "Your documents need re-indexing",
            "questions": [],
            "levels": requested_levels,
            "level_counts": {},
            "warnings": []
        }

    top_pages = _retrieve_or_503(db, current_user.id, topic, top_k=5, apply_threshold=True)

    # Relevance checking using both cosine similarity and lexical overlap
    temp_context = " ".join([page.content for _, page in top_pages])
    if not top_pages or not check_context_relevance(topic, temp_context, top_pages[0][0]):
        return {
            "topic": topic,
            "quiz": "Not enough information found in uploaded documents.",
            "questions": [],
            "levels": requested_levels,
            "level_counts": {},
            "warnings": []
        }

    level_counts = bloom.distribute(count, requested_levels)
    drop_log = []
    warnings = []

    try:
        questions = _generate_quiz_with_gemini(topic, top_pages, requested_levels, level_counts, drop_log)
    except NotImplementedError:
        # LLM_PROVIDER=local has no structured JSON output -- go straight
        # to the local hybrid quiz generator below.
        questions = None
    except llm.LLMUnavailableError:
        raise HTTPException(status_code=503, detail=AI_BUSY_DETAIL)

    if questions is None:
        # Fewer than 3 valid AI questions (or LLM_PROVIDER=local): fall back
        # to the local hybrid generator, which knows nothing about Bloom
        # levels -- label each question after the fact with the verb
        # classifier instead (None -> remember, the safest default) and
        # leave bloom_check "unchecked" since no independent check ran.
        # No top-up here -- the fallback generator ignores levels/count
        # entirely, so there's nothing level-shaped to compare against.
        context = "\n\n".join(page.content for _, page in top_pages)
        quiz_text, questions = generate_quiz(context, topic)
        for q in questions:
            q["bloom_level"] = bloom.classify_by_verbs(q["question"])["level"] or "remember"
            q["bloom_check"] = "unchecked"
            q["detected_level"] = None
    else:
        questions = _check_and_fix_bloom_levels(questions, topic, top_pages, requested_levels, drop_log)
        questions, warnings = _top_up_missing_levels(questions, topic, top_pages, level_counts, drop_log)
        quiz_text = format_quiz_text(questions)

    if drop_log:
        reason_counts = Counter(drop_log)
        logger.info(
            "Quiz generation for topic '%s': %s",
            topic, "; ".join(f"dropped {count}: {reason}" for reason, count in reason_counts.items())
        )

    response_questions = _save_generated_questions(db, current_user.id, topic, questions, top_pages)

    actual_level_counts = {}
    for q in questions:
        actual_level_counts[q["bloom_level"]] = actual_level_counts.get(q["bloom_level"], 0) + 1

    return {
        "topic": topic,
        "quiz": quiz_text,
        "questions": response_questions,
        "levels": requested_levels,
        "level_counts": actual_level_counts,
        "warnings": warnings
    }


# -----------------------------
# PHASE 4B: BLOOM'S TAXONOMY LEVELS + SAVED QUESTIONS
# -----------------------------
@app.get("/bloom/levels")
def get_bloom_levels(current_user: User = Depends(get_current_user)):
    return [
        {
            "key": level.key,
            "label": level.label,
            "order": level.order,
            "description": level.description,
            "question_types": list(level.question_types)
        }
        for level in bloom.LEVELS
    ]


def _question_source(db: Session, question: Question):
    if not question.book_id:
        return None

    book = db.query(Book).filter(Book.id == question.book_id).first()
    if not book:
        return None

    page_number = None
    if question.page_id:
        page = db.query(Page).filter(Page.id == question.page_id).first()
        if page:
            page_number = page.page_number

    return {"book_name": book.name, "page_number": page_number}


def _question_summary(db: Session, question: Question) -> dict:
    level = bloom.get_level(question.bloom_level)
    return {
        "question_id": question.id,
        "topic": question.topic,
        "type": question.type,
        "bloom_level": question.bloom_level,
        "bloom_label": level.label,
        "bloom_check": question.bloom_check,
        "detected_level": question.detected_level,
        "question": question.question,
        "options": json.loads(question.options) if question.options else None,
        "correct_answer": question.correct_answer,
        "explanation": question.explanation,
        "created_at": question.created_at.isoformat(),
        "source": _question_source(db, question)
    }


def _get_owned_question_or_404(db: Session, question_id: int, user_id: int) -> Question:
    question = db.query(Question).filter(
        Question.id == question_id,
        Question.user_id == user_id
    ).first()

    if not question:
        raise HTTPException(status_code=404, detail="Question not found")

    return question


@app.get("/questions")
def list_questions(
    level: str | None = None,
    topic: str | None = None,
    check: str | None = None,
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if level is not None and not bloom.is_valid_level(level):
        raise HTTPException(status_code=422, detail=f"Unknown Bloom's level: '{level}'")
    if check is not None and check not in QUESTION_CHECK_STATUSES:
        raise HTTPException(status_code=422, detail=f"Unknown check status: '{check}'")
    if not (1 <= limit <= 200):
        raise HTTPException(status_code=422, detail="limit must be between 1 and 200.")
    if offset < 0:
        raise HTTPException(status_code=422, detail="offset must be >= 0.")

    query = db.query(Question).filter(Question.user_id == current_user.id)
    if level is not None:
        query = query.filter(Question.bloom_level == level)
    if check is not None:
        query = query.filter(Question.bloom_check == check)
    if topic is not None and topic.strip():
        query = query.filter(Question.topic.ilike(f"%{_escape_like(topic)}%", escape="\\"))

    total = query.count()
    rows = query.order_by(Question.created_at.desc()).offset(offset).limit(limit).all()

    return {"total": total, "items": [_question_summary(db, row) for row in rows]}


class QuestionUpdateRequest(BaseModel):
    question: str | None = None
    options: list | None = None
    correct_answer: str | None = None
    explanation: str | None = None
    bloom_level: str | None = None


@app.patch("/questions/{question_id}")
def update_question(
    question_id: int,
    payload: QuestionUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    question = _get_owned_question_or_404(db, question_id, current_user.id)

    new_question_text = payload.question if payload.question is not None else question.question
    new_options = payload.options if payload.options is not None else (
        json.loads(question.options) if question.options else None
    )
    new_correct_answer = payload.correct_answer if payload.correct_answer is not None else question.correct_answer
    new_explanation = payload.explanation if payload.explanation is not None else question.explanation

    if not isinstance(new_question_text, str) or not new_question_text.strip():
        raise HTTPException(status_code=400, detail="question cannot be empty.")
    if not isinstance(new_correct_answer, str) or not new_correct_answer.strip():
        raise HTTPException(status_code=400, detail="correct_answer cannot be empty.")
    if not isinstance(new_explanation, str) or not new_explanation.strip():
        raise HTTPException(status_code=400, detail="explanation cannot be empty.")

    if question.type == "mcq":
        option_texts = [str(o).strip() for o in (new_options or []) if str(o).strip()]
        if len(option_texts) != 4:
            raise HTTPException(status_code=400, detail="mcq questions need exactly 4 options.")
        if len({o.lower() for o in option_texts}) != 4:
            raise HTTPException(status_code=400, detail="mcq options must be 4 distinct values.")
        if new_correct_answer not in ("A", "B", "C", "D"):
            raise HTTPException(status_code=400, detail="mcq correct_answer must be A, B, C, or D.")
        new_options = option_texts
    elif question.type == "true_false":
        if [str(o).strip() for o in (new_options or [])] != ["True", "False"]:
            raise HTTPException(status_code=400, detail='true_false options must be ["True", "False"].')
        if new_correct_answer not in ("A", "B"):
            raise HTTPException(status_code=400, detail="true_false correct_answer must be A or B.")
        new_options = ["True", "False"]
    else:
        new_options = None  # other types never carry options

    question.question = new_question_text.strip()
    question.options = json.dumps(new_options) if new_options is not None else None
    question.correct_answer = new_correct_answer.strip()
    question.explanation = new_explanation.strip()

    if payload.bloom_level is not None:
        if not bloom.is_valid_level(payload.bloom_level):
            raise HTTPException(status_code=422, detail=f"Unknown Bloom's level: '{payload.bloom_level}'")
        if question.type not in bloom.get_level(payload.bloom_level).question_types:
            raise HTTPException(
                status_code=400,
                detail=f"Question type '{question.type}' is not valid for Bloom level '{payload.bloom_level}'."
            )
        question.bloom_level = payload.bloom_level
        question.bloom_check = "teacher_set"
        question.detected_level = None

    db.commit()
    return _question_summary(db, question)


@app.delete("/questions/{question_id}")
def delete_question(
    question_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    question = _get_owned_question_or_404(db, question_id, current_user.id)
    db.delete(question)
    db.commit()
    return {"status": "Question deleted successfully"}
