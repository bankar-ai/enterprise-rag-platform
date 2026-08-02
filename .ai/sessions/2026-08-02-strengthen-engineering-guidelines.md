# Session — Strengthen Engineering Guidelines

Date: 2026-08-02
Tickets Touched: ERP-006

## Decisions

- **90% coverage threshold**, not 95% or 100%: measured against a baseline of 99% (206 statements, 3 missed, all in `chunker.py`'s defensive error-path branches). 90% is high enough to catch a real regression while leaving slack so a legitimately-hard-to-test defensive branch doesn't immediately break CI.
- **Coverage gate is CI-only, not a `pre-commit` hook**: same reasoning as Mypy in ERP-007 — a full-suite coverage run is too slow for a per-commit hook; CI still gates merges.
- **stdlib `logging`, not a third-party library**: the codebase is single-process with no distributed tracing yet, so structlog/loguru would be premature. Explicitly flagged as a future reconsideration point once observability (a roadmap item) is built out.
- **Retrofit the code, not just document the convention**: the codebase had zero logging calls anywhere before this session, including in `app/ingestion/jobs.py`'s exception handler, which silently swallowed a failed ingestion job into a string field with no log line — a real, present-day violation of the guidelines' pre-existing "Log unexpected failures" line. Landing a new logging convention without fixing the one place it already applied was judged worse than not landing it at all, so the retrofit was pulled into scope rather than deferred (user's explicit choice during brainstorming).
- **Cross-reference, don't duplicate**, the routes/service-layer principle already stated in `docs/architecture.md`: a one-line pointer from `docs/engineering-guidelines.md` instead of restating the rule, to avoid the two docs drifting out of sync.

## Implementation Summary

- `pyproject.toml` / `.github/workflows/ci.yml`: `pytest-cov` added as a dev dependency; CI's test step becomes `uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=90`.
- `app/ingestion/jobs.py`: added a module-level `logger = logging.getLogger(__name__)` and a `logger.exception(...)` call in `run_ingestion_job`'s except block, ahead of recording the `FAILED` status. New TDD-driven test (`test_run_ingestion_job_logs_on_failure`, using `caplog`) confirms the log record is emitted; written first, confirmed to fail (no logger existed yet), then made to pass.
- `docs/engineering-guidelines.md`: coverage marker flipped from `[tooling candidate]` to `[enforced: pytest-cov, --cov-fail-under=90]`; new `## Logging` section added (stdlib `logging`, module-level loggers, `logger.exception` on non-re-raising excepts, no full-body/secret logging, cross-referencing `CLAUDE.md`'s no-`print()` rule rather than restating it); one-line cross-reference to `docs/architecture.md`'s routes/service-layer principle added to Coding Standards; the top status note rewritten since it previously pointed at "pending ERP-006," which was no longer true — a deviation from the literal plan text, made because a stale status note actively contradicting the doc's own enforced-markers would be misleading (same principle `CLAUDE.md` states about `current-state.md`).
- Guardrail verification (Task 4): coverage was deliberately dropped to 58% (by temporarily moving `test_jobs.py` and `test_router.py` out of the way — removing just `test_jobs.py` alone only dropped coverage to ~96%, since `test_router.py` exercises `jobs.py` indirectly through the HTTP layer), confirmed `pytest --cov-fail-under=90` fails with a clear message, then both files restored and the gate reconfirmed passing at ~98.6%.
- Final full-suite verification: `pytest --cov=app --cov-fail-under=90` (33 tests, ~98.6% coverage), `ruff check .`, `uv run mypy app` all run clean end-to-end.

## Blockers

None.

## Next Steps

- Merge `develop` → `main` (same PR pattern as ERP-007/ERP-010).
- Embedding generation + Postgres/FAISS/BM25 persistence remains the next vertical slice of application work.
- Consider requiring CI status checks in `main`'s branch protection, now that CI enforces ruff, mypy, pytest+coverage, and gitleaks.
