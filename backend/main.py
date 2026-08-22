from fastapi import FastAPI, UploadFile, File, HTTPException, Depends
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
import pdfplumber
import json
import numpy as np

from embeddings import create_embedding
from rag import generate_answer, generate_quiz, paraphrase_concept, check_context_relevance, synthesize_educational_response
from document_parsers import parse_document, chunk_parsed_document

from database import engine, SessionLocal
from models import Base, Book, Page, User

from auth import (
    create_access_token,
    verify_password,
    hash_password,
    get_current_user
)

app = FastAPI()
chat_memory = []
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "http://localhost:5175",
        "http://127.0.0.1:5175",
        "http://localhost:3000",
        "http://127.0.0.1:3000"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
Base.metadata.create_all(bind=engine)


# -----------------------------
# TEXT CHUNKING FUNCTION
# -----------------------------
import re

def clean_pdf_text(text: str) -> str:
    if not text:
        return ""
    
    # Normalize line endings
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    lines = text.split('\n')
    cleaned_lines = []
    
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        
        # Filter page numbers (e.g. "page 12", "12", "12 of 120")
        if re.match(r'^(page\s+)?\d+(\s+of\s+\d+)?$', stripped, re.IGNORECASE):
            continue
            
        # Standardize spaces
        cleaned_line = re.sub(r'[ \t]+', ' ', stripped)
        cleaned_lines.append(cleaned_line)
        
    return "\n".join(cleaned_lines)

def split_text(text: str, chunk_size=120, overlap=25) -> list:
    cleaned = clean_pdf_text(text)
    if not cleaned:
        return []
        
    lines = cleaned.split('\n')
    chunks = []
    current_chunk_lines = []
    current_word_count = 0
    
    for line in lines:
        line_words = line.split()
        if not line_words:
            continue
            
        line_word_count = len(line_words)
        
        if current_word_count + line_word_count > chunk_size and current_chunk_lines:
            chunk_text = "\n".join(current_chunk_lines)
            chunks.append(chunk_text)
            
            # Preserve the last line for overlap
            if len(current_chunk_lines) > 1:
                current_chunk_lines = current_chunk_lines[-1:]
                current_word_count = len(current_chunk_lines[0].split())
            else:
                current_chunk_lines = []
                current_word_count = 0
                
        current_chunk_lines.append(line)
        current_word_count += line_word_count
        
    if current_chunk_lines:
        chunk_text = "\n".join(current_chunk_lines)
        chunks.append(chunk_text)
        
    return chunks


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
def register(username: str, password: str):

    db = SessionLocal()

    existing_user = db.query(User).filter(
        User.username == username
    ).first()

    if existing_user:

        db.close()

        raise HTTPException(
            status_code=400,
            detail="Username already exists"
        )

    hashed_password = hash_password(password)

    new_user = User(
        username=username,
        password=hashed_password
    )

    db.add(new_user)
    db.commit()

    db.close()

    return {
        "message": "User registered successfully"
    }


# -----------------------------
# LOGIN
# -----------------------------
@app.post("/login")
def login(
    form_data: OAuth2PasswordRequestForm = Depends()
):

    db = SessionLocal()

    user = db.query(User).filter(
        User.username == form_data.username
    ).first()

    if not user:

        db.close()

        raise HTTPException(
            status_code=401,
            detail="Invalid username"
        )

    if not verify_password(
        form_data.password,
        user.password
    ):

        db.close()

        raise HTTPException(
            status_code=401,
            detail="Invalid password"
        )

    token = create_access_token(
        data={"sub": user.username}
    )

    db.close()

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
    current_user: str = Depends(get_current_user)
):

    db = SessionLocal()

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

    finally:
        db.close()


# -----------------------------
# GET ALL BOOKS
# -----------------------------
@app.get("/books")
def get_books(current_user: str = Depends(get_current_user)):

    db = SessionLocal()

    books = db.query(Book).all()

    db.close()

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
    current_user: str = Depends(get_current_user)
):

    db = SessionLocal()

    book = db.query(Book).filter(
        Book.id == book_id
    ).first()

    if not book:

        db.close()

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
    db.close()

    return {
        "status": "Book and associated chunks deleted successfully"
    }


# -----------------------------
# NORMAL SEARCH
# -----------------------------
@app.get("/search")
def search_content(query: str):

    db = SessionLocal()

    results = db.query(Page).filter(
        Page.content.ilike(f"%{query}%")
    ).all()

    db.close()

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
    current_user: str = Depends(get_current_user)
):

    db = SessionLocal()

    pages = db.query(Page).all()

    if not pages:

        db.close()

        return {
            "message": "No documents available"
        }

    query_vector = create_embedding(query)

    similarities = []

    for page in pages:

        if not page.embedding:
            continue

        try:

            page_vector = np.array(
                json.loads(page.embedding)
            )

            score = np.dot(
                query_vector,
                page_vector
            )

            similarities.append((score, page))

        except Exception:
            continue

    similarities.sort(
        reverse=True,
        key=lambda x: x[0]
    )


    top_pages = similarities[:3]

    db.close()

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
    current_user: str = Depends(get_current_user)
):

    db = SessionLocal()

    try:
        pages = db.query(Page).all()

        if not pages:
            return {
                "message": "No documents uploaded"
            }

        # Create embedding for question
        query_vector = create_embedding(question)

        similarities = []

        # Compare embeddings
        for page in pages:

            if not page.embedding:
                continue

            try:

                page_vector = np.array(
                    json.loads(page.embedding)
                )

                score = np.dot(
                    query_vector,
                    page_vector
                )

                similarities.append((score, page))

            except Exception:
                continue

        # Sort similarities
        similarities.sort(
            reverse=True,
            key=lambda x: x[0]
        )

        # Filter weak matches and apply relative retrieval thresholding
        filtered_results = []
        if similarities:
            top_score = similarities[0][0]
            for score, page in similarities:
                if score > 0.22:
                    if top_score > 0.40:
                        if score >= top_score - 0.18:
                            filtered_results.append((score, page))
                    else:
                        filtered_results.append((score, page))

        # Deduplicate retrieved chunks (Jaccard similarity > 0.5 is considered duplicate)
        top_pages = []
        seen_contents = []
        for score, page in filtered_results:
            is_duplicate = False
            page_words = set(page.content.lower().split())
            if not page_words:
                continue
                
            for seen_text in seen_contents:
                seen_words = set(seen_text.lower().split())
                if seen_words:
                    intersection = page_words.intersection(seen_words)
                    union = page_words.union(seen_words)
                    overlap = len(intersection) / len(union)
                    if overlap > 0.5:
                        is_duplicate = True
                        break
            if not is_duplicate:
                top_pages.append((score, page))
                seen_contents.append(page.content)
                if len(top_pages) >= 3: # Retrieve max 3 distinct chunks
                    break

        # Relevance checking using both cosine similarity and lexical overlap
        temp_context = " ".join([page.content for _, page in top_pages])
        if not top_pages or not check_context_relevance(question, temp_context, top_pages[0][0]):
            answer = "The uploaded documents do not contain enough information for this question."
            chat_memory.append({
                "role": "assistant",
                "content": answer
            })
            return {
                "question": question,
                "answer": answer,
                "sources": []
            }

        # Retrieve recent memory
        recent_memory = "\n".join([
            f"{msg['role']}: {msg['content']}"
            for msg in chat_memory[-6:]
        ])

        # Build PDF context
        pdf_context = "\n\n".join([
            f"--- Document Source Block ---\n{page.content}"
            for score, page in top_pages
        ])

        # Combine memory + PDF context
        context = f"""
Conversation History:
{recent_memory}

PDF Context:
{pdf_context}
"""

        # Store user message in history
        chat_memory.append({
            "role": "user",
            "content": question
        })

        # Generate AI answer (Flan-T5 generated definition/direct answer)
        direct_answer = generate_answer(
            pdf_context,
            question
        )

        if not direct_answer or "do not contain enough information" in direct_answer.lower():
            answer = "The uploaded documents do not contain enough information for this question."
            chat_memory.append({
                "role": "assistant",
                "content": answer
            })
            return {
                "question": question,
                "answer": answer,
                "sources": []
            }

        # Resolve topic from book name
        first_book_id = top_pages[0][1].book_id
        book_obj = db.query(Book).filter(Book.id == first_book_id).first()
        topic = book_obj.name.split('.')[0] if book_obj else "Uploaded Material"

        # Synthesize a beautiful, multi-paragraph ChatGPT-style response using hybrid techniques
        answer = synthesize_educational_response(question, top_pages, direct_answer, topic)

        # Store AI response
        chat_memory.append({
            "role": "assistant",
            "content": answer
        })

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

    finally:
        db.close()


@app.get("/generate-quiz")
def generate_ai_quiz(
    topic: str,
    current_user: str = Depends(get_current_user)
):

    db = SessionLocal()

    try:
        pages = db.query(Page).all()

        if not pages:
            return {
                "topic": topic,
                "quiz": "Not enough information found in uploaded documents.",
                "questions": []
            }

        query_vector = create_embedding(topic)

        similarities = []

        for page in pages:

            if not page.embedding:
                continue

            try:

                page_vector = np.array(
                    json.loads(page.embedding)
                )

                score = np.dot(
                    query_vector,
                    page_vector
                )

                similarities.append((score, page))

            except Exception:
                continue

        similarities.sort(
            reverse=True,
            key=lambda x: x[0]
        )

        # Filter weak matches and apply relative retrieval thresholding
        filtered_results = []
        if similarities:
            top_score = similarities[0][0]
            for score, page in similarities:
                if score > 0.22:
                    if top_score > 0.40:
                        if score >= top_score - 0.18:
                            filtered_results.append((score, page))
                    else:
                        filtered_results.append((score, page))

        # Deduplicate retrieved chunks
        top_pages = []
        seen_contents = []
        for score, page in filtered_results:
            is_duplicate = False
            page_words = set(page.content.lower().split())
            if not page_words:
                continue
            for seen_text in seen_contents:
                seen_words = set(seen_text.lower().split())
                if seen_words:
                    intersection = page_words.intersection(seen_words)
                    union = page_words.union(seen_words)
                    overlap = len(intersection) / len(union)
                    if overlap > 0.5:
                        is_duplicate = True
                        break
            if not is_duplicate:
                top_pages.append((score, page))
                seen_contents.append(page.content)
                if len(top_pages) >= 3:
                    break

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

    finally:
        db.close()