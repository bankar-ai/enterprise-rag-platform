# Evaluation (Retrieval Quality) Design

Date: 2026-09-06
Status: Approved

## Context

`docs/architecture.md`/`docs/roadmap.md` name Evaluation as a project goal; nothing exists yet. A separate future "LLMOps & Evaluation Platform" repo is named as the eventual generalized, cross-project evaluation service (see the design-constraint note added to `docs/architecture.md` on 2026-09-06) — this ticket is explicitly **not** that platform. It's a lightweight, standalone harness scoped to this repo, understood to likely be replaced/integrated with that platform later.

Scope is split in two: **retrieval quality** (this ticket) first, since it's cheap (no LLM-judge calls, deterministic, fast), and **generation quality** (LLM-as-judge metrics) deferred as a follow-up.

## Decision

A small hand-authored golden dataset (documents + chunks + queries with known-relevant chunk indices) is fed through the real `embed_and_persist` + `retrieval.search()` pipeline (real Ollama embeddings + real FAISS + real Postgres BM25, run manually by a developer — not mocked, since the point is evaluating the real pipeline), scored with Precision@k/Recall@k/MRR, persisted to a new `evaluation_runs` Postgres table, and printed as a report. Invoked as `uv run python -m app.evaluation.run`, not a new API endpoint. Not wired into CI as a gate (no established acceptable-threshold baseline yet) — CI only runs the harness's own correctness tests against fake embeddings.

## Why hand-authored `Chunk` objects, not PDF fixtures

`document_id` is a fresh UUID generated at ingest time, and `chunk_id` is derived from it (`f"{document_id}-{index}"`) — so labels can't hardcode a chunk_id ahead of time; only `chunk_index` within a document is stable. Building the golden dataset from real PDFs (via `ingest_pdf`, going through PyMuPDF4LLM's font-size-based markdown header detection) would make the exact number and boundaries of resulting chunks a property of that heuristic, not something the dataset author fully controls — fragile and indirect. Retrieval-quality evaluation is about whether, *given a known set of chunks*, the search pipeline (embedding + FAISS + BM25 + RRF fusion) returns the right ones — not about PDF parsing, which is already covered by `tests/ingestion/`. So the golden dataset is authored directly as `app.ingestion.schemas.Chunk` objects with explicit `chunk_index`/`section_path`/`text`, bypassing `ingest_pdf` entirely and calling `embed_and_persist` directly. This is deterministic and has zero PDF/font-heuristic dependency.

## Golden Dataset

Two documents, five chunks, four queries — small enough to run in seconds, large enough to force real discrimination between documents:

- **Document "cats"**: chunk 0 (`["Cats", "Diet"]`, obligate-carnivore diet text), chunk 1 (`["Cats", "Behavior"]`, crepuscular grooming/activity text), chunk 2 (`["Cats", "Habitat"]`, apartment/house adaptability text)
- **Document "dogs"**: chunk 0 (`["Dogs", "Training"]`, positive-reinforcement training text), chunk 1 (`["Dogs", "Diet"]`, omnivore diet text)

Queries (each naming its target document + expected `chunk_index`es):
1. "What do cats eat?" → cats chunk 0
2. "How are dogs trained?" → dogs chunk 0
3. "When are cats most active?" → cats chunk 1
4. "What can dogs eat?" → dogs chunk 1

`top_k` defaults to 3 (meaningful discrimination against a 5-chunk corpus). Default `rerank=False`, `expand_sections=False` — baseline hybrid search only; the runner accepts both as parameters for future extension, not hardcoded.

## Components

- `app/evaluation/dataset.py`: `EvalChunk` (index, section_path, text — a template, not yet stamped with a real `chunk_id`/`document_id`), `EvalDocument` (label, chunks: list[EvalChunk]), `EvalQuery` (query text, document label, expected_chunk_indices), `GOLDEN_DOCUMENTS: list[EvalDocument]`, `GOLDEN_QUERIES: list[EvalQuery]`.
- `app/evaluation/metrics.py`: pure functions `precision_at_k(retrieved: list[str], relevant: set[str], k: int) -> float`, `recall_at_k(...) -> float`, `reciprocal_rank(retrieved: list[str], relevant: set[str]) -> float` (0.0 if no relevant item is retrieved at all).
- `app/evaluation/schemas.py`: `QueryResult` (query, precision, recall, reciprocal_rank, retrieved_chunk_ids, relevant_chunk_ids), `EvaluationSummary` (top_k, num_queries, mean_precision, mean_recall, mrr, per_query: list[QueryResult]).
- `app/evaluation/models.py`: `EvaluationRunRecord` (new `evaluation_runs` table, sharing `app.ingestion.models.Base`) — `id` (UUID pk), `run_at` (server default now), `top_k`, `num_queries`, `mean_precision_at_k`, `mean_recall_at_k`, `mrr`, `details` (JSON — the full per-query breakdown, for later inspection even though nothing reads it back programmatically in v1).
- `app/evaluation/repository.py`: `save_evaluation_run(session, summary) -> EvaluationRunRecord`, `cleanup_eval_data(session, document_ids: list[str], owner_id: uuid.UUID)` (deletes chunks, then documents, then the eval user row — no cascade FKs exist, so order matters).
- `app/evaluation/runner.py`: `run_evaluation(top_k=3, embedding_client=None, faiss_index=None) -> EvaluationSummary`. Orchestration:
  1. Create a throwaway eval user (random UUID/email, via `app.auth.repository.create_user`) to satisfy `documents.owner_id`'s FK.
  2. Build `faiss_index` if not injected — always a **dedicated temp-file-backed `FaissIndex`**, never the real app's persisted index (`settings.faiss_index_path`), so running the harness never pollutes or depends on a developer's real local index.
  3. For each `EvalDocument`: generate a fresh `document_id`, stamp its `EvalChunk`s into real `Chunk` objects (`chunk_id=f"{document_id}-{index}"`), call `embed_and_persist(...)` with the shared `faiss_index`.
  4. For each `EvalQuery`: resolve its `relevant_chunk_ids` using the concrete `document_id` assigned to its target document, call `retrieval.service.search(query, top_k, owner_id=eval_user_id, faiss_index=faiss_index)`, compute per-query metrics.
  5. Aggregate into `EvaluationSummary`, persist via `save_evaluation_run`.
  6. Clean up: delete the eval documents/chunks/user (`cleanup_eval_data`) and the temp FAISS index file — **the `evaluation_runs` row is never cleaned up**; that's the durable history this exists to build.
- `app/evaluation/run.py`: `if __name__ == "__main__":` entrypoint calling `run_evaluation()` and printing a formatted report table to stdout. Invoked via `uv run python -m app.evaluation.run`.
- New Alembic migration: `create_evaluation_runs_table`.

## Testing

- `tests/evaluation/test_metrics.py`: pure-function unit tests for `precision_at_k`/`recall_at_k`/`reciprocal_rank` — no DB, no Ollama.
- `tests/evaluation/test_dataset.py`: a regression guard asserting every `expected_chunk_indices` entry in `GOLDEN_QUERIES` actually exists in its referenced document's chunk list (catches a dataset-authoring typo before it silently scores wrong).
- `tests/evaluation/test_runner.py`: exercises the full `run_evaluation()` orchestration (persistence, cleanup, metric aggregation) against a **fake embedding client** returning crafted, distinguishable vectors per chunk (so FAISS actually discriminates correctly without needing live Ollama) — this is what makes the harness's own correctness testable in CI, consistent with this repo's existing pattern of never calling real Ollama in tests. Real-Ollama usage only happens when a developer runs `python -m app.evaluation.run` unmocked.
- Not wired into CI as a pipeline gate (per the scoping decision) — CI runs `tests/evaluation/` like any other test module (fast, fake-embedding-based), but nothing currently fails a build based on retrieval-quality thresholds.

## Future Follow-ups (not in this ticket)

- Generation-quality evaluation (LLM-as-judge: faithfulness, answer relevancy, context precision).
- Wiring this harness as a CI regression gate once a baseline/acceptable-threshold exists.
- Replacing/integrating this with the future LLMOps & Evaluation Platform once it exists.
