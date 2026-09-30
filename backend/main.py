import logging
import os
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, UploadFile, File, HTTPException, Depends
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session
import json

import embeddings
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
from document_parsers import parse_document, chunk_parsed_document
from retrieval import retrieve, invalidate_cache, user_has_documents, needs_reembedding

from database import engine, get_db
from models import Base, Book, Page, User

from auth import (
    create_access_token,
    verify_password,
    hash_password,
    get_current_user
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

    logger.info("Startup complete")
    yield
    logger.info("Shutting down")


limiter = Limiter(key_func=get_remote_address)

app = FastAPI(lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


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
MAX_UPLOAD_MB = float(os.getenv("MAX_UPLOAD_MB", "25"))
MAX_UPLOAD_BYTES = int(MAX_UPLOAD_MB * 1024 * 1024)

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
# PDF UPLOAD + CHUNKING
# -----------------------------
@app.post("/upload-book")
async def upload_book(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):

    filename = file.filename or ""
    ext = os.path.splitext(filename)[1].lower()

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

    # Parse and chunk before touching the database — nothing is saved unless
    # the whole pipeline succeeds, so a failed upload never leaves an empty book.
    try:
        pages_data = parse_document(file.file, filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("Failed to parse uploaded document '%s'", filename)
        raise HTTPException(status_code=500, detail="Failed to process document file.")

    chunks = chunk_parsed_document(pages_data)

    if not chunks:
        raise HTTPException(
            status_code=422,
            detail="No readable text found. This looks like a scanned document — OCR support is coming soon."
        )

    texts = [chunk_item["content"] for chunk_item in chunks]

    try:
        embedding_vectors = embeddings.embed_documents(texts)
    except embeddings.EmbeddingServiceBusyError:
        logger.warning("Embedding service busy while embedding %d chunk(s) for '%s'", len(texts), filename)
        raise HTTPException(
            status_code=503,
            detail="Search service is busy. Please try uploading again in a minute."
        )
    except Exception:
        logger.exception("Failed to embed %d chunk(s) for '%s'", len(texts), filename)
        raise HTTPException(status_code=500, detail="Failed to generate embeddings for this document.")

    if len(embedding_vectors) != len(texts):
        logger.error(
            "Embedding count mismatch for '%s': expected %d chunks, got %d embeddings",
            filename, len(texts), len(embedding_vectors)
        )
        raise HTTPException(status_code=500, detail="Failed to embed all chunks of this document.")

    embedding_model = embeddings.active_model_name()

    try:
        new_book = Book(name=filename, user_id=current_user.id)
        db.add(new_book)
        db.flush()

        for chunk_index, (chunk_item, embedding) in enumerate(zip(chunks, embedding_vectors)):
            db.add(Page(
                book_id=new_book.id,
                page_number=chunk_item["page_number"],
                chunk_number=chunk_index + 1,
                content=chunk_item["content"],
                embedding=json.dumps(embedding.tolist()),
                embedding_model=embedding_model
            ))

        db.commit()
        db.refresh(new_book)

    except Exception:
        db.rollback()
        logger.exception("Failed to save uploaded document '%s' to the database", filename)
        raise HTTPException(status_code=500, detail="Failed to save the uploaded document.")

    invalidate_cache(current_user.id)

    return {
        "status": "Document uploaded and chunked successfully",
        "book_id": new_book.id,
        "chunks": len(chunks),
        "pages": len(pages_data)
    }


# -----------------------------
# GET ALL BOOKS
# -----------------------------
@app.get("/books")
def get_books(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):

    books = db.query(Book).filter(Book.user_id == current_user.id).all()

    return [
        {
            "id": book.id,
            "name": book.name
        }
        for book in books
    ]


# -----------------------------
# DELETE BOOK
# -----------------------------
@app.delete("/books/{book_id}")
def delete_book(
    book_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):

    book = db.query(Book).filter(
        Book.id == book_id,
        Book.user_id == current_user.id
    ).first()

    if not book:

        raise HTTPException(
            status_code=404,
            detail="Book not found"
        )

    # Cascades to the book's pages via the Book.pages relationship
    db.delete(book)
    db.commit()
    invalidate_cache(current_user.id)

    return {
        "status": "Book and associated chunks deleted successfully"
    }


# -----------------------------
# NORMAL SEARCH
# -----------------------------
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
        Page.content.ilike(f"%{query}%")
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
def chat_with_pdf(
    question: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    return _answer_question(question, current_user, db)


# -----------------------------
# AI TOOLS: SUMMARIZER, FLASHCARDS, TUTOR
# -----------------------------
@app.post("/tools/summarize")
def summarize_notes(
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
def generate_flashcards(
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
def ai_tutor(
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
def generate_ai_quiz(
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
