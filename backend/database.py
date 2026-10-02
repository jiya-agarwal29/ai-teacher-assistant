import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Absolute path next to this file, not a "./"-relative one -- a relative
# path resolves against the process's current working directory, which
# depends on where uvicorn/pytest/etc. happen to be launched from, and can
# silently point at (or create) the wrong database file.
_DEFAULT_DB_PATH = (Path(__file__).resolve().parent / "teacher_ai.db").as_posix()
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{_DEFAULT_DB_PATH}")

engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()