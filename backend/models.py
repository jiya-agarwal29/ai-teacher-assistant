from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, Integer, String, Text, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from database import Base

# Lifecycle of Book.status: a new upload starts "processing" (background
# job running). If any page needed OCR and review_ocr was set, it stops at
# "needs_review" (DocumentPage rows exist, but nothing is chunked/embedded
# yet -- no review/approve endpoint exists yet, a later phase adds one).
# Otherwise it proceeds straight to "ready" (searchable) or "failed" (error
# set, original file(s) kept on disk for /books/{id}/retry).
class Book(Base):
    __tablename__ = "books"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=True)

    status = Column(String, default="processing", nullable=False)
    error = Column(Text, nullable=True)
    source_type = Column(String, nullable=True)
    pages_total = Column(Integer, default=0, nullable=False)
    pages_done = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    # Whether OCR'd pages need manual review before being indexed (the
    # upload-time checkbox choice) -- stored so /books/{id}/retry can
    # re-run processing with the same choice instead of re-asking for it.
    review_ocr = Column(Boolean, default=True, nullable=False)

    pages = relationship("Page", cascade="all, delete-orphan")
    document_pages = relationship("DocumentPage", cascade="all, delete-orphan")


class Page(Base):
    __tablename__ = "pages"

    id = Column(Integer, primary_key=True, index=True)

    book_id = Column(
        Integer,
        ForeignKey("books.id"),
        index=True
    )

    page_number = Column(Integer)

    chunk_number = Column(Integer)

    content = Column(Text)

    embedding = Column(Text)

    # Which provider+model produced `embedding` (e.g. "voyage:voyage-4" or
    # "local:all-MiniLM-L6-v2") -- vectors from different models have
    # different dimensions and can't be compared, so retrieval only scores
    # pages whose embedding_model matches the currently active one.
    embedding_model = Column(String, nullable=True)


class DocumentPage(Base):
    """
    One page of a book's source material, however it was produced: real
    extracted text (method="text") or a Gemini-vision transcription of a
    rendered/photographed page image (method="ocr"). This is the canonical
    per-page record jobs.py chunks from -- Page rows (the searchable index)
    are only ever built from here, once every page's review_status is
    "auto_approved"/"approved" (see Book.status="needs_review").
    """
    __tablename__ = "document_pages"

    id = Column(Integer, primary_key=True, index=True)
    book_id = Column(Integer, ForeignKey("books.id"), index=True, nullable=False)
    page_number = Column(Integer, nullable=False)

    # Path to the saved page image on disk -- only set for method="ocr"
    # (a rendered PDF page or an uploaded photo); null for method="text".
    image_path = Column(String, nullable=True)

    extracted_text = Column(Text, nullable=True)

    # "text" (pdfplumber/docx/pptx/txt extraction) or "ocr" (Gemini vision).
    method = Column(String, nullable=False)

    # "auto_approved" (used as-is), "needs_review" (OCR'd, awaiting a human
    # check -- no review/approve endpoint exists yet), or "approved" (will
    # be set by that future endpoint).
    review_status = Column(String, nullable=False)

    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False
    )


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True)
    password = Column(String)