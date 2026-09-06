# Session — Evaluation (generation quality)

Date: 2026-09-06
Tickets Touched: ERP-030

## Decisions

- Researched RAGAS vs. hand-rolled LLM-as-judge prompts before recommending (per `CLAUDE.md`'s research-before-recommending rule); RAGAS confirmed as the standard tool for the RAG triad (Faithfulness, Answer Relevancy, Context Precision). User approved it as a new dependency after being shown the real cost: it needs `langchain-ollama` too (to wrap `ChatOllama`/`OllamaEmbeddings`), not just `ragas` alone, plus community-reported timeout issues specifically against local Ollama models.
- User's explicit addition: build `OllamaLLMClientJudge` (hand-rolled prompts against the existing `OllamaLLMClient`) as a **selectable** fallback, not an automatic silent one — silent fallback-on-exception would mask real quality differences between judges without the user knowing which one actually scored a given run.
- Reused ERP-029's exact golden dataset unchanged: the three chosen metrics (Faithfulness, Answer Relevancy, `LLMContextPrecisionWithoutReference`) are all reference-free, so no ground-truth answers needed authoring.
- **Real blocker found during implementation, not anticipated in the design**: `ragas==0.4.3`'s `import ragas` is completely broken — unconditionally imports `langchain_community.chat_models.vertexai.ChatVertexAI`, a path removed in `langchain-community>=0.4` (confirmed upstream bug, ragas issues #2741/#2745/#2753, affecting every user regardless of LLM backend, not an Ollama-specific problem). Pinning `ragas==0.3.9` alone did NOT fix it (same bug present); the actual fix required constraining `langchain-community<0.4` as well. Verified the full import chain (`ragas`, `SingleTurnSample`, all three metrics, both Langchain wrappers, `ChatOllama`/`OllamaEmbeddings`) actually works with `ragas==0.3.9` + `langchain-community<0.4` before writing any implementation code, rather than assuming from documentation.
- Declared `langchain-community<0.4` via `[tool.uv] constraint-dependencies` rather than as a direct project dependency (`uv add`'s default) — this codebase never imports `langchain_community` itself; it's purely a transitive-compatibility pin for ragas's bug, and the pyproject.toml comment says so explicitly for future maintainers.
- Generation runner calls `search()` → `build_prompt()` → `llm_client.generate()` directly (the same building blocks `app.generation.service.generate()` uses internally) rather than calling `generate()` itself, because `generate()`'s public return only carries citation metadata, not the actual chunk text needed as Ragas's `retrieved_contexts` input.
- CLI (`uv run python -m app.evaluation.generation_run --judge ragas|ollama`), matching ERP-029's CLI-not-endpoint pattern.

## Implementation Summary

- `app/evaluation/judges.py` (new): `GenerationJudge` Protocol, `RagasJudge` (lazy-imports `ragas`/`langchain_ollama` inside `__init__` so importing this module doesn't require them to be importable in contexts that only need the fallback), `OllamaLLMClientJudge` (three hand-written prompts, one per metric, float-parsed via regex with fail-safe-to-0.0 behavior on an unparseable response).
- `app/evaluation/schemas.py`: added `GenerationScores`, `GenerationQueryResult`, `GenerationEvaluationSummary`.
- `app/evaluation/models.py`: added `GenerationEvaluationRunRecord` (new `generation_evaluation_runs` table).
- `app/evaluation/repository.py`: added `save_generation_evaluation_run` (reuses ERP-029's `cleanup_eval_data` unchanged — same document/chunk/user shape).
- New Alembic migration `f30732b09f7a` (chains from ERP-029's `0aa457a07a61`), verified upgrade/downgrade/upgrade plus an empty autogenerate diff against real Postgres.
- `app/evaluation/generation_runner.py`: `run_generation_evaluation()`, same ingest/cleanup lifecycle as ERP-029's `run_evaluation`.
- `app/evaluation/generation_run.py`: CLI entrypoint with `--judge` flag.
- `pyproject.toml`: added `ragas==0.3.9`, `langchain-ollama`; added `[tool.uv] constraint-dependencies = ["langchain-community<0.4"]` with an explanatory comment.
- Tests: `tests/evaluation/test_judges.py` (float-parsing edge cases for the fallback judge; a mocked-Ragas-metrics wiring test for `RagasJudge` — patches `langchain_ollama`/`ragas` classes at their import source, verified the mocks actually get called rather than trusting the patch targets blindly), `tests/evaluation/test_generation_runner.py` (fake `LLMClient` + fake judge, mirroring ERP-029's fake-embedding-client pattern; a second test covering the self-managed temp-FAISS-index path, closing a coverage gap the same way ERP-029 needed).
- **Verified end-to-end against live Ollama, both judges**: `--judge ollama` completed cleanly in seconds (Faithfulness=1.0, Answer Relevancy=0.75, Context Precision=0.575 on the golden dataset, confirmed persisted and no stray data left behind). `--judge ragas` hung 10+ minutes with zero output before being manually killed — confirming the documented local-Ollama reliability issue in practice, not just in research. The killed run's transient eval data (4 documents, 2 users) was manually cleaned up from the dev DB afterward, since the kill happened before the runner's own cleanup code could execute.
- Verified: ruff/mypy clean (61 files), full suite 298 passed at 97.06% coverage, pre-commit clean on every file changed on this branch. Two unrelated `docling` `std::bad_alloc` test failures during the first full-suite run were resource-exhaustion flakes from the concurrent Ragas process — confirmed by re-running the affected tests in isolation (all passed) and the full suite again afterward (298/298 passed).

## Blockers

None remaining — the ragas import bug was a real blocker but is now resolved via the version/constraint pin.

## Next Steps

Push, open PR into `develop` (same `gh pr merge` classifier note as every prior PR in this project — the user merges directly). After this, no non-deferred Evaluation work remains; the only follow-ups are the deferred items already tracked in `.ai/memory/current-state.md` (per-tenant FAISS, external IdP, admin cross-user visibility, self-service admin creation) and the future LLMOps & Evaluation Platform itself.
