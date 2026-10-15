"""
Phase 7A: guards against drift between models.py and the Alembic migration
set in backend/alembic/versions/ -- the two are maintained by hand (every
schema change is its own `alembic revision --autogenerate`), so nothing
else catches them silently falling out of sync.
"""
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

import database
from models import Base

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _table_columns(engine):
    insp = inspect(engine)
    return {
        table: sorted(c["name"] for c in insp.get_columns(table))
        for table in insp.get_table_names()
        if table != "alembic_version"
    }


def test_alembic_upgrade_head_matches_base_metadata_schema(tmp_path, monkeypatch):
    """
    Running every migration up to head against a brand-new, empty SQLite
    database must produce exactly the same tables/columns
    Base.metadata.create_all() would build directly from the current
    models -- if they disagree, a migration is missing, wrong, or models.py
    changed without a matching revision.
    """
    db_path = tmp_path / "alembic_drift_check.db"
    db_url = f"sqlite:///{db_path.as_posix()}"

    # alembic/env.py reads database.DATABASE_URL itself (not alembic.ini's
    # own value, and not anything set on the Config object below) -- the
    # same single-source-of-truth wiring the real app uses -- so that's
    # what needs patching to point this migration run at a throwaway
    # database instead of whatever DATABASE_URL the test session has.
    monkeypatch.setattr(database, "DATABASE_URL", db_url)

    alembic_cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(alembic_cfg, "head")

    migrated_engine = create_engine(db_url)
    try:
        migrated_tables = _table_columns(migrated_engine)
    finally:
        migrated_engine.dispose()

    meta_engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(bind=meta_engine)
        meta_tables = _table_columns(meta_engine)
    finally:
        meta_engine.dispose()

    assert migrated_tables == meta_tables
