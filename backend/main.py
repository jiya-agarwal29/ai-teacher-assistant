import glob
import json
import logging
import os
import re
import shutil
import tempfile
import time
from collections import Counter
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException, Depends
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

import bloom
import document_parsers
import paper
import paper_export
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
from models import (
    Base, Book, DocumentPage, Page, Question, Paper, PaperQuestion, User,
    ActivityEvent, QuizAttempt, QuizAnswer
)

from auth import (
    create_access_token,
    verify_password,
    hash_password,
    get_current_user,
    verify_token,
    ACCESS_TOKEN_EXPIRE_MINUTES
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


def _fail_interrupted_papers(db: Session) -> int:
    """
    Same reasoning as _fail_interrupted_books: a Paper stuck "generating"
    belonged to a worker thread that no longer exists after a restart.
    POST /papers/{id}/generate can simply be called again -- it only ever
    fills still-empty slots.
    """
    interrupted = db.query(Paper).filter(Paper.status == "generating").all()
    for paper_row in interrupted:
        paper_row.status = "failed"
        paper_row.error = "Generation was interrupted, click Generate to continue."
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
        failed_paper_count = _fail_interrupted_papers(db)
        if failed_paper_count:
            logger.warning("Marked %d interrupted paper generation(s) as failed on startup", failed_paper_count)
    finally:
        db.close()

    logger.info("Upload limit: %g MB, OCR page limit: %d", MAX_UPLOAD_MB, MAX_OCR_PAGES)
    logger.info("Session length: %d minutes", ACCESS_TOKEN_EXPIRE_MINUTES)
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
    # Content-Disposition isn't one of the CORS-safelisted response headers,
    # so without this, frontend fetch() calls across origins (the dev
    # server on :5173 talking to the API on :8000) can't read the
    # server-chosen filename off a download response (api.papers.download)
    # -- it silently comes back null and falls back to a generic name.
    expose_headers=["Content-Disposition"],
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

# Same shape as QUIZ_SCHEMA, but marks/marking_scheme are REQUIRED -- used
# only for exam-paper generation (Phase 5B), where every question must
# carry both. A separate schema object (not QUIZ_SCHEMA with an extra
# optional field) so the plain quiz path never has to think about marks
# at all, and Gemini's structured-output mode reliably returns these
# fields only when they're actually required.
PAPER_QUIZ_SCHEMA = {
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
                    "source_index": {"type": "integer"},
                    "marks": {"type": "integer"},
                    "marking_scheme": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "point": {"type": "string"},
                                "marks": {"type": "integer"}
                            },
                            "required": ["point", "marks"]
                        }
                    }
                },
                "required": [
                    "id", "type", "bloom_level", "question", "correctAnswer",
                    "explanation", "source_index", "marks", "marking_scheme"
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
    "must test a different idea, concept, or scenario from every other question "
    "in this quiz and from the sources -- no two questions may share the same "
    "underlying concept or scenario setup, even if worded differently. Every "
    "question needs a source_index (the number of the source chunk it's "
    "grounded in) and a bloom_level matching exactly the level it was written "
    "for below."
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


# Phase 5E: POST /papers/import-pattern's structured-output schema -- reads
# an old exam paper's layout (sections + the individual questions printed
# in them) back out of Gemini.
PATTERN_IMPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "duration_minutes": {"type": "integer"},
        "total_marks": {"type": "integer"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "instructions": {"type": "string"},
                    "marks_each": {"type": "integer"},
                    "num_questions": {"type": "integer"},
                    "choice": {"type": "string", "enum": list(paper.CHOICE_KINDS)},
                    "attempt": {"type": "integer"}
                },
                "required": ["title", "instructions", "marks_each", "num_questions", "choice"]
            }
        },
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "section": {"type": "string"},
                    "number": {"type": "string"},
                    "option": {"type": "string"},
                    "text": {"type": "string"},
                    "marks": {"type": "integer"}
                },
                "required": ["section", "number", "text", "marks"]
            }
        }
    },
    "required": ["sections", "questions"]
}


def _bloom_level_prompt_block(
    level_counts: dict, marks_by_level: dict = None, paper_kind: str = "written", avoid_verbs: dict = None
) -> str:
    """
    One bullet per requested level with a non-zero count: its label,
    description, prompt_rules, allowed question types and how many to
    write. `marks_by_level` (Phase 5B exam papers only -- a question at
    this level must be worth exactly this many marks) swaps in
    paper.allowed_types_for_slot's marks-aware type list instead of the
    level's full type list, and adds the marks/marking_scheme instruction.

    `avoid_verbs` (Phase 5 fixes from browser testing, item 5 -- exam
    papers only, {level: [verb, ...]}): appends a line telling the model
    not to open a question at that level with one of these verbs, because
    an earlier attempt at this exact slot used one and read as a
    different Bloom level (see _apply_verb_guard). Omitted entirely,
    nothing changes here.
    """
    lines = []
    for key, count in level_counts.items():
        if count <= 0:
            continue
        level = bloom.get_level(key)
        marks = (marks_by_level or {}).get(key)
        if marks is not None:
            allowed = ", ".join(paper.allowed_types_for_slot(key, marks, paper_kind))
            lines.append(
                f"- {level.label} -- write exactly {count} question(s) at this level, "
                f"each worth EXACTLY {marks} marks (a {marks}-mark question needs about a "
                f"{marks}-mark answer -- longer/deeper for more marks). {level.description} "
                f"{level.prompt_rules} Allowed question types: {allowed}. Also return "
                f"marking_scheme: a list of {{point, marks}} key points a full answer needs, "
                f"whose marks add up to EXACTLY {marks} (e.g. a 2-mark question needs 2 points "
                "worth 1 each, or 1 point worth 2; an mcq always has exactly one point worth "
                "all the marks)."
            )
        else:
            allowed = ", ".join(level.question_types)
            lines.append(
                f"- {level.label} -- write exactly {count} question(s) at this level. "
                f"{level.description} {level.prompt_rules} Allowed question types: {allowed}."
            )
        avoid = (avoid_verbs or {}).get(key)
        if avoid:
            verb_list = ", ".join(f"'{v}'" for v in avoid)
            lines[-1] += (
                f" Avoid opening this question with {verb_list} -- a previous attempt at this "
                f"slot used that wording and read as a different Bloom level, not {level.label}."
            )
    return "\n".join(lines)


def _build_quiz_prompt(
    topic: str, top_pages, level_counts: dict, recent_questions: list = None,
    marks_by_level: dict = None, paper_kind: str = "written", avoid_verbs: dict = None
) -> str:
    """
    `recent_questions` (up to 15 of the user's own previously-saved question
    *texts* for this topic, newest first -- never another user's, and never
    answers/explanations) is handed to the model as extra context on what
    not to repeat; omitted entirely when there's no history yet.
    `marks_by_level`/`paper_kind`/`avoid_verbs` -- see _bloom_level_prompt_block.
    """
    numbered_sources = "\n\n".join(
        f"[{idx}] {page.content}" for idx, (_, page) in enumerate(top_pages, start=1)
    )
    recent_block = ""
    if recent_questions:
        recent_list = "\n".join(f"- {text}" for text in recent_questions)
        recent_block = (
            "\n\nThe following questions were already asked on this topic -- this "
            "list is NOT a source, and none of its facts may be used in your "
            "questions, answers, or marking scheme. Do not repeat or closely "
            f"paraphrase any of these questions already asked on this topic:\n{recent_list}"
        )
    return (
        f"Create a quiz about '{topic}' based on the numbered sources below.\n\n"
        f"{QUIZ_PROMPT_RULES}\n\n"
        f"Bloom's taxonomy levels to write:"
        f"\n{_bloom_level_prompt_block(level_counts, marks_by_level, paper_kind, avoid_verbs)}"
        f"{recent_block}\n\n"
        f"Sources:\n{numbered_sources}"
    )


def _bloom_level_descriptions_block() -> str:
    return "\n".join(f"- {level.key}: {level.description}" for level in bloom.LEVELS)


# Mirrors bloom.py's NO_DIRECT_ANSWER_CONSTRAINT (asked of the writer for
# Apply and above) from the checker's side: even a question phrased with an
# "Apply"/"Analyze"/etc. verb is really no harder than Understand if the
# sources just hand over the answer.
CHECKER_DIRECT_ANSWER_RULE = (
    "If the answer is stated directly (or almost word for word) in the "
    "sources, the question is at most Understand, whatever verbs it uses. "
    "This also applies when the sources already contain the full answer as "
    "a ready-made plan, list, or comparison table (e.g. a design or "
    "hardening checklist copied straight from the text) -- that is still at "
    "most Understand, not Create, even if the question asks the student to "
    "'design' or 'propose' it."
)


def _classify_questions_with_gemini(question_texts: list, top_pages=None):
    """
    Asks Gemini to classify each question's Bloom level independently, from
    the question text alone -- it is never told which level the question was
    written for, since this is a check on that choice, not a source of it.
    `top_pages` (the same numbered sources the questions were generated
    from) lets the checker apply CHECKER_DIRECT_ANSWER_RULE; omitted, the
    rule is simply skipped for that call. Returns a list of levels (or None
    entries) aligned by position with `question_texts`, or None if the
    whole response wasn't shaped as expected (treated the same as
    llm.LLMUnavailableError by the caller: leave the affected questions
    unchecked rather than fail the quiz).
    """
    numbered = "\n".join(f"{i}. {text}" for i, text in enumerate(question_texts, start=1))
    sources_block = ""
    if top_pages:
        numbered_sources = "\n\n".join(
            f"[{idx}] {page.content}" for idx, (_, page) in enumerate(top_pages, start=1)
        )
        sources_block = f"\n\n{CHECKER_DIRECT_ANSWER_RULE}\n\nSources:\n{numbered_sources}"
    prompt = (
        "Classify each numbered question below into exactly one Bloom's "
        "taxonomy level, based only on what it asks the student to do. "
        f"The six levels are:\n{_bloom_level_descriptions_block()}"
        f"{sources_block}\n\n"
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


def _generate_and_validate_questions(
    topic: str, top_pages, level_counts: dict, drop_log: list = None, recent_questions: list = None,
    marks_by_level: dict = None, paper_kind: str = "written", avoid_verbs: dict = None
) -> list:
    """
    ONE generate_json call for exactly the levels/counts in `level_counts`
    (the same per-level prompt rules and anti-repeat context as a full
    quiz), validated and cleaned via _validate_quiz_questions. No retry
    loop and no minimum-count threshold -- those belong to "generate a
    whole quiz" (_generate_quiz_with_gemini) alone. Every other caller --
    the mismatch-regeneration and missing-level top-up below, and the
    single-question regenerate endpoint -- wants exactly one shot at
    exactly the slots it asked for, so they all share this instead of
    duplicating the prompt-build-validate sequence. Propagates
    llm.LLMUnavailableError; callers decide how to handle it.

    `marks_by_level`/`paper_kind` (Phase 5B exam papers only): forwarded to
    _build_quiz_prompt/_validate_quiz_questions, and switch the structured-
    output schema to PAPER_QUIZ_SCHEMA (marks/marking_scheme required)
    instead of QUIZ_SCHEMA. Omitted entirely, this behaves exactly as before.
    `avoid_verbs` -- see _bloom_level_prompt_block; forwarded as-is.
    """
    prompt = _build_quiz_prompt(
        topic, top_pages, level_counts, recent_questions, marks_by_level, paper_kind, avoid_verbs
    )
    schema = PAPER_QUIZ_SCHEMA if marks_by_level else QUIZ_SCHEMA
    result = llm.generate_json(prompt, schema=schema)
    raw_questions = result.get("questions", []) if isinstance(result, dict) else []
    return _validate_quiz_questions(
        raw_questions, len(top_pages), list(level_counts.keys()), drop_log, marks_by_level, paper_kind
    )


def _check_and_fix_bloom_levels(
    questions: list, topic: str, top_pages, drop_log: list = None, recent_questions: list = None,
    marks_by_level: dict = None, paper_kind: str = "written"
) -> list:
    """
    Runs the Bloom level check on `questions` (each already carrying the
    bloom_level it was generated for) and fixes up any disagreement by
    regenerating just those slots once. Mutates and returns `questions`
    with bloom_check/detected_level set on every item. Never raises -- an
    unavailable or malformed checker call just leaves the affected
    questions bloom_check="unchecked" instead of failing the whole quiz.

    `marks_by_level`/`paper_kind` (Phase 5B exam papers only): forwarded to
    the regeneration call so replacements still carry marks/marking_scheme.
    """
    for q in questions:
        verb_level = bloom.classify_by_verbs(q["question"])["level"]
        if verb_level and verb_level != q["bloom_level"]:
            logger.debug(
                "classify_by_verbs disagrees with intended level (intended=%s, verb=%s): %r",
                q["bloom_level"], verb_level, q["question"][:80]
            )

    try:
        detected_levels = _classify_questions_with_gemini([q["question"] for q in questions], top_pages)
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
        regenerated = _generate_and_validate_questions(
            topic, top_pages, regen_counts, drop_log, recent_questions, marks_by_level, paper_kind
        )
    except llm.LLMUnavailableError:
        regenerated = []

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
            recheck = _classify_questions_with_gemini(
                [questions[i]["question"] for i in replaced_indices], top_pages
            )
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


def _apply_verb_guard(
    questions: list, topic: str, top_pages, drop_log: list = None, recent_questions: list = None,
    marks_by_level: dict = None, paper_kind: str = "written"
) -> list:
    """
    Exam papers only (Phase 5 fixes from browser testing, item 5): a cheap
    lexical sanity check run on every freshly-written paper question,
    BEFORE the slower Gemini-based checker in _check_and_fix_bloom_levels
    gets a turn. bloom.classify_by_verbs() catches the writer having
    opened a question with a verb that unambiguously belongs to a
    different level than the slot it was written for -- e.g. "Propose a
    new scheme..." (a Create verb) landing in an Apply slot. A caught
    question is regenerated ONCE, with an extra prompt instruction naming
    the exact verb to avoid (see _bloom_level_prompt_block's avoid_verbs);
    if nothing usable comes back (busy AI, or the replacement doesn't
    survive validation), the original question is kept as-is -- the
    Gemini-based checker downstream still gets a chance to catch/flag it.
    Mutates and returns `questions`.
    """
    mismatches = {}
    for i, q in enumerate(questions):
        verb_hit = bloom.classify_by_verbs(q["question"])
        if verb_hit["level"] and verb_hit["level"] != q["bloom_level"]:
            mismatches.setdefault(q["bloom_level"], []).append((i, verb_hit["matched"]))

    if not mismatches:
        return questions

    for level_key, entries in mismatches.items():
        verbs_to_avoid = sorted({verb for _, verb in entries})
        logger.info(
            "Paper question verb guard: %d question(s) at level '%s' opened with verb(s) %s "
            "typical of a different level -- regenerating once.",
            len(entries), level_key, verbs_to_avoid
        )
        try:
            replacements = _generate_and_validate_questions(
                topic, top_pages, {level_key: len(entries)}, drop_log, recent_questions,
                marks_by_level, paper_kind, avoid_verbs={level_key: verbs_to_avoid}
            )
        except llm.LLMUnavailableError:
            replacements = []

        for (i, _verb), replacement in zip(entries, replacements):
            replacement["id"] = questions[i]["id"]
            questions[i] = replacement

    return questions


NEAR_DUPLICATE_JACCARD_THRESHOLD = 0.5
_WORD_PATTERN = re.compile(r"[a-z0-9]+")


def _normalized_words(text: str) -> set:
    return set(_WORD_PATTERN.findall(text.lower()))


def _jaccard_similarity(text_a: str, text_b: str) -> float:
    words_a = _normalized_words(text_a)
    words_b = _normalized_words(text_b)
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


def _drop_near_duplicates(questions: list, drop_log: list = None) -> list:
    """
    _validate_quiz_questions already drops *exact*-text duplicates; this
    catches near-identical rewordings it wouldn't, by comparing every pair
    of questions' word-overlap (Jaccard similarity on normalized text).
    For any pair at or above NEAR_DUPLICATE_JACCARD_THRESHOLD, drops the
    LATER one -- the caller's existing top-up mechanism (_top_up_missing_levels)
    naturally refills whichever level that leaves short.
    """
    dropped_indices = set()
    for i in range(len(questions)):
        if i in dropped_indices:
            continue
        for j in range(i + 1, len(questions)):
            if j in dropped_indices:
                continue
            similarity = _jaccard_similarity(questions[i]["question"], questions[j]["question"])
            if similarity >= NEAR_DUPLICATE_JACCARD_THRESHOLD:
                dropped_indices.add(j)
                if drop_log is not None:
                    drop_log.append("too similar to another question in this quiz")

    return [q for idx, q in enumerate(questions) if idx not in dropped_indices]


# Phase 5B quality fix: a paper question must actually be grounded in the
# ONE source window it was written for -- caught a real failure where a
# slot labelled against one document ended up asking about a completely
# different topic (symmetric/asymmetric encryption on a CRC/Hamming-only
# document). Simple lexical overlap, not a semantic check: deliberately
# cheap and deterministic, no extra embedding/LLM call per question.
GROUNDING_MIN_SHARED_TERMS = 2
# Common words long enough to survive the >=4-letter filter below but
# useless as grounding evidence (generic instruction/question vocabulary
# rather than source-specific facts).
_GROUNDING_STOPWORDS = frozenset({
    "what", "which", "that", "this", "these", "those", "with", "from",
    "into", "about", "over", "after", "before", "between", "during",
    "each", "some", "any", "all", "most", "more", "other", "same", "such",
    "also", "using", "used", "according", "based", "explain", "explains",
    "describe", "describes", "define", "defines", "identify", "identifies",
    "discuss", "discusses", "compare", "compares", "contrast", "contrasts",
    "evaluate", "evaluates", "analyze", "analyzes", "design", "designs",
    "propose", "proposes", "student", "students", "question", "answer",
    "source", "sources", "text", "following", "above", "below", "exam",
    "marks", "point", "points", "scheme",
})


def _grounding_terms(text: str) -> set:
    return {
        w for w in _normalized_words(text)
        if len(w) >= 4 and w not in _GROUNDING_STOPWORDS
    }


def _is_grounded(question: dict, window) -> bool:
    """
    True if `question`'s own text + correct answer share at least
    GROUNDING_MIN_SHARED_TERMS non-generic terms (4+ letters) with the
    specific source window it was written for. Never raises -- an empty/
    missing window or question text just fails the check (not grounded).
    """
    combined = f"{question.get('question', '')} {question.get('correctAnswer', '')}"
    question_terms = _grounding_terms(combined)
    source_terms = _grounding_terms(getattr(window, "content", "") or "")
    return len(question_terms & source_terms) >= GROUNDING_MIN_SHARED_TERMS


def _filter_grounded(questions: list, slot_windows: list, drop_log: list = None) -> list:
    """
    Drops any question whose self-reported source_index points at a window
    (within `slot_windows`, the SAME list _build_quiz_prompt's numbered
    sources were built from) it isn't actually grounded in -- see
    _is_grounded. _validate_quiz_questions already guarantees source_index
    is a valid 1-based index into that same list, so every question here
    has a real window to check against. Dropped questions are logged as
    "not grounded in the slot's document" so the caller's existing top-up
    mechanism naturally refills whichever slot that leaves empty.
    """
    kept = []
    for q in questions:
        window = slot_windows[q["source_index"] - 1][1]
        if _is_grounded(q, window):
            kept.append(q)
        elif drop_log is not None:
            drop_log.append("not grounded in the slot's document")
    return kept


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
    # from its pages/chunks) -- delete them explicitly. Any paper slot that
    # pointed at one of these questions becomes empty (not deleted) --
    # the paper's status is untouched, the slot just shows as unfilled.
    doomed_question_ids = [
        qid for (qid,) in db.query(Question.id).filter(Question.book_id == book_id).all()
    ]
    if doomed_question_ids:
        db.query(PaperQuestion).filter(PaperQuestion.question_id.in_(doomed_question_ids)).update(
            {PaperQuestion.question_id: None}, synchronize_session=False
        )
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

    _log_activity(db, current_user.id, "tool", meta={"tool": "semantic_search"})

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
        _log_activity(db, current_user.id, "query")
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
        _log_activity(db, current_user.id, "query")
        return {
            "question": question,
            "answer": "The uploaded documents do not contain enough information for this question.",
            "sources": []
        }

    _log_activity(db, current_user.id, "query")
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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
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

    _log_activity(db, current_user.id, "tool", meta={"tool": "summarize"})
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

    _log_activity(db, current_user.id, "tool", meta={"tool": "flashcards"})

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


def _validate_quiz_questions(
    raw_questions, num_sources: int, requested_levels: list, drop_log: list = None,
    marks_by_level: dict = None, paper_kind: str = "written"
):
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

    `marks_by_level` (Phase 5B exam papers only -- {level: exact marks}):
    when a question's bloom_level is a key in this dict, its type is
    checked against paper.allowed_types_for_slot (marks-aware) instead of
    the level's full type list, and it must additionally carry `marks`
    equal to that exact value and a `marking_scheme` (list of {point,
    marks}) whose marks sum to exactly that value. Omitted entirely
    (marks_by_level=None, the plain-quiz-question default) skips all of
    this and behaves exactly as before.

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
        slot_marks = (marks_by_level or {}).get(bloom_level)
        if slot_marks is not None:
            if q_type not in paper.allowed_types_for_slot(bloom_level, slot_marks, paper_kind):
                _drop(f"type '{q_type}' not allowed for '{bloom_level}' at {slot_marks} marks")
                continue
        elif q_type not in bloom.get_level(bloom_level).question_types:
            _drop(f"type '{q_type}' not allowed for '{bloom_level}'")
            continue

        marking_scheme = None
        if slot_marks is not None:
            marks_value = q.get("marks")
            if marks_value != slot_marks:
                _drop(f"marks {marks_value!r} does not match the required {slot_marks}")
                continue
            raw_scheme = q.get("marking_scheme")
            if not isinstance(raw_scheme, list) or not raw_scheme:
                _drop("missing or empty marking_scheme")
                continue
            cleaned_scheme = []
            scheme_total = 0
            scheme_ok = True
            for point in raw_scheme:
                if (
                    not isinstance(point, dict)
                    or not isinstance(point.get("point"), str) or not point["point"].strip()
                    or not isinstance(point.get("marks"), int)
                ):
                    scheme_ok = False
                    break
                cleaned_scheme.append({"point": point["point"].strip(), "marks": point["marks"]})
                scheme_total += point["marks"]
            if not scheme_ok:
                _drop("marking_scheme entries are malformed")
                continue
            if scheme_total != slot_marks:
                _drop(f"marking_scheme marks sum to {scheme_total}, not {slot_marks}")
                continue
            marking_scheme = cleaned_scheme

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
        if slot_marks is not None:
            cleaned["marks"] = slot_marks
            cleaned["marking_scheme"] = marking_scheme
        valid.append(cleaned)

    return valid


def _generate_quiz_with_gemini(
    topic: str, top_pages, level_counts: dict, drop_log: list = None, recent_questions: list = None
):
    """
    Asks Gemini for a full quiz as JSON (via _generate_and_validate_questions)
    and returns the cleaned question list on success, or None if fewer than
    3 valid questions remain even after one retry -- the caller falls back
    to generate_quiz() (the local hybrid generator) in that case. Raises
    NotImplementedError for LLM_PROVIDER=local and llm.LLMUnavailableError
    on a persistently unavailable provider, same as llm.generate_json()
    itself. Does not run the Bloom level check or the missing-level top-up
    -- those are the caller's job (_check_and_fix_bloom_levels,
    _top_up_missing_levels), since they need the final assembled list, not
    whatever a single attempt happens to be.
    """
    for _attempt in range(2):  # one retry if the first pass yields too few valid questions
        questions = _generate_and_validate_questions(topic, top_pages, level_counts, drop_log, recent_questions)
        if len(questions) >= 3:
            return questions

    return None


def _top_up_missing_levels(
    questions: list, topic: str, top_pages, level_counts: dict, drop_log: list = None, recent_questions: list = None,
    marks_by_level: dict = None, paper_kind: str = "written"
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

    `marks_by_level`/`paper_kind` (Phase 5B exam papers only): forwarded to
    both the top-up generation and its own Bloom check, so topped-up
    questions still carry marks/marking_scheme.
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
        topped_up = _generate_and_validate_questions(
            topic, top_pages, missing_counts, drop_log, recent_questions, marks_by_level, paper_kind
        )
    except llm.LLMUnavailableError:
        topped_up = []

    topped_up = _check_and_fix_bloom_levels(
        topped_up, topic, top_pages, drop_log, recent_questions, marks_by_level, paper_kind
    )

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
RECENT_QUESTIONS_LIMIT = 15


def _recent_question_texts(db: Session, user_id: int, topic: str, limit: int = RECENT_QUESTIONS_LIMIT) -> list:
    """
    This user's own up-to-`limit` most recently saved question *texts* for
    `topic` (case-insensitive match), newest first -- handed to the
    generation prompt as "don't repeat these". Scoped to user_id, so
    another user's questions for the same topic never appear here. Only
    the question text is read, never the answer/explanation.
    """
    rows = (
        db.query(Question.question)
        .filter(Question.user_id == user_id, Question.topic.ilike(_escape_like(topic), escape="\\"))
        .order_by(Question.created_at.desc())
        .limit(limit)
        .all()
    )
    return [row[0] for row in rows]


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
    recent_questions = _recent_question_texts(db, current_user.id, topic)

    try:
        questions = _generate_quiz_with_gemini(topic, top_pages, level_counts, drop_log, recent_questions)
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
        questions = _check_and_fix_bloom_levels(questions, topic, top_pages, drop_log, recent_questions)
        questions = _drop_near_duplicates(questions, drop_log)
        questions, warnings = _top_up_missing_levels(
            questions, topic, top_pages, level_counts, drop_log, recent_questions
        )
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

    _log_activity(db, current_user.id, "quiz_generated")

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


def _question_papers_using(db: Session, question_id: int) -> list:
    """
    Papers that currently have a slot pointing at this question (Phase 5
    fixes from browser testing, item 9a/9c) -- a question swapped or
    regenerated out of every paper slot that once held it just comes back
    empty here, it's never deleted or specially marked; a question used in
    several papers at once (the same bank question swapped into more than
    one) lists every one of them.
    """
    rows = (
        db.query(Paper.id, Paper.title)
        .join(PaperQuestion, PaperQuestion.paper_id == Paper.id)
        .filter(PaperQuestion.question_id == question_id)
        .distinct()
        .all()
    )
    return [{"paper_id": pid, "title": title} for pid, title in rows]


def _question_summary(db: Session, question: Question) -> dict:
    level = bloom.get_level(question.bloom_level)
    # origin: a question written for a paper slot always carries `marks`
    # (Phase 5B); a plain quiz question never does -- see Question's
    # docstring in models.py. used_in_papers is about CURRENT usage, which
    # can differ from origin (a paper-origin question swapped/regenerated
    # out of every slot that held it is "paper"-origin but used nowhere).
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
        "marks": question.marks,
        "marking_scheme": json.loads(question.marking_scheme) if question.marking_scheme else None,
        "created_at": question.created_at.isoformat(),
        "source": _question_source(db, question),
        "origin": "paper" if question.marks is not None else "quiz",
        "used_in_papers": _question_papers_using(db, question.id),
    }


def _get_owned_question_or_404(db: Session, question_id: int, user_id: int) -> Question:
    question = db.query(Question).filter(
        Question.id == question_id,
        Question.user_id == user_id
    ).first()

    if not question:
        raise HTTPException(status_code=404, detail="Question not found")

    return question


QUESTION_SOURCE_FILTERS = ("quiz", "paper", "unused")


@app.get("/questions")
def list_questions(
    level: str | None = None,
    topic: str | None = None,
    check: str | None = None,
    source: str | None = None,
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    level_keys = None
    if level is not None and level.strip():
        level_keys = [key.strip() for key in level.split(",") if key.strip()]
        for key in level_keys:
            if not bloom.is_valid_level(key):
                raise HTTPException(status_code=422, detail=f"Unknown Bloom's level: '{key}'")
    if check is not None and check not in QUESTION_CHECK_STATUSES:
        raise HTTPException(status_code=422, detail=f"Unknown check status: '{check}'")
    if source is not None and source not in QUESTION_SOURCE_FILTERS:
        raise HTTPException(status_code=422, detail=f"Unknown source filter: '{source}'")
    if not (1 <= limit <= 200):
        raise HTTPException(status_code=422, detail="limit must be between 1 and 200.")
    if offset < 0:
        raise HTTPException(status_code=422, detail="offset must be >= 0.")

    query = db.query(Question).filter(Question.user_id == current_user.id)
    if level_keys:
        query = query.filter(Question.bloom_level.in_(level_keys))
    if check is not None:
        query = query.filter(Question.bloom_check == check)
    if topic is not None and topic.strip():
        query = query.filter(Question.topic.ilike(f"%{_escape_like(topic)}%", escape="\\"))
    # source: "quiz"/"paper" is ORIGIN (whether this question was ever written
    # for a paper slot, via the `marks` column -- see models.Question's
    # docstring); "unused" is CURRENT usage (no paper_questions row points at
    # it right now), which applies to quiz-origin questions too (never used
    # in a paper to begin with) as well as a paper-origin one swapped out of
    # every slot that held it (Phase 5 fixes from browser testing, item 9a).
    if source == "quiz":
        query = query.filter(Question.marks.is_(None))
    elif source == "paper":
        query = query.filter(Question.marks.isnot(None))
    elif source == "unused":
        used_subquery = db.query(PaperQuestion.question_id).filter(PaperQuestion.question_id.isnot(None))
        query = query.filter(~Question.id.in_(used_subquery))

    total = query.count()
    rows = query.order_by(Question.created_at.desc()).offset(offset).limit(limit).all()

    return {"total": total, "items": [_question_summary(db, row) for row in rows]}


@app.get("/questions/summary")
def get_questions_summary(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    base_query = db.query(Question).filter(Question.user_id == current_user.id)

    total = base_query.count()
    flagged = base_query.filter(Question.bloom_check == "flagged").count()

    by_level = {
        level_key: count
        for level_key, count in db.query(Question.bloom_level, func.count(Question.id))
        .filter(Question.user_id == current_user.id)
        .group_by(Question.bloom_level)
        .all()
    }

    return {"total": total, "by_level": by_level, "flagged": flagged}


class QuestionUpdateRequest(BaseModel):
    question: str | None = None
    options: list | None = None
    correct_answer: str | None = None
    explanation: str | None = None
    bloom_level: str | None = None
    marking_scheme: list | None = None


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

    if payload.marking_scheme is not None:
        if question.marks is None:
            raise HTTPException(
                status_code=400, detail="This question has no marks to check a marking scheme against."
            )
        if not isinstance(payload.marking_scheme, list) or not payload.marking_scheme:
            raise HTTPException(status_code=400, detail="marking_scheme must be a non-empty list.")
        cleaned_scheme = []
        scheme_total = 0
        for point in payload.marking_scheme:
            if (
                not isinstance(point, dict)
                or not isinstance(point.get("point"), str) or not point["point"].strip()
                or not isinstance(point.get("marks"), int)
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Each marking_scheme point needs a 'point' string and an integer 'marks'."
                )
            cleaned_scheme.append({"point": point["point"].strip(), "marks": point["marks"]})
            scheme_total += point["marks"]
        if scheme_total != question.marks:
            raise HTTPException(
                status_code=400,
                detail=f"marking_scheme marks must sum to {question.marks} (got {scheme_total})."
            )
        question.marking_scheme = json.dumps(cleaned_scheme)

    db.commit()
    return _question_summary(db, question)


@app.delete("/questions/unused")
def delete_unused_questions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Deletes every one of the caller's bank questions not currently
    referenced by any paper slot (Phase 5 fixes from browser testing, item
    9b) -- the Question Bank's "Delete unused questions" cleanup button.
    Quiz questions (never used in a paper to begin with) and a paper
    question swapped/regenerated out of every slot that held it are both
    "unused" by this definition; a question still used in even one paper
    (including an old draft) is left untouched. Declared before
    DELETE /questions/{question_id} so "unused" is never parsed as an id.
    """
    used_subquery = db.query(PaperQuestion.question_id).filter(PaperQuestion.question_id.isnot(None))
    unused = db.query(Question).filter(
        Question.user_id == current_user.id, ~Question.id.in_(used_subquery)
    ).all()
    deleted = len(unused)
    for question in unused:
        db.delete(question)
    db.commit()
    return {"deleted": deleted}


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


class RegenerateQuestionRequest(BaseModel):
    type: str | None = None


@app.post("/questions/{question_id}/regenerate")
@limiter.limit("10/minute", key_func=_user_rate_limit_key)
def regenerate_question(
    request: Request,
    question_id: int,
    payload: RegenerateQuestionRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Writes ONE new question for the same topic and the same bloom_level as
    `question_id` (optionally a different type, still allowed at that
    level), reusing the exact same sources-based generation and Bloom
    level check as /generate-quiz (Phase 4B), and replaces the row's
    content in place -- same id, same topic, same bloom_level.
    """
    question = _get_owned_question_or_404(db, question_id, current_user.id)
    level = bloom.get_level(question.bloom_level)

    new_type = payload.type if payload and payload.type else question.type
    if new_type not in level.question_types:
        raise HTTPException(
            status_code=400,
            detail=f"Question type '{new_type}' is not valid for Bloom level '{question.bloom_level}'."
        )

    top_pages = _retrieve_or_503(db, current_user.id, question.topic, top_k=5, apply_threshold=True)
    if not top_pages:
        raise HTTPException(
            status_code=400,
            detail="Not enough information found in uploaded documents to regenerate this question."
        )

    recent_questions = _recent_question_texts(db, current_user.id, question.topic)
    drop_log = []
    level_counts = {question.bloom_level: 1}

    # First attempt asks for `new_type`; if that comes up empty, one retry
    # tries a *different* allowed type for this level (if the level has
    # one) rather than just asking the same question again and hoping.
    fallback_types = [t for t in level.question_types if t != new_type]
    types_to_try = [new_type] + fallback_types[:1]

    try:
        candidates = []
        for candidate_type in types_to_try:
            batch = _generate_and_validate_questions(
                question.topic, top_pages, level_counts, drop_log, recent_questions
            )
            candidates = [q for q in batch if q["type"] == candidate_type]
            if candidates:
                new_type = candidate_type
                break
    except llm.LLMUnavailableError:
        raise HTTPException(status_code=503, detail=AI_BUSY_DETAIL)

    if drop_log:
        reason_counts = Counter(drop_log)
        logger.info(
            "Question regeneration for topic '%s': %s",
            question.topic, "; ".join(f"dropped {count}: {reason}" for reason, count in reason_counts.items())
        )

    if not candidates:
        raise HTTPException(
            status_code=422,
            detail="Couldn't write a different question on this topic. Try another question type, or edit this one yourself."
        )

    new_question = candidates[0]
    new_question["id"] = 1
    [new_question] = _check_and_fix_bloom_levels(
        [new_question], question.topic, top_pages, drop_log, recent_questions
    )

    _, book_id, page_id = _resolve_question_source(db, top_pages, new_question.get("source_index"))

    question.type = new_question["type"]
    question.bloom_check = new_question["bloom_check"]
    question.detected_level = new_question.get("detected_level")
    question.question = new_question["question"]
    question.options = json.dumps(new_question["options"]) if new_question.get("options") else None
    question.correct_answer = new_question["correctAnswer"]
    question.explanation = new_question["explanation"]
    question.book_id = book_id
    question.page_id = page_id

    db.commit()
    return _question_summary(db, question)


# -----------------------------
# PHASE 5B: EXAM PAPER GENERATION
# -----------------------------
class _SourceWindow:
    """
    Adapts a contiguous block of a book's Page rows into the single-object
    shape _build_quiz_prompt/_resolve_question_source/_classify_questions_
    with_gemini already expect from a (score, page) tuple's page half --
    this lets paper generation reuse every one of those functions
    completely unmodified. `.content` joins the block's chunks so the
    model sees them as one numbered source; `.book_id`/`.id`/`.page_number`
    all come from the block's first page (good enough for "where did this
    come from" attribution -- paper slots don't need per-paragraph
    precision).
    """
    def __init__(self, pages: list):
        self.pages = pages
        self.content = "\n\n".join(p.content for p in pages)
        self.book_id = pages[0].book_id
        self.id = pages[0].id
        self.page_number = pages[0].page_number


def _assign_source_windows(db: Session, book_id: int, num_slots: int, window_size: int = 3) -> list:
    """
    Splits a book's Page rows (ordered by page_number, chunk_number) into
    `num_slots` non-overlapping contiguous blocks spread evenly through the
    whole document, and wraps each in a _SourceWindow of up to
    `window_size` pages -- so a paper's questions are grounded across the
    WHOLE document instead of just the most topically-relevant chunks
    (unlike plain quiz generation's retrieve()), and no chunk is used
    twice within one call. Shrinks each window automatically when a block
    has fewer than `window_size` pages. Returns exactly `num_slots`
    windows (empty list if the book has no indexed pages at all).
    """
    pages = (
        db.query(Page)
        .filter(Page.book_id == book_id)
        .order_by(Page.page_number, Page.chunk_number)
        .all()
    )
    if not pages or num_slots <= 0:
        return []

    total = len(pages)
    boundaries = [round(i * total / num_slots) for i in range(num_slots + 1)]

    windows = []
    for i in range(num_slots):
        start, end = boundaries[i], boundaries[i + 1]
        if end <= start:
            start = min(start, total - 1)
            end = start + 1
        end = min(end, total)
        block = pages[start:end]

        if len(block) > window_size:
            center = len(block) // 2
            offset = max(0, center - window_size // 2)
            block = block[offset:offset + window_size]

        windows.append(_SourceWindow(block))

    return windows


def _pattern_to_dict(pattern: paper.Pattern) -> dict:
    return {
        "key": pattern.key,
        "name": pattern.name,
        "duration_minutes": pattern.duration_minutes,
        "paper_kind": pattern.paper_kind,
        "sections": [
            {
                "key": s.key, "title": s.title, "instructions": s.instructions,
                "marks_each": s.marks_each, "num_questions": s.num_questions,
                "choice": s.choice, "attempt": s.attempt,
            }
            for s in pattern.sections
        ],
    }


def _pattern_from_dict(d: dict) -> paper.Pattern:
    sections = tuple(
        paper.Section(
            key=s["key"],
            title=s.get("title", s["key"]),
            instructions=s.get("instructions", ""),
            marks_each=s["marks_each"],
            num_questions=s["num_questions"],
            choice=s.get("choice", "none"),
            attempt=s.get("attempt"),
        )
        for s in d.get("sections", [])
    )
    return paper.Pattern(
        key=d.get("key", "custom"),
        name=d.get("name", "Custom Pattern"),
        duration_minutes=d.get("duration_minutes", 60),
        sections=sections,
        paper_kind=d.get("paper_kind", "written"),
    )


def _book_weight(db: Session, book_id: int) -> int:
    return db.query(Page).filter(Page.book_id == book_id).count()


UNIT_WEIGHTING_MODES = ("equal", "by_size", "custom")


def _resolve_unit_weights(db: Session, books: list, unit_weighting: str, custom_weights: dict | None) -> dict:
    """
    Maps each selected book to the weight its unit gets in paper.plan_paper,
    per `unit_weighting`:
      - "equal" (default): every unit weighted 1 -- the plan spreads marks
        evenly across documents regardless of how much either one has.
      - "by_size": weighted by chunk count (what Phase 5B originally always
        did) -- a bigger document earns a bigger share of the paper.
      - "custom": the teacher's own `custom_weights` ({book_id: weight},
        JSON string keys), which must cover every selected book with a
        positive number.
    Returns {book_id: weight}. Raises HTTPException(422) for a bad/missing
    custom weight.
    """
    if unit_weighting == "custom":
        weights = {}
        for book in books:
            raw = (custom_weights or {}).get(str(book.id))
            if not isinstance(raw, (int, float)) or raw <= 0:
                raise HTTPException(
                    status_code=422,
                    detail=[f"unit_weights is missing a positive weight for book '{book.name}' (id {book.id})."]
                )
            weights[book.id] = float(raw)
        return weights

    if unit_weighting == "by_size":
        return {book.id: (_book_weight(db, book.id) or 1) for book in books}

    return {book.id: 1 for book in books}  # "equal"


def _slot_label(slot: "PaperQuestion") -> str:
    option_suffix = f" ({slot.option})" if slot.option else ""
    return f"Section '{slot.section}' question {slot.number}{option_suffix}"


class PaperCreateRequest(BaseModel):
    title: str
    subject: str | None = None
    pattern_key: str | None = None
    pattern: dict | None = None
    bloom_preset: str | None = None
    bloom_targets: dict | None = None
    book_ids: list[int]
    duration_minutes: int | None = None
    unit_weighting: str | None = None  # "equal" (default) | "by_size" | "custom"
    unit_weights: dict[str, float] | None = None  # required when unit_weighting == "custom"


class PaperUpdateRequest(BaseModel):
    title: str | None = None
    subject: str | None = None
    duration_minutes: int | None = None


class SlotSwapRequest(BaseModel):
    question_id: int


def _get_owned_paper_or_404(db: Session, paper_id: int, user_id: int) -> Paper:
    paper_row = db.query(Paper).filter(Paper.id == paper_id, Paper.user_id == user_id).first()
    if not paper_row:
        raise HTTPException(status_code=404, detail="Paper not found")
    return paper_row


def _get_paper_slot_or_404(db: Session, paper_id: int, slot_id: int) -> PaperQuestion:
    slot = db.query(PaperQuestion).filter(
        PaperQuestion.id == slot_id, PaperQuestion.paper_id == paper_id
    ).first()
    if not slot:
        raise HTTPException(status_code=404, detail="Slot not found")
    return slot


def _paper_summary(paper_row: Paper) -> dict:
    # total_marks is what a student actually answers (paper.total_marks()
    # -- e.g. 50 for mid_sem_50's "any 4 of 5" section), not marks_on_paper
    # (55, every printed question). Phase 5E EXTRA fix: this used to sum
    # marks_on_paper and call it total_marks.
    pattern_obj = _pattern_from_dict(json.loads(paper_row.pattern))
    return {
        "id": paper_row.id,
        "title": paper_row.title,
        "status": paper_row.status,
        "total_marks": paper.total_marks(pattern_obj),
        "created_at": paper_row.created_at.isoformat() if paper_row.created_at else None,
    }


def _compute_blueprint_actual(db: Session, paper_row: Paper) -> dict:
    """
    Marks per level and per unit from the questions actually saved into
    this paper's slots so far. A flagged question (the independent
    checker disagreed with its intended level) counts at its DETECTED
    level here, not the intended one it was planned/written for -- unlike
    blueprint_target/blueprint_planned, which both describe the PLAN and
    so always use the intended level, "actual" is meant to show the real
    mix of what's really on the paper right now (Phase 5 fixes from
    browser testing, item 3); the mismatch itself is still listed in
    `warnings` separately. Only one option of an either_or pair is
    counted (marks_on_paper convention), same as blueprint_target/
    blueprint_planned.
    """
    slots = (
        db.query(PaperQuestion)
        .filter(
            PaperQuestion.paper_id == paper_row.id,
            PaperQuestion.question_id.isnot(None),
            or_(PaperQuestion.option.is_(None), PaperQuestion.option == "a")
        )
        .all()
    )

    by_level = {level: 0 for level in bloom.level_keys()}
    by_unit = {}

    if slots:
        question_ids = [s.question_id for s in slots]
        questions_by_id = {
            q.id: q for q in db.query(Question).filter(Question.id.in_(question_ids)).all()
        }
        for slot in slots:
            question = questions_by_id.get(slot.question_id)
            if not question:
                continue
            effective_level = (
                question.detected_level
                if question.bloom_check == "flagged" and question.detected_level
                else question.bloom_level
            )
            by_level[effective_level] = by_level.get(effective_level, 0) + slot.marks
            if slot.book_id is not None:
                by_unit[slot.book_id] = by_unit.get(slot.book_id, 0) + slot.marks

    return {"by_level": by_level, "by_unit": by_unit}


def _paper_detail(db: Session, paper_row: Paper) -> dict:
    slots = (
        db.query(PaperQuestion)
        .filter(PaperQuestion.paper_id == paper_row.id)
        .order_by(PaperQuestion.position)
        .all()
    )
    question_ids = [s.question_id for s in slots if s.question_id]
    questions_by_id = {
        q.id: q for q in db.query(Question).filter(Question.id.in_(question_ids)).all()
    } if question_ids else {}

    slot_items = [
        {
            "id": slot.id,
            "section": slot.section,
            "number": slot.number,
            "option": slot.option,
            "marks": slot.marks,
            "bloom_level": slot.bloom_level or None,
            "book_id": slot.book_id,
            "question": _question_summary(db, questions_by_id[slot.question_id])
            if slot.question_id in questions_by_id else None,
        }
        for slot in slots
    ]

    plan = json.loads(paper_row.plan)
    pattern_dict = json.loads(paper_row.pattern)
    # marks_on_paper (marks_on_paper convention): the basis plan_paper()
    # itself used for blueprint_target's percentages -- kept as-is here.
    # total_marks (what a student actually answers, e.g. 50 not 55 for
    # mid_sem_50) is exposed separately below for display (Phase 5E EXTRA).
    paper_total = sum(s["marks"] for s in plan if s.get("option") in (None, "a"))
    bloom_targets = json.loads(paper_row.bloom_targets)

    blueprint_target = {
        level: round((bloom_targets.get(level, 0) / 100.0) * paper_total, 2)
        for level in bloom.level_keys()
    }
    blueprint_planned_by_level = {level: 0 for level in bloom.level_keys()}
    blueprint_planned_by_unit = {}
    for s in plan:
        if s.get("option") not in (None, "a"):
            continue
        if s.get("bloom_level"):
            blueprint_planned_by_level[s["bloom_level"]] = (
                blueprint_planned_by_level.get(s["bloom_level"], 0) + s["marks"]
            )
        if s.get("unit_id") is not None:
            blueprint_planned_by_unit[s["unit_id"]] = blueprint_planned_by_unit.get(s["unit_id"], 0) + s["marks"]

    return {
        "id": paper_row.id,
        "title": paper_row.title,
        "subject": paper_row.subject,
        "pattern": pattern_dict,
        "total_marks": paper.total_marks(_pattern_from_dict(pattern_dict)),
        "bloom_targets": bloom_targets,
        "book_ids": json.loads(paper_row.book_ids),
        "unit_weights": json.loads(paper_row.unit_weights) if paper_row.unit_weights else {},
        "status": paper_row.status,
        "error": paper_row.error,
        "waiting_for_ai": paper_row.waiting_for_ai,
        "progress_done": paper_row.progress_done,
        "progress_total": paper_row.progress_total,
        "warnings": json.loads(paper_row.warnings) if paper_row.warnings else [],
        "plan": plan,
        "slots": slot_items,
        "blueprint_target": blueprint_target,
        "blueprint_planned": {"by_level": blueprint_planned_by_level, "by_unit": blueprint_planned_by_unit},
        "blueprint_actual": _compute_blueprint_actual(db, paper_row),
        "created_at": paper_row.created_at.isoformat() if paper_row.created_at else None,
        "updated_at": paper_row.updated_at.isoformat() if paper_row.updated_at else None,
    }


def _extract_text_for_pattern_import(file_path: str, filename: str) -> str:
    """
    Extracts plain text from an uploaded file for POST /papers/import-
    pattern -- never creates a Book/DocumentPage/Page row (this file is
    never added to the user's documents or search index), just returns
    the combined text so it can be handed to Gemini and the temp file
    deleted right after. Reuses document_parsers.py for text extraction
    and the same Gemini-vision OCR fallback jobs.py's background pipeline
    uses for a low-text PDF page or a bare photo/scan -- run synchronously
    here (no Book to track progress against, and the whole import has to
    finish within one request) rather than as a background job.
    """
    ext = os.path.splitext(filename)[1].lower()

    if ext in ALLOWED_IMAGE_EXTENSIONS:
        with open(file_path, "rb") as f:
            raw_bytes = f.read()
        prepared = ocr.prepare_image_for_ocr(raw_bytes)
        return llm.read_image(prepared, mime_type="image/jpeg")

    if ext == ".pdf":
        with open(file_path, "rb") as f:
            pages_data = document_parsers.parse_pdf(f)

        low_text_pages = [p for p in pages_data if len(p["content"].strip()) < jobs.OCR_MIN_CHARS]
        if len(low_text_pages) > MAX_OCR_PAGES:
            raise ValueError(
                f"This document has {len(low_text_pages)} scanned or low-text pages, "
                f"over the {MAX_OCR_PAGES}-page OCR limit."
            )

        texts = []
        for page in pages_data:
            content = page["content"]
            if len(content.strip()) >= jobs.OCR_MIN_CHARS:
                texts.append(content)
                continue
            rendered = ocr.render_pdf_page_to_png(file_path, page_index=page["page_number"] - 1)
            prepared = ocr.prepare_image_for_ocr(rendered)
            texts.append(llm.read_image(prepared, mime_type="image/jpeg"))
        return "\n\n".join(texts)

    with open(file_path, "rb") as f:
        pages_data = document_parsers.parse_document(f, filename)
    return "\n\n".join(p["content"] for p in pages_data)


def _build_pattern_import_prompt(text: str) -> str:
    return (
        "The following is the text of an old/existing exam question paper "
        "(it may have been OCR'd from a scan or photo, so formatting can be "
        "rough). Identify its structure and transcribe its questions exactly "
        "as written -- do not invent or rephrase anything.\n\n"
        "Return:\n"
        "- sections: one entry per printed section (e.g. 'Section A'), each "
        "with title, instructions (its printed instruction line, if any), "
        "marks_each (every question in that section is worth the same marks), "
        "num_questions (how many questions are printed in that section), "
        "choice ('none' if every question must be answered, 'any_n' if the "
        "student only attempts some of them, 'either_or' if each numbered "
        "question offers two alternative options worth the same marks), and "
        "attempt (only when choice is 'any_n': how many of num_questions the "
        "student actually answers).\n"
        "- duration_minutes and total_marks for the whole paper, if stated "
        "anywhere on it.\n"
        "- questions: every individual question found, each with section "
        "(matching one of the section titles above exactly), number (its "
        "printed question number), option ('a' or 'b' if it's one half of an "
        "either/or pair, omitted otherwise), text (the question exactly as "
        "written), and marks.\n\n"
        f"Paper text:\n{text}"
    )


def _pattern_from_import_result(result: dict) -> paper.Pattern:
    raw_sections = result.get("sections") or []
    if not raw_sections:
        raise ValueError("no sections found")

    sections = tuple(
        paper.Section(
            key=_section_key_for_index(idx),
            title=(s.get("title") or f"Section {_section_key_for_index(idx)}").strip(),
            instructions=(s.get("instructions") or "").strip(),
            marks_each=int(s["marks_each"]),
            num_questions=int(s["num_questions"]),
            choice=s.get("choice") if s.get("choice") in paper.CHOICE_KINDS else "none",
            attempt=int(s["attempt"]) if s.get("choice") == "any_n" and s.get("attempt") else None,
        )
        for idx, s in enumerate(raw_sections)
    )

    return paper.Pattern(
        key="imported",
        name="Imported Pattern",
        duration_minutes=int(result.get("duration_minutes") or 60),
        sections=sections,
        paper_kind="written",
    )


def _section_key_for_index(idx: int) -> str:
    return chr(65 + idx) if idx < 26 else str(idx + 1)


def _estimate_bloom_mix(raw_questions: list):
    """
    Classifies an imported paper's questions with the existing (Phase 4B)
    level checker, in one batched call, and returns marks + percentage per
    level -- always clearly an ESTIMATE (the checker is independently
    guessing a level from question text alone, same as it does for a
    freshly-generated question, just with no "intended" level to compare
    against here). None if there are no questions to classify, or the
    checker is unavailable -- never blocks the import itself.
    """
    if not raw_questions:
        return None

    question_texts = [str(q.get("text") or "") for q in raw_questions]
    try:
        detected_levels = _classify_questions_with_gemini(question_texts)
    except llm.LLMUnavailableError:
        detected_levels = None
    if not detected_levels:
        return None

    marks_by_level = {level: 0 for level in bloom.level_keys()}
    for q, level in zip(raw_questions, detected_levels):
        if level and bloom.is_valid_level(level):
            marks_by_level[level] += int(q.get("marks") or 0)

    total = sum(marks_by_level.values())
    return {
        "estimated": True,
        "marks": marks_by_level,
        "percent": {
            level: (round((marks / total) * 100, 1) if total else 0)
            for level, marks in marks_by_level.items()
        },
    }


@app.post("/papers/import-pattern")
@limiter.limit("5/minute", key_func=_user_rate_limit_key)
def import_paper_pattern(
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user)
):
    """
    Reads an old/existing question paper (a document, or a single photo/
    scan) and extracts its section layout + questions with Gemini, for the
    "New paper" builder's custom pattern editor to start from. Never saved
    as a Book or added to the user's documents/search index -- the
    uploaded file is deleted again once its text has been read, whether
    this succeeds or fails.
    """
    filename = file.filename or ""
    ext = os.path.splitext(filename)[1].lower()
    is_image = ext in ALLOWED_IMAGE_EXTENSIONS

    if is_image:
        file.file.seek(0, os.SEEK_END)
        size = file.file.tell()
        file.file.seek(0)
        if size > MAX_IMAGE_BYTES:
            raise HTTPException(
                status_code=413, detail=f"Image exceeds the maximum allowed size of {MAX_IMAGE_MB:g} MB"
            )
    elif ext in ALLOWED_UPLOAD_EXTENSIONS:
        _validate_document_upload(file)
    else:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported file extension '{ext}'. "
                f"Allowed: {', '.join(sorted(ALLOWED_UPLOAD_EXTENSIONS | ALLOWED_IMAGE_EXTENSIONS))}"
            )
        )

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            file.file.seek(0)
            shutil.copyfileobj(file.file, tmp)
            tmp_path = tmp.name

        try:
            text = _extract_text_for_pattern_import(tmp_path, filename)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except llm.LLMUnavailableError:
            raise HTTPException(status_code=503, detail=AI_BUSY_DETAIL)

        if not text or not text.strip():
            raise HTTPException(status_code=422, detail="Could not read any text from this file.")

        try:
            result = llm.generate_json(_build_pattern_import_prompt(text), schema=PATTERN_IMPORT_SCHEMA)
        except llm.LLMUnavailableError:
            raise HTTPException(status_code=503, detail=AI_BUSY_DETAIL)

        if not isinstance(result, dict) or not result.get("sections"):
            raise HTTPException(
                status_code=422,
                detail="Couldn't find a question paper layout in this file. Try a clearer scan or a different file."
            )

        try:
            pattern = _pattern_from_import_result(result)
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"Couldn't understand this paper's layout ({exc}).")

        pattern_errors = paper.validate_pattern(pattern)
        if pattern_errors:
            raise HTTPException(status_code=422, detail=pattern_errors)

        raw_questions = result.get("questions") or []

        return {
            "pattern": _pattern_to_dict(pattern),
            "questions": raw_questions,
            "bloom_mix": _estimate_bloom_mix(raw_questions),
        }
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


@app.get("/paper-patterns")
def get_paper_patterns(current_user: User = Depends(get_current_user)):
    return {
        "patterns": [_pattern_to_dict(p) for p in paper.PRESET_PATTERNS.values()],
        "bloom_presets": paper.BLOOM_PRESETS,
    }


@app.post("/papers")
def create_paper(
    payload: PaperCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if payload.pattern_key:
        preset = paper.PRESET_PATTERNS.get(payload.pattern_key)
        if not preset:
            raise HTTPException(status_code=422, detail=[f"Unknown pattern_key '{payload.pattern_key}'."])
        pattern = preset
    elif payload.pattern:
        try:
            pattern = _pattern_from_dict(payload.pattern)
        except (KeyError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=[f"Invalid pattern: {exc}"])
    else:
        raise HTTPException(status_code=422, detail=["pattern_key or pattern is required."])

    if payload.duration_minutes:
        pattern = replace(pattern, duration_minutes=payload.duration_minutes)

    pattern_errors = paper.validate_pattern(pattern)
    if pattern_errors:
        raise HTTPException(status_code=422, detail=pattern_errors)

    if payload.bloom_preset:
        bloom_targets = paper.BLOOM_PRESETS.get(payload.bloom_preset)
        if not bloom_targets:
            raise HTTPException(status_code=422, detail=[f"Unknown bloom_preset '{payload.bloom_preset}'."])
    elif payload.bloom_targets:
        bloom_targets = payload.bloom_targets
    else:
        raise HTTPException(status_code=422, detail=["bloom_preset or bloom_targets is required."])

    bloom_errors = paper.validate_bloom_targets(bloom_targets)
    if bloom_errors:
        raise HTTPException(status_code=422, detail=bloom_errors)

    if not payload.book_ids:
        raise HTTPException(status_code=422, detail=["At least one book is required."])

    unit_weighting = payload.unit_weighting or "equal"
    if unit_weighting not in UNIT_WEIGHTING_MODES:
        raise HTTPException(status_code=422, detail=[f"Unknown unit_weighting '{unit_weighting}'."])
    if unit_weighting == "custom" and not payload.unit_weights:
        raise HTTPException(status_code=422, detail=["unit_weights is required when unit_weighting is 'custom'."])

    books = []
    for book_id in payload.book_ids:
        book = _get_owned_book_or_404(db, book_id, current_user.id)
        if book.status != "ready":
            raise HTTPException(status_code=422, detail=[f"Book '{book.name}' is not ready yet."])
        books.append(book)

    weights_by_book = _resolve_unit_weights(db, books, unit_weighting, payload.unit_weights)
    units = [{"id": book.id, "name": book.name, "weight": weights_by_book[book.id]} for book in books]

    result = paper.plan_paper(pattern, bloom_targets, units)

    paper_row = Paper(
        user_id=current_user.id,
        title=payload.title,
        subject=payload.subject,
        pattern=json.dumps(_pattern_to_dict(pattern)),
        bloom_targets=json.dumps(bloom_targets),
        book_ids=json.dumps(payload.book_ids),
        unit_weights=json.dumps({str(bid): w for bid, w in weights_by_book.items()}),
        plan=json.dumps(result["plan"]),
        status="draft",
        progress_done=0,
        progress_total=len(result["plan"]),
        warnings=json.dumps(result["warnings"]),
    )
    db.add(paper_row)
    db.flush()

    for position, slot in enumerate(result["plan"]):
        db.add(PaperQuestion(
            paper_id=paper_row.id,
            section=slot["section"],
            number=slot["number"],
            option=slot["option"],
            marks=slot["marks"],
            bloom_level=slot["bloom_level"] or "",
            book_id=slot["unit_id"],
            question_id=None,
            position=position,
        ))
    db.commit()
    db.refresh(paper_row)

    return _paper_detail(db, paper_row)


@app.get("/papers")
def list_papers(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    papers = db.query(Paper).filter(Paper.user_id == current_user.id).order_by(Paper.created_at.desc()).all()
    return [_paper_summary(p) for p in papers]


@app.get("/papers/{paper_id}")
def get_paper(paper_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    paper_row = _get_owned_paper_or_404(db, paper_id, current_user.id)
    return _paper_detail(db, paper_row)


@app.get("/papers/{paper_id}/status")
def get_paper_status(paper_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    paper_row = _get_owned_paper_or_404(db, paper_id, current_user.id)
    return {
        "id": paper_row.id,
        "status": paper_row.status,
        "error": paper_row.error,
        "waiting_for_ai": paper_row.waiting_for_ai,
        "progress_done": paper_row.progress_done,
        "progress_total": paper_row.progress_total,
    }


@app.patch("/papers/{paper_id}")
def update_paper(
    paper_id: int,
    payload: PaperUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    paper_row = _get_owned_paper_or_404(db, paper_id, current_user.id)

    if payload.title is not None:
        if not payload.title.strip():
            raise HTTPException(status_code=400, detail="title cannot be empty.")
        paper_row.title = payload.title.strip()

    if payload.subject is not None:
        paper_row.subject = payload.subject.strip() or None

    if payload.duration_minutes is not None:
        if payload.duration_minutes <= 0:
            raise HTTPException(status_code=400, detail="duration_minutes must be positive.")
        pattern_dict = json.loads(paper_row.pattern)
        pattern_dict["duration_minutes"] = payload.duration_minutes
        paper_row.pattern = json.dumps(pattern_dict)

    db.commit()
    return _paper_detail(db, paper_row)


@app.delete("/papers/{paper_id}")
def delete_paper(paper_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    paper_row = _get_owned_paper_or_404(db, paper_id, current_user.id)
    # PaperQuestions are deleted (this paper's slots), but the underlying
    # Questions stay in the user's bank -- a paper is just one arrangement
    # of bank questions, not their owner.
    db.query(PaperQuestion).filter(PaperQuestion.paper_id == paper_id).delete()
    db.delete(paper_row)
    db.commit()
    return {"status": "Paper deleted successfully"}


@app.post("/papers/{paper_id}/generate", status_code=202)
@limiter.limit("3/minute", key_func=_user_rate_limit_key)
def generate_paper_route(
    request: Request,
    paper_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    paper_row = _get_owned_paper_or_404(db, paper_id, current_user.id)

    if paper_row.status == "generating":
        raise HTTPException(status_code=400, detail="This paper is already generating.")

    paper_row.status = "generating"
    paper_row.error = None
    paper_row.waiting_for_ai = False
    db.commit()

    jobs.submit_paper_generation_job(paper_row.id)
    db.refresh(paper_row)

    return {"paper_id": paper_row.id, "status": paper_row.status}


PAPER_JOB_MAX_WAIT_SECONDS = float(os.getenv("PAPER_JOB_MAX_WAIT_SECONDS", "1800"))
_PAPER_BUSY_BASE_WAIT_SECONDS = 60.0
_PAPER_BUSY_MAX_WAIT_SECONDS = 300.0


def _make_patient_caller(db: Session, paper_row: Paper):
    """
    Returns a `call_patiently(call_fn)` closure for generate_paper: calls
    `call_fn()`, and on llm.LLMUnavailableError waits out the busy period
    instead of giving up immediately -- honouring a Retry-After header
    when the provider gave one, else a 60s wait that doubles on each
    further busy response (capped at 5 minutes), continuing until either
    `call_fn()` succeeds or the shared wait budget (PAPER_JOB_MAX_WAIT_
    SECONDS total, across every call this job makes, not reset per call)
    runs out, at which point it re-raises. While actually waiting,
    paper_row.waiting_for_ai is set True (and committed, so a concurrent
    GET /papers/{id}/status sees it) and cleared again once the wait ends.
    """
    state = {"remaining": PAPER_JOB_MAX_WAIT_SECONDS, "wait_seconds": _PAPER_BUSY_BASE_WAIT_SECONDS}

    def call_patiently(call_fn):
        while True:
            try:
                return call_fn()
            except llm.LLMUnavailableError as exc:
                this_wait = llm.get_retry_after_seconds(exc) or state["wait_seconds"]
                if this_wait > state["remaining"]:
                    raise
                state["remaining"] -= this_wait

                paper_row.waiting_for_ai = True
                db.commit()
                logger.info(
                    "Paper %d generation: AI busy, waiting %.0fs (%.0fs of budget left)",
                    paper_row.id, this_wait, state["remaining"]
                )
                time.sleep(this_wait)
                paper_row.waiting_for_ai = False
                db.commit()

                state["wait_seconds"] = min(state["wait_seconds"] * 2, _PAPER_BUSY_MAX_WAIT_SECONDS)

    return call_patiently


def _clear_slot_warnings(warnings: list, slot: PaperQuestion) -> list:
    """Drops any existing warning for `slot` -- its label prefix is stable regardless of content."""
    prefix = f"{_slot_label(slot)}:"
    return [w for w in warnings if not w.startswith(prefix)]


def _sync_slot_warning(warnings: list, slot: PaperQuestion, q: dict, intended_level: str) -> list:
    """
    The one place a slot's warning is kept in sync with its current
    question: drops any stale warning this slot already had (a previous
    "couldn't generate"/"classified as X"/"swapped in a Y question"), then
    adds a fresh "classified as X, not Y" warning if `q` came back
    flagged. Shared by generate_paper's bulk pass and the slot-level
    regenerate/swap routes so a warning never outlives the problem it
    described, and a newly-introduced one is reported the same way
    everywhere (Phase 5C EXTRA B).
    """
    warnings = _clear_slot_warnings(warnings, slot)
    if q.get("bloom_check") == "flagged":
        detected = q.get("detected_level")
        detected_label = bloom.get_level(detected).label if detected else "a different level"
        warnings.append(
            f"{_slot_label(slot)}: classified as {detected_label}, "
            f"not the intended {bloom.get_level(intended_level).label}."
        )
    return warnings


def _generate_one_question_for_slot(
    topic: str, slot: PaperQuestion, window: "_SourceWindow", pattern: paper.Pattern,
    recent_questions: list, drop_log: list
):
    """
    Writes ONE question for `slot`, grounded in `window` alone -- shared
    by generate_paper's widened-window retry pass (Phase 5C EXTRA A) and
    POST /papers/{id}/slots/{id}/regenerate, so both go through the exact
    same generate -> check -> ground pipeline instead of duplicating it.
    Returns the cleaned question dict, or None if nothing usable survived
    (dropped reasons land in `drop_log` either way). Propagates
    llm.LLMUnavailableError; callers decide how to handle it.
    """
    top_pages = [(1.0, window)]
    level_counts = {slot.bloom_level: 1}
    marks_by_level = {slot.bloom_level: slot.marks}

    questions = _generate_and_validate_questions(
        topic, top_pages, level_counts, drop_log, recent_questions, marks_by_level, pattern.paper_kind
    )
    questions = _apply_verb_guard(
        questions, topic, top_pages, drop_log, recent_questions, marks_by_level, pattern.paper_kind
    )
    questions = _check_and_fix_bloom_levels(
        questions, topic, top_pages, drop_log, recent_questions, marks_by_level, pattern.paper_kind
    )
    questions = _filter_grounded(questions, [(slot, window)], drop_log)
    return questions[0] if questions else None


def generate_paper(db: Session, paper_row: Paper) -> None:
    """
    Fills every still-empty slot of `paper_row` (status is already
    "generating", set by the route above before this runs). Reuses the
    exact same Phase 4B pipeline /generate-quiz uses --
    _generate_and_validate_questions, _check_and_fix_bloom_levels,
    _drop_near_duplicates, _top_up_missing_levels -- once per (bloom_level,
    marks) group of slots, grounding each slot in its own non-overlapping
    source window (_assign_source_windows) instead of topic-based
    retrieval. Called from jobs.py's background job, which owns `db`'s
    commit/close lifecycle and translates any exception into a "failed"
    paper with a friendly error.

    The main per-group generation call is wrapped in call_patiently (see
    _make_patient_caller) so a busy AI service pauses and resumes the job
    instead of failing it outright -- only once the whole job's wait
    budget (PAPER_JOB_MAX_WAIT_SECONDS) is used up does
    llm.LLMUnavailableError finally propagate out of this function
    uncaught, for jobs.py to turn into a "failed" paper.
    """
    call_patiently = _make_patient_caller(db, paper_row)
    pattern = _pattern_from_dict(json.loads(paper_row.pattern))
    slots = (
        db.query(PaperQuestion)
        .filter(PaperQuestion.paper_id == paper_row.id)
        .order_by(PaperQuestion.position)
        .all()
    )
    empty_slots = [s for s in slots if s.question_id is None and s.bloom_level]
    warnings = json.loads(paper_row.warnings) if paper_row.warnings else []

    # One set of non-overlapping source windows per book, sized to exactly
    # how many of ITS still-empty slots need filling -- a retry after a
    # partial failure only consumes windows for the slots still empty.
    book_ids = sorted({s.book_id for s in empty_slots if s.book_id})
    windows_by_book = {
        book_id: _assign_source_windows(db, book_id, sum(1 for s in empty_slots if s.book_id == book_id))
        for book_id in book_ids
    }
    next_window_index = {book_id: 0 for book_id in book_ids}

    groups = {}
    for slot in empty_slots:
        pool = windows_by_book.get(slot.book_id) or []
        idx = next_window_index.get(slot.book_id, 0)
        if idx >= len(pool):
            continue
        window = pool[idx]
        next_window_index[slot.book_id] = idx + 1
        # Grouping key includes book_id (not just level/marks) so a single
        # generate_json call's numbered sources are ALWAYS all from one
        # document -- batching slots from different books into one call let
        # the model answer a CN-labelled slot with NS-document content in
        # practice (Phase 5B quality fix); grounding is only guaranteed by
        # construction when every source in the prompt is from the slot's
        # own book to begin with.
        groups.setdefault((slot.bloom_level, slot.marks, slot.book_id), []).append((slot, window))

    topic = paper_row.subject or paper_row.title

    for (level, marks, _book_id), slot_windows in groups.items():
        top_pages = [(1.0, window) for _, window in slot_windows]
        level_counts = {level: len(slot_windows)}
        marks_by_level = {level: marks}
        drop_log = []
        recent_questions = _recent_question_texts(db, paper_row.user_id, topic)

        questions = call_patiently(lambda: _generate_and_validate_questions(
            topic, top_pages, level_counts, drop_log, recent_questions, marks_by_level, pattern.paper_kind
        ))
        questions = _apply_verb_guard(
            questions, topic, top_pages, drop_log, recent_questions, marks_by_level, pattern.paper_kind
        )
        questions = _check_and_fix_bloom_levels(
            questions, topic, top_pages, drop_log, recent_questions, marks_by_level, pattern.paper_kind
        )
        questions = _drop_near_duplicates(questions, drop_log)
        questions = _filter_grounded(questions, slot_windows, drop_log)
        questions, _ = _top_up_missing_levels(
            questions, topic, top_pages, level_counts, drop_log, recent_questions, marks_by_level, pattern.paper_kind
        )
        questions = _filter_grounded(questions, slot_windows, drop_log)

        if drop_log:
            reason_counts = Counter(drop_log)
            logger.info(
                "Paper %d generation for level '%s' (%d marks): %s",
                paper_row.id, level, marks,
                "; ".join(f"dropped {c}: {r}" for r, c in reason_counts.items())
            )

        # Match each returned question back to the slot whose window it
        # was (self-reported, via source_index) grounded in; anything left
        # over fills whatever slot in this group is still unmatched, in
        # order. A slot nothing is left for just stays empty (warning).
        by_source_index = {}
        leftovers = []
        for q in questions:
            idx = q.get("source_index")
            if isinstance(idx, int) and 1 <= idx <= len(slot_windows) and idx not in by_source_index:
                by_source_index[idx] = q
            else:
                leftovers.append(q)

        for idx, (slot, window) in enumerate(slot_windows, start=1):
            q = by_source_index.pop(idx, None)
            if q is None and leftovers:
                q = leftovers.pop(0)
            if q is None:
                warnings = _clear_slot_warnings(warnings, slot)
                warnings.append(f"{_slot_label(slot)}: couldn't generate a question for this slot.")
                continue

            db_question = Question(
                user_id=paper_row.user_id,
                book_id=window.book_id,
                page_id=window.id,
                topic=topic,
                type=q["type"],
                bloom_level=q["bloom_level"],
                bloom_check=q["bloom_check"],
                detected_level=q.get("detected_level"),
                question=q["question"],
                options=json.dumps(q["options"]) if q.get("options") else None,
                correct_answer=q["correctAnswer"],
                explanation=q["explanation"],
                marks=q["marks"],
                marking_scheme=json.dumps(q["marking_scheme"]),
            )
            db.add(db_question)
            db.flush()
            slot.question_id = db_question.id
            warnings = _sync_slot_warning(warnings, slot, q, level)

        paper_row.progress_done = sum(1 for s in slots if s.question_id is not None)
        paper_row.warnings = json.dumps(warnings)
        db.commit()

    # EXTRA (Phase 5C review): any slot still empty after the main pass
    # gets ONE more attempt with a wider source window (5 chunks instead
    # of the usual <=3) from the SAME document before it's given up on --
    # a bigger window sometimes lands on content that's actually
    # answerable/grounded when the original narrower one wasn't.
    still_empty = [s for s in slots if s.question_id is None and s.bloom_level and s.book_id]
    for slot in still_empty:
        windows = _assign_source_windows(db, slot.book_id, 1, window_size=5)
        if not windows:
            continue
        window = windows[0]
        drop_log = []
        recent_questions = _recent_question_texts(db, paper_row.user_id, topic)

        q = call_patiently(lambda: _generate_one_question_for_slot(
            topic, slot, window, pattern, recent_questions, drop_log
        ))

        if drop_log:
            reason_counts = Counter(drop_log)
            logger.info(
                "Paper %d widened-window retry for %s: %s",
                paper_row.id, _slot_label(slot),
                "; ".join(f"dropped {c}: {r}" for r, c in reason_counts.items())
            )

        if q is None:
            continue

        db_question = Question(
            user_id=paper_row.user_id,
            book_id=window.book_id,
            page_id=window.id,
            topic=topic,
            type=q["type"],
            bloom_level=q["bloom_level"],
            bloom_check=q["bloom_check"],
            detected_level=q.get("detected_level"),
            question=q["question"],
            options=json.dumps(q["options"]) if q.get("options") else None,
            correct_answer=q["correctAnswer"],
            explanation=q["explanation"],
            marks=q["marks"],
            marking_scheme=json.dumps(q["marking_scheme"]),
        )
        db.add(db_question)
        db.flush()
        slot.question_id = db_question.id
        warnings = _sync_slot_warning(warnings, slot, q, slot.bloom_level)

        paper_row.progress_done = sum(1 for s in slots if s.question_id is not None)
        paper_row.warnings = json.dumps(warnings)
        db.commit()

    paper_row.status = "ready"
    paper_row.warnings = json.dumps(warnings)
    db.commit()
    _log_activity(db, paper_row.user_id, "paper_generated")


@app.patch("/papers/{paper_id}/slots/{slot_id}")
def swap_paper_slot(
    paper_id: int,
    slot_id: int,
    payload: SlotSwapRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    paper_row = _get_owned_paper_or_404(db, paper_id, current_user.id)
    slot = _get_paper_slot_or_404(db, paper_row.id, slot_id)
    question = _get_owned_question_or_404(db, payload.question_id, current_user.id)

    if question.marks is not None and question.marks != slot.marks:
        raise HTTPException(
            status_code=400,
            detail=f"This question is worth {question.marks} marks, but the slot needs {slot.marks}."
        )

    allowed_levels = paper.levels_allowed_for_marks(slot.marks)
    if question.bloom_level not in allowed_levels:
        raise HTTPException(
            status_code=400,
            detail=f"'{question.bloom_level}' is not an allowed level for a {slot.marks}-mark slot."
        )

    pattern = _pattern_from_dict(json.loads(paper_row.pattern))
    if question.type not in paper.allowed_types_for_slot(question.bloom_level, slot.marks, pattern.paper_kind):
        raise HTTPException(
            status_code=400,
            detail=f"Question type '{question.type}' doesn't fit this slot."
        )

    if question.marks is None:
        question.marks = slot.marks

    slot.question_id = question.id

    warnings = json.loads(paper_row.warnings) if paper_row.warnings else []
    warnings = _sync_slot_warning(
        warnings, slot, {"bloom_check": question.bloom_check, "detected_level": question.detected_level},
        question.bloom_level
    )
    if question.bloom_level != slot.bloom_level:
        warnings.append(
            f"{_slot_label(slot)}: swapped in a {bloom.get_level(question.bloom_level).label} question "
            f"(planned {bloom.get_level(slot.bloom_level).label if slot.bloom_level else 'none'})."
        )
    paper_row.warnings = json.dumps(warnings)

    db.commit()
    return _paper_detail(db, paper_row)


@app.post("/papers/{paper_id}/slots/{slot_id}/regenerate")
@limiter.limit("10/minute", key_func=_user_rate_limit_key)
def regenerate_paper_slot(
    request: Request,
    paper_id: int,
    slot_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    paper_row = _get_owned_paper_or_404(db, paper_id, current_user.id)
    slot = _get_paper_slot_or_404(db, paper_row.id, slot_id)

    if not slot.bloom_level:
        raise HTTPException(status_code=400, detail="This slot has no Bloom level assigned and can't be filled.")
    if not slot.book_id:
        raise HTTPException(status_code=400, detail="This slot has no book assigned.")

    pattern = _pattern_from_dict(json.loads(paper_row.pattern))
    windows = _assign_source_windows(db, slot.book_id, 1)
    if not windows:
        raise HTTPException(status_code=400, detail="This slot's book has no indexed content to generate from.")
    window = windows[0]

    topic = paper_row.subject or paper_row.title
    drop_log = []
    recent_questions = _recent_question_texts(db, current_user.id, topic)

    try:
        q = _generate_one_question_for_slot(topic, slot, window, pattern, recent_questions, drop_log)
    except llm.LLMUnavailableError:
        raise HTTPException(status_code=503, detail=AI_BUSY_DETAIL)

    if q is None:
        raise HTTPException(status_code=422, detail="Couldn't write a question for this slot. Try again.")

    db_question = Question(
        user_id=current_user.id,
        book_id=window.book_id,
        page_id=window.id,
        topic=topic,
        type=q["type"],
        bloom_level=q["bloom_level"],
        bloom_check=q["bloom_check"],
        detected_level=q.get("detected_level"),
        question=q["question"],
        options=json.dumps(q["options"]) if q.get("options") else None,
        correct_answer=q["correctAnswer"],
        explanation=q["explanation"],
        marks=q["marks"],
        marking_scheme=json.dumps(q["marking_scheme"]),
    )
    db.add(db_question)
    db.flush()
    slot.question_id = db_question.id

    warnings = json.loads(paper_row.warnings) if paper_row.warnings else []
    warnings = _sync_slot_warning(warnings, slot, q, slot.bloom_level)
    paper_row.warnings = json.dumps(warnings)

    db.commit()

    return _paper_detail(db, paper_row)


@app.get("/papers/{paper_id}/slots/{slot_id}/candidates")
def get_paper_slot_candidates(
    paper_id: int,
    slot_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    paper_row = _get_owned_paper_or_404(db, paper_id, current_user.id)
    slot = _get_paper_slot_or_404(db, paper_row.id, slot_id)

    allowed_levels = paper.levels_allowed_for_marks(slot.marks)
    if not allowed_levels:
        return []

    pattern = _pattern_from_dict(json.loads(paper_row.pattern))

    candidates = (
        db.query(Question)
        .filter(
            Question.user_id == current_user.id,
            Question.bloom_level.in_(allowed_levels),
            (Question.marks == slot.marks) | (Question.marks.is_(None))
        )
        .order_by(Question.created_at.desc())
        .limit(100)
        .all()
    )

    fitting = [
        q for q in candidates
        if q.type in paper.allowed_types_for_slot(q.bloom_level, slot.marks, pattern.paper_kind)
    ]
    # Same book as the slot first; stable sort keeps created_at-desc order within each group.
    fitting.sort(key=lambda q: q.book_id != slot.book_id)

    return [_question_summary(db, q) for q in fitting[:20]]


@app.get("/papers/{paper_id}/export")
def export_paper(
    paper_id: int,
    kind: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Downloads `paper_id` as a .docx -- see paper_export.py for what each
    `kind` ("student" | "teacher" | "answer_key") actually contains. Built
    straight from _paper_detail()'s own dict shape, so the export can
    never drift from what GET /papers/{id} already shows the frontend.
    """
    paper_row = _get_owned_paper_or_404(db, paper_id, current_user.id)

    if kind not in paper_export.EXPORT_KINDS:
        raise HTTPException(
            status_code=422, detail=f"Unknown export kind '{kind}'. Use one of: {', '.join(paper_export.EXPORT_KINDS)}."
        )

    if paper_row.status != "ready":
        raise HTTPException(status_code=409, detail="This paper isn't ready yet.")

    detail = _paper_detail(db, paper_row)

    if kind == "student" and any(slot["question"] is None for slot in detail["slots"]):
        raise HTTPException(status_code=409, detail="Fill all questions first.")

    buffer = paper_export.build_docx(detail, kind)

    safe_title = re.sub(r"[^A-Za-z0-9 _-]", "", paper_row.title).strip() or "paper"
    filename = f"{safe_title}-{kind}.docx"

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


# -----------------------------
# PHASE 6A: ANALYTICS
# -----------------------------
def _log_activity(db: Session, user_id: int, kind: str, value: int = 1, meta: dict = None) -> None:
    """
    Records one ActivityEvent -- called only after the real work of a
    request has already succeeded (see each call site below), so these
    numbers reflect genuine usage and can't be inflated from the browser.
    A logging failure must never take the response down with it: caught
    here and logged as a warning instead of propagating, per the Phase 6A
    spec ("Logging must never break the request").
    """
    try:
        db.add(ActivityEvent(
            user_id=user_id, kind=kind, value=value,
            meta=json.dumps(meta) if meta else None,
        ))
        db.commit()
    except Exception:
        logger.warning("Failed to log activity event (kind=%s, user_id=%s)", kind, user_id, exc_info=True)


DEFAULT_ANALYTICS_TZ = "Asia/Kolkata"
ANALYTICS_SUMMARY_DAY_CHOICES = (7, 30, 90)
ACTIVE_TIME_MIN_SECONDS = 1
ACTIVE_TIME_MAX_SECONDS = 120
MIN_QUIZ_ATTEMPT_ANSWERS = 1
MAX_QUIZ_ATTEMPT_ANSWERS = 50
MAX_IMPORT_DAYS = 400
MAX_IMPORT_QUIZ_ENTRIES = 1000
MAX_IMPORT_QUERIES_PER_DAY = 2000
MAX_IMPORT_SECONDS_PER_DAY = 86400


def _resolve_timezone(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        raise HTTPException(status_code=422, detail=f"Unknown timezone: '{tz_name}'")


def _as_utc(dt: datetime) -> datetime:
    """
    A datetime read back from SQLite always comes back naive (tzinfo
    stripped by the round-trip) even though every created_at default
    saved it as datetime.now(timezone.utc) -- so "naive" here always
    means "UTC wall-clock time", and this just reattaches that tzinfo so
    it can be converted to a user's real timezone.
    """
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=dt_timezone.utc)


def _local_date_key(dt: datetime, tz: ZoneInfo) -> str:
    return _as_utc(dt).astimezone(tz).date().isoformat()


def _today_local(tz: ZoneInfo):
    return datetime.now(dt_timezone.utc).astimezone(tz).date()


def _day_keys(days: int, tz: ZoneInfo) -> list:
    """Oldest-first list of local 'YYYY-MM-DD' date keys for the last `days` days, ending today."""
    today = _today_local(tz)
    return [(today - timedelta(days=offset)).isoformat() for offset in range(days - 1, -1, -1)]


def _window_start_utc(days: int, tz: ZoneInfo) -> datetime:
    """The naive-UTC instant of local midnight, `days - 1` days before today, in `tz` -- the inclusive start of the summary window."""
    start_date = _today_local(tz) - timedelta(days=days - 1)
    start_local = datetime(start_date.year, start_date.month, start_date.day, tzinfo=tz)
    return start_local.astimezone(dt_timezone.utc).replace(tzinfo=None)


def _noon_utc_naive(local_day, tz: ZoneInfo) -> datetime:
    """
    Noon local time on `local_day`, converted to naive UTC for storage --
    used only by POST /analytics/import, which has no real time-of-day for
    an old day-bucketed count, just a date. Noon (rather than midnight)
    keeps it safely inside the same UTC calendar day across any realistic
    timezone offset, so _local_date_key() always buckets it back onto the
    same local day it was imported for.
    """
    local_noon = datetime(local_day.year, local_day.month, local_day.day, 12, 0, tzinfo=tz)
    return local_noon.astimezone(dt_timezone.utc).replace(tzinfo=None)


def _compute_paper_coverage(db: Session, user_id: int) -> dict:
    """
    Marks per Bloom level across every one of this user's READY papers
    (not time-windowed -- a paper's coverage is a structural property of
    the paper, not a recent-activity metric). A flagged question counts
    at its detected level, not its intended one, matching main.
    _compute_blueprint_actual's same "show the real mix" rule -- just
    aggregated across every ready paper instead of just one. Only one
    option of an either_or pair is counted (marks_on_paper convention).
    """
    ready_paper_ids = [
        pid for (pid,) in
        db.query(Paper.id).filter(Paper.user_id == user_id, Paper.status == "ready").all()
    ]

    by_level = {level: 0 for level in bloom.level_keys()}
    if ready_paper_ids:
        slots = (
            db.query(PaperQuestion)
            .filter(
                PaperQuestion.paper_id.in_(ready_paper_ids),
                PaperQuestion.question_id.isnot(None),
                or_(PaperQuestion.option.is_(None), PaperQuestion.option == "a")
            )
            .all()
        )
        question_ids = [s.question_id for s in slots]
        questions_by_id = {
            q.id: q for q in db.query(Question).filter(Question.id.in_(question_ids)).all()
        } if question_ids else {}
        for slot in slots:
            question = questions_by_id.get(slot.question_id)
            if not question:
                continue
            effective_level = (
                question.detected_level
                if question.bloom_check == "flagged" and question.detected_level
                else question.bloom_level
            )
            by_level[effective_level] = by_level.get(effective_level, 0) + slot.marks

    return {"by_level": by_level, "papers": len(ready_paper_ids)}


class ActiveTimeRequest(BaseModel):
    seconds: int


class QuizAnswerPayload(BaseModel):
    question_id: int | None = None
    bloom_level: str | None = None
    question_type: str
    is_correct: bool
    self_graded: bool


class QuizAttemptRequest(BaseModel):
    topic: str
    answers: list[QuizAnswerPayload]


class ImportQuizHistoryEntry(BaseModel):
    topic: str = "General"
    correct: int
    total: int
    timestamp: float  # ms since epoch (JS Date.now()), per the old localStorage shape
    byLevel: dict[str, dict] | None = None


class AnalyticsImportRequest(BaseModel):
    queries_by_day: dict[str, int] = {}
    active_seconds_by_day: dict[str, int] = {}
    quiz_history: list[ImportQuizHistoryEntry] = []


@app.post("/analytics/active-time")
@limiter.limit("4/minute", key_func=_user_rate_limit_key)
def record_active_time(
    request: Request,
    payload: ActiveTimeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not (ACTIVE_TIME_MIN_SECONDS <= payload.seconds <= ACTIVE_TIME_MAX_SECONDS):
        raise HTTPException(
            status_code=422,
            detail=f"seconds must be between {ACTIVE_TIME_MIN_SECONDS} and {ACTIVE_TIME_MAX_SECONDS}."
        )
    db.add(ActivityEvent(user_id=current_user.id, kind="active_seconds", value=payload.seconds))
    db.commit()
    return {"status": "recorded"}


@app.post("/analytics/quiz-attempts")
@limiter.limit("10/minute", key_func=_user_rate_limit_key)
def record_quiz_attempt(
    request: Request,
    payload: QuizAttemptRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not (MIN_QUIZ_ATTEMPT_ANSWERS <= len(payload.answers) <= MAX_QUIZ_ATTEMPT_ANSWERS):
        raise HTTPException(
            status_code=422,
            detail=f"answers must have between {MIN_QUIZ_ATTEMPT_ANSWERS} and {MAX_QUIZ_ATTEMPT_ANSWERS} entries."
        )
    for ans in payload.answers:
        if ans.bloom_level is not None and not bloom.is_valid_level(ans.bloom_level):
            raise HTTPException(status_code=422, detail=f"Unknown Bloom's level: '{ans.bloom_level}'")
        if not ans.question_type or not ans.question_type.strip():
            raise HTTPException(status_code=422, detail="question_type cannot be empty.")

    topic = (payload.topic or "General").strip() or "General"
    correct = sum(1 for ans in payload.answers if ans.is_correct)
    total = len(payload.answers)

    attempt = QuizAttempt(user_id=current_user.id, topic=topic, correct=correct, total=total)
    db.add(attempt)
    db.flush()

    for ans in payload.answers:
        question_id = ans.question_id
        if question_id is not None:
            owned = db.query(Question.id).filter(
                Question.id == question_id, Question.user_id == current_user.id
            ).first()
            if not owned:
                question_id = None  # not this user's question -- ignore it rather than 404
        db.add(QuizAnswer(
            attempt_id=attempt.id, question_id=question_id, bloom_level=ans.bloom_level,
            question_type=ans.question_type.strip(), is_correct=ans.is_correct, self_graded=ans.self_graded,
        ))

    db.commit()
    return {"attempt_id": attempt.id, "topic": topic, "correct": correct, "total": total}


@app.get("/analytics/summary")
def get_analytics_summary(
    days: int = 30,
    tz: str = DEFAULT_ANALYTICS_TZ,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if days not in ANALYTICS_SUMMARY_DAY_CHOICES:
        raise HTTPException(
            status_code=422,
            detail=f"days must be one of {ANALYTICS_SUMMARY_DAY_CHOICES} (got {days})."
        )
    user_tz = _resolve_timezone(tz)

    window_start = _window_start_utc(days, user_tz)
    day_keys = _day_keys(days, user_tz)

    events = (
        db.query(ActivityEvent)
        .filter(ActivityEvent.user_id == current_user.id, ActivityEvent.created_at >= window_start)
        .all()
    )

    queries_by_day = {key: 0 for key in day_keys}
    active_seconds_by_day = {key: 0 for key in day_keys}
    totals_queries = 0
    totals_tools = 0
    totals_quiz_generated = 0
    totals_active_seconds = 0

    for event in events:
        if event.kind == "query":
            totals_queries += event.value
            day_key = _local_date_key(event.created_at, user_tz)
            if day_key in queries_by_day:
                queries_by_day[day_key] += event.value
        elif event.kind == "tool":
            totals_tools += event.value
        elif event.kind == "quiz_generated":
            totals_quiz_generated += event.value
        elif event.kind == "active_seconds":
            totals_active_seconds += event.value
            day_key = _local_date_key(event.created_at, user_tz)
            if day_key in active_seconds_by_day:
                active_seconds_by_day[day_key] += event.value
        # "paper_generated" isn't surfaced as its own total here -- it's
        # recorded (item 4) for completeness/future use; paper_coverage
        # below already reports on the user's ready papers directly.

    # --- Quiz attempts in this window ---
    attempts = (
        db.query(QuizAttempt)
        .filter(QuizAttempt.user_id == current_user.id, QuizAttempt.created_at >= window_start)
        .all()
    )
    quizzes_taken = len(attempts)
    average_score_pct = (
        round(
            sum((a.correct / a.total) * 100 for a in attempts if a.total > 0) / quizzes_taken, 1
        ) if quizzes_taken > 0 else None
    )

    accuracy_by_level = {level: {"correct": 0, "total": 0, "pct": None} for level in bloom.level_keys()}
    topics_map = {}
    if attempts:
        attempt_ids = [a.id for a in attempts]
        answers = db.query(QuizAnswer).filter(QuizAnswer.attempt_id.in_(attempt_ids)).all()
        for ans in answers:
            if not ans.bloom_level or ans.bloom_level not in accuracy_by_level:
                continue
            accuracy_by_level[ans.bloom_level]["total"] += 1
            if ans.is_correct:
                accuracy_by_level[ans.bloom_level]["correct"] += 1
        for level_stats in accuracy_by_level.values():
            if level_stats["total"] > 0:
                level_stats["pct"] = round(level_stats["correct"] / level_stats["total"] * 100, 1)

        for a in attempts:
            key = a.topic.strip().lower() or "general"
            entry = topics_map.setdefault(
                key, {"topic": a.topic, "attempts": 0, "_pct_sum": 0.0, "last_attempt_at": None}
            )
            entry["attempts"] += 1
            # Mean of each attempt's own percentage (not a totals-weighted
            # average), matching totals.average_score_pct's same "mean of
            # attempt %" rule -- a 2-question quiz and a 20-question quiz
            # count equally toward a topic's average either way.
            entry["_pct_sum"] += (a.correct / a.total * 100) if a.total > 0 else 0.0
            if entry["last_attempt_at"] is None or a.created_at > entry["last_attempt_at"]:
                entry["last_attempt_at"] = a.created_at
                entry["topic"] = a.topic  # keep the most recently-used casing/spelling

    topics = []
    for entry in topics_map.values():
        average_pct = round(entry["_pct_sum"] / entry["attempts"], 1) if entry["attempts"] > 0 else 0.0
        topics.append({
            "topic": entry["topic"],
            "attempts": entry["attempts"],
            "average_pct": average_pct,
            "last_attempt_at": _as_utc(entry["last_attempt_at"]).isoformat(),
        })

    weak_topics = sorted(
        (t for t in topics if t["attempts"] >= 2 and t["average_pct"] < 60),
        key=lambda t: t["average_pct"]
    )[:5]

    recent_rows = (
        db.query(QuizAttempt)
        .filter(QuizAttempt.user_id == current_user.id)
        .order_by(QuizAttempt.created_at.desc())
        .limit(10)
        .all()
    )
    recent_attempts = [
        {
            "topic": a.topic, "correct": a.correct, "total": a.total,
            "pct": round(a.correct / a.total * 100, 1) if a.total > 0 else 0.0,
            "created_at": _as_utc(a.created_at).isoformat(),
        }
        for a in recent_rows
    ]

    documents_ready = db.query(Book).filter(Book.user_id == current_user.id, Book.status == "ready").count()
    questions_in_bank = db.query(Question).filter(Question.user_id == current_user.id).count()
    papers_ready = db.query(Paper).filter(Paper.user_id == current_user.id, Paper.status == "ready").count()

    return {
        "days": days,
        "tz": tz,
        "totals": {
            "queries": totals_queries,
            "tools_used": totals_tools,
            "quizzes_generated": totals_quiz_generated,
            "quizzes_taken": quizzes_taken,
            "average_score_pct": average_score_pct,
            "active_seconds": totals_active_seconds,
            "documents_ready": documents_ready,
            "questions_in_bank": questions_in_bank,
            "papers_ready": papers_ready,
        },
        "queries_by_day": [{"date": k, "count": queries_by_day[k]} for k in day_keys],
        "active_seconds_by_day": [{"date": k, "seconds": active_seconds_by_day[k]} for k in day_keys],
        "accuracy_by_level": accuracy_by_level,
        "topics": topics,
        "weak_topics": weak_topics,
        "recent_attempts": recent_attempts,
        "paper_coverage": _compute_paper_coverage(db, current_user.id),
    }


@app.get("/analytics/import-status")
def get_analytics_import_status(current_user: User = Depends(get_current_user)):
    return {"imported": current_user.analytics_imported_at is not None}


@app.post("/analytics/import")
def import_analytics(
    payload: AnalyticsImportRequest,
    tz: str = DEFAULT_ANALYTICS_TZ,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    One-time import of a user's pre-Phase-6A, browser-only (localStorage)
    analytics history -- see hooks/useQueryHistory.js, useActiveTime.js,
    and useQuizHistory.js on the frontend for the exact shape this mirrors.
    Day-bucketed counts (no real time-of-day) are stored as one
    ActivityEvent dated at local noon on that day, so _local_date_key()
    always buckets them back onto the same day; quiz_history entries
    already carry a real timestamp and keep it. 409s on a second attempt
    -- analytics_imported_at is set here and never cleared by DELETE
    /analytics, so the same old browser data can't be imported twice.
    """
    if current_user.analytics_imported_at is not None:
        raise HTTPException(status_code=409, detail="Already imported")

    user_tz = _resolve_timezone(tz)

    if len(payload.queries_by_day) > MAX_IMPORT_DAYS:
        raise HTTPException(status_code=422, detail=f"queries_by_day can have at most {MAX_IMPORT_DAYS} days.")
    if len(payload.active_seconds_by_day) > MAX_IMPORT_DAYS:
        raise HTTPException(
            status_code=422, detail=f"active_seconds_by_day can have at most {MAX_IMPORT_DAYS} days."
        )
    if len(payload.quiz_history) > MAX_IMPORT_QUIZ_ENTRIES:
        raise HTTPException(
            status_code=422, detail=f"quiz_history can have at most {MAX_IMPORT_QUIZ_ENTRIES} entries."
        )

    today_local = _today_local(user_tz)
    now_utc = datetime.now(dt_timezone.utc)

    def _parse_day(date_str: str):
        try:
            parsed = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Invalid date '{date_str}' -- expected YYYY-MM-DD.")
        if parsed > today_local:
            raise HTTPException(status_code=422, detail=f"Date '{date_str}' is in the future.")
        return parsed

    events_to_add = []

    for date_str, count in payload.queries_by_day.items():
        if not isinstance(count, int) or not (0 <= count <= MAX_IMPORT_QUERIES_PER_DAY):
            raise HTTPException(
                status_code=422,
                detail=f"queries_by_day['{date_str}'] must be between 0 and {MAX_IMPORT_QUERIES_PER_DAY}."
            )
        local_day = _parse_day(date_str)
        if count > 0:
            events_to_add.append(ActivityEvent(
                user_id=current_user.id, kind="query", value=count,
                created_at=_noon_utc_naive(local_day, user_tz),
            ))

    for date_str, seconds in payload.active_seconds_by_day.items():
        if not isinstance(seconds, int) or not (0 <= seconds <= MAX_IMPORT_SECONDS_PER_DAY):
            raise HTTPException(
                status_code=422,
                detail=f"active_seconds_by_day['{date_str}'] must be between 0 and {MAX_IMPORT_SECONDS_PER_DAY}."
            )
        local_day = _parse_day(date_str)
        if seconds > 0:
            events_to_add.append(ActivityEvent(
                user_id=current_user.id, kind="active_seconds", value=seconds,
                created_at=_noon_utc_naive(local_day, user_tz),
            ))

    attempts_to_add = []
    for entry in payload.quiz_history:
        if entry.total <= 0 or entry.correct < 0 or entry.correct > entry.total:
            raise HTTPException(
                status_code=422, detail="Each quiz_history entry needs total > 0 and 0 <= correct <= total."
            )
        try:
            created_at_utc = datetime.fromtimestamp(entry.timestamp / 1000, tz=dt_timezone.utc)
        except (OverflowError, OSError, ValueError):
            raise HTTPException(status_code=422, detail=f"Invalid timestamp {entry.timestamp!r}.")
        if created_at_utc > now_utc:
            raise HTTPException(status_code=422, detail="A quiz_history entry is dated in the future.")

        attempt = QuizAttempt(
            user_id=current_user.id, topic=(entry.topic or "General").strip() or "General",
            correct=entry.correct, total=entry.total, created_at=created_at_utc.replace(tzinfo=None),
        )
        answers = []
        if entry.byLevel:
            for level, counts in entry.byLevel.items():
                if not bloom.is_valid_level(level) or not isinstance(counts, dict):
                    continue
                level_correct = int(counts.get("correct") or 0)
                level_total = int(counts.get("total") or 0)
                # Only the aggregate (correct, total) survived in the old
                # localStorage shape -- individual answers are
                # reconstructed as `level_total` rows, the first
                # `level_correct` of them marked correct. question_type
                # is unknown for imported history, so it's recorded as
                # "imported" rather than guessed.
                for i in range(level_total):
                    answers.append(QuizAnswer(
                        bloom_level=level, question_type="imported",
                        is_correct=(i < level_correct), self_graded=True,
                    ))
        attempts_to_add.append((attempt, answers))

    for event in events_to_add:
        db.add(event)
    for attempt, answers in attempts_to_add:
        attempt.answers = answers
        db.add(attempt)

    current_user.analytics_imported_at = datetime.now(dt_timezone.utc)
    db.commit()

    return {
        "imported_days": len(set(payload.queries_by_day) | set(payload.active_seconds_by_day)),
        "imported_quiz_attempts": len(attempts_to_add),
    }


@app.delete("/analytics")
def delete_analytics(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Deletes this user's ActivityEvents and QuizAttempts (QuizAnswers
    cascade with their attempt) -- documents, bank questions, and papers
    are all left untouched. Deliberately does NOT clear
    analytics_imported_at: that flag means "the one-time import already
    happened", which stays true even after the imported data is wiped, so
    the same old browser data can't be re-imported and double-counted.
    Confirming with the user before calling this is the frontend's job.
    """
    db.query(ActivityEvent).filter(ActivityEvent.user_id == current_user.id).delete(synchronize_session=False)

    attempt_ids = [
        aid for (aid,) in
        db.query(QuizAttempt.id).filter(QuizAttempt.user_id == current_user.id).all()
    ]
    if attempt_ids:
        db.query(QuizAnswer).filter(QuizAnswer.attempt_id.in_(attempt_ids)).delete(synchronize_session=False)
    db.query(QuizAttempt).filter(QuizAttempt.user_id == current_user.id).delete(synchronize_session=False)

    db.commit()
    return {"status": "Analytics data deleted successfully"}
