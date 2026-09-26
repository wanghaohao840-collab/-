"""Alembic connection policy for the explicit PostgreSQL cutover."""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def database_url():
    value = os.environ.get("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL is required for Alembic migrations")
    url = make_url(value)
    if url.drivername not in {"postgresql", "postgresql+psycopg"}:
        raise RuntimeError("Alembic migrations require a postgresql or postgresql+psycopg DATABASE_URL")
    return url.set(drivername="postgresql+psycopg")


def run_migrations_offline() -> None:
    context.configure(url=database_url().render_as_string(hide_password=False), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url(), poolclass=NullPool)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
