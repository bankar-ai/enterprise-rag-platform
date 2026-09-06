# Evaluation (Generation Quality) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Score the LLM's generated answers (not just retrieval) against the ERP-029 golden dataset, using Ragas's Faithfulness/Answer-Relevancy/Context-Precision metrics by default, with a hand-rolled Ollama-prompt judge as a selectable fallback.

**Architecture:** `app/evaluation/judges.py` defines a `GenerationJudge` interface with two implementations. `app/evaluation/generation_runner.py` reuses ERP-029's golden dataset and ingest/cleanup lifecycle, but for each query calls `search()` → `build_prompt()` → `llm_client.generate()` directly (not `app.generation.service.generate()`, whose public return doesn't carry chunk text) to get `(answer, contexts)`, then scores with the selected judge. Results persist to a new `generation_evaluation_runs` table. A new CLI entrypoint mirrors ERP-029's.

**Tech Stack:** New: `ragas==0.3.9` (pinned — `ragas` 0.4.3 and 0.3.9 both fail to import against `langchain-community>=0.4`; a `[tool.uv] constraint-dependencies` pin on `langchain-community<0.4` is required, verified working), `langchain-ollama` (`ChatOllama`/`OllamaEmbeddings`, wrapped via `ragas.llms.LangchainLLMWrapper`/`ragas.embeddings.LangchainEmbeddingsWrapper` — these wrappers are deprecated-but-functional in this ragas version; the deprecation warning is expected and harmless). Existing: `app.generation.client.OllamaLLMClient`, `app.retrieval.service.search`, `app.generation.prompt.build_prompt`.

**Spec:** `docs/superpowers/specs/2026-09-06-generation-evaluation-design.md`

## Global Constraints

- `ragas` MUST be pinned to `==0.3.9` in `pyproject.toml`, and `[tool.uv] constraint-dependencies` MUST include `"langchain-community<0.4"` — without both, `import ragas` raises `ModuleNotFoundError` (confirmed during design; see spec).
- No real Ollama or real Ragas calls in automated tests — mock `single_turn_ascore` for `RagasJudge` tests, use a fake `LLMClient` for orchestration tests (same pattern as ERP-029).
- `ruff`, `mypy --strict`, `pytest-cov --cov-fail-under=90` must all pass.
- Reuses `app.evaluation.dataset.GOLDEN_DOCUMENTS`/`GOLDEN_QUERIES` unchanged — no dataset edits in this plan.
- Same never-load-bearing cleanup lifecycle as ERP-029: dedicated temp FAISS index, delete all transient eval rows (user, documents, chunks) after each run; only the summary row persists.

---

### Task 1: Judges (Ragas + Ollama-fallback) with unit-testable scoring logic

**Files:**
- Create: `app/evaluation/judges.py`
- Modify: `app/evaluation/schemas.py` (add `GenerationScores`)
- Test: `tests/evaluation/test_judges.py`

**Interfaces:**
- Produces: `app.evaluation.judges.GenerationJudge` (Protocol: `score(user_input: str, response: str, retrieved_contexts: list[str]) -> GenerationScores`), `RagasJudge`, `OllamaLLMClientJudge`.
- Produces: `app.evaluation.schemas.GenerationScores` (`faithfulness: float`, `answer_relevancy: float`, `context_precision: float`).
- Consumes: `app.generation.client.LLMClient` (existing).

- [ ] **Step 1: Add `GenerationScores` to `app/evaluation/schemas.py`**

Add to the existing file:

```python
class GenerationScores(BaseModel):
    """One query's generation-quality scores, each in [0.0, 1.0]."""

    faithfulness: float
    answer_relevancy: float
    context_precision: float
```

- [ ] **Step 2: Write the failing tests for `OllamaLLMClientJudge`**

Create `tests/evaluation/test_judges.py`:

```python
from app.evaluation.judges import OllamaLLMClientJudge


class _StubLLMClient:
    def __init__(self, responses: list[str]):
        self._responses = iter(responses)

    def generate(self, system_prompt, user_prompt):
        return next(self._responses)

    def generate_stream(self, system_prompt, user_prompt):
        raise NotImplementedError


def test_ollama_judge_parses_well_formed_scores():
    client = _StubLLMClient(
        [
            "Score: 0.9 -- every claim is grounded in the context.",
            "Score: 0.7 -- mostly answers the question.",
            "Score: 0.5 -- half the retrieved context is relevant.",
        ]
    )
    judge = OllamaLLMClientJudge(client)

    scores = judge.score("q", "a", ["context"])

    assert scores.faithfulness == 0.9
    assert scores.answer_relevancy == 0.7
    assert scores.context_precision == 0.5


def test_ollama_judge_clamps_out_of_range_scores():
    client = _StubLLMClient(["Score: 1.5", "Score: -0.2", "Score: 0.5"])
    judge = OllamaLLMClientJudge(client)

    scores = judge.score("q", "a", ["context"])

    assert scores.faithfulness == 1.0
    assert scores.answer_relevancy == 0.0
    assert scores.context_precision == 0.5


def test_ollama_judge_scores_zero_and_warns_on_unparseable_response():
    client = _StubLLMClient(["I cannot determine a score.", "Score: 0.5", "Score: 0.5"])
    judge = OllamaLLMClientJudge(client)

    scores = judge.score("q", "a", ["context"])

    assert scores.faithfulness == 0.0
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/evaluation/test_judges.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.evaluation.judges'`

- [ ] **Step 4: Implement `app/evaluation/judges.py`**

```python
"""Generation-quality judges: score an (input, response, contexts) triple.

`RagasJudge` (default) uses Ragas's Faithfulness/Answer-Relevancy/Context-Precision metrics
against a local Ollama judge model. `OllamaLLMClientJudge` is a selectable fallback using
hand-written prompts against the existing `OllamaLLMClient`, for when Ragas's documented
local-Ollama reliability issues (timeouts) make the default judge unusable -- see
`docs/superpowers/specs/2026-09-06-generation-evaluation-design.md`.
"""

import asyncio
import logging
import re
from typing import Protocol

from app.embedding.config import get_embedding_settings
from app.evaluation.schemas import GenerationScores
from app.generation.client import LLMClient
from app.generation.config import get_generation_settings

logger = logging.getLogger(__name__)

_SCORE_PATTERN = re.compile(r"(-?\d+(?:\.\d+)?)")


class GenerationJudge(Protocol):
    """Anything that can score one generated answer's quality against its context."""

    def score(self, user_input: str, response: str, retrieved_contexts: list[str]) -> GenerationScores:
        """Score `response` (answering `user_input`) against `retrieved_contexts`."""
        ...


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _parse_score(raw_response: str, metric_name: str) -> float:
    match = _SCORE_PATTERN.search(raw_response)
    if match is None:
        logger.warning(
            "Could not parse a numeric score for %s from judge response: %r", metric_name, raw_response
        )
        return 0.0
    return _clamp(float(match.group(1)))


_FAITHFULNESS_PROMPT = (
    "You are grading whether an answer's claims are all supported by the given context.\n"
    "Context:\n{context}\n\nAnswer:\n{response}\n\n"
    "Respond with 'Score: X' where X is a number from 0.0 (no claims supported) to 1.0 "
    "(every claim supported), followed by a one-sentence justification."
)
_ANSWER_RELEVANCY_PROMPT = (
    "You are grading whether an answer actually addresses the question asked.\n"
    "Question:\n{user_input}\n\nAnswer:\n{response}\n\n"
    "Respond with 'Score: X' where X is a number from 0.0 (does not address the question) to "
    "1.0 (directly and fully addresses it), followed by a one-sentence justification."
)
_CONTEXT_PRECISION_PROMPT = (
    "You are grading what fraction of the retrieved context was actually relevant to answering "
    "the question.\nQuestion:\n{user_input}\n\nContext:\n{context}\n\n"
    "Respond with 'Score: X' where X is a number from 0.0 (none relevant) to 1.0 (all relevant), "
    "followed by a one-sentence justification."
)
_JUDGE_SYSTEM_PROMPT = "You are a strict, precise evaluator of RAG system outputs."


class OllamaLLMClientJudge:
    """`GenerationJudge` using hand-written prompts against an existing `LLMClient`."""

    def __init__(self, llm_client: LLMClient) -> None:
        """Build a judge that scores using `llm_client` (e.g. `OllamaLLMClient`)."""
        self._llm_client = llm_client

    def score(self, user_input: str, response: str, retrieved_contexts: list[str]) -> GenerationScores:
        """Score `response` via three separate judge-LLM calls, one per metric."""
        context = "\n---\n".join(retrieved_contexts)

        faithfulness_raw = self._llm_client.generate(
            _JUDGE_SYSTEM_PROMPT, _FAITHFULNESS_PROMPT.format(context=context, response=response)
        )
        relevancy_raw = self._llm_client.generate(
            _JUDGE_SYSTEM_PROMPT,
            _ANSWER_RELEVANCY_PROMPT.format(user_input=user_input, response=response),
        )
        precision_raw = self._llm_client.generate(
            _JUDGE_SYSTEM_PROMPT,
            _CONTEXT_PRECISION_PROMPT.format(user_input=user_input, context=context),
        )

        return GenerationScores(
            faithfulness=_parse_score(faithfulness_raw, "faithfulness"),
            answer_relevancy=_parse_score(relevancy_raw, "answer_relevancy"),
            context_precision=_parse_score(precision_raw, "context_precision"),
        )


class RagasJudge:
    """`GenerationJudge` using Ragas's Faithfulness/ResponseRelevancy/LLMContextPrecisionWithoutReference."""

    def __init__(self) -> None:
        """Build Ragas metric scorers backed by local Ollama models (judge LLM + embeddings)."""
        from langchain_ollama import ChatOllama, OllamaEmbeddings
        from ragas.embeddings import LangchainEmbeddingsWrapper
        from ragas.llms import LangchainLLMWrapper
        from ragas.metrics import Faithfulness, LLMContextPrecisionWithoutReference, ResponseRelevancy

        generation_settings = get_generation_settings()
        embedding_settings = get_embedding_settings()
        judge_llm = LangchainLLMWrapper(
            ChatOllama(model=generation_settings.model, base_url=generation_settings.ollama_host)
        )
        judge_embeddings = LangchainEmbeddingsWrapper(
            OllamaEmbeddings(model=embedding_settings.model, base_url=embedding_settings.ollama_host)
        )
        self._faithfulness = Faithfulness(llm=judge_llm)
        self._answer_relevancy = ResponseRelevancy(llm=judge_llm, embeddings=judge_embeddings)
        self._context_precision = LLMContextPrecisionWithoutReference(llm=judge_llm)

    def score(self, user_input: str, response: str, retrieved_contexts: list[str]) -> GenerationScores:
        """Score `response` via three Ragas metrics, each an async call run to completion here."""
        from ragas import SingleTurnSample

        sample = SingleTurnSample(
            user_input=user_input, response=response, retrieved_contexts=retrieved_contexts
        )
        return GenerationScores(
            faithfulness=asyncio.run(self._faithfulness.single_turn_ascore(sample)),
            answer_relevancy=asyncio.run(self._answer_relevancy.single_turn_ascore(sample)),
            context_precision=asyncio.run(self._context_precision.single_turn_ascore(sample)),
        )
```

Note for the implementer: `RagasJudge.__init__` imports `langchain_ollama`/`ragas` lazily (inside the method, not at module top) so that importing `app.evaluation.judges` itself never requires those heavy packages to be importable in a context that only needs `OllamaLLMClientJudge` -- though in practice both are always installed here, this keeps the fallback judge's tests fully independent of Ragas's own import health.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/evaluation/test_judges.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Add a mocked-Ragas wiring test**

Append to `tests/evaluation/test_judges.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch


def test_ragas_judge_wires_scores_from_each_metric():
    with (
        patch("langchain_ollama.ChatOllama"),
        patch("langchain_ollama.OllamaEmbeddings"),
        patch("ragas.llms.LangchainLLMWrapper"),
        patch("ragas.embeddings.LangchainEmbeddingsWrapper"),
        patch("ragas.metrics.Faithfulness") as mock_faithfulness_cls,
        patch("ragas.metrics.ResponseRelevancy") as mock_relevancy_cls,
        patch("ragas.metrics.LLMContextPrecisionWithoutReference") as mock_precision_cls,
    ):
        mock_faithfulness_cls.return_value.single_turn_ascore = AsyncMock(return_value=0.9)
        mock_relevancy_cls.return_value.single_turn_ascore = AsyncMock(return_value=0.8)
        mock_precision_cls.return_value.single_turn_ascore = AsyncMock(return_value=0.7)

        from app.evaluation.judges import RagasJudge

        judge = RagasJudge()
        scores = judge.score("question", "answer", ["context one"])

        assert scores.faithfulness == 0.9
        assert scores.answer_relevancy == 0.8
        assert scores.context_precision == 0.7
```

Run: `uv run pytest tests/evaluation/test_judges.py -v`
Expected: PASS (4 tests). If the `patch(...)` targets don't take effect (because `RagasJudge.__init__`'s lazy imports re-import the real classes rather than the patched names), patch the fully-qualified import paths actually used inside `judges.py` instead (e.g. `app.evaluation.judges.Faithfulness` won't exist since the import is local to the method -- patch at the source module Ragas imports from, verified by running with `-s` and checking whether the mock's `call_count` is nonzero; adjust the patch targets until the mocks are actually invoked, don't leave a test that passes only because nothing got called).

- [ ] **Step 7: Run ruff and mypy**

Run: `uv run ruff check app/evaluation tests/evaluation && uv run mypy app/evaluation`
Expected: both clean. `RagasJudge`'s lazy imports of untyped third-party modules may need `# type: ignore[import-untyped]` on the import lines if `ragas`/`langchain_ollama` ship no type stubs mypy recognizes -- check the actual mypy output before adding suppressions, don't add them speculatively.

- [ ] **Step 8: Commit**

```bash
git add app/evaluation/judges.py app/evaluation/schemas.py tests/evaluation/test_judges.py
git commit -m "feat: add Ragas and Ollama-fallback generation-quality judges"
```

---

### Task 2: Generation-evaluation persistence

**Files:**
- Modify: `app/evaluation/models.py` (add `GenerationEvaluationRunRecord`)
- Modify: `app/evaluation/schemas.py` (add `GenerationQueryResult`, `GenerationEvaluationSummary`)
- Modify: `app/evaluation/repository.py` (add `save_generation_evaluation_run`)
- Create: `alembic/versions/<new>_create_generation_evaluation_runs_table.py`
- Test: extend `tests/evaluation/test_repository.py`

**Interfaces:**
- Produces: `app.evaluation.models.GenerationEvaluationRunRecord`, `app.evaluation.schemas.GenerationQueryResult`/`GenerationEvaluationSummary`, `app.evaluation.repository.save_generation_evaluation_run(session, summary) -> GenerationEvaluationRunRecord`.
- Consumes: `app.evaluation.schemas.GenerationScores` (Task 1).

- [ ] **Step 1: Check the current Alembic head**

Run: `uv run alembic heads` (expect `0aa457a07a61` from ERP-029, but confirm -- do not assume).

- [ ] **Step 2: Write the failing test**

Append to `tests/evaluation/test_repository.py`:

```python
from app.evaluation.repository import save_generation_evaluation_run
from app.evaluation.schemas import GenerationEvaluationSummary, GenerationQueryResult


def _generation_summary() -> GenerationEvaluationSummary:
    return GenerationEvaluationSummary(
        judge="ragas",
        num_queries=1,
        mean_faithfulness=0.9,
        mean_answer_relevancy=0.8,
        mean_context_precision=0.7,
        per_query=[
            GenerationQueryResult(
                query="q",
                answer="a",
                faithfulness=0.9,
                answer_relevancy=0.8,
                context_precision=0.7,
            )
        ],
    )


def test_save_generation_evaluation_run_persists_summary_fields():
    session_factory = get_session_factory()
    with session_factory() as session:
        record = save_generation_evaluation_run(session, _generation_summary())
        session.commit()

        assert record.judge == "ragas"
        assert record.num_queries == 1
        assert record.mean_faithfulness == 0.9
        assert record.mean_answer_relevancy == 0.8
        assert record.mean_context_precision == 0.7
        assert record.details[0]["query"] == "q"

        record_id = record.id
        from app.evaluation.models import GenerationEvaluationRunRecord

        session.query(GenerationEvaluationRunRecord).filter(
            GenerationEvaluationRunRecord.id == record_id
        ).delete()
        session.commit()
```

(`get_session_factory` is already imported at the top of this test file from ERP-029.)

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/evaluation/test_repository.py -v -k generation`
Expected: FAIL (`ImportError`/`AttributeError` -- schemas/model/repository function don't exist yet)

- [ ] **Step 4: Add schemas to `app/evaluation/schemas.py`**

```python
class GenerationQueryResult(BaseModel):
    """One golden query's generated answer and its scored quality."""

    query: str
    answer: str
    faithfulness: float
    answer_relevancy: float
    context_precision: float


class GenerationEvaluationSummary(BaseModel):
    """Aggregate result of one full generation-quality evaluation run."""

    judge: str
    num_queries: int
    mean_faithfulness: float
    mean_answer_relevancy: float
    mean_context_precision: float
    per_query: list[GenerationQueryResult]
```

- [ ] **Step 5: Add `GenerationEvaluationRunRecord` to `app/evaluation/models.py`**

```python
class GenerationEvaluationRunRecord(Base):
    """One persisted generation-quality evaluation run's aggregate metrics and per-query breakdown."""

    __tablename__ = "generation_evaluation_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_at: Mapped[datetime] = mapped_column(server_default=func.now())
    judge: Mapped[str]
    num_queries: Mapped[int]
    mean_faithfulness: Mapped[float]
    mean_answer_relevancy: Mapped[float]
    mean_context_precision: Mapped[float]
    details: Mapped[list[dict[str, object]]] = mapped_column(JSON)
```

- [ ] **Step 6: Add `save_generation_evaluation_run` to `app/evaluation/repository.py`**

```python
def save_generation_evaluation_run(
    session: Session, summary: GenerationEvaluationSummary
) -> GenerationEvaluationRunRecord:
    """Persist `summary` as a new `GenerationEvaluationRunRecord`. Does not commit."""
    record = GenerationEvaluationRunRecord(
        judge=summary.judge,
        num_queries=summary.num_queries,
        mean_faithfulness=summary.mean_faithfulness,
        mean_answer_relevancy=summary.mean_answer_relevancy,
        mean_context_precision=summary.mean_context_precision,
        details=[result.model_dump() for result in summary.per_query],
    )
    session.add(record)
    session.flush()
    return record
```

(Add `GenerationEvaluationRunRecord` and `GenerationEvaluationSummary` to this file's existing imports.)

- [ ] **Step 7: Create the Alembic migration**

Run: `uv run alembic revision -m "create generation_evaluation_runs table"`, then fill in (matching ERP-029's `0aa457a07a61` migration's exact style):

```python
def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "generation_evaluation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("judge", sa.String(), nullable=False),
        sa.Column("num_queries", sa.Integer(), nullable=False),
        sa.Column("mean_faithfulness", sa.Float(), nullable=False),
        sa.Column("mean_answer_relevancy", sa.Float(), nullable=False),
        sa.Column("mean_context_precision", sa.Float(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("generation_evaluation_runs")
```

(`from sqlalchemy.dialects import postgresql` in the imports, matching the ERP-029 migration.)

- [ ] **Step 8: Run test to verify it passes**

Run: `DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/erp_test" AUTH_JWT_SECRET_KEY="test-only-secret-do-not-use-in-production" uv run pytest tests/evaluation/ -v`

- [ ] **Step 9: Verify the migration against real Postgres**

Same upgrade/downgrade/upgrade/empty-autogenerate-diff cycle as ERP-029's Task 2 Step 9. Delete the throwaway diff-check migration file afterward.

- [ ] **Step 10: Run ruff and mypy**

Run: `uv run ruff check app/evaluation tests/evaluation && uv run mypy app/evaluation`

- [ ] **Step 11: Commit**

```bash
git add app/evaluation/models.py app/evaluation/schemas.py app/evaluation/repository.py alembic/versions/ tests/evaluation/test_repository.py
git commit -m "feat: persist generation-quality evaluation run summaries"
```

---

### Task 3: The generation runner + CLI entrypoint

**Files:**
- Create: `app/evaluation/generation_runner.py`
- Create: `app/evaluation/generation_run.py`
- Test: `tests/evaluation/test_generation_runner.py`

**Interfaces:**
- Consumes: `app.evaluation.dataset.GOLDEN_DOCUMENTS`/`GOLDEN_QUERIES`, `app.evaluation.judges.GenerationJudge`/`RagasJudge`/`OllamaLLMClientJudge` (Task 1), `app.evaluation.schemas.GenerationQueryResult`/`GenerationEvaluationSummary` (Task 2), `app.evaluation.repository.save_generation_evaluation_run` (Task 2), `app.evaluation.repository.cleanup_eval_data` (ERP-029), `app.retrieval.service.search`, `app.generation.prompt.build_prompt`/`SYSTEM_PROMPT`, `app.generation.client.OllamaLLMClient`.
- Produces: `app.evaluation.generation_runner.run_generation_evaluation(judge: GenerationJudge | None = None, llm_client=None, embedding_client=None, faiss_index=None) -> GenerationEvaluationSummary`.

- [ ] **Step 1: Write the failing test**

Create `tests/evaluation/test_generation_runner.py`:

```python
from app.core.db import get_session_factory
from app.embedding.index import FaissIndex
from app.evaluation.dataset import GOLDEN_QUERIES
from app.evaluation.generation_runner import run_generation_evaluation
from app.evaluation.models import GenerationEvaluationRunRecord
from app.evaluation.schemas import GenerationScores


class _FixedFakeJudge:
    def score(self, user_input, response, retrieved_contexts):
        return GenerationScores(faithfulness=0.9, answer_relevancy=0.8, context_precision=0.7)


class _FakeLLMClient:
    def generate(self, system_prompt, user_prompt):
        return "a fake grounded answer"

    def generate_stream(self, system_prompt, user_prompt):
        raise NotImplementedError


class _DiscriminatingFakeEmbeddingClient:
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


def test_run_generation_evaluation_persists_and_cleans_up(tmp_path):
    faiss_index = FaissIndex(str(tmp_path / "gen-eval.index"), dimension=32)

    summary = run_generation_evaluation(
        judge=_FixedFakeJudge(),
        llm_client=_FakeLLMClient(),
        embedding_client=_DiscriminatingFakeEmbeddingClient(),
        faiss_index=faiss_index,
    )

    assert summary.judge == "_FixedFakeJudge"
    assert summary.num_queries == len(GOLDEN_QUERIES)
    assert summary.mean_faithfulness == 0.9
    assert all(r.answer == "a fake grounded answer" for r in summary.per_query)

    session_factory = get_session_factory()
    with session_factory() as session:
        latest = (
            session.query(GenerationEvaluationRunRecord)
            .order_by(GenerationEvaluationRunRecord.run_at.desc())
            .first()
        )
        assert latest is not None
        assert latest.num_queries == len(GOLDEN_QUERIES)
```

Note for the implementer: `summary.judge` is derived from `type(judge).__name__` in the runner (see Step 3) -- this test's exact assertion string must match whatever naming convention the implementation actually uses; if the implementation instead takes an explicit `judge_name: str` parameter, update this assertion to match that instead. Don't silently change the test to pass without understanding which naming approach was actually implemented.

- [ ] **Step 2: Run test to verify it fails**

Run: `DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/erp_test" AUTH_JWT_SECRET_KEY="test-only-secret-do-not-use-in-production" uv run pytest tests/evaluation/test_generation_runner.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `app/evaluation/generation_runner.py`**

```python
"""Orchestrates one full generation-quality evaluation run.

Ingests the golden dataset (same as `app.evaluation.runner`'s retrieval-quality harness),
generates a real answer for each golden query via the real pipeline building blocks, scores
each with the selected judge, persists a summary, and cleans up.
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
from app.evaluation.judges import GenerationJudge, RagasJudge
from app.evaluation.repository import cleanup_eval_data, save_generation_evaluation_run
from app.evaluation.schemas import GenerationEvaluationSummary, GenerationQueryResult
from app.generation.client import LLMClient, OllamaLLMClient
from app.generation.config import get_generation_settings
from app.generation.prompt import SYSTEM_PROMPT, build_prompt
from app.ingestion.schemas import Chunk
from app.retrieval.service import search


def run_generation_evaluation(
    judge: GenerationJudge | None = None,
    llm_client: LLMClient | None = None,
    embedding_client: EmbeddingClient | None = None,
    faiss_index: FaissIndex | None = None,
    top_k: int = 3,
) -> GenerationEvaluationSummary:
    """Run the golden dataset through the real generation pipeline and return a scored summary.

    `judge` defaults to `RagasJudge()`; pass `OllamaLLMClientJudge(...)` to use the fallback.
    Same isolation/cleanup lifecycle as `app.evaluation.runner.run_evaluation`: a dedicated
    temp FAISS index (never the real app's persisted one), and every transient row this run
    creates (eval user, documents, chunks) is deleted before returning -- only the summary
    row persists.
    """
    judge = judge or RagasJudge()
    generation_settings = get_generation_settings()
    llm_client = llm_client or OllamaLLMClient(generation_settings)

    owned_temp_index_path: str | None = None
    if faiss_index is None:
        owned_temp_index_path = f"{tempfile.gettempdir()}/gen-eval-{uuid.uuid4().hex}.index"
        faiss_index = FaissIndex(owned_temp_index_path, get_embedding_settings().dimension)

    session_factory = get_session_factory()
    with session_factory() as session:
        eval_user = create_user(session, f"gen-eval-{uuid.uuid4()}@internal", "!")
        session.commit()
        owner_id = eval_user.id

    all_document_ids: list[str] = []
    for eval_document in GOLDEN_DOCUMENTS:
        document_id = str(uuid.uuid4())
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

    per_query: list[GenerationQueryResult] = []
    for eval_query in GOLDEN_QUERIES:
        chunks_found = search(
            query=eval_query.query,
            top_k=top_k,
            owner_id=owner_id,
            embedding_client=embedding_client,
            faiss_index=faiss_index,
        )
        user_prompt, included_chunks = build_prompt(
            eval_query.query, chunks_found, generation_settings.max_context_chars
        )
        answer = llm_client.generate(SYSTEM_PROMPT, user_prompt)
        contexts = [chunk.text for chunk in included_chunks]

        scores = judge.score(eval_query.query, answer, contexts)
        per_query.append(
            GenerationQueryResult(
                query=eval_query.query,
                answer=answer,
                faithfulness=scores.faithfulness,
                answer_relevancy=scores.answer_relevancy,
                context_precision=scores.context_precision,
            )
        )

    summary = GenerationEvaluationSummary(
        judge=type(judge).__name__,
        num_queries=len(per_query),
        mean_faithfulness=sum(r.faithfulness for r in per_query) / len(per_query),
        mean_answer_relevancy=sum(r.answer_relevancy for r in per_query) / len(per_query),
        mean_context_precision=sum(r.context_precision for r in per_query) / len(per_query),
        per_query=per_query,
    )

    with session_factory() as session:
        save_generation_evaluation_run(session, summary)
        cleanup_eval_data(session, all_document_ids, owner_id)
        session.commit()

    if owned_temp_index_path is not None:
        import os

        if os.path.exists(owned_temp_index_path):
            os.remove(owned_temp_index_path)

    return summary
```

- [ ] **Step 4: Implement `app/evaluation/generation_run.py`**

```python
"""CLI entrypoint: `uv run python -m app.evaluation.generation_run [--judge ragas|ollama]`.

Runs the generation-quality evaluation harness and prints a report. This module's stdout
report output is a deliberate, documented exception to the no-`print()` rule, same as
`app.evaluation.run`.
"""

import argparse

from app.evaluation.generation_runner import run_generation_evaluation
from app.evaluation.judges import OllamaLLMClientJudge, RagasJudge
from app.generation.client import OllamaLLMClient
from app.generation.config import get_generation_settings


def main() -> None:
    """Parse `--judge`, run the evaluation harness, and print a summary report."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--judge", choices=["ragas", "ollama"], default="ragas")
    args = parser.parse_args()

    judge = (
        RagasJudge()
        if args.judge == "ragas"
        else OllamaLLMClientJudge(OllamaLLMClient(get_generation_settings()))
    )

    summary = run_generation_evaluation(judge=judge)

    print(f"Generation evaluation run ({summary.judge}): {summary.num_queries} queries")
    print(f"  Mean Faithfulness:      {summary.mean_faithfulness:.3f}")
    print(f"  Mean Answer Relevancy:  {summary.mean_answer_relevancy:.3f}")
    print(f"  Mean Context Precision: {summary.mean_context_precision:.3f}")
    print()
    for result in summary.per_query:
        print(f"  {result.query!r}")
        print(f"      answer: {result.answer[:200]}")
        print(
            f"      faithfulness={result.faithfulness:.2f} "
            f"relevancy={result.answer_relevancy:.2f} "
            f"precision={result.context_precision:.2f}"
        )


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/erp_test" AUTH_JWT_SECRET_KEY="test-only-secret-do-not-use-in-production" uv run pytest tests/evaluation/ -v`

- [ ] **Step 6: Manually run the real CLI against live Ollama (both judges) -- developer verification, not an automated test**

Run: `DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/erp" AUTH_JWT_SECRET_KEY="test-only-secret-do-not-use-in-production" uv run python -m app.evaluation.generation_run --judge ragas`

Then: `... uv run python -m app.evaluation.generation_run --judge ollama`

Expected: both produce a printed report with plausible scores and no traceback. Confirm a row was persisted in `generation_evaluation_runs` for each, and that no stray eval documents/users remain (same DB checks as ERP-029's Task 3 Step 6). If `--judge ragas` hits the documented Ollama-timeout issue, note that in the session log as confirmed-in-practice, not just theoretical -- and confirm `--judge ollama` still works as the fallback.

- [ ] **Step 7: Run ruff and mypy**

Run: `uv run ruff check app/evaluation tests/evaluation && uv run mypy app/evaluation`

- [ ] **Step 8: Commit**

```bash
git add app/evaluation/generation_runner.py app/evaluation/generation_run.py tests/evaluation/test_generation_runner.py
git commit -m "feat: add the generation-quality evaluation runner and CLI entrypoint"
```

---

### Task 4: Final verification and documentation

**Files:**
- Modify: `.ai/memory/current-state.md`
- Create: `.ai/tickets/ERP-030.md`
- Create: `.ai/sessions/2026-09-06-generation-evaluation.md`

- [ ] **Step 1: Run the full test suite with coverage**

Run: `DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/erp_test" AUTH_JWT_SECRET_KEY="test-only-secret-do-not-use-in-production" uv run pytest -q --cov=app --cov-fail-under=90`

- [ ] **Step 2: Run ruff, mypy, and pre-commit across everything changed**

Run: `uv run ruff check app tests && uv run mypy app`, then `uv run pre-commit run --files <git diff --name-only develop...HEAD>`.

- [ ] **Step 3: Write `.ai/tickets/ERP-030.md`**

Follow the format of `.ai/tickets/ERP-029.md`. Note the `ragas==0.3.9` pin and the `langchain-community<0.4` `constraint-dependencies` entry explicitly in Notes, with a one-line pointer to the upstream ragas issues, so a future dependency bump doesn't silently reintroduce the broken import.

- [ ] **Step 4: Write `.ai/sessions/2026-09-06-generation-evaluation.md`**

Follow the format of `.ai/sessions/2026-09-06-evaluation.md`. Must capture: the ragas 0.4.3 import bug and the version-pin fix, the reused-dataset decision, the fallback-judge decision, and whichever judge(s) were actually confirmed working end-to-end during Task 3 Step 6.

- [ ] **Step 5: Update `.ai/memory/current-state.md`**

Add a bullet for ERP-030. Update "Next Planned Work" to remove the "generation-quality evaluation" line (now done) -- if nothing else is queued, say so plainly rather than inventing a new item.

- [ ] **Step 6: Commit, push, open PR**

```bash
git add .ai/tickets/ERP-030.md .ai/sessions/2026-09-06-generation-evaluation.md .ai/memory/current-state.md
git commit -m "docs: close out ERP-030 (generation-quality evaluation)"
git push -u origin erp-030-generation-quality-evaluation
gh pr create --base develop --title "feat: generation-quality evaluation harness (ERP-030)" --body "..."
```

Follow the same PR body structure as PR #24/#25/#26/#27 (Summary + Test plan). Explicitly call out the `ragas` version pin and constraint-dependency in the PR description, since it's the kind of thing a future contributor could easily "clean up" without realizing it breaks the import.
