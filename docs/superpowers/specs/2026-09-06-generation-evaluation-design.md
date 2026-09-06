# Evaluation (Generation Quality) Design

Date: 2026-09-06
Status: Approved

## Context

Second half of the "Evaluation" project goal, following ERP-029's retrieval-quality harness. Scores the LLM's generated answers, not just what retrieval finds. Same constraints as ERP-029: lightweight, standalone, not the future LLMOps & Evaluation Platform, not wired into CI as a gate yet.

## Decision

Use **Ragas** (`docs/superpowers/specs/2026-09-06-evaluation-design.md`'s sibling research established it as the standard tool for this) for the three reference-free RAG-triad metrics: Faithfulness, Answer Relevancy, and Context Precision (the "without reference" variant, so no ground-truth answers need authoring). Reuses ERP-029's exact golden dataset unchanged. Adds a hand-rolled `OllamaLLMClientJudge` as a selectable fallback (not automatic), since Ragas has documented reliability issues (timeouts) specifically against local Ollama models.

## New dependencies

- `ragas` — the RAG-evaluation library itself.
- `langchain-ollama` — provides `ChatOllama` and `OllamaEmbeddings`, which Ragas's judge/embedding wrappers require (`LangchainLLMWrapper(ChatOllama(...))`, `LangchainEmbeddingsWrapper(OllamaEmbeddings(...))`). Ragas has no native Ollama client of its own.

Both are real new dependencies, approved after explaining the concrete cost (a bigger footprint than ERP-029's zero-new-deps approach, plus `ragas`'s own transitive dependencies) and a known local-Ollama reliability risk — hence the fallback judge.

## Components

- **Dataset**: no changes — `app.evaluation.dataset.GOLDEN_DOCUMENTS`/`GOLDEN_QUERIES` (ERP-029) reused as-is.
- **`app/evaluation/judges.py`**: a `GenerationJudge` Protocol (`score(user_input: str, response: str, retrieved_contexts: list[str]) -> GenerationScores`), with:
  - `RagasJudge`: builds a `ragas.SingleTurnSample`, scores it with `ragas.metrics.Faithfulness`, `ResponseRelevancy`, `LLMContextPrecisionWithoutReference` (each constructed once with a shared `LangchainLLMWrapper(ChatOllama(...))` judge LLM; `ResponseRelevancy` additionally needs `LangchainEmbeddingsWrapper(OllamaEmbeddings(...))`). Ragas's `single_turn_ascore` is async; the judge wraps each call in `asyncio.run(...)` since the rest of this codebase (and this CLI tool) is synchronous.
  - `OllamaLLMClientJudge`: three hand-written scoring prompts (one per metric) against the existing `app.generation.client.OllamaLLMClient`, each asking for a `0.0`-`1.0` score with a one-line justification, parsed via a regex extracting the first float in the response. A response the parser can't extract a float from logs a warning and scores `0.0` for that metric (fails safe toward "looks bad, go check it," not toward a silently-inflated fake pass).
- **`GenerationScores`** (`app/evaluation/schemas.py`): `faithfulness: float`, `answer_relevancy: float`, `context_precision: float`.
- **`app/evaluation/generation_runner.py`**: `run_generation_evaluation(judge: GenerationJudge | None = None, llm_client=None, embedding_client=None, faiss_index=None) -> GenerationEvaluationSummary`. For each golden query: `search()` → `build_prompt()` → `llm_client.generate()` (the same building blocks `app.generation.service.generate()` uses internally, called directly here rather than through `generate()` itself, so the runner keeps access to the actual retrieved chunk texts as `retrieved_contexts` — `generate()`'s public return only carries citation metadata, not chunk text). Scores each `(query, answer, contexts)` with the selected judge (`RagasJudge` by default). Same ingest/cleanup lifecycle as ERP-029's `run_evaluation` (dedicated temp FAISS index, throwaway eval user/documents/chunks deleted after the run; only the summary row persists).
- **`app/evaluation/models.py`**: new `GenerationEvaluationRunRecord` (`generation_evaluation_runs` table: id, run_at, judge name, num_queries, mean_faithfulness, mean_answer_relevancy, mean_context_precision, details JSON).
- **`app/evaluation/generation_run.py`**: CLI entrypoint, `uv run python -m app.evaluation.generation_run [--judge ragas|ollama]` (default `ragas`).
- New Alembic migration: `create_generation_evaluation_runs_table`.

## Testing

- `OllamaLLMClientJudge`'s float-parsing logic: unit-tested against crafted LLM response strings (well-formed, malformed, missing) — no real Ollama needed.
- `RagasJudge`'s wiring (sample construction, async handling): tested by mocking each Ragas metric class's `single_turn_ascore` to return a fixed score, verifying the judge calls it with the right sample fields and returns the right `GenerationScores` shape — not a real Ragas/Ollama integration test (consistent with this repo never calling real Ollama in CI).
- `run_generation_evaluation`'s orchestration (persistence, cleanup, aggregation): tested with a fake `LLMClient` and a fake `GenerationJudge` returning fixed scores, mirroring ERP-029's fake-embedding-client pattern.
- Same as ERP-029: not wired into CI as a gate. A developer-run smoke test against live Ollama + real Ragas is a manual verification step during implementation, not an automated test.

## Future Follow-ups

- Reference-based metrics (Answer Correctness, Context Recall) if a ground-truth-answer dataset is ever authored.
- Wiring either harness as a CI regression gate once a baseline exists.
- Eventual replacement/integration with the future LLMOps & Evaluation Platform.
