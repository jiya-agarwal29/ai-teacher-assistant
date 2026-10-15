"""
DEPRECATED (Phase 7A) -- schema changes now go through Alembic migrations
(see backend/alembic/, and README's "Database migrations" section) instead
of this script. Kept only so a local SQLite dev database created before
Alembic existed can still be brought current by running it one last time,
then `alembic stamp head` (see the README) to mark it as up to date without
Alembic trying to recreate anything it already has. Do not add anything new
here -- a new schema change is a new `alembic revision --autogenerate`.

One-off migration for existing SQLite databases created before books were
tied to a user account, and before pages tracked which embedding model
produced their vector.

Adds the books.user_id column if it isn't already there, then assigns every
currently-unowned book to the given username. Also adds the
pages.embedding_model column if missing and backfills it to
"local:all-MiniLM-L6-v2" on existing rows -- all pages embedded before this
column existed were embedded with the local MiniLM model. Also creates an
index on pages.book_id if missing (new databases get it automatically via
models.py; this backfills it onto existing ones). Also adds the Phase 3A
background-processing columns (books.status/error/source_type/pages_total/
pages_done/created_at) if missing -- every book that existed before these
columns did was fully processed synchronously, so it backfills
status="ready" on those rows (never "processing" or "failed", which only
apply to the new background pipeline). Also adds the Phase 3B OCR columns
(books.review_ocr, and the whole document_pages table) if missing. Also
creates the Phase 4B questions table if missing. Also adds the Phase 5B
questions.marks/marking_scheme columns and the papers/paper_questions
tables, plus the Phase 5B fix columns papers.unit_weights/waiting_for_ai,
if missing. Also adds the Phase 6A users.analytics_imported_at column and
the activity_events/quiz_attempts/quiz_answers tables, plus the Phase 6C
users.chats_imported_at column and the conversations/chat_messages
tables, if missing. Safe to run more than once: every ALTER TABLE /
CREATE TABLE / CREATE INDEX is skipped once it already exists, and every
UPDATE only ever touches rows that still need it.

Usage:
    python migrate.py <username>
"""
import logging
import os
import sqlite3
import sys

from database import DATABASE_URL, engine
from models import Base

DB_PATH = DATABASE_URL.replace("sqlite:///", "", 1)

# All pages embedded before pages.embedding_model existed were embedded with
# the local MiniLM model -- Voyage support (and this column) came later.
LEGACY_EMBEDDING_MODEL = "local:all-MiniLM-L6-v2"

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(message)s",
    stream=sys.stdout
)
logger = logging.getLogger(__name__)


def column_exists(cursor, table, column):
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def index_exists(cursor, table, index_name):
    cursor.execute(f"PRAGMA index_list({table})")
    return any(row[1] == index_name for row in cursor.fetchall())


def table_exists(cursor, table):
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cursor.fetchone() is not None


def main():
    if len(sys.argv) != 2:
        logger.info("Usage: python migrate.py <username>")
        sys.exit(1)

    username = sys.argv[1]

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    if column_exists(cursor, "books", "user_id"):
        logger.info("books.user_id column already exists, skipping ALTER TABLE.")
    else:
        cursor.execute("ALTER TABLE books ADD COLUMN user_id INTEGER REFERENCES users(id)")
        conn.commit()
        logger.info("Added books.user_id column.")

    if column_exists(cursor, "pages", "embedding_model"):
        logger.info("pages.embedding_model column already exists, skipping ALTER TABLE.")
    else:
        cursor.execute("ALTER TABLE pages ADD COLUMN embedding_model TEXT")
        conn.commit()
        logger.info("Added pages.embedding_model column.")

    cursor.execute("SELECT COUNT(*) FROM pages WHERE embedding_model IS NULL")
    unset_embedding_model_count = cursor.fetchone()[0]
    cursor.execute(
        "UPDATE pages SET embedding_model = ? WHERE embedding_model IS NULL",
        (LEGACY_EMBEDDING_MODEL,)
    )
    conn.commit()
    logger.info(
        "Set embedding_model='%s' on %d existing page(s).",
        LEGACY_EMBEDDING_MODEL, unset_embedding_model_count
    )

    if index_exists(cursor, "pages", "ix_pages_book_id"):
        logger.info("pages.book_id index already exists, skipping CREATE INDEX.")
    else:
        cursor.execute("CREATE INDEX ix_pages_book_id ON pages (book_id)")
        conn.commit()
        logger.info("Created index on pages.book_id.")

    # Phase 3A: background document processing. A book from before this
    # existed was necessarily processed synchronously to completion already,
    # so it backfills to status="ready" (not "processing"/"failed", which
    # only ever come from the new background pipeline).
    new_book_columns = [
        ("status", "TEXT"),
        ("error", "TEXT"),
        ("source_type", "TEXT"),
        ("pages_total", "INTEGER"),
        ("pages_done", "INTEGER"),
        ("created_at", "TIMESTAMP"),
    ]
    for column, ddl_type in new_book_columns:
        if column_exists(cursor, "books", column):
            logger.info("books.%s column already exists, skipping ALTER TABLE.", column)
        else:
            cursor.execute(f"ALTER TABLE books ADD COLUMN {column} {ddl_type}")
            conn.commit()
            logger.info("Added books.%s column.", column)

    cursor.execute("SELECT COUNT(*) FROM books WHERE status IS NULL")
    unset_status_count = cursor.fetchone()[0]
    cursor.execute("UPDATE books SET status = 'ready' WHERE status IS NULL")
    cursor.execute("UPDATE books SET pages_total = 0 WHERE pages_total IS NULL")
    cursor.execute("UPDATE books SET pages_done = 0 WHERE pages_done IS NULL")
    cursor.execute("UPDATE books SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL")
    conn.commit()
    logger.info("Set status='ready' on %d existing book(s).", unset_status_count)

    # Phase 3B: OCR. A pre-existing book was never OCR'd, so review_ocr's
    # actual historical value doesn't matter -- it's only read again if that
    # book is later retried, and a non-OCR'd document ignores it entirely.
    if column_exists(cursor, "books", "review_ocr"):
        logger.info("books.review_ocr column already exists, skipping ALTER TABLE.")
    else:
        cursor.execute("ALTER TABLE books ADD COLUMN review_ocr BOOLEAN")
        conn.commit()
        logger.info("Added books.review_ocr column.")
    cursor.execute("UPDATE books SET review_ocr = 1 WHERE review_ocr IS NULL")
    conn.commit()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS document_pages (
            id INTEGER PRIMARY KEY,
            book_id INTEGER NOT NULL REFERENCES books(id),
            page_number INTEGER NOT NULL,
            image_path VARCHAR,
            extracted_text TEXT,
            method VARCHAR NOT NULL,
            review_status VARCHAR NOT NULL,
            updated_at TIMESTAMP NOT NULL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS ix_document_pages_book_id ON document_pages (book_id)")
    conn.commit()
    logger.info("Ensured document_pages table and index exist.")

    # Phase 4B: the questions table, and Phase 5B: questions.marks/
    # marking_scheme plus the papers/paper_questions tables. create_all()
    # only ever creates tables that don't exist yet (every other table
    # here is left untouched), so it's safe even though this file
    # otherwise speaks raw sqlite3 -- but it needs its own SQLAlchemy
    # engine connection, so check existence here (on the already-open
    # cursor) and run create_all() after conn.close() below, rather than
    # holding both connections open at once.
    questions_table_already_existed = table_exists(cursor, "questions")
    papers_tables_already_existed = table_exists(cursor, "papers") and table_exists(cursor, "paper_questions")

    if questions_table_already_existed:
        for column, ddl_type in (("marks", "INTEGER"), ("marking_scheme", "TEXT")):
            if column_exists(cursor, "questions", column):
                logger.info("questions.%s column already exists, skipping ALTER TABLE.", column)
            else:
                cursor.execute(f"ALTER TABLE questions ADD COLUMN {column} {ddl_type}")
                conn.commit()
                logger.info("Added questions.%s column.", column)

    # Phase 5B fix: papers.unit_weights (the weights actually used, JSON)
    # and papers.waiting_for_ai (set while the background job is paused on
    # a busy AI response). Only relevant if the papers table already
    # existed -- a brand-new one gets both from create_all() below.
    if table_exists(cursor, "papers"):
        if column_exists(cursor, "papers", "unit_weights"):
            logger.info("papers.unit_weights column already exists, skipping ALTER TABLE.")
        else:
            cursor.execute("ALTER TABLE papers ADD COLUMN unit_weights TEXT")
            conn.commit()
            logger.info("Added papers.unit_weights column.")

        if column_exists(cursor, "papers", "waiting_for_ai"):
            logger.info("papers.waiting_for_ai column already exists, skipping ALTER TABLE.")
        else:
            cursor.execute("ALTER TABLE papers ADD COLUMN waiting_for_ai BOOLEAN")
            conn.commit()
            cursor.execute("UPDATE papers SET waiting_for_ai = 0 WHERE waiting_for_ai IS NULL")
            conn.commit()
            logger.info("Added papers.waiting_for_ai column.")

    # Phase 6A: users.analytics_imported_at (null means that user's old
    # localStorage analytics history was never imported).
    if column_exists(cursor, "users", "analytics_imported_at"):
        logger.info("users.analytics_imported_at column already exists, skipping ALTER TABLE.")
    else:
        cursor.execute("ALTER TABLE users ADD COLUMN analytics_imported_at TIMESTAMP")
        conn.commit()
        logger.info("Added users.analytics_imported_at column.")

    analytics_tables_already_existed = (
        table_exists(cursor, "activity_events")
        and table_exists(cursor, "quiz_attempts")
        and table_exists(cursor, "quiz_answers")
    )

    # Phase 6C: users.chats_imported_at (null means that user's old
    # localStorage chat history was never imported).
    if column_exists(cursor, "users", "chats_imported_at"):
        logger.info("users.chats_imported_at column already exists, skipping ALTER TABLE.")
    else:
        cursor.execute("ALTER TABLE users ADD COLUMN chats_imported_at TIMESTAMP")
        conn.commit()
        logger.info("Added users.chats_imported_at column.")

    chat_tables_already_existed = (
        table_exists(cursor, "conversations") and table_exists(cursor, "chat_messages")
    )

    cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
    row = cursor.fetchone()

    if row is None:
        logger.error("No user named '%s' found. Register that account first, then re-run this migration.", username)
        conn.close()
        sys.exit(1)

    user_id = row[0]

    cursor.execute("SELECT COUNT(*) FROM books WHERE user_id IS NULL")
    unowned_count = cursor.fetchone()[0]

    cursor.execute("UPDATE books SET user_id = ? WHERE user_id IS NULL", (user_id,))
    conn.commit()
    conn.close()

    if (
        questions_table_already_existed and papers_tables_already_existed
        and analytics_tables_already_existed and chat_tables_already_existed
    ):
        logger.info("questions/papers/paper_questions/analytics/chat tables already exist, skipping create_all.")
    else:
        Base.metadata.create_all(bind=engine)
        if not questions_table_already_existed:
            logger.info("Created questions table.")
        if not papers_tables_already_existed:
            logger.info("Created papers and paper_questions tables.")
        if not analytics_tables_already_existed:
            logger.info("Created activity_events, quiz_attempts, and quiz_answers tables.")
        if not chat_tables_already_existed:
            logger.info("Created conversations and chat_messages tables.")

    logger.info("Assigned %d previously-unowned book(s) to '%s' (user_id=%d).", unowned_count, username, user_id)


if __name__ == "__main__":
    main()
