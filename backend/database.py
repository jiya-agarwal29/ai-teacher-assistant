from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base

from env_utils import get_env

# "production" skips the dev-only create_all() in main.py's lifespan --
# schema changes in production go through Alembic migrations instead (see
# backend/alembic/). Any other value (default: "development") keeps the
# old create-on-startup convenience for a local SQLite dev database.
ENV = get_env("ENV", "development")

# Absolute path next to this file, not a "./"-relative one -- a relative
# path resolves against the process's current working directory, which
# depends on where uvicorn/pytest/etc. happen to be launched from, and can
# silently point at (or create) the wrong database file.
_DEFAULT_DB_PATH = (Path(__file__).resolve().parent / "teacher_ai.db").as_posix()


def normalize_database_url(url: str) -> str:
    """
    Render/Heroku/Neon and most managed-Postgres hosts hand out a
    "postgres://" or driverless "postgresql://" URL -- SQLAlchemy's own
    default for either is psycopg2, which isn't installed here (this
    project uses psycopg 3). Rewrites both to the explicit
    "postgresql+psycopg://" driver URL; anything else (sqlite:///..., or a
    URL that already names its own driver) is returned unchanged.
    """
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


# The raw, un-defaulted setting -- exposed separately from DATABASE_URL
# below so main.py's production validation can tell "nothing set" / "a
# non-Postgres URL" apart from an actually-valid one, instead of only ever
# seeing the already-defaulted-to-sqlite value.
RAW_DATABASE_URL_SETTING = get_env("DATABASE_URL")

# Checked here, before create_engine() ever sees the value (not only in
# main.py's own _validate_production_settings(), which runs too late --
# `import main` reaches `import jobs` -> `import database` -> the
# `engine = make_engine(...)` line below long before it gets to its own
# validation). Without this, a malformed production DATABASE_URL (e.g.
# missing "://") fails here first with SQLAlchemy's own cryptic "Could not
# parse SQLAlchemy URL from given URL string" instead of a clear message
# (see DEPLOY.md's troubleshooting section -- this *is* that bug).
if ENV == "production" and not (RAW_DATABASE_URL_SETTING or "").startswith(("postgres://", "postgresql://")):
    raise RuntimeError(
        "DATABASE_URL looks malformed — expected postgresql://user:pass@host/db"
    )

DATABASE_URL = normalize_database_url(RAW_DATABASE_URL_SETTING or f"sqlite:///{_DEFAULT_DB_PATH}")


def make_engine(url: str):
    """
    The one engine-construction path for this URL, shared by the app
    itself (`engine` below) and the test suite (tests/test_api.py's own
    engine, pointed at TEST_DATABASE_URL) -- so a Postgres connection gets
    the same pooling treatment in both instead of that logic drifting
    between two copies.
    """
    if url.startswith("sqlite"):
        # One shared file, opened from multiple threads (FastAPI's
        # threadpool, jobs.py's background worker) -- check_same_thread
        # must be off for any of them to use it.
        sqlite_engine = create_engine(url, connect_args={"check_same_thread": False})

        # SQLite ignores FOREIGN KEY constraints by default (unlike
        # Postgres, which always enforces them) -- this had let a real bug
        # (DELETE /books/{id} leaving a dangling PaperQuestion.book_id)
        # pass silently on SQLite for the project's whole lifetime until
        # Postgres caught it. Turning this on for every new connection
        # makes local dev and the SQLite test run enforce the same links
        # Postgres does, instead of relying on Postgres to catch it.
        @event.listens_for(sqlite_engine, "connect")
        def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        return sqlite_engine

    # Postgres (Neon, etc.): pool_pre_ping discovers and transparently
    # replaces a connection Neon's own idle-compute-suspend already
    # dropped, instead of handing back a dead one on first use;
    # pool_recycle=300 proactively retires connections before Neon's own
    # idle timeout gets to. A small pool keeps this well under a managed
    # Postgres instance's total connection cap even with a few uvicorn
    # workers. SSL (sslmode=require, as Neon's own connection strings
    # already include) needs no extra handling -- SQLAlchemy forwards any
    # query parameter on the URL straight through to psycopg as a connect
    # argument.
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=300,
        pool_size=5,
        max_overflow=5,
    )


engine = make_engine(DATABASE_URL)

SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
