import os
import re

from fastapi import FastAPI, UploadFile, File, HTTPException, Depends
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
import json

from embeddings import create_embedding
from rag import generate_answer, generate_quiz, check_context_relevance, synthesize_educational_response
from document_parsers import parse_document, chunk_parsed_document
from retrieval import retrieve, invalidate_cache

from database import engine, get_db
from models import Base, Book, Page, User

from auth import (
    create_access_token,
    verify_password,
    hash_password,
    get_current_user
)

app = FastAPI()

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
Base.metadata.create_all(bind=engine)

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")

ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".docx", ".pptx", ".doc", ".ppt", ".txt", ".md"}
MAX_UPLOAD_MB = float(os.getenv("MAX_UPLOAD_MB", "25"))
MAX_UPLOAD_BYTES = int(MAX_UPLOAD_MB * 1024 * 1024)


class RegisterRequest(BaseModel):
    username: str
    password: str


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

    return {
        "status": "Server is healthy"
    }


# -----------------------------
# REGISTER
# -----------------------------
@app.post("/register")
def register(payload: RegisterRequest, db: Session = Depends(get_db)):

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
def login(
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

    try:
        # Save book
        new_book = Book(name=file.filename)

        db.add(new_book)
        db.commit()
        db.refresh(new_book)

        # Parse document using our modular document parser
        pages_data = parse_document(file.file, file.filename)

        # Chunk pages semantically
        chunks = chunk_parsed_document(pages_data)

        for chunk_index, chunk_item in enumerate(chunks):
            chunk = chunk_item["content"]
            page_num = chunk_item["page_number"]
            try:
                embedding = create_embedding(chunk)

                new_page = Page(
                    book_id=new_book.id,
                    page_number=page_num,
                    chunk_number=chunk_index + 1,
                    content=chunk,
                    embedding=json.dumps(
                        embedding.tolist()
                    )
                )

                db.add(new_page)

            except Exception:
                continue

        db.commit()
        invalidate_cache()
        return {
            "status": "Document uploaded and chunked successfully",
            "book_id": new_book.id
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process document file: {str(e)}"
        )


# -----------------------------
# GET ALL BOOKS
# -----------------------------
@app.get("/books")
def get_books(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):

    books = db.query(Book).all()

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
        Book.id == book_id
    ).first()

    if not book:

        raise HTTPException(
            status_code=404,
            detail="Book not found"
        )

    # Delete related pages
    db.query(Page).filter(
        Page.book_id == book_id
    ).delete()

    db.delete(book)
    db.commit()
    invalidate_cache()

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

    results = db.query(Page).filter(
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

    if not db.query(Page.id).first():

        return {
            "message": "No documents available"
        }

    top_pages = retrieve(db, query, top_k=3, apply_threshold=False)

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
@app.get("/chat")
def chat_with_pdf(
    question: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):

    if not db.query(Page.id).first():
        return {
            "message": "No documents uploaded"
        }

    top_pages = retrieve(db, question, top_k=3, apply_threshold=True)

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
                "similarity_score": round(float(score), 4)
            })

    return {
        "question": question,
        "answer": answer,
        "sources": sources
    }


@app.get("/generate-quiz")
def generate_ai_quiz(
    topic: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):

    if not db.query(Page.id).first():
        return {
            "topic": topic,
            "quiz": "Not enough information found in uploaded documents.",
            "questions": []
        }

    top_pages = retrieve(db, topic, top_k=3, apply_threshold=True)

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
