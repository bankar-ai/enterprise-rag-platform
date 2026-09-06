# Evaluation (Retrieval Quality) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A lightweight, standalone retrieval-quality evaluation harness for this repo — a hand-authored golden dataset scored with Precision@k/Recall@k/MRR against the real hybrid search pipeline, persisted to Postgres, run via CLI.

**Architecture:** `app/evaluation/dataset.py` defines a small golden dataset as `Chunk`-shaped templates (not yet stamped with a real `document_id`). `app/evaluation/runner.py`'s `run_evaluation()` stamps them into real `Chunk` objects, persists them via the existing `embed_and_persist`, runs each golden query through the existing `retrieval.service.search()`, scores the results, persists a summary row, and cleans up its own transient data (never the summary rows). `app/evaluation/run.py` is the CLI entrypoint.

**Tech Stack:** Existing stack only — no new dependencies. SQLAlchemy/Alembic for the new `evaluation_runs` table, the existing `app.embedding.service.embed_and_persist` and `app.retrieval.service.search`.

**Spec:** `docs/superpowers/specs/2026-09-06-evaluation-design.md`

## Global Constraints

- No new dependencies (`CLAUDE.md`'s dependency policy) — everything here is existing stack.
- `ruff`, `mypy --strict` (scoped to `app/`), `pytest-cov --cov-fail-under=90` must all pass.
- No `print()` in application code (`docs/engineering-guidelines.md`) — `app/evaluation/run.py`'s CLI report output is the one deliberate, documented exception (a CLI tool's whole purpose is stdout output), same category as an argparse `--help` message; the module has a one-line comment noting this.
- Real Ollama is never called in tests (repo-wide existing convention, confirmed via `.github/workflows/ci.yml` having no Ollama service) — `tests/evaluation/test_runner.py` uses a fake embedding client.
- `documents.owner_id` has a `ForeignKey("users.id")` with no cascade — cleanup must delete chunks, then documents, then the user, in that order.
- `chunk_id` is derived from a fresh per-run `document_id` (`f"{document_id}-{index}"`) — the golden dataset must reference chunks by `chunk_index`, never a hardcoded `chunk_id`.
- Use `uv run alembic revision` for the new migration — never write a migration file with a stale `down_revision`; always check the actual current head first.

---

### Task 1: Golden dataset + metrics (pure, no DB/Ollama)

**Files:**
- Create: `app/evaluation/__init__.py`
- Create: `app/evaluation/dataset.py`
- Create: `app/evaluation/metrics.py`
- Test: `tests/evaluation/__init__.py`
- Test: `tests/evaluation/test_dataset.py`
- Test: `tests/evaluation/test_metrics.py`

**Interfaces:**
- Produces: `app.evaluation.dataset.EvalChunk` (dataclass: `index: int`, `section_path: list[str]`, `text: str`, `page: int`), `EvalDocument` (dataclass: `label: str`, `chunks: list[EvalChunk]`), `EvalQuery` (dataclass: `query: str`, `document_label: str`, `expected_chunk_indices: list[int]`), `GOLDEN_DOCUMENTS: list[EvalDocument]`, `GOLDEN_QUERIES: list[EvalQuery]`.
- Produces: `app.evaluation.metrics.precision_at_k(retrieved: list[str], relevant: set[str], k: int) -> float`, `recall_at_k(retrieved: list[str], relevant: set[str], k: int) -> float`, `reciprocal_rank(retrieved: list[str], relevant: set[str]) -> float`.
- Consumes: nothing from other tasks.

- [ ] **Step 1: Write the failing tests**

Create `tests/evaluation/__init__.py` (empty file).

Create `tests/evaluation/test_metrics.py`:

```python
from app.evaluation.metrics import precision_at_k, recall_at_k, reciprocal_rank


def test_precision_at_k_counts_relevant_among_top_k():
    retrieved = ["a", "b", "c"]
    relevant = {"a", "c"}
    assert precision_at_k(retrieved, relevant, k=3) == 2 / 3


def test_precision_at_k_uses_k_not_len_retrieved_as_denominator():
    retrieved = ["a"]
    relevant = {"a"}
    assert precision_at_k(retrieved, relevant, k=3) == 1 / 3


def test_precision_at_k_only_considers_top_k_items():
    retrieved = ["z", "z", "a"]  # "a" (relevant) is ranked 3rd
    relevant = {"a"}
    assert precision_at_k(retrieved, relevant, k=2) == 0.0


def test_recall_at_k_divides_by_total_relevant_count():
    retrieved = ["a", "z", "z"]
    relevant = {"a", "b"}
    assert recall_at_k(retrieved, relevant, k=3) == 1 / 2


def test_recall_at_k_with_no_relevant_items_is_zero_not_a_division_error():
    assert recall_at_k(["a"], set(), k=3) == 0.0


def test_reciprocal_rank_of_first_relevant_item():
    retrieved = ["z", "a", "b"]
    relevant = {"a"}
    assert reciprocal_rank(retrieved, relevant) == 1 / 2


def test_reciprocal_rank_is_zero_when_nothing_relevant_is_retrieved():
    assert reciprocal_rank(["z", "y"], {"a"}) == 0.0


def test_reciprocal_rank_of_empty_retrieved_list_is_zero():
    assert reciprocal_rank([], {"a"}) == 0.0
```

Create `tests/evaluation/test_dataset.py`:

```python
from app.evaluation.dataset import GOLDEN_DOCUMENTS, GOLDEN_QUERIES


def test_every_query_references_an_existing_document_label():
    labels = {doc.label for doc in GOLDEN_DOCUMENTS}
    for query in GOLDEN_QUERIES:
        assert query.document_label in labels


def test_every_expected_chunk_index_exists_in_its_document():
    chunks_by_label = {doc.label: {c.index for c in doc.chunks} for doc in GOLDEN_DOCUMENTS}
    for query in GOLDEN_QUERIES:
        available = chunks_by_label[query.document_label]
        for expected_index in query.expected_chunk_indices:
            assert expected_index in available, (
                f"query {query.query!r} expects chunk_index {expected_index} "
                f"in document {query.document_label!r}, but only {available} exist"
            )


def test_every_query_has_at_least_one_expected_chunk():
    for query in GOLDEN_QUERIES:
        assert len(query.expected_chunk_indices) > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/evaluation/ -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.evaluation'`

- [ ] **Step 3: Implement `app/evaluation/metrics.py`**

```python
"""Pure retrieval-quality metrics: Precision@k, Recall@k, and reciprocal rank."""


def precision_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """Fraction of the top-`k` retrieved IDs that are in `relevant`.

    Divides by `k` (not `len(retrieved)`), so returning fewer than `k` results is penalized --
    matching the standard Precision@k definition.
    """
    top_k = retrieved[:k]
    hits = sum(1 for item in top_k if item in relevant)
    return hits / k


def recall_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """Fraction of all `relevant` IDs found within the top-`k` retrieved IDs.

    `0.0` if `relevant` is empty (no relevant items exist to find), rather than raising.
    """
    if not relevant:
        return 0.0
    top_k = retrieved[:k]
    hits = sum(1 for item in top_k if item in relevant)
    return hits / len(relevant)


def reciprocal_rank(retrieved: list[str], relevant: set[str]) -> float:
    """`1 / rank` of the first relevant ID in `retrieved` (1-indexed), or `0.0` if none is found."""
    for rank, item in enumerate(retrieved, start=1):
        if item in relevant:
            return 1.0 / rank
    return 0.0
```

- [ ] **Step 4: Implement `app/evaluation/dataset.py`**

```python
"""A small, hand-authored golden dataset for retrieval-quality evaluation.

Chunks are authored as templates (`EvalChunk`), not real `app.ingestion.schemas.Chunk` objects --
a real `document_id` (and therefore `chunk_id`, which is derived from it) only exists once
`app.evaluation.runner.run_evaluation` stamps these into a fresh ingestion run. Queries reference
their expected chunks by stable `chunk_index`, never a `chunk_id`.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class EvalChunk:
    """One chunk template within an `EvalDocument`."""

    index: int
    section_path: list[str]
    text: str
    page: int = 1


@dataclass(frozen=True)
class EvalDocument:
    """One document template in the golden dataset."""

    label: str
    chunks: list[EvalChunk]


@dataclass(frozen=True)
class EvalQuery:
    """One golden query, naming its target document and the chunk indices that answer it."""

    query: str
    document_label: str
    expected_chunk_indices: list[int]


GOLDEN_DOCUMENTS: list[EvalDocument] = [
    EvalDocument(
        label="cats",
        chunks=[
            EvalChunk(
                index=0,
                section_path=["Cats", "Diet"],
                text=(
                    "Cats are obligate carnivores and need a diet rich in animal protein "
                    "such as meat and fish."
                ),
            ),
            EvalChunk(
                index=1,
                section_path=["Cats", "Behavior"],
                text=(
                    "Cats often groom themselves for hours and are most active during "
                    "dawn and dusk, a behavior pattern called crepuscular."
                ),
            ),
            EvalChunk(
                index=2,
                section_path=["Cats", "Habitat"],
                text="Domestic cats can adapt to apartments as well as houses with outdoor access.",
            ),
        ],
    ),
    EvalDocument(
        label="dogs",
        chunks=[
            EvalChunk(
                index=0,
                section_path=["Dogs", "Training"],
                text=(
                    "Dogs respond well to positive reinforcement training methods "
                    "such as treats and praise."
                ),
            ),
            EvalChunk(
                index=1,
                section_path=["Dogs", "Diet"],
                text="Dogs are omnivores and can eat a balanced mix of meat, vegetables, and grains.",
            ),
        ],
    ),
]

GOLDEN_QUERIES: list[EvalQuery] = [
    EvalQuery(query="What do cats eat?", document_label="cats", expected_chunk_indices=[0]),
    EvalQuery(query="How are dogs trained?", document_label="dogs", expected_chunk_indices=[0]),
    EvalQuery(query="When are cats most active?", document_label="cats", expected_chunk_indices=[1]),
    EvalQuery(query="What can dogs eat?", document_label="dogs", expected_chunk_indices=[1]),
]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/evaluation/ -v`
Expected: PASS (11 tests)

- [ ] **Step 6: Run ruff and mypy**

Run: `uv run ruff check app/evaluation tests/evaluation && uv run mypy app/evaluation`
Expected: both clean.

- [ ] **Step 7: Commit**

```bash
git add app/evaluation/__init__.py app/evaluation/dataset.py app/evaluation/metrics.py tests/evaluation/
git commit -m "feat: add golden retrieval-eval dataset and Precision/Recall/MRR metrics"
```

---

### Task 2: `evaluation_runs` persistence

**Files:**
- Create: `app/evaluation/models.py`
- Create: `app/evaluation/schemas.py`
- Create: `app/evaluation/repository.py`
- Create: `alembic/versions/<new>_create_evaluation_runs_table.py`
- Test: `tests/evaluation/test_repository.py`

**Interfaces:**
- Consumes: nothing from Task 1 directly (schemas are new).
- Produces: `app.evaluation.schemas.QueryResult`, `EvaluationSummary` (used by Task 3). `app.evaluation.models.EvaluationRunRecord`. `app.evaluation.repository.save_evaluation_run(session, summary) -> EvaluationRunRecord`, `cleanup_eval_data(session, document_ids: list[str], owner_id) -> None`.

- [ ] **Step 1: Check the current Alembic head**

Run: `uv run alembic heads`
Note the returned revision ID -- it becomes this migration's `down_revision`. (At the time this plan was written, ERP-028 added no migration, so the head is still `cc12bb2f6bc7` from ERP-027 -- confirm this is still true before writing the migration; do not assume.)

- [ ] **Step 2: Write the failing test**

Create `tests/evaluation/test_repository.py`:

```python
import uuid

from app.auth.repository import create_user
from app.core.db import get_session_factory
from app.evaluation.repository import cleanup_eval_data, save_evaluation_run
from app.evaluation.schemas import EvaluationSummary, QueryResult
from app.ingestion.repository import get_chunks_by_vector_ids, save_document_and_chunks
from app.ingestion.schemas import Chunk


def _summary() -> EvaluationSummary:
    return EvaluationSummary(
        top_k=3,
        num_queries=1,
        mean_precision=0.5,
        mean_recall=1.0,
        mrr=1.0,
        per_query=[
            QueryResult(
                query="q",
                precision=0.5,
                recall=1.0,
                reciprocal_rank=1.0,
                retrieved_chunk_ids=["a-0"],
                relevant_chunk_ids=["a-0"],
            )
        ],
    )


def test_save_evaluation_run_persists_summary_fields():
    session_factory = get_session_factory()
    with session_factory() as session:
        record = save_evaluation_run(session, _summary())
        session.commit()

        assert record.top_k == 3
        assert record.num_queries == 1
        assert record.mean_precision_at_k == 0.5
        assert record.mean_recall_at_k == 1.0
        assert record.mrr == 1.0
        assert record.details[0]["query"] == "q"


def test_cleanup_eval_data_removes_chunks_documents_and_user():
    session_factory = get_session_factory()
    with session_factory() as session:
        user = create_user(session, f"eval-cleanup-{uuid.uuid4()}@test", "x")
        session.flush()
        document_id = str(uuid.uuid4())
        chunk = Chunk(
            chunk_id=f"{document_id}-0",
            document_id=document_id,
            chunk_index=0,
            text="text",
            section_path=["A"],
            page_start=1,
            page_end=1,
            char_count=4,
            parser_used="fast",
            source_filename="doc.pdf",
        )
        records = save_document_and_chunks(session, document_id, "doc.pdf", [chunk], user.id)
        vector_id = records[0].vector_id
        session.commit()

        cleanup_eval_data(session, [document_id], user.id)
        session.commit()

        assert get_chunks_by_vector_ids(session, [vector_id], user.id) == {}
```

Note for the implementer: check `Chunk`'s exact field names/required fields in `app/ingestion/schemas.py` before writing this test -- match what's actually there.

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/evaluation/test_repository.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.evaluation.repository'`

- [ ] **Step 4: Implement `app/evaluation/schemas.py`**

```python
"""Pydantic schemas for one evaluation run's per-query results and aggregate summary."""

from pydantic import BaseModel


class QueryResult(BaseModel):
    """One golden query's scored result."""

    query: str
    precision: float
    recall: float
    reciprocal_rank: float
    retrieved_chunk_ids: list[str]
    relevant_chunk_ids: list[str]


class EvaluationSummary(BaseModel):
    """Aggregate result of one full evaluation run."""

    top_k: int
    num_queries: int
    mean_precision: float
    mean_recall: float
    mrr: float
    per_query: list[QueryResult]
```

- [ ] **Step 5: Implement `app/evaluation/models.py`**

```python
"""SQLAlchemy ORM model for persisted evaluation run summaries."""

import uuid
from datetime import datetime

from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.ingestion.models import Base


class EvaluationRunRecord(Base):
    """One persisted evaluation run's aggregate metrics and per-query breakdown."""

    __tablename__ = "evaluation_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_at: Mapped[datetime] = mapped_column(server_default=func.now())
    top_k: Mapped[int]
    num_queries: Mapped[int]
    mean_precision_at_k: Mapped[float]
    mean_recall_at_k: Mapped[float]
    mrr: Mapped[float]
    details: Mapped[list[dict[str, object]]] = mapped_column(JSON)
```

- [ ] **Step 6: Implement `app/evaluation/repository.py`**

```python
"""Persistence for evaluation run summaries, and cleanup of an eval run's transient data."""

import uuid

from sqlalchemy.orm import Session

from app.evaluation.models import EvaluationRunRecord
from app.evaluation.schemas import EvaluationSummary
from app.ingestion.models import ChunkRecord, DocumentRecord
from app.auth.models import UserRecord


def save_evaluation_run(session: Session, summary: EvaluationSummary) -> EvaluationRunRecord:
    """Persist `summary` as a new `EvaluationRunRecord`. Does not commit."""
    record = EvaluationRunRecord(
        top_k=summary.top_k,
        num_queries=summary.num_queries,
        mean_precision_at_k=summary.mean_precision,
        mean_recall_at_k=summary.mean_recall,
        mrr=summary.mrr,
        details=[result.model_dump() for result in summary.per_query],
    )
    session.add(record)
    session.flush()
    return record


def cleanup_eval_data(session: Session, document_ids: list[str], owner_id: uuid.UUID) -> None:
    """Delete the chunks, documents, and user created by one eval run. Does not commit.

    Order matters: `chunks.document_id` and `documents.owner_id` are both plain (non-cascading)
    foreign keys, so children must be deleted before their parents.
    """
    for document_id in document_ids:
        session.query(ChunkRecord).filter(ChunkRecord.document_id == document_id).delete()
    for document_id in document_ids:
        session.query(DocumentRecord).filter(DocumentRecord.document_id == document_id).delete()
    session.query(UserRecord).filter(UserRecord.id == owner_id).delete()
```

- [ ] **Step 7: Create the Alembic migration**

Run: `uv run alembic revision -m "create evaluation_runs table"`

Edit the generated file (fill in the real `down_revision` from Step 1's `alembic heads` output):

```python
def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "evaluation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("top_k", sa.Integer(), nullable=False),
        sa.Column("num_queries", sa.Integer(), nullable=False),
        sa.Column("mean_precision_at_k", sa.Float(), nullable=False),
        sa.Column("mean_recall_at_k", sa.Float(), nullable=False),
        sa.Column("mrr", sa.Float(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("evaluation_runs")
```

(Add `from sqlalchemy.dialects import postgresql` to the generated file's imports if not already present.)

- [ ] **Step 8: Run test to verify it passes**

Run: `DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/erp_test" AUTH_JWT_SECRET_KEY="test-only-secret-do-not-use-in-production" uv run pytest tests/evaluation/ -v`
Expected: PASS (13 tests -- the test suite bootstraps schema via `Base.metadata.create_all`, so the new migration doesn't need to run for tests to pass, but still verify the migration applies cleanly against the real dev DB per the next step)

- [ ] **Step 9: Verify the migration applies cleanly against a real Postgres DB**

Run: `DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/erp" uv run alembic upgrade head` then `uv run alembic downgrade -1` then `uv run alembic upgrade head` then `uv run alembic revision --autogenerate -m "check_empty_diff"` and confirm the generated file's `upgrade()`/`downgrade()` bodies are both empty (`pass`) -- then delete that throwaway check-diff migration file.

- [ ] **Step 10: Run ruff and mypy**

Run: `uv run ruff check app/evaluation tests/evaluation && uv run mypy app/evaluation`
Expected: both clean.

- [ ] **Step 11: Commit**

```bash
git add app/evaluation/models.py app/evaluation/schemas.py app/evaluation/repository.py alembic/versions/ tests/evaluation/test_repository.py
git commit -m "feat: persist evaluation run summaries to a new evaluation_runs table"
```

---

### Task 3: The runner + CLI entrypoint

**Files:**
- Create: `app/evaluation/runner.py`
- Create: `app/evaluation/run.py`
- Test: `tests/evaluation/test_runner.py`

**Interfaces:**
- Consumes: `app.evaluation.dataset.GOLDEN_DOCUMENTS`/`GOLDEN_QUERIES` (Task 1), `app.evaluation.metrics.*` (Task 1), `app.evaluation.schemas.QueryResult`/`EvaluationSummary` (Task 2), `app.evaluation.repository.save_evaluation_run`/`cleanup_eval_data` (Task 2), `app.embedding.service.embed_and_persist`, `app.retrieval.service.search`, `app.embedding.index.FaissIndex`, `app.auth.repository.create_user`.
- Produces: `app.evaluation.runner.run_evaluation(top_k: int = 3, embedding_client=None, faiss_index=None) -> EvaluationSummary`, used by `run.py`.

- [ ] **Step 1: Write the failing test**

Create `tests/evaluation/test_runner.py`:

```python
import uuid

from app.core.db import get_session_factory
from app.embedding.index import FaissIndex
from app.evaluation.dataset import GOLDEN_DOCUMENTS, GOLDEN_QUERIES
from app.evaluation.models import EvaluationRunRecord
from app.evaluation.runner import run_evaluation
from app.ingestion.models import ChunkRecord, DocumentRecord


class _DiscriminatingFakeEmbeddingClient:
    """Returns a distinct one-hot-ish vector per distinct text, so FAISS can tell them apart.

    Real embeddings aren't needed to test the runner's own orchestration (persistence,
    cleanup, metric aggregation) -- only that distinguishable inputs produce distinguishable,
    correctly-ranked outputs. Every unique text seen gets its own fixed dimension "hot".
    """

    def __init__(self):
        self._dimension = 32
        self._assigned: dict[str, int] = {}

    def embed(self, texts):
        vectors = []
        for text in texts:
            if text not in self._assigned:
                self._assigned[text] = len(self._assigned) % self._dimension
            vector = [0.0] * self._dimension
            vector[self._assigned[text]] = 1.0
            vectors.append(vector)
        return vectors


def test_run_evaluation_persists_a_summary_row_and_cleans_up_transient_data(tmp_path):
    faiss_index = FaissIndex(str(tmp_path / "eval.index"), dimension=32)
    embedding_client = _DiscriminatingFakeEmbeddingClient()

    summary = run_evaluation(top_k=3, embedding_client=embedding_client, faiss_index=faiss_index)

    assert summary.num_queries == len(GOLDEN_QUERIES)
    assert summary.mean_precision > 0.0
    assert summary.mrr > 0.0

    session_factory = get_session_factory()
    with session_factory() as session:
        rows = session.query(EvaluationRunRecord).all()
        assert len(rows) == 1
        assert rows[0].num_queries == len(GOLDEN_QUERIES)

        # transient eval data (documents/chunks) must not remain after cleanup
        total_docs = sum(len(doc.chunks) for doc in GOLDEN_DOCUMENTS)
        assert session.query(ChunkRecord).count() == 0 or session.query(ChunkRecord).count() < total_docs
        remaining_docs = session.query(DocumentRecord).all()
        assert all(doc.filename not in {d.label for d in GOLDEN_DOCUMENTS} for doc in remaining_docs)
```

Note for the implementer: because this test doesn't run in isolation from other tests sharing the same `erp_test` database, the `ChunkRecord`/`DocumentRecord` count assertions above are deliberately loose (not asserting exactly zero globally) -- they only need to confirm *this run's* documents/chunks are gone, which the `remaining_docs` check does precisely (by filename/label, not by count). Tighten this if a cleaner isolation approach becomes obvious during implementation, but do not assert global table emptiness.

- [ ] **Step 2: Run test to verify it fails**

Run: `DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/erp_test" AUTH_JWT_SECRET_KEY="test-only-secret-do-not-use-in-production" uv run pytest tests/evaluation/test_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.evaluation.runner'`

- [ ] **Step 3: Implement `app/evaluation/runner.py`**

```python
"""Orchestrates one full retrieval-quality evaluation run: ingest the golden dataset, query
it through the real hybrid search pipeline, score the results, persist, and clean up.
"""

import tempfile
import uuid

from app.auth.repository import create_user
from app.core.db import get_session_factory
from app.embedding.client import EmbeddingClient
from app.embedding.config import get_embedding_settings
from app.embedding.index import FaissIndex
from app.embedding.service import embed_and_persist
from app.evaluation.dataset import GOLDEN_DOCUMENTS, GOLDEN_QUERIES
from app.evaluation.metrics import precision_at_k, recall_at_k, reciprocal_rank
from app.evaluation.repository import cleanup_eval_data, save_evaluation_run
from app.evaluation.schemas import EvaluationSummary, QueryResult
from app.ingestion.schemas import Chunk
from app.retrieval.service import search


def run_evaluation(
    top_k: int = 3,
    embedding_client: EmbeddingClient | None = None,
    faiss_index: FaissIndex | None = None,
) -> EvaluationSummary:
    """Run the golden dataset through the real search pipeline and return a scored summary.

    Always uses a dedicated temp-file-backed FAISS index when `faiss_index` isn't injected,
    never the real app's persisted index -- running this must never pollute or depend on a
    developer's local index. Persists the resulting summary to Postgres and deletes every
    other row it created (the eval user, its documents, its chunks) before returning -- the
    persisted summary row is the only durable trace of having run this.
    """
    if faiss_index is None:
        temp_path = tempfile.NamedTemporaryFile(suffix=".index", delete=False).name
        faiss_index = FaissIndex(temp_path, get_embedding_settings().dimension)

    session_factory = get_session_factory()
    with session_factory() as session:
        eval_user = create_user(session, f"eval-{uuid.uuid4()}@internal", "!")
        session.commit()
        owner_id = eval_user.id

    document_ids_by_label: dict[str, str] = {}
    all_document_ids: list[str] = []
    for eval_document in GOLDEN_DOCUMENTS:
        document_id = str(uuid.uuid4())
        document_ids_by_label[eval_document.label] = document_id
        all_document_ids.append(document_id)
        chunks = [
            Chunk(
                chunk_id=f"{document_id}-{eval_chunk.index}",
                document_id=document_id,
                chunk_index=eval_chunk.index,
                text=eval_chunk.text,
                section_path=eval_chunk.section_path,
                page_start=eval_chunk.page,
                page_end=eval_chunk.page,
                char_count=len(eval_chunk.text),
                parser_used="fast",
                source_filename=f"{eval_document.label}.pdf",
            )
            for eval_chunk in eval_document.chunks
        ]
        embed_and_persist(
            document_id=document_id,
            source_filename=eval_document.label,
            chunks=chunks,
            owner_id=owner_id,
            embedding_client=embedding_client,
            faiss_index=faiss_index,
        )

    per_query: list[QueryResult] = []
    for eval_query in GOLDEN_QUERIES:
        document_id = document_ids_by_label[eval_query.document_label]
        relevant_ids = {f"{document_id}-{index}" for index in eval_query.expected_chunk_indices}

        results = search(
            query=eval_query.query,
            top_k=top_k,
            owner_id=owner_id,
            embedding_client=embedding_client,
            faiss_index=faiss_index,
        )
        retrieved_ids = [chunk.chunk_id for chunk in results]

        per_query.append(
            QueryResult(
                query=eval_query.query,
                precision=precision_at_k(retrieved_ids, relevant_ids, top_k),
                recall=recall_at_k(retrieved_ids, relevant_ids, top_k),
                reciprocal_rank=reciprocal_rank(retrieved_ids, relevant_ids),
                retrieved_chunk_ids=retrieved_ids,
                relevant_chunk_ids=sorted(relevant_ids),
            )
        )

    summary = EvaluationSummary(
        top_k=top_k,
        num_queries=len(per_query),
        mean_precision=sum(r.precision for r in per_query) / len(per_query),
        mean_recall=sum(r.recall for r in per_query) / len(per_query),
        mrr=sum(r.reciprocal_rank for r in per_query) / len(per_query),
        per_query=per_query,
    )

    with session_factory() as session:
        save_evaluation_run(session, summary)
        cleanup_eval_data(session, all_document_ids, owner_id)
        session.commit()

    return summary
```

Note for the implementer: `search()`'s cache-aside layer (`app/retrieval/cache.py`) is keyed by `(query, top_k, rerank, expand_sections, owner_id)`. Since `owner_id` is a fresh random UUID every run, cache collisions across runs are impossible -- no need to bypass or clear the cache here.

- [ ] **Step 4: Implement `app/evaluation/run.py`**

```python
"""CLI entrypoint: `uv run python -m app.evaluation.run`.

Runs the retrieval-quality evaluation harness and prints a report. This module's stdout
report output is a deliberate, documented exception to the no-`print()` rule (a CLI tool's
whole purpose is stdout output) -- not a violation to fix.
"""

from app.evaluation.runner import run_evaluation


def main() -> None:
    """Run the evaluation harness and print a summary report."""
    summary = run_evaluation()

    print(f"Evaluation run: {summary.num_queries} queries, top_k={summary.top_k}")
    print(f"  Mean Precision@{summary.top_k}: {summary.mean_precision:.3f}")
    print(f"  Mean Recall@{summary.top_k}:    {summary.mean_recall:.3f}")
    print(f"  MRR:                    {summary.mrr:.3f}")
    print()
    for result in summary.per_query:
        print(f"  [{result.reciprocal_rank:.2f} RR] {result.query!r}")
        print(f"      retrieved: {result.retrieved_chunk_ids}")
        print(f"      relevant:  {result.relevant_chunk_ids}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/erp_test" AUTH_JWT_SECRET_KEY="test-only-secret-do-not-use-in-production" uv run pytest tests/evaluation/ -v`
Expected: PASS (all evaluation tests)

- [ ] **Step 6: Manually run the real CLI against live Ollama (developer verification, not an automated test)**

Run: `DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/erp" AUTH_JWT_SECRET_KEY="test-only-secret-do-not-use-in-production" uv run python -m app.evaluation.run`
Expected: a printed report with a plausible Precision/Recall/MRR (not necessarily perfect -- real embeddings may rank differently than the crafted fake ones) and no traceback. Requires a local Ollama server running the configured embedding model. Confirm afterward (`SELECT * FROM evaluation_runs ORDER BY run_at DESC LIMIT 1;`) that a row was persisted, and that no stray `documents`/`chunks`/`users` rows from this run remain.

- [ ] **Step 7: Run ruff and mypy**

Run: `uv run ruff check app/evaluation tests/evaluation && uv run mypy app/evaluation`
Expected: both clean. `app/evaluation/run.py`'s `print()` calls will need a targeted `# noqa` only if `ruff`'s configured rule set actually flags bare `print()` (check `pyproject.toml`'s `[tool.ruff.lint] select` list first -- if `T20` isn't in it, no suppression is needed at all).

- [ ] **Step 8: Commit**

```bash
git add app/evaluation/runner.py app/evaluation/run.py tests/evaluation/test_runner.py
git commit -m "feat: add the evaluation runner and uv run python -m app.evaluation.run CLI entrypoint"
```

---

### Task 4: Final verification and documentation

**Files:**
- Modify: `.ai/memory/current-state.md`
- Create: `.ai/tickets/ERP-029.md`
- Create: `.ai/sessions/2026-09-06-evaluation.md`

**Interfaces:**
- Consumes: nothing (verifies and documents Tasks 1-3).
- Produces: nothing (terminal task).

- [ ] **Step 1: Run the full test suite with coverage**

Run: `DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/erp_test" AUTH_JWT_SECRET_KEY="test-only-secret-do-not-use-in-production" uv run pytest -q --cov=app --cov-fail-under=90`
Expected: all tests pass (existing + all new evaluation tests), coverage ≥90%.

- [ ] **Step 2: Run ruff, mypy, and pre-commit across everything changed**

Run: `uv run ruff check app tests && uv run mypy app`
Expected: both clean.

Run: `uv run pre-commit run --files <every file changed on this branch, via git diff --name-only develop...HEAD>`
Expected: gitleaks and ruff both pass.

- [ ] **Step 3: Write `.ai/tickets/ERP-029.md`**

Follow the format of `.ai/tickets/ERP-028.md` (Status Done, Depends On: None, Description referencing this being the retrieval-quality half of the "Evaluation" project goal, generation-quality deferred, Acceptance Criteria matching this plan's Task 1-3 deliverables, Notes linking to `docs/superpowers/specs/2026-09-06-evaluation-design.md` and the design-constraint note added to `docs/architecture.md`).

- [ ] **Step 4: Write `.ai/sessions/2026-09-06-evaluation.md`**

Follow the format of `.ai/sessions/2026-09-06-observability.md` (Decisions: hand-authored `Chunk` templates over PDF fixtures and why, CLI-not-endpoint, not-a-CI-gate-yet, fake-embedding-client testing strategy; Implementation Summary: every file touched; Blockers: None or whatever came up; Next Steps: generation-quality evaluation is the natural follow-up, and the LLMOps platform's generalized-platform constraint documented in `docs/architecture.md` on 2026-09-06 should be revisited once that platform exists).

- [ ] **Step 5: Update `.ai/memory/current-state.md`**

Add a bullet describing what was built (following the ERP-028 bullet's style), and update "Next Planned Work" to note generation-quality evaluation as the natural follow-up (Evaluation itself is no longer fully open, but not fully closed either -- only the retrieval half is done).

- [ ] **Step 6: Commit the documentation**

```bash
git add .ai/tickets/ERP-029.md .ai/sessions/2026-09-06-evaluation.md .ai/memory/current-state.md
git commit -m "docs: close out ERP-029 (retrieval-quality evaluation)"
```

- [ ] **Step 7: Push and open a PR into `develop`**

```bash
git push -u origin erp-029-evaluation-retrieval-quality
gh pr create --base develop --title "feat: retrieval-quality evaluation harness (ERP-029)" --body "..."
```

Follow the same PR body structure used for PR #24/#25 (Summary bullets + Test plan checklist referencing the verification in Steps 1-2 above).
