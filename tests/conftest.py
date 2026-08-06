"""Shared pytest fixtures: points every test run at a real test Postgres and manages schema."""

import os

os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/erp_test"
)

import pytest  # noqa: E402

from app.core.db import get_engine  # noqa: E402
from app.ingestion.models import Base  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _database_schema():
    """Create all tables before the test session, drop them after."""
    engine = get_engine()
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)
