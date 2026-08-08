# Embedding Generation & Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a `DONE` ingestion job mean the document's chunks are embedded (Nomic Embed via Ollama) and durably persisted (Postgres for text/metadata, FAISS for vectors), instead of living only in an in-memory job record.

**Architecture:** Two new modules — `app/core/` (shared DB engine/session) and `app/embedding/` (Ollama client, FAISS wrapper, orchestration service) — plus `app/ingestion/models.py`/`repository.py` for Postgres persistence. `run_ingestion_job` (`app/ingestion/jobs.py`) is extended to call the new `embed_and_persist` orchestration after the existing parse+chunk step, inside the same try/except so failures are still caught, logged, and reported as `FAILED`.

**Tech Stack:** SQLAlchemy 2.0 + Alembic + `psycopg[binary]` (Postgres), `ollama` Python client (embeddings), `faiss-cpu` (vector index), pytest against a real Postgres service container.

## Global Constraints

- Never use `pip install`/`pip uninstall` — always `uv add`/`uv remove`/`uv sync`/`uv run` (`CLAUDE.md`).
- Never use `print()` in application code; use the existing stdlib `logging` convention (module-level `logger = logging.getLogger(__name__)`, `logger.exception(...)` on non-re-raising excepts) (`docs/engineering-guidelines.md`, ERP-006).
- `--strict` Mypy applies to `app/` (not `tests/`); new third-party modules with no type stubs need a `[[tool.mypy.overrides]]` entry with `ignore_missing_imports = true`, following the existing `pymupdf4llm` precedent in `pyproject.toml`.
- `pytest --cov=app --cov-fail-under=90` gates CI; all new modules need real test coverage, not placeholder tests.
- Business logic never lives in API routes (`docs/architecture.md`) — not directly relevant here since no new routes are added, but `app/ingestion/router.py` must stay untouched.
- All dependencies must be open-source and free (`docs/architecture.md`) — every dependency added below is.
- Never hardcode secrets; Postgres credentials in `docker-compose.yml`/CI are local dev-only placeholders (`postgres`/`postgres`), consistent with how other local-only, non-production credentials are handled in this repo.
- Redis embedding cache is explicitly out of scope for this plan (deferred per the design spec).

---

## File Structure

- `pyproject.toml` — modify: add 5 new runtime dependencies, 2 new mypy overrides.
- `app/core/__init__.py` — create: empty package marker.
- `app/core/config.py` — create: `DatabaseSettings` (reads `DATABASE_URL`).
- `app/core/db.py` — create: lazily-constructed SQLAlchemy engine + session factory.
- `app/ingestion/models.py` — create: `Base`, `DocumentRecord`, `ChunkRecord` ORM models.
- `app/ingestion/repository.py` — create: `save_document_and_chunks`.
- `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`, `alembic/versions/<rev>_create_documents_and_chunks.py` — create: migration environment + initial schema revision.
- `app/embedding/__init__.py` — create: empty package marker.
- `app/embedding/config.py` — create: `EmbeddingSettings` (Ollama host/model/dimension, FAISS index path).
- `app/embedding/client.py` — create: `EmbeddingClient` protocol + `OllamaEmbeddingClient`.
- `app/embedding/index.py` — create: `FaissIndex` wrapper.
- `app/embedding/service.py` — create: `embed_and_persist` orchestration.
- `app/ingestion/jobs.py` — modify: `run_ingestion_job` calls `embed_and_persist` after `ingest_pdf`.
- `docker-compose.yml` — create: local-dev Postgres service.
- `.github/workflows/ci.yml` — modify: add Postgres service container, alembic-applies-cleanly check.
- `.gitignore` — modify: ignore the local FAISS index data directory.
- `tests/conftest.py` — create: session-scoped fixture that points `DATABASE_URL` at a test database and creates/drops the schema.
- `tests/ingestion/test_repository.py` — create.
- `tests/embedding/__init__.py`, `tests/embedding/test_client.py`, `tests/embedding/test_index.py`, `tests/embedding/test_service.py` — create.
- `tests/ingestion/test_jobs.py` — modify: extend for the embed+persist stage.

---

### Task 1: Add dependencies

**Files:**
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `sqlalchemy`, `alembic`, `psycopg[binary]`, `ollama`, `faiss-cpu` importable from the project's venv.

- [ ] **Step 1: Add the runtime dependencies**

Run:
```bash
uv add sqlalchemy alembic "psycopg[binary]" ollama faiss-cpu
```
Expected: `pyproject.toml`'s `[project].dependencies` gains all 5 packages; `uv.lock` updates.

- [ ] **Step 2: Add mypy overrides for untyped packages**

Edit `pyproject.toml`, after the existing `pymupdf4llm` override:

```toml
[[tool.mypy.overrides]]
module = "pymupdf4llm"
ignore_missing_imports = true

[[tool.mypy.overrides]]
module = "faiss"
ignore_missing_imports = true

[[tool.mypy.overrides]]
module = "ollama"
ignore_missing_imports = true
```

- [ ] **Step 3: Verify the environment builds and existing checks still pass**

Run: `uv sync && uv run ruff check . && uv run mypy app`
Expected: all pass (no code uses the new packages yet, so nothing new to check).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add sqlalchemy, alembic, psycopg, ollama, faiss-cpu dependencies"
```

---

### Task 2: Database config and session factory

**Files:**
- Create: `app/core/__init__.py`
- Create: `app/core/config.py`
- Create: `app/core/db.py`
- Test: `tests/core/__init__.py`, `tests/core/test_db.py`

**Interfaces:**
- Produces: `DatabaseSettings` (field `database_url: str`), `get_database_settings() -> DatabaseSettings`, `get_engine() -> Engine`, `get_session_factory() -> sessionmaker[Session]`.

- [ ] **Step 1: Create the package marker**

Create `app/core/__init__.py`:
```python
"""Shared, cross-feature infrastructure (database, future cross-cutting concerns)."""
```

- [ ] **Step 2: Write the failing test for settings**

Create `tests/core/__init__.py` (empty file).

Create `tests/core/test_db.py`:
```python
import os

from app.core.config import DatabaseSettings
from app.core.db import get_engine, get_session_factory


def test_database_settings_reads_env_var(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@example.invalid:5432/db")
    settings = DatabaseSettings()
    assert settings.database_url == "postgresql+psycopg://u:p@example.invalid:5432/db"


def test_database_settings_has_a_default():
    assert "postgresql" in DatabaseSettings().database_url


def test_get_engine_and_session_factory_are_cached(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ.get("DATABASE_URL", DatabaseSettings().database_url))
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    assert get_engine() is get_engine()
    assert get_session_factory() is get_session_factory()
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/core/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.config'`

- [ ] **Step 4: Implement `app/core/config.py`**

```python
"""Database connection settings, loaded from environment variables."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """Postgres connection settings. Overridable via the `DATABASE_URL` env var."""

    model_config = SettingsConfigDict(env_prefix="")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/erp"


@lru_cache
def get_database_settings() -> DatabaseSettings:
    """Return the process-wide cached `DatabaseSettings` instance."""
    return DatabaseSettings()
```

- [ ] **Step 5: Implement `app/core/db.py`**

```python
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
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `uv run pytest tests/core/test_db.py -v`
Expected: PASS (3 tests)

- [ ] **Step 7: Commit**

```bash
git add app/core tests/core
git commit -m "feat: add database settings and lazily-constructed session factory"
```

---

### Task 3: SQLAlchemy models

**Files:**
- Create: `app/ingestion/models.py`
- Test: `tests/ingestion/test_models.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `Base` (declarative base, its `.metadata` is used by Task 4's Alembic env and `tests/conftest.py`), `DocumentRecord(document_id: str, filename: str, created_at: datetime)`, `ChunkRecord(chunk_id: str, document_id: str, chunk_index: int, text: str, section_path: list[str], page_start: int, page_end: int, char_count: int, parser_used: str, source_filename: str, vector_id: int)`.

- [ ] **Step 1: Write the failing test**

Create `tests/ingestion/test_models.py`:
```python
from app.ingestion.models import Base, ChunkRecord, DocumentRecord


def test_document_record_table_name():
    assert DocumentRecord.__tablename__ == "documents"


def test_chunk_record_table_name():
    assert ChunkRecord.__tablename__ == "chunks"


def test_chunk_record_columns_present():
    columns = {c.name for c in ChunkRecord.__table__.columns}
    assert columns == {
        "chunk_id",
        "document_id",
        "chunk_index",
        "text",
        "section_path",
        "page_start",
        "page_end",
        "char_count",
        "parser_used",
        "source_filename",
        "vector_id",
    }


def test_base_metadata_knows_both_tables():
    assert {"documents", "chunks"} <= set(Base.metadata.tables)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingestion/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.ingestion.models'`

- [ ] **Step 3: Implement `app/ingestion/models.py`**

```python
"""SQLAlchemy ORM models for persisted documents and chunks."""

from datetime import datetime

from sqlalchemy import JSON, ForeignKey, Identity, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    """Shared declarative base for all ingestion ORM models."""


class DocumentRecord(Base):
    """A single ingested document."""

    __tablename__ = "documents"

    document_id: Mapped[str] = mapped_column(primary_key=True)
    filename: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ChunkRecord(Base):
    """A single persisted chunk of a document, with full provenance metadata.

    `vector_id` is a separate autoincrement integer identity, distinct from the
    business-facing string `chunk_id` — FAISS requires int64 vector IDs.
    """

    __tablename__ = "chunks"

    chunk_id: Mapped[str] = mapped_column(primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.document_id"), index=True)
    chunk_index: Mapped[int]
    text: Mapped[str] = mapped_column(Text)
    section_path: Mapped[list[str]] = mapped_column(JSON)
    page_start: Mapped[int]
    page_end: Mapped[int]
    char_count: Mapped[int]
    parser_used: Mapped[str]
    source_filename: Mapped[str]
    vector_id: Mapped[int] = mapped_column(Identity(always=True), unique=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ingestion/test_models.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/models.py tests/ingestion/test_models.py
git commit -m "feat: add DocumentRecord and ChunkRecord SQLAlchemy models"
```

---

### Task 4: Alembic migration environment and initial schema

**Files:**
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/script.py.mako` (generated by `alembic init`, left as-is)
- Create: `alembic/versions/<generated>_create_documents_and_chunks.py`
- Create: `docker-compose.yml` (needed here to have a Postgres to autogenerate against)

**Interfaces:**
- Consumes: `app.ingestion.models.Base`, `app.core.config.get_database_settings`.
- Produces: `alembic upgrade head` creates the `documents` and `chunks` tables matching Task 3's models exactly.

- [ ] **Step 1: Add the local-dev Postgres service**

Create `docker-compose.yml`:
```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: erp
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data

volumes:
  postgres_data:
```

Run: `docker compose up -d postgres`
Expected: container starts and reports healthy (`docker compose ps` shows `Up`).

- [ ] **Step 2: Initialize the Alembic environment**

Run: `uv run alembic init alembic`
Expected: creates `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`, `alembic/versions/`.

Exclude the generated directory from Ruff — `alembic init`'s boilerplate (and future autogenerated revisions) isn't held to the same docstring/style conventions as `app/`. Edit `pyproject.toml`'s `[tool.ruff]` section:

```toml
[tool.ruff]
line-length = 110
target-version = "py312"
extend-exclude = ["alembic"]
```

Run: `uv run ruff check .`
Expected: passes (alembic's generated files are now excluded).

- [ ] **Step 3: Point Alembic at the app's models and settings**

Edit `alembic/env.py`, replacing the `target_metadata = None` line and adding the URL wiring near the top of the file (after the existing imports, before `config = context.config`):

```python
from app.core.config import get_database_settings
from app.ingestion.models import Base

target_metadata = Base.metadata
```

Then find the line `config = context.config` and add immediately after it:
```python
config.set_main_option("sqlalchemy.url", get_database_settings().database_url)
```

Remove the line `target_metadata = None` that `alembic init` generated (it's superseded by the `target_metadata = Base.metadata` line added above).

- [ ] **Step 4: Generate the initial revision**

Run:
```bash
DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/erp" uv run alembic revision --autogenerate -m "create documents and chunks tables"
```
Expected: a new file appears under `alembic/versions/`, containing `op.create_table("documents", ...)` and `op.create_table("chunks", ...)` matching Task 3's models (including the `chunks.document_id` foreign key and the `vector_id` identity column). Open the generated file and confirm both tables and all columns from `test_chunk_record_columns_present` (Task 3) are present.

- [ ] **Step 5: Apply and verify the migration**

Run:
```bash
DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/erp" uv run alembic upgrade head
```
Expected: exits 0. Verify tables exist:
```bash
docker compose exec postgres psql -U postgres -d erp -c "\dt"
```
Expected: lists `documents`, `chunks`, and `alembic_version`.

- [ ] **Step 6: Commit**

```bash
git add alembic.ini alembic docker-compose.yml
git commit -m "feat: add alembic migration environment and initial schema"
```

---

### Task 5: Repository — persist a document and its chunks

**Files:**
- Create: `app/ingestion/repository.py`
- Create: `tests/conftest.py`
- Create: `tests/ingestion/test_repository.py`

**Interfaces:**
- Consumes: `app.ingestion.models.{Base, DocumentRecord, ChunkRecord}`, `app.ingestion.schemas.Chunk`, `app.core.db.get_engine`.
- Produces: `save_document_and_chunks(session: Session, document_id: str, source_filename: str, chunks: list[Chunk]) -> list[ChunkRecord]` — rows are flushed (not yet committed), so each returned `ChunkRecord.vector_id` is populated.

- [ ] **Step 1: Add the shared test-database fixture**

Create `tests/conftest.py`:
```python
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
```

This requires a Postgres reachable at `DATABASE_URL` (or its default) with database `erp_test` already existing. Run:
```bash
docker compose exec postgres psql -U postgres -c "CREATE DATABASE erp_test;"
```
Expected: `CREATE DATABASE` (or already exists — safe to ignore if re-running).

- [ ] **Step 2: Write the failing test**

Create `tests/ingestion/test_repository.py`:
```python
from app.core.db import get_session_factory
from app.ingestion.repository import save_document_and_chunks
from app.ingestion.schemas import Chunk


def _chunk(document_id: str, index: int) -> Chunk:
    return Chunk(
        chunk_id=f"{document_id}-{index}",
        document_id=document_id,
        chunk_index=index,
        text=f"chunk text {index}",
        section_path=["Intro"],
        page_start=1,
        page_end=1,
        char_count=13,
        parser_used="fast",
        source_filename="doc.pdf",
    )


def test_save_document_and_chunks_persists_rows_and_assigns_vector_ids():
    document_id = "doc-repo-test"
    chunks = [_chunk(document_id, 0), _chunk(document_id, 1)]

    session_factory = get_session_factory()
    with session_factory() as session:
        records = save_document_and_chunks(session, document_id, "doc.pdf", chunks)
        session.commit()

        assert len(records) == 2
        assert all(isinstance(record.vector_id, int) for record in records)
        assert records[0].vector_id != records[1].vector_id
        assert [r.chunk_id for r in records] == ["doc-repo-test-0", "doc-repo-test-1"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/ingestion/test_repository.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.ingestion.repository'`

- [ ] **Step 4: Implement `app/ingestion/repository.py`**

```python
"""Persistence for ingested documents and their chunks."""

from sqlalchemy.orm import Session

from app.ingestion.models import ChunkRecord, DocumentRecord
from app.ingestion.schemas import Chunk


def save_document_and_chunks(
    session: Session, document_id: str, source_filename: str, chunks: list[Chunk]
) -> list[ChunkRecord]:
    """Persist one document and its chunks in `session`, flushing so `vector_id`s are assigned.

    Does not commit — the caller controls the transaction boundary.
    """
    session.add(DocumentRecord(document_id=document_id, filename=source_filename))

    records = [
        ChunkRecord(
            chunk_id=chunk.chunk_id,
            document_id=chunk.document_id,
            chunk_index=chunk.chunk_index,
            text=chunk.text,
            section_path=chunk.section_path,
            page_start=chunk.page_start,
            page_end=chunk.page_end,
            char_count=chunk.char_count,
            parser_used=chunk.parser_used,
            source_filename=chunk.source_filename,
        )
        for chunk in chunks
    ]
    session.add_all(records)
    session.flush()
    return records
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/ingestion/test_repository.py tests/ingestion/test_models.py tests/core -v`
Expected: PASS (all tests)

- [ ] **Step 6: Commit**

```bash
git add app/ingestion/repository.py tests/conftest.py tests/ingestion/test_repository.py
git commit -m "feat: persist ingested documents and chunks to postgres"
```

---

### Task 6: Embedding settings

**Files:**
- Create: `app/embedding/__init__.py`
- Create: `app/embedding/config.py`
- Test: `tests/embedding/__init__.py`, `tests/embedding/test_config.py`

**Interfaces:**
- Produces: `EmbeddingSettings(ollama_host: str, model: str, dimension: int, faiss_index_path: str)`, `get_embedding_settings() -> EmbeddingSettings`.

- [ ] **Step 1: Create package markers**

Create `app/embedding/__init__.py`:
```python
"""Embedding generation and vector index persistence."""
```

Create `tests/embedding/__init__.py` (empty file).

- [ ] **Step 2: Write the failing test**

Create `tests/embedding/test_config.py`:
```python
from app.embedding.config import EmbeddingSettings


def test_defaults():
    settings = EmbeddingSettings()
    assert settings.model == "nomic-embed-text"
    assert settings.dimension == 768
    assert settings.ollama_host.startswith("http")
    assert settings.faiss_index_path


def test_reads_env_vars(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL", "custom-model")
    monkeypatch.setenv("EMBEDDING_DIMENSION", "4")
    settings = EmbeddingSettings()
    assert settings.model == "custom-model"
    assert settings.dimension == 4
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/embedding/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.embedding.config'`

- [ ] **Step 4: Implement `app/embedding/config.py`**

```python
"""Embedding generation settings, loaded from environment variables."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class EmbeddingSettings(BaseSettings):
    """Configuration for embedding generation and vector index storage.

    Overridable via `EMBEDDING_*` env vars.
    """

    model_config = SettingsConfigDict(env_prefix="EMBEDDING_")

    ollama_host: str = "http://localhost:11434"
    model: str = "nomic-embed-text"
    dimension: int = 768
    faiss_index_path: str = "data/faiss_index.bin"


@lru_cache
def get_embedding_settings() -> EmbeddingSettings:
    """Return the process-wide cached `EmbeddingSettings` instance."""
    return EmbeddingSettings()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/embedding/test_config.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add app/embedding/__init__.py app/embedding/config.py tests/embedding/__init__.py tests/embedding/test_config.py
git commit -m "feat: add embedding settings"
```

---

### Task 7: Ollama embedding client

**Files:**
- Create: `app/embedding/client.py`
- Test: `tests/embedding/test_client.py`

**Interfaces:**
- Consumes: `app.embedding.config.EmbeddingSettings`.
- Produces: `EmbeddingClient` (`Protocol` with `embed(self, texts: list[str]) -> list[list[float]]`), `OllamaEmbeddingClient(settings: EmbeddingSettings)` implementing it.

- [ ] **Step 1: Write the failing test**

Create `tests/embedding/test_client.py`:
```python
from app.embedding.client import OllamaEmbeddingClient
from app.embedding.config import EmbeddingSettings


class _FakeOllamaClient:
    def __init__(self, host):
        self.host = host
        self.calls = []

    def embed(self, model, input):
        self.calls.append((model, input))
        return {"embeddings": [[0.1, 0.2, 0.3] for _ in input]}


def test_embed_calls_ollama_with_model_and_texts(monkeypatch):
    fake = _FakeOllamaClient(host="http://fake:11434")
    monkeypatch.setattr(
        "app.embedding.client.ollama.Client", lambda host: fake
    )
    settings = EmbeddingSettings(ollama_host="http://fake:11434", model="test-model")

    client = OllamaEmbeddingClient(settings)
    vectors = client.embed(["a", "b"])

    assert vectors == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]
    assert fake.calls == [("test-model", ["a", "b"])]


def test_embed_empty_list_returns_empty_without_calling_ollama(monkeypatch):
    fake = _FakeOllamaClient(host="http://fake:11434")
    monkeypatch.setattr(
        "app.embedding.client.ollama.Client", lambda host: fake
    )
    client = OllamaEmbeddingClient(EmbeddingSettings())

    assert client.embed([]) == []
    assert fake.calls == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/embedding/test_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.embedding.client'`

- [ ] **Step 3: Implement `app/embedding/client.py`**

```python
"""Embedding generation via a local Ollama server."""

from typing import Protocol

import ollama

from app.embedding.config import EmbeddingSettings


class EmbeddingClient(Protocol):
    """Anything that can turn a batch of texts into embedding vectors."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text, in the same order."""
        ...


class OllamaEmbeddingClient:
    """`EmbeddingClient` backed by a local Ollama server running `settings.model`."""

    def __init__(self, settings: EmbeddingSettings) -> None:
        """Build a client bound to `settings.ollama_host` and `settings.model`."""
        self._client = ollama.Client(host=settings.ollama_host)
        self._model = settings.model

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per text in `texts`, in the same order."""
        if not texts:
            return []
        response = self._client.embed(model=self._model, input=texts)
        return list(response["embeddings"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/embedding/test_client.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add app/embedding/client.py tests/embedding/test_client.py
git commit -m "feat: add ollama-backed embedding client"
```

---

### Task 8: FAISS index wrapper

**Files:**
- Create: `app/embedding/index.py`
- Test: `tests/embedding/test_index.py`

**Interfaces:**
- Produces: `FaissIndex(path: str, dimension: int)` with `.add(vector_ids: list[int], vectors: list[list[float]]) -> None`, `.save() -> None`, `.ntotal -> int` property.

- [ ] **Step 1: Write the failing test**

Create `tests/embedding/test_index.py`:
```python
from app.embedding.index import FaissIndex


def test_new_index_starts_empty(tmp_path):
    index = FaissIndex(str(tmp_path / "index.bin"), dimension=4)
    assert index.ntotal == 0


def test_add_increases_ntotal(tmp_path):
    index = FaissIndex(str(tmp_path / "index.bin"), dimension=4)
    index.add([1, 2], [[0.1, 0.2, 0.3, 0.4], [0.5, 0.6, 0.7, 0.8]])
    assert index.ntotal == 2


def test_add_empty_is_a_noop(tmp_path):
    index = FaissIndex(str(tmp_path / "index.bin"), dimension=4)
    index.add([], [])
    assert index.ntotal == 0


def test_save_and_reload_preserves_vectors(tmp_path):
    path = str(tmp_path / "nested" / "index.bin")
    index = FaissIndex(path, dimension=4)
    index.add([1, 2], [[0.1, 0.2, 0.3, 0.4], [0.5, 0.6, 0.7, 0.8]])
    index.save()

    reloaded = FaissIndex(path, dimension=4)
    assert reloaded.ntotal == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/embedding/test_index.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.embedding.index'`

- [ ] **Step 3: Implement `app/embedding/index.py`**

```python
"""A FAISS vector index persisted to a local file."""

import os

import faiss
import numpy as np


class FaissIndex:
    """A flat L2 FAISS index, addressable by explicit int64 IDs, persisted to `path`."""

    def __init__(self, path: str, dimension: int) -> None:
        """Load the index at `path` if it exists, otherwise create an empty one."""
        self._path = path
        self._dimension = dimension
        self._index = self._load_or_create()

    def _load_or_create(self) -> faiss.IndexIDMap:
        if os.path.exists(self._path):
            return faiss.read_index(self._path)
        return faiss.IndexIDMap(faiss.IndexFlatL2(self._dimension))

    @property
    def ntotal(self) -> int:
        """Number of vectors currently in the index."""
        return int(self._index.ntotal)

    def add(self, vector_ids: list[int], vectors: list[list[float]]) -> None:
        """Add `vectors`, keyed by the parallel `vector_ids`. No-op if either is empty."""
        if not vector_ids:
            return
        ids = np.array(vector_ids, dtype="int64")
        matrix = np.array(vectors, dtype="float32")
        self._index.add_with_ids(matrix, ids)

    def save(self) -> None:
        """Persist the index to `path`, creating parent directories if needed."""
        directory = os.path.dirname(self._path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        faiss.write_index(self._index, self._path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/embedding/test_index.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add app/embedding/index.py tests/embedding/test_index.py
git commit -m "feat: add faiss index wrapper with disk persistence"
```

---

### Task 9: Embedding + persistence orchestration service

**Files:**
- Create: `app/embedding/service.py`
- Test: `tests/embedding/test_service.py`

**Interfaces:**
- Consumes: `app.embedding.client.EmbeddingClient`, `app.embedding.config.{EmbeddingSettings, get_embedding_settings}`, `app.embedding.index.FaissIndex`, `app.ingestion.repository.save_document_and_chunks`, `app.ingestion.schemas.Chunk`, `app.core.db.get_session_factory`.
- Produces: `embed_and_persist(document_id: str, source_filename: str, chunks: list[Chunk], settings: EmbeddingSettings | None = None, embedding_client: EmbeddingClient | None = None, faiss_index: FaissIndex | None = None) -> None`. Used by Task 10's `run_ingestion_job`.

- [ ] **Step 1: Write the failing test**

Create `tests/embedding/test_service.py`:
```python
from app.core.db import get_session_factory
from app.embedding.config import EmbeddingSettings
from app.embedding.index import FaissIndex
from app.embedding.service import embed_and_persist
from app.ingestion.models import ChunkRecord
from app.ingestion.schemas import Chunk


class _FakeEmbeddingClient:
    def __init__(self, dimension):
        self._dimension = dimension
        self.calls = []

    def embed(self, texts):
        self.calls.append(texts)
        return [[0.1] * self._dimension for _ in texts]


def _chunk(document_id: str, index: int) -> Chunk:
    return Chunk(
        chunk_id=f"{document_id}-{index}",
        document_id=document_id,
        chunk_index=index,
        text=f"chunk text {index}",
        section_path=["Intro"],
        page_start=1,
        page_end=1,
        char_count=13,
        parser_used="fast",
        source_filename="doc.pdf",
    )


def test_embed_and_persist_writes_to_postgres_and_faiss(tmp_path):
    document_id = "doc-service-test"
    chunks = [_chunk(document_id, 0), _chunk(document_id, 1)]
    fake_client = _FakeEmbeddingClient(dimension=4)
    faiss_index = FaissIndex(str(tmp_path / "index.bin"), dimension=4)
    settings = EmbeddingSettings(dimension=4)

    embed_and_persist(
        document_id=document_id,
        source_filename="doc.pdf",
        chunks=chunks,
        settings=settings,
        embedding_client=fake_client,
        faiss_index=faiss_index,
    )

    assert fake_client.calls == [["chunk text 0", "chunk text 1"]]
    assert faiss_index.ntotal == 2

    session_factory = get_session_factory()
    with session_factory() as session:
        rows = (
            session.query(ChunkRecord)
            .filter(ChunkRecord.document_id == document_id)
            .order_by(ChunkRecord.chunk_index)
            .all()
        )
        assert [row.chunk_id for row in rows] == ["doc-service-test-0", "doc-service-test-1"]


def test_embed_and_persist_noop_for_empty_chunks(tmp_path):
    faiss_index = FaissIndex(str(tmp_path / "index.bin"), dimension=4)
    fake_client = _FakeEmbeddingClient(dimension=4)

    embed_and_persist(
        document_id="doc-empty",
        source_filename="doc.pdf",
        chunks=[],
        settings=EmbeddingSettings(dimension=4),
        embedding_client=fake_client,
        faiss_index=faiss_index,
    )

    assert fake_client.calls == []
    assert faiss_index.ntotal == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/embedding/test_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.embedding.service'`

- [ ] **Step 3: Implement `app/embedding/service.py`**

```python
"""Orchestrates embedding a document's chunks and persisting them to Postgres + FAISS."""

from app.core.db import get_session_factory
from app.embedding.client import EmbeddingClient, OllamaEmbeddingClient
from app.embedding.config import EmbeddingSettings, get_embedding_settings
from app.embedding.index import FaissIndex
from app.ingestion.repository import save_document_and_chunks
from app.ingestion.schemas import Chunk


def embed_and_persist(
    document_id: str,
    source_filename: str,
    chunks: list[Chunk],
    settings: EmbeddingSettings | None = None,
    embedding_client: EmbeddingClient | None = None,
    faiss_index: FaissIndex | None = None,
) -> None:
    """Embed `chunks`, persist them to Postgres, and add their vectors to the FAISS index.

    No-op if `chunks` is empty. `embedding_client`/`faiss_index` are injectable for testing;
    default to Ollama/local-disk implementations built from `settings` (or the process-wide
    cached `EmbeddingSettings` if `settings` is not given).
    """
    if not chunks:
        return

    settings = settings or get_embedding_settings()
    embedding_client = embedding_client or OllamaEmbeddingClient(settings)
    faiss_index = faiss_index or FaissIndex(settings.faiss_index_path, settings.dimension)

    vectors = embedding_client.embed([chunk.text for chunk in chunks])

    session_factory = get_session_factory()
    with session_factory() as session:
        records = save_document_and_chunks(session, document_id, source_filename, chunks)
        vector_ids = [record.vector_id for record in records]
        session.commit()

    faiss_index.add(vector_ids, vectors)
    faiss_index.save()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/embedding/test_service.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add app/embedding/service.py tests/embedding/test_service.py
git commit -m "feat: add embed_and_persist orchestration service"
```

---

### Task 10: Wire embedding + persistence into the ingestion job pipeline

**Files:**
- Modify: `app/ingestion/jobs.py`
- Test: `tests/ingestion/test_jobs.py`

**Interfaces:**
- Consumes: `app.embedding.service.embed_and_persist`, `app.embedding.client.EmbeddingClient`, `app.embedding.index.FaissIndex`.
- Produces: `run_ingestion_job` gains two new optional trailing parameters (`embedding_client`, `faiss_index`) — existing 4-positional-arg call sites are unaffected.

- [ ] **Step 1: Write the failing tests**

Replace the import block at the top of `tests/ingestion/test_jobs.py` (the existing 3 `from app.ingestion...` lines) with:

```python
# tests/ingestion/test_jobs.py
from app.core.db import get_session_factory
from app.embedding.index import FaissIndex
from app.ingestion.config import IngestionSettings
from app.ingestion.jobs import create_job, get_job, run_ingestion_job
from app.ingestion.models import ChunkRecord
from app.ingestion.schemas import JobStatus
```

Add near the other fixtures/helpers at the top of the file (after `_settings()`):
```python
class _FakeEmbeddingClient:
    def embed(self, texts):
        return [[0.1] * 4 for _ in texts]
```

Append these tests to the end of the file:
```python
def test_run_ingestion_job_persists_chunks_and_vectors(simple_text_pdf, tmp_path):
    job_id = create_job()
    faiss_index = FaissIndex(str(tmp_path / "index.bin"), dimension=4)

    run_ingestion_job(
        job_id,
        simple_text_pdf,
        "simple.pdf",
        _settings(),
        embedding_client=_FakeEmbeddingClient(),
        faiss_index=faiss_index,
    )

    record = get_job(job_id)
    assert record.status == JobStatus.DONE
    document_id = record.result.document_id

    assert faiss_index.ntotal == len(record.result.chunks)

    session_factory = get_session_factory()
    with session_factory() as session:
        rows = session.query(ChunkRecord).filter(ChunkRecord.document_id == document_id).all()
        assert len(rows) == len(record.result.chunks)


def test_run_ingestion_job_marks_failed_if_persistence_fails(simple_text_pdf, tmp_path):
    class _BrokenEmbeddingClient:
        def embed(self, texts):
            raise RuntimeError("embedding backend unreachable")

    job_id = create_job()
    run_ingestion_job(
        job_id,
        simple_text_pdf,
        "simple.pdf",
        _settings(),
        embedding_client=_BrokenEmbeddingClient(),
        faiss_index=FaissIndex(str(tmp_path / "index.bin"), dimension=4),
    )

    record = get_job(job_id)
    assert record.status == JobStatus.FAILED
    assert record.error is not None
    assert "embedding backend unreachable" in record.error
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `uv run pytest tests/ingestion/test_jobs.py -v`
Expected: FAIL on the two new tests with `TypeError: run_ingestion_job() got an unexpected keyword argument 'embedding_client'`

- [ ] **Step 3: Modify `app/ingestion/jobs.py`**

Update the imports at the top of the file:
```python
"""In-memory async ingestion job tracking (no persistent queue; single-process only)."""

import logging
import threading
import uuid

from app.embedding.client import EmbeddingClient
from app.embedding.index import FaissIndex
from app.embedding.service import embed_and_persist
from app.ingestion.config import IngestionSettings
from app.ingestion.schemas import IngestResponse, JobStatus
from app.ingestion.service import ingest_pdf
```

Replace the `run_ingestion_job` function with:
```python
def run_ingestion_job(
    job_id: str,
    pdf_path: str,
    filename: str,
    settings: IngestionSettings,
    embedding_client: EmbeddingClient | None = None,
    faiss_index: FaissIndex | None = None,
) -> None:
    """Run ingestion for `job_id`, recording DONE + result or FAILED + error on the job record.

    On success, also embeds and durably persists the resulting chunks (Postgres + FAISS) —
    a DONE job means the data is embedded and persisted, not just held in memory.
    """
    with _lock:
        _jobs[job_id].status = JobStatus.PROCESSING

    try:
        result = ingest_pdf(pdf_path, filename, settings)
        embed_and_persist(
            document_id=result.document_id,
            source_filename=filename,
            chunks=result.chunks,
            embedding_client=embedding_client,
            faiss_index=faiss_index,
        )
    except Exception as exc:  # noqa: BLE001 - job failure is reported via status, not raised
        logger.exception("Ingestion job %s failed for file %r", job_id, filename)
        with _lock:
            _jobs[job_id].status = JobStatus.FAILED
            _jobs[job_id].error = str(exc)
        return

    with _lock:
        _jobs[job_id].status = JobStatus.DONE
        _jobs[job_id].result = result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ingestion/test_jobs.py -v`
Expected: PASS (7 tests: the 5 existing plus 2 new)

- [ ] **Step 5: Run the full suite with coverage**

Run: `uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=90`
Expected: PASS, coverage at or above 90%.

- [ ] **Step 6: Commit**

```bash
git add app/ingestion/jobs.py tests/ingestion/test_jobs.py
git commit -m "feat: embed and persist chunks after ingestion job succeeds"
```

---

### Task 11: CI Postgres service and migration check

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `.gitignore`

**Interfaces:**
- None (infra-only).

- [ ] **Step 1: Ignore the local FAISS data directory**

Edit `.gitignore`, under the "Logs and local data" section, add:
```
data/
```

- [ ] **Step 2: Add a Postgres service container and env var to the CI job**

Edit `.github/workflows/ci.yml`:
```yaml
name: CI

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main, develop]

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16-alpine
        env:
          POSTGRES_USER: postgres
          POSTGRES_PASSWORD: postgres
          POSTGRES_DB: erp_test
        ports:
          - 5432:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
    env:
      DATABASE_URL: postgresql+psycopg://postgres:postgres@localhost:5432/erp_test
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v3

      - name: Install dependencies
        run: uv sync

      - name: Scan for secrets (gitleaks)
        run: uv run pre-commit run gitleaks --all-files

      - name: Lint (ruff)
        run: uv run ruff check .

      - name: Type check (mypy)
        run: uv run mypy app

      - name: Verify alembic migration applies cleanly
        run: uv run alembic upgrade head

      - name: Run tests (with coverage gate)
        run: uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=90
```

Note: `tests/conftest.py` (Task 5) creates/drops the schema itself via `Base.metadata.create_all`/`drop_all`, independent of the "Verify alembic migration applies cleanly" step above — that step exists solely to catch migration/model drift (e.g. someone edits `app/ingestion/models.py` without regenerating a migration), not to provision the schema tests run against.

- [ ] **Step 3: Verify locally that the same steps pass**

Run (with local `docker compose up -d postgres` already running and `erp_test` database created, per Task 5 Step 1):
```bash
DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/erp_test" uv run alembic upgrade head
DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/erp_test" uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=90
```
Expected: both pass.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml .gitignore
git commit -m "ci: run tests against a real postgres service container"
```

- [ ] **Step 5: Push and confirm CI is green**

```bash
git push
```
Then check the GitHub Actions run for this push and confirm all steps pass, including the new Postgres-backed ones.

---

### Task 12: Close out ERP-011

**Files:**
- Modify: `.ai/tickets/ERP-011.md`
- Create: `.ai/sessions/2026-08-06-embedding-persistence.md`
- Modify: `.ai/memory/current-state.md`

**Interfaces:**
- None (docs-only).

- [ ] **Step 1: Mark ERP-011's acceptance criteria complete**

Edit `.ai/tickets/ERP-011.md`: change `Status: In Progress` to `Status: Done`, and check off every `- [ ]` acceptance criterion to `- [x]`.

- [ ] **Step 2: Write the session log**

Create `.ai/sessions/2026-08-06-embedding-persistence.md` using `.ai/templates/session.md`'s structure, summarizing: the Postgres/FAISS/Ollama decisions made during brainstorming (SQLAlchemy+Alembic, `ollama` client, mocked embedding tests, real-Postgres DB tests, Redis deferred), what was built (list the new modules from the File Structure section above), any blockers encountered during implementation, and next steps (Redis embedding cache follow-up ticket; retrieval/query endpoint is the next vertical slice after that).

- [ ] **Step 3: Update current-state.md**

Edit `.ai/memory/current-state.md`:
- Move the "No embedding generation, no Postgres/FAISS/BM25 persistence" line out of "What Does Not Exist Yet" and replace it with a new bullet under "What Exists" describing the now-live embedding + persistence pipeline (Postgres via SQLAlchemy/Alembic, FAISS local-disk index, Ollama/Nomic Embed, wired into `run_ingestion_job`).
- Add a bullet noting the Redis embedding cache is still not implemented (deferred per ADR-003/ERP-011's notes).
- Update "Next Planned Work" to reflect what naturally follows (Redis embedding cache, or retrieval/query endpoint — whichever the session log's "Next Steps" settled on).

- [ ] **Step 4: Commit**

```bash
git add .ai/tickets/ERP-011.md .ai/sessions/2026-08-06-embedding-persistence.md .ai/memory/current-state.md
git commit -m "docs: close out ERP-011 ticket, session log, and current-state"
```

---

## Self-Review Notes

- **Spec coverage:** embedding client (Task 7), Postgres schema/migrations (Tasks 3–4), repository persistence (Task 5), FAISS index (Task 8), orchestration wiring into the job pipeline with failure handling preserved (Task 9–10), docker-compose + CI Postgres service (Tasks 4, 11), new-dependency approval already granted by the user before this plan was written. All spec sections have a corresponding task.
- **Type consistency checked:** `EmbeddingClient.embed` / `FaissIndex.add` / `save_document_and_chunks` signatures are used identically across Tasks 5, 7, 8, 9, 10 — confirmed no drift (e.g. `embed_and_persist`'s injected `embedding_client`/`faiss_index` params match the types Tasks 7–8 produce).
- **Redis embedding cache** intentionally has no task — out of scope per the approved spec, called out in Task 12 as a follow-up.
