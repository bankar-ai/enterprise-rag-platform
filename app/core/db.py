"""SQLAlchemy engine and session factory, lazily constructed from `DatabaseSettings`."""

from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_database_settings


@lru_cache
def get_engine() -> Engine:
    """Return the process-wide cached SQLAlchemy engine."""
    return create_engine(get_database_settings().database_url, pool_pre_ping=True)


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    """Return the process-wide cached session factory, bound to `get_engine()`."""
    return sessionmaker(bind=get_engine(), expire_on_commit=False)
