from sqlalchemy import Column, Integer, String, Text, ForeignKey
from sqlalchemy.orm import relationship
from database import Base

class Book(Base):
    __tablename__ = "books"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=True)

    pages = relationship("Page", cascade="all, delete-orphan")


class Page(Base):
    __tablename__ = "pages"

    id = Column(Integer, primary_key=True, index=True)

    book_id = Column(
        Integer,
        ForeignKey("books.id")
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