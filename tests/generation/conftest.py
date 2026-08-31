"""Generation test configuration - disable global database fixture."""

import pytest


@pytest.fixture(scope="session", autouse=True)
def _database_schema():
    """Override global database fixture - generation tests don't need a database."""
    yield
