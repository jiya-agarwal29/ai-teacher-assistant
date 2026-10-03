from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from database import Base

# Lifecycle of Book.status: a new upload starts "processing" (background
# job running), then becomes "ready" (searchable) or "failed" (error set,
# original file kept on disk for /books/{id}/retry). "needs_review" is
# reserved for a future OCR step and isn't produced yet.
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

    pages = relationship("Page", cascade="all, delete-orphan")


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


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True)
    password = Column(String)