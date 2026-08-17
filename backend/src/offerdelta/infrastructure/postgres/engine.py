"""Database connections.

Neon puts the database behind a pooler that can drop an idle connection at any
time, so `pool_pre_ping` is on: a stale connection is discovered and replaced
before a query rather than surfacing as a mystery failure mid-request.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from offerdelta.config import get_settings


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    settings = get_settings()
    return create_engine(
        settings.sqlalchemy_dsn,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        connect_args={"connect_timeout": 15},
    )


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


@contextmanager
def unit_of_work() -> Iterator[Session]:
    """One transaction per business operation.

    Commits on success, rolls back on any exception. A partially written run is
    worse than no run at all: the breakdown would no longer add up to its own
    headline figure.
    """
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
