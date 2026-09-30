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
    generate_answer,
    generate_quiz,
    check_context_relevance,
    synthesize_educational_response,
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

    top_pages = retrieve(db, current_user.id, query, top_k=3, apply_threshold=False)

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

    top_pages = retrieve(db, current_user.id, question, top_k=3, apply_threshold=True)

    # Relevance checking using both cosine similarity and lexical overlap
    temp_context = " ".join([page.content for _, page in top_pages])
    if not top_pages or not check_context_relevance(question, temp_context, top_pages[0][0]):
        return {
            "question": question,
            "answer": "The uploaded documents do not contain enough information for this question.",
            "sources": []
        }

    # Build PDF context
    pdf_context = "\n\n".join([
        f"--- Document Source Block ---\n{page.content}"
        for score, page in top_pages
    ])

    # Generate AI answer (Flan-T5 generated definition/direct answer)
    direct_answer = generate_answer(
        pdf_context,
        question
    )

    if not direct_answer or "do not contain enough information" in direct_answer.lower():
        return {
            "question": question,
            "answer": "The uploaded documents do not contain enough information for this question.",
            "sources": []
        }

    # Resolve topic from book name
    first_book_id = top_pages[0][1].book_id
    book_obj = db.query(Book).filter(Book.id == first_book_id).first()
    topic = book_obj.name.split('.')[0] if book_obj else "Uploaded Material"

    # Synthesize a beautiful, multi-paragraph ChatGPT-style response using hybrid techniques
    answer = synthesize_educational_response(question, top_pages, direct_answer, topic)

    # Build sources
    sources = []

    for score, page in top_pages:

        book = db.query(Book).filter(
            Book.id == page.book_id
        ).first()

        if book:

            sources.append({
                "book_name": book.name,
                "page_number": page.page_number,
                "chunk_number": page.chunk_number,
                "similarity_score": round(float(score), 4),
                "page_id": page.id,
                "content": page.content
            })

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

    bullets = summarize_text_in_bullets(text)

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

    top_pages = retrieve(db, current_user.id, topic, top_k=5, apply_threshold=True)

    combined_text = "\n".join(page.content for _, page in top_pages)
    if not combined_text.strip():
        return {"cards": [], "message": "No relevant content found in your documents for this topic."}

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

    top_pages = retrieve(db, current_user.id, topic, top_k=3, apply_threshold=True)

    # Relevance checking using both cosine similarity and lexical overlap
    temp_context = " ".join([page.content for _, page in top_pages])
    if not top_pages or not check_context_relevance(topic, temp_context, top_pages[0][0]):
        return {
            "topic": topic,
            "quiz": "Not enough information found in uploaded documents.",
            "questions": []
        }

    context = "\n\n".join([
        page.content
        for score, page in top_pages
    ])

    quiz_text, questions = generate_quiz(
        context,
        topic
    )

    return {
        "topic": topic,
        "quiz": quiz_text,
        "questions": questions
    }
