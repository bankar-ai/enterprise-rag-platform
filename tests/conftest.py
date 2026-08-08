"""Shared pytest fixtures: points every test run at a real test Postgres and manages schema."""

import os

os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/erp_test"
)

import fitz  # noqa: E402
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


@pytest.fixture
def simple_text_pdf(tmp_path):
    """A one-page PDF with a heading and body text — should stay on the fast path.

    Shared at the repo root (rather than tests/ingestion/conftest.py) because the retrieval
    router's end-to-end test also needs it to exercise the full ingestion -> retrieval flow.
    """
    path = tmp_path / "simple.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Introduction", fontsize=18)
    page.insert_text(
        (72, 100), "This is a simple paragraph of body text for testing extraction.", fontsize=11
    )
    doc.save(str(path))
    doc.close()
    return str(path)
