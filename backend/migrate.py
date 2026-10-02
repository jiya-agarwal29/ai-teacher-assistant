"""
One-off migration for existing SQLite databases created before books were
tied to a user account, and before pages tracked which embedding model
produced their vector.

Adds the books.user_id column if it isn't already there, then assigns every
currently-unowned book to the given username. Also adds the
pages.embedding_model column if missing and backfills it to
"local:all-MiniLM-L6-v2" on existing rows -- all pages embedded before this
column existed were embedded with the local MiniLM model. Also creates an
index on pages.book_id if missing (new databases get it automatically via
models.py; this backfills it onto existing ones). Safe to run more than
once: every ALTER TABLE / CREATE INDEX is skipped once it already exists,
and every UPDATE only ever touches rows that still need it.

Usage:
    python migrate.py <username>
"""
import logging
import os
import sqlite3
import sys

from database import DATABASE_URL

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

    logger.info("Assigned %d previously-unowned book(s) to '%s' (user_id=%d).", unowned_count, username, user_id)


if __name__ == "__main__":
    main()
