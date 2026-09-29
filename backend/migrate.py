"""
One-off migration for existing SQLite databases created before books were
tied to a user account.

Adds the books.user_id column if it isn't already there, then assigns every
currently-unowned book to the given username. Safe to run more than once:
the ALTER TABLE is skipped once the column exists, and the UPDATE only ever
touches books that still have no owner.

Usage:
    python migrate.py <username>
"""
import logging
import os
import sqlite3
import sys

from database import DATABASE_URL

DB_PATH = DATABASE_URL.replace("sqlite:///", "", 1)

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(message)s",
    stream=sys.stdout
)
logger = logging.getLogger(__name__)


def column_exists(cursor, table, column):
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


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
