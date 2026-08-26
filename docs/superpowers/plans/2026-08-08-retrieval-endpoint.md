# Retrieval Endpoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a synchronous `POST /retrieval/query` endpoint that embeds a query, searches the FAISS index, hydrates matching chunks from Postgres, and returns them ranked by similarity.

**Architecture:** Two small additions to existing modules (`FaissIndex.search`, `repository.get_chunks_by_vector_ids`), plus a new `app/retrieval/` module (`schemas.py`, `service.py`, `router.py`) following the same layout as `app/ingestion/` and `app/embedding/`. No new jobs/background tasks — retrieval is a plain synchronous request.

**Tech Stack:** FastAPI, Pydantic v2, SQLAlchemy (existing session factory), FAISS (existing index), `ollama` (existing embedding client) — no new dependencies.

## Global Constraints

- Never use `pip install`/`pip uninstall` — always `uv add`/`uv remove`/`uv sync`/`uv run` (`CLAUDE.md`).
- Never use `print()` in application code; use the existing stdlib `logging` convention if logging is needed.
- `--strict` Mypy applies to `app/` (not `tests/`).
- `pytest --cov=app --cov-fail-under=90` gates CI; all new/modified modules need real test coverage.
- Business logic never lives in API routes (`docs/architecture.md`) — `app/retrieval/router.py` must stay a thin validate → call service → return wrapper.
- No per-document filtering, no BM25/hybrid/reranking/PageIndex, no response caching, no in-memory FAISS caching across requests — all explicitly out of scope per the design spec.
- `top_k`: default `5`, bounded `1`–`50`. `query`: minimum length `1`.
- Score formula: `score = 1.0 / (1.0 + distance)` — higher is better, bounded `(0, 1]`.

---

## File Structure

- `app/embedding/index.py` — modify: add `FaissIndex.search`.
- `tests/embedding/test_index.py` — modify: add tests for `search`.
- `app/ingestion/repository.py` — modify: add `get_chunks_by_vector_ids`.
- `tests/ingestion/test_repository.py` — modify: add tests for `get_chunks_by_vector_ids`.
- `app/retrieval/__init__.py` — create: empty package marker.
- `app/retrieval/schemas.py` — create: `RetrievalQuery`, `RetrievedChunk`, `RetrievalResponse`.
- `app/retrieval/service.py` — create: `search`.
- `app/retrieval/router.py` — create: `POST /retrieval/query`.
- `app/main.py` — modify: register the retrieval router.
- `tests/retrieval/__init__.py`, `tests/retrieval/test_schemas.py`, `tests/retrieval/test_service.py`, `tests/retrieval/test_router.py` — create.

---

### Task 1: `FaissIndex.search`

**Files:**
- Modify: `app/embedding/index.py`
- Test: `tests/embedding/test_index.py`

**Interfaces:**
- Consumes: nothing new (uses the existing `self._index`, a `faiss.IndexIDMap`).
- Produces: `FaissIndex.search(vector: list[float], k: int) -> list[tuple[int, float]]` — nearest-first `(vector_id, distance)` pairs. Returns `[]` if the index is empty or `k <= 0`. FAISS pads results with `vector_id == -1` when the index has fewer than `k` vectors; those entries are dropped, not returned.

- [ ] **Step 1: Write the failing tests**

Append to `tests/embedding/test_index.py`:
```python
def test_search_on_empty_index_returns_empty_list(tmp_path):
    index = FaissIndex(str(tmp_path / "index.bin"), dimension=4)
    assert index.search([0.1, 0.2, 0.3, 0.4], k=5) == []


def test_search_returns_nearest_first(tmp_path):
    index = FaissIndex(str(tmp_path / "index.bin"), dimension=4)
    index.add(
        [1, 2, 3],
        [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.9, 0.1, 0.0, 0.0],
        ],
    )
    results = index.search([1.0, 0.0, 0.0, 0.0], k=2)
    assert [vector_id for vector_id, _ in results] == [1, 3]
    assert results[0][1] < results[1][1]


def test_search_k_larger_than_ntotal_returns_all_available(tmp_path):
    index = FaissIndex(str(tmp_path / "index.bin"), dimension=4)
    index.add([1, 2], [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]])
    results = index.search([1.0, 0.0, 0.0, 0.0], k=10)
    assert len(results) == 2
    assert {vector_id for vector_id, _ in results} == {1, 2}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/embedding/test_index.py -v`
Expected: FAIL with `AttributeError: 'FaissIndex' object has no attribute 'search'`

- [ ] **Step 3: Implement `FaissIndex.search`**

Add to `app/embedding/index.py`, after the existing `add` method:
```python
    def search(self, vector: list[float], k: int) -> list[tuple[int, float]]:
        """Return up to `k` nearest `(vector_id, distance)` pairs, nearest-first.

        Empty list if the index has no vectors or `k <= 0`. Padding entries FAISS
        returns when the index has fewer than `k` vectors (`vector_id == -1`) are
        dropped.
        """
        if self._index.ntotal == 0 or k <= 0:
            return []
        query = np.array([vector], dtype="float32")
        distances, ids = self._index.search(query, k)
        return [
            (int(vector_id), float(distance))
            for vector_id, distance in zip(ids[0], distances[0], strict=True)
            if vector_id != -1
        ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/embedding/test_index.py -v`
Expected: PASS (7 tests: 4 existing + 3 new)

- [ ] **Step 5: Run ruff and mypy**

Run: `uv run ruff check . && uv run mypy app`
Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git add app/embedding/index.py tests/embedding/test_index.py
git commit -m "feat: add nearest-neighbor search to FaissIndex"
```

---

### Task 2: `repository.get_chunks_by_vector_ids`

**Files:**
- Modify: `app/ingestion/repository.py`
- Test: `tests/ingestion/test_repository.py`

**Interfaces:**
- Consumes: `app.ingestion.models.ChunkRecord`, `sqlalchemy.orm.Session`.
- Produces: `get_chunks_by_vector_ids(session: Session, vector_ids: list[int]) -> dict[int, ChunkRecord]` — keyed by `vector_id`. Returns `{}` for an empty input list without querying.

- [ ] **Step 1: Write the failing test**

Append to `tests/ingestion/test_repository.py`:
```python
from app.ingestion.repository import get_chunks_by_vector_ids, save_document_and_chunks


def test_get_chunks_by_vector_ids_returns_rows_keyed_by_vector_id():
    document_id = "doc-lookup-test"
    chunks = [_chunk(document_id, 0), _chunk(document_id, 1)]

    session_factory = get_session_factory()
    with session_factory() as session:
        records = save_document_and_chunks(session, document_id, "doc.pdf", chunks)
        session.commit()
        vector_ids = [record.vector_id for record in records]

    with session_factory() as session:
        found = get_chunks_by_vector_ids(session, vector_ids)

        assert set(found.keys()) == set(vector_ids)
        for vector_id, record in found.items():
            assert record.vector_id == vector_id
            assert record.document_id == document_id


def test_get_chunks_by_vector_ids_empty_input_returns_empty_dict():
    session_factory = get_session_factory()
    with session_factory() as session:
        assert get_chunks_by_vector_ids(session, []) == {}


def test_get_chunks_by_vector_ids_ignores_unknown_ids():
    session_factory = get_session_factory()
    with session_factory() as session:
        assert get_chunks_by_vector_ids(session, [999_999_999]) == {}
```

Note: replace the existing `from app.ingestion.repository import save_document_and_chunks` import line at the top of the file with the combined import shown above (both names from the same module), so there's only one import line for `app.ingestion.repository`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingestion/test_repository.py -v`
Expected: FAIL with `ImportError: cannot import name 'get_chunks_by_vector_ids'`

- [ ] **Step 3: Implement `get_chunks_by_vector_ids`**

Add to `app/ingestion/repository.py`, after `save_document_and_chunks`:
```python
def get_chunks_by_vector_ids(session: Session, vector_ids: list[int]) -> dict[int, ChunkRecord]:
    """Fetch chunk rows by their `vector_id`s, keyed by `vector_id`. `{}` for empty input."""
    if not vector_ids:
        return {}
    rows = session.query(ChunkRecord).filter(ChunkRecord.vector_id.in_(vector_ids)).all()
    return {row.vector_id: row for row in rows}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ingestion/test_repository.py -v`
Expected: PASS (4 tests: 1 existing + 3 new)

- [ ] **Step 5: Run ruff and mypy**

Run: `uv run ruff check . && uv run mypy app`
Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git add app/ingestion/repository.py tests/ingestion/test_repository.py
git commit -m "feat: add vector-id lookup for hydrating FAISS search hits"
```

---

### Task 3: Retrieval schemas

**Files:**
- Create: `app/retrieval/__init__.py`
- Create: `app/retrieval/schemas.py`
- Test: `tests/retrieval/__init__.py`, `tests/retrieval/test_schemas.py`

**Interfaces:**
- Produces: `RetrievalQuery(query: str, top_k: int = 5)`, `RetrievedChunk(chunk_id: str, document_id: str, text: str, section_path: list[str], page_start: int, page_end: int, source_filename: str, score: float)`, `RetrievalResponse(results: list[RetrievedChunk])`.

- [ ] **Step 1: Create package markers**

Create `app/retrieval/__init__.py`:
```python
"""Semantic retrieval: search over ingested, embedded chunks."""
```

Create `tests/retrieval/__init__.py` (empty file).

- [ ] **Step 2: Write the failing test**

Create `tests/retrieval/test_schemas.py`:
```python
import pytest
from pydantic import ValidationError

from app.retrieval.schemas import RetrievalQuery, RetrievalResponse, RetrievedChunk


def test_retrieval_query_defaults_top_k_to_five():
    assert RetrievalQuery(query="what is x?").top_k == 5


def test_retrieval_query_rejects_empty_query():
    with pytest.raises(ValidationError):
        RetrievalQuery(query="")


def test_retrieval_query_rejects_top_k_out_of_bounds():
    with pytest.raises(ValidationError):
        RetrievalQuery(query="x", top_k=0)
    with pytest.raises(ValidationError):
        RetrievalQuery(query="x", top_k=51)


def test_retrieval_response_holds_ranked_chunks():
    chunk = RetrievedChunk(
        chunk_id="doc-1-0",
        document_id="doc-1",
        text="hello",
        section_path=["Intro"],
        page_start=1,
        page_end=1,
        source_filename="doc.pdf",
        score=0.9,
    )
    response = RetrievalResponse(results=[chunk])
    assert response.results[0].score == 0.9
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/retrieval/test_schemas.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.retrieval.schemas'`

- [ ] **Step 4: Implement `app/retrieval/schemas.py`**

```python
"""Pydantic schemas for the retrieval API's request and response."""

from pydantic import BaseModel, Field


class RetrievalQuery(BaseModel):
    """A semantic search request."""

    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)


class RetrievedChunk(BaseModel):
    """A single retrieved chunk, with full provenance metadata and a similarity score."""

    chunk_id: str
    document_id: str
    text: str
    section_path: list[str]
    page_start: int
    page_end: int
    source_filename: str
    score: float


class RetrievalResponse(BaseModel):
    """Ranked results of a semantic search query, most relevant first."""

    results: list[RetrievedChunk]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/retrieval/test_schemas.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Run ruff and mypy**

Run: `uv run ruff check . && uv run mypy app`
Expected: both clean.

- [ ] **Step 7: Commit**

```bash
git add app/retrieval/__init__.py app/retrieval/schemas.py tests/retrieval/__init__.py tests/retrieval/test_schemas.py
git commit -m "feat: add retrieval request/response schemas"
```

---

### Task 4: Retrieval service

**Files:**
- Create: `app/retrieval/service.py`
- Test: `tests/retrieval/test_service.py`

**Interfaces:**
- Consumes: `app.embedding.client.{EmbeddingClient, OllamaEmbeddingClient}`, `app.embedding.config.{EmbeddingSettings, get_embedding_settings}`, `app.embedding.index.FaissIndex` (its `.search(vector, k)` from Task 1), `app.ingestion.repository.get_chunks_by_vector_ids` (Task 2), `app.core.db.get_session_factory`, `app.retrieval.schemas.RetrievedChunk` (Task 3).
- Produces: `search(query: str, top_k: int, settings: EmbeddingSettings | None = None, embedding_client: EmbeddingClient | None = None, faiss_index: FaissIndex | None = None) -> list[RetrievedChunk]`. Used by Task 5's router.

- [ ] **Step 1: Write the failing tests**

Create `tests/retrieval/test_service.py`:
```python
from app.core.db import get_session_factory
from app.embedding.config import EmbeddingSettings
from app.embedding.index import FaissIndex
from app.ingestion.repository import save_document_and_chunks
from app.ingestion.schemas import Chunk
from app.retrieval.service import search


class _FakeEmbeddingClient:
    def __init__(self, vector):
        self._vector = vector
        self.calls = []

    def embed(self, texts):
        self.calls.append(texts)
        return [self._vector for _ in texts]


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


def _persist_and_index(document_id, chunks, vectors, faiss_index):
    session_factory = get_session_factory()
    with session_factory() as session:
        records = save_document_and_chunks(session, document_id, "doc.pdf", chunks)
        session.commit()
        vector_ids = [record.vector_id for record in records]
    faiss_index.add(vector_ids, vectors)
    faiss_index.save()
    return vector_ids


def test_search_returns_ranked_chunks(tmp_path):
    document_id = "doc-search-test"
    chunks = [_chunk(document_id, 0), _chunk(document_id, 1)]
    vectors = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]]
    faiss_index = FaissIndex(str(tmp_path / "index.bin"), dimension=4)
    _persist_and_index(document_id, chunks, vectors, faiss_index)

    fake_client = _FakeEmbeddingClient(vector=[1.0, 0.0, 0.0, 0.0])
    results = search(
        query="find chunk 0",
        top_k=5,
        settings=EmbeddingSettings(dimension=4),
        embedding_client=fake_client,
        faiss_index=faiss_index,
    )

    assert fake_client.calls == [["find chunk 0"]]
    assert len(results) == 2
    assert results[0].chunk_id == "doc-search-test-0"
    assert results[0].score > results[1].score


def test_search_on_empty_index_returns_empty_list(tmp_path):
    faiss_index = FaissIndex(str(tmp_path / "index.bin"), dimension=4)
    fake_client = _FakeEmbeddingClient(vector=[1.0, 0.0, 0.0, 0.0])

    results = search(
        query="anything",
        top_k=5,
        settings=EmbeddingSettings(dimension=4),
        embedding_client=fake_client,
        faiss_index=faiss_index,
    )

    assert results == []


def test_search_top_k_larger_than_available_returns_all(tmp_path):
    document_id = "doc-search-test-2"
    chunks = [_chunk(document_id, 0)]
    vectors = [[1.0, 0.0, 0.0, 0.0]]
    faiss_index = FaissIndex(str(tmp_path / "index.bin"), dimension=4)
    _persist_and_index(document_id, chunks, vectors, faiss_index)

    fake_client = _FakeEmbeddingClient(vector=[1.0, 0.0, 0.0, 0.0])
    results = search(
        query="find it",
        top_k=10,
        settings=EmbeddingSettings(dimension=4),
        embedding_client=fake_client,
        faiss_index=faiss_index,
    )

    assert len(results) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/retrieval/test_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.retrieval.service'`

- [ ] **Step 3: Implement `app/retrieval/service.py`**

```python
"""Semantic search over ingested chunks: embed a query, search FAISS, hydrate from Postgres."""

from app.core.db import get_session_factory
from app.embedding.client import EmbeddingClient, OllamaEmbeddingClient
from app.embedding.config import EmbeddingSettings, get_embedding_settings
from app.embedding.index import FaissIndex
from app.ingestion.repository import get_chunks_by_vector_ids
from app.retrieval.schemas import RetrievedChunk


def search(
    query: str,
    top_k: int,
    settings: EmbeddingSettings | None = None,
    embedding_client: EmbeddingClient | None = None,
    faiss_index: FaissIndex | None = None,
) -> list[RetrievedChunk]:
    """Embed `query`, search the FAISS index, and return up to `top_k` chunks ranked by similarity.

    `embedding_client`/`faiss_index`/`settings` are injectable for testing; default to
    Ollama/local-disk implementations built from `settings` (or the process-wide cached
    `EmbeddingSettings` if `settings` is not given).
    """
    settings = settings or get_embedding_settings()
    embedding_client = embedding_client or OllamaEmbeddingClient(settings)
    faiss_index = faiss_index or FaissIndex(settings.faiss_index_path, settings.dimension)

    vectors = embedding_client.embed([query])
    hits = faiss_index.search(vectors[0], top_k)
    if not hits:
        return []

    session_factory = get_session_factory()
    with session_factory() as session:
        chunks_by_vector_id = get_chunks_by_vector_ids(session, [vector_id for vector_id, _ in hits])
        results = []
        for vector_id, distance in hits:
            chunk = chunks_by_vector_id.get(vector_id)
            if chunk is None:
                continue
            results.append(
                RetrievedChunk(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    text=chunk.text,
                    section_path=chunk.section_path,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    source_filename=chunk.source_filename,
                    score=1.0 / (1.0 + distance),
                )
            )
        return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/retrieval/test_service.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run ruff and mypy**

Run: `uv run ruff check . && uv run mypy app`
Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git add app/retrieval/service.py tests/retrieval/test_service.py
git commit -m "feat: add semantic search service"
```

---

### Task 5: Retrieval router and app wiring

**Files:**
- Create: `app/retrieval/router.py`
- Modify: `app/main.py`
- Test: `tests/retrieval/test_router.py`

**Interfaces:**
- Consumes: `app.retrieval.schemas.{RetrievalQuery, RetrievalResponse}`, `app.retrieval.service.search` (Task 4).
- Produces: `POST /retrieval/query` endpoint, registered on the FastAPI `app`.

- [ ] **Step 1: Write the failing tests**

Create `tests/retrieval/test_router.py`:
```python
import time

import pytest
from fastapi.testclient import TestClient

from app.embedding.client import OllamaEmbeddingClient
from app.embedding.config import get_embedding_settings
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _stub_embedding_backend(monkeypatch, tmp_path):
    """Stub Ollama and redirect the FAISS index to a temp path, same pattern as the ingestion
    router's tests — POST /retrieval/query uses production defaults with no injected fakes.
    """
    monkeypatch.setenv("EMBEDDING_FAISS_INDEX_PATH", str(tmp_path / "retrieval_router_index.bin"))
    get_embedding_settings.cache_clear()

    def _fake_embed(self, texts):
        dimension = get_embedding_settings().dimension
        return [[0.1] * dimension for _ in texts]

    monkeypatch.setattr(OllamaEmbeddingClient, "embed", _fake_embed)
    try:
        yield
    finally:
        get_embedding_settings.cache_clear()


def test_query_on_empty_index_returns_empty_results():
    response = client.post("/retrieval/query", json={"query": "anything"})
    assert response.status_code == 200
    assert response.json() == {"results": []}


def test_query_rejects_empty_query_string():
    response = client.post("/retrieval/query", json={"query": ""})
    assert response.status_code == 422


def test_query_rejects_top_k_out_of_bounds():
    response = client.post("/retrieval/query", json={"query": "x", "top_k": 0})
    assert response.status_code == 422


def test_query_returns_ingested_chunk(simple_text_pdf):
    with open(simple_text_pdf, "rb") as pdf_file:
        upload = client.post(
            "/ingestion/pdf",
            files={"file": ("simple.pdf", pdf_file, "application/pdf")},
        )
    assert upload.status_code == 202
    job_id = upload.json()["job_id"]

    deadline = time.monotonic() + 60.0
    status_body = None
    while time.monotonic() < deadline:
        status_response = client.get(f"/ingestion/jobs/{job_id}")
        status_body = status_response.json()
        if status_body["status"] in ("done", "failed"):
            break
        time.sleep(0.1)
    assert status_body is not None
    assert status_body["status"] == "done"

    response = client.post("/retrieval/query", json={"query": "introduction", "top_k": 3})
    assert response.status_code == 200
    results = response.json()["results"]
    assert results
    assert results[0]["document_id"] == status_body["result"]["document_id"]
    assert 0 < results[0]["score"] <= 1.0
```

Note: `simple_text_pdf` is an existing fixture from `tests/ingestion/conftest.py`; it's already available to any test under `tests/` via pytest's fixture discovery, no import needed. `test_query_returns_ingested_chunk` also needs `jobs` cleared between test runs — check `tests/ingestion/conftest.py` for whether job state is reset per-test; if `app.ingestion.jobs`'s in-memory `_jobs` dict is module-level and persists across tests in the same process, that's fine here since this test only asserts on its own `job_id`, not on the full job list.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/retrieval/test_router.py -v`
Expected: FAIL — `test_query_on_empty_index_returns_empty_results` etc. fail with a `404` (no such route yet), not the expected `200`/`422`.

- [ ] **Step 3: Implement `app/retrieval/router.py`**

```python
"""Retrieval API: semantic search over ingested chunks."""

from fastapi import APIRouter

from app.retrieval.schemas import RetrievalQuery, RetrievalResponse
from app.retrieval.service import search

router = APIRouter(prefix="/retrieval", tags=["retrieval"])


@router.post("/query")
def query(request: RetrievalQuery) -> RetrievalResponse:
    """Run a semantic search query and return ranked matching chunks."""
    results = search(request.query, request.top_k)
    return RetrievalResponse(results=results)
```

- [ ] **Step 4: Register the router in `app/main.py`**

Replace the full contents of `app/main.py` with:
```python
"""FastAPI application entrypoint."""

from fastapi import FastAPI

from app.ingestion.router import router as ingestion_router
from app.retrieval.router import router as retrieval_router

app = FastAPI(title="Enterprise RAG Platform")
app.include_router(ingestion_router)
app.include_router(retrieval_router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/retrieval/test_router.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Run the full suite with coverage**

Run: `uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=90`
Expected: PASS, coverage at or above 90%. A Postgres container must be running locally (`docker compose up -d postgres`; the `erp_test` database from ERP-011 should already exist — if this is a fresh environment, see `docs/superpowers/plans/2026-08-06-embedding-persistence.md` Task 5 Step 1 for how to create it).

- [ ] **Step 7: Run ruff and mypy**

Run: `uv run ruff check . && uv run mypy app`
Expected: both clean.

- [ ] **Step 8: Commit**

```bash
git add app/retrieval/router.py app/main.py tests/retrieval/test_router.py
git commit -m "feat: add POST /retrieval/query endpoint"
```

---

### Task 6: Close out ERP-012

**Files:**
- Modify: `.ai/tickets/ERP-012.md`
- Create: `.ai/sessions/2026-08-08-retrieval-endpoint.md`
- Modify: `.ai/memory/current-state.md`

**Interfaces:**
- None (docs-only).

- [ ] **Step 1: Mark ERP-012's acceptance criteria complete**

Edit `.ai/tickets/ERP-012.md`: change `Status: In Progress` to `Status: Done`, and check off every `- [ ]` acceptance criterion to `- [x]`.

- [ ] **Step 2: Write the session log**

Create `.ai/sessions/2026-08-08-retrieval-endpoint.md` using `.ai/templates/session.md`'s structure (Decisions / Implementation Summary / Blockers / Next Steps), summarizing: the vector-only scope decision and why BM25/reranking/PageIndex remain additive (from the design spec's Context section), the score formula (`1 / (1 + distance)`), the new module layout, and next steps (BM25 via Postgres full-text search, reranking, PageIndex-style retrieval, or Redis embedding cache — whichever the user prioritizes next).

- [ ] **Step 3: Update current-state.md**

Edit `.ai/memory/current-state.md`:
- Move "No retrieval or generation code at all yet" out of "What Does Not Exist Yet" and add a new bullet under "What Exists" describing the live `POST /retrieval/query` endpoint (semantic/vector-only, no filtering, no caching).
- Update "Next Planned Work" to list the deferred retrieval extensions (BM25, reranking, PageIndex) alongside the still-pending Redis embedding cache from ERP-011.

- [ ] **Step 4: Commit**

```bash
git add .ai/tickets/ERP-012.md .ai/sessions/2026-08-08-retrieval-endpoint.md .ai/memory/current-state.md
git commit -m "docs: close out ERP-012 ticket, session log, and current-state"
```

---

## Self-Review Notes

- **Spec coverage:** `FaissIndex.search` (Task 1), `get_chunks_by_vector_ids` (Task 2), schemas (Task 3), `service.search` including empty-index and orphaned-vector-id handling (Task 4), router + app wiring + validation (`top_k` bounds, empty query) (Task 5), docs closeout (Task 6). All spec sections have a corresponding task.
- **Type consistency checked:** `FaissIndex.search`'s return type (`list[tuple[int, float]]`, Task 1) matches how Task 4's `service.search` consumes `hits` (`for vector_id, distance in hits`). `get_chunks_by_vector_ids`'s return type (`dict[int, ChunkRecord]`, Task 2) matches Task 4's `chunks_by_vector_id.get(vector_id)` usage. `RetrievedChunk`'s fields (Task 3) match exactly what Task 4's `service.search` constructs.
- **No new dependencies** — confirmed nothing in this plan requires `uv add`.
