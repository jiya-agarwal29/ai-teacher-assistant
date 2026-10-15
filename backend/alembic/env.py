import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# backend/ (this file's grandparent) on sys.path, regardless of the cwd
# `alembic` was invoked from, so `import database`/`models` below always
# resolves the same way main.py's own imports do.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
from models import Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# The same DATABASE_URL (env var, normalized postgres://->postgresql+psycopg://)
# the app itself connects with -- not alembic.ini's own sqlalchemy.url, so
# there's exactly one place this is configured, not two that can drift.
config.set_main_option("sqlalchemy.url", database.DATABASE_URL)

# Interpret the config file for Python logging.
# This line sets up loggers basically.
# disable_existing_loggers=False -- fileConfig()'s own default (True) tears
# down every logger/handler that isn't named in alembic.ini's own config,
# which otherwise silently breaks pytest's caplog capture (and main.py's
# own app-wide logging) for the rest of the process once an Alembic
# command has run once in it (e.g. tests/test_alembic_migrations.py).
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# add your model's MetaData object here
# for 'autogenerate' support
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=url.startswith("sqlite"),
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # SQLite can't ALTER TABLE for most operations (drop/alter a
        # column, add a constraint, ...) -- batch mode emulates each of
        # those by building a new table and copying the data over instead.
        # Postgres supports real ALTER TABLE, so it skips this.
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=connection.dialect.name == "sqlite",
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
