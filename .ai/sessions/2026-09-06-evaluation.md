# Session — Evaluation (retrieval quality)

Date: 2026-09-06
Tickets Touched: ERP-029

## Decisions

- Before scoping this ticket, the user asked what was planned for the future "LLMOps & Evaluation Platform" (named in `docs/architecture.md` as a future foundation repo). Nothing was documented beyond its name. User directed that it be defined as a **generalized, standalone platform** other projects can link to (not coupled to this repo) — recorded as a design-constraint note directly in `docs/architecture.md` (2026-09-06), for whenever that platform's own repo actually starts.
- Given that, the user chose to still build a **lightweight standalone eval harness in this repo now**, understanding it will likely be replaced/integrated with the LLMOps platform later rather than waiting for that platform to exist first.
- Scope split in two, matching a recommendation: **retrieval quality** first (this ticket — cheap, deterministic, no LLM-judge calls), **generation quality** (LLM-as-judge) deferred as a follow-up.
- Classified as Architectural (brainstorming skill): new subsystem, no existing evaluation flow to extend.
- Key design call, made after checking the actual `Chunk`/`document_id` generation code: the golden dataset is authored as `Chunk`-shaped **templates**, not real PDF fixtures run through `ingest_pdf`. `document_id` is a fresh UUID per run and `chunk_id` is derived from it, so a dataset can't hardcode a `chunk_id` ahead of time — only `chunk_index` is stable. Building the dataset from real PDFs would also make chunk boundaries a property of PyMuPDF4LLM's font-size heuristics, not something the dataset author fully controls. Bypassing `ingest_pdf` entirely and calling `embed_and_persist` directly with hand-authored chunks is deterministic and has zero PDF-parsing dependency — retrieval-quality eval is about the search pipeline, not ingestion parsing (already covered by `tests/ingestion/`).
- CLI (`uv run python -m app.evaluation.run`), not a new API endpoint — matches how eval harnesses are normally used (developer/CI tool, not live production traffic).
- Not wired into CI as a gate yet (no established acceptable-threshold baseline) — `tests/evaluation/` still runs in CI like any other module, but only tests the harness's own correctness via a fake embedding client, never real Ollama (consistent with the rest of this repo — confirmed via `.github/workflows/ci.yml` having no Ollama service).
- The runner must never be load-bearing on real app data: always builds its own temp-file-backed FAISS index, never `settings.faiss_index_path`, and deletes every row it creates (eval user, documents, chunks) after each run — except the `evaluation_runs` summary row itself, which is the durable point of persisting results at all (per ADR-003, which had already named "evaluation results" as a reason Postgres was chosen).

## Implementation Summary

- `app/evaluation/dataset.py`: `EvalChunk`/`EvalDocument`/`EvalQuery` dataclasses, `GOLDEN_DOCUMENTS` (2 docs, 5 chunks), `GOLDEN_QUERIES` (4 queries).
- `app/evaluation/metrics.py`: `precision_at_k`, `recall_at_k`, `reciprocal_rank` (pure functions).
- `app/evaluation/schemas.py`: `QueryResult`, `EvaluationSummary`.
- `app/evaluation/models.py`: `EvaluationRunRecord` (new `evaluation_runs` table).
- `app/evaluation/repository.py`: `save_evaluation_run`, `cleanup_eval_data` (deletes chunks, then documents, then the eval user — no cascade FKs, so order matters).
- New Alembic migration `0aa457a07a61_create_evaluation_runs_table.py`. `alembic/env.py` and `tests/conftest.py` both gained an explicit `import app.evaluation.models` — without it, autogenerate reported an empty diff as "detected removed table" since nothing registered `EvaluationRunRecord` on `Base.metadata` for the CLI's own model-scanning process.
- `app/evaluation/runner.py`: `run_evaluation()` orchestrates the full flow. Caught and fixed during implementation: `tempfile.NamedTemporaryFile(...).name` pre-creates an empty file, which `FaissIndex` then tries to read as a corrupt index and fails — fixed by generating a path string that doesn't exist yet instead.
- `app/evaluation/run.py`: CLI entrypoint, prints a report.
- Tests: `tests/evaluation/test_dataset.py`, `test_metrics.py`, `test_repository.py`, `test_runner.py` — all using OTel-independent, Ollama-independent fixtures (a crafted fake embedding client with distinguishable one-hot vectors for the runner test). Caught and fixed during implementation: the repository test originally left a stray `evaluation_runs` row behind, which broke the runner test's original "exactly 1 row" assertion on a second run against the same persistent dev/test DB — fixed by having the repository test clean up its own row, and by changing the runner test to assert against the most-recent row rather than a total count (since real `evaluation_runs` rows are meant to accumulate across runs by design).
- Verified end-to-end against live Ollama and real Postgres (not just unit tests): `uv run python -m app.evaluation.run` produced Precision@3=0.333, Recall@3=1.0, MRR=1.0 on the golden dataset, with a confirmed persisted row and zero stray eval documents/users left in the database afterward.
- Verified: ruff clean, mypy --strict clean (58 files), full suite 291 passed at 98.21% coverage, pre-commit (gitleaks + ruff) clean on every file changed on this branch, migration verified upgrade/downgrade/upgrade plus an empty autogenerate diff against real Postgres.

## Blockers

None.

## Next Steps

Push, open PR into `develop` (`gh pr merge` will need the user to run it directly, per the environment's auto-mode permission classifier — same as ERP-027/ERP-028). After this, the remaining Evaluation follow-up is generation-quality evaluation (LLM-as-judge metrics), and the LLMOps & Evaluation Platform's generalized-platform design constraint (documented in `docs/architecture.md`, 2026-09-06) should be revisited once that platform's own repo actually starts.
