# ERP-006 — Strengthen Engineering Guidelines Design

Date: 2026-08-02

## Context

`docs/engineering-guidelines.md` was written in prose before any real application code or tooling existed in the repository (per ERP-002's `Depends On` note: "ERP-006 needs real code before its guidelines can be enforced"). ERP-007 (this same day) configured Ruff and Mypy and marked several guideline lines `[enforced: ...]`, leaving one remaining `[tooling candidate]` item (a pytest coverage threshold) and no changes to the guidelines' actual content — only its markers. This spec covers ERP-006 itself: closing that last tooling gap, and strengthening the guidelines' content now that `app/ingestion/` gives something real to check them against.

## Scope

In scope: a pytest coverage gate, a new logging convention (the codebase currently has zero logging calls anywhere, despite an existing "Log unexpected failures" guideline line), retrofitting the one concrete violation of that guideline already in the code, and a one-line cross-reference to `docs/architecture.md`'s existing routes/service-layer principle.

Out of scope (explicitly deferred): rewriting guideline sections that are already clear, auditing all of `app/` for gaps beyond the one known logging violation, adding a logging library (structlog/loguru — stdlib `logging` is sufficient at this scale), any change to `docs/architecture.md` itself.

## Coverage Gate

`pytest-cov` added as a dev dependency (`uv add --dev pytest-cov`) — the standard tool for this in the Python ecosystem; the standard library has no coverage measurement capability, and no existing project dependency provides it, so this satisfies the dependency policy's "stdlib first, then existing deps, then justify" order.

CI's pytest step becomes `uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=90`. 90% was chosen over 95%/100% after measuring current coverage (99%, 206 statements, 3 missed — all in `chunker.py`'s defensive error-path branches): high enough to catch a real regression, with enough slack that a legitimately-hard-to-test defensive branch doesn't immediately break CI. Not run via `pre-commit` (same reasoning as Mypy in ERP-007 — a full coverage run needs the whole suite, which is too slow for a per-commit hook; CI is the right gate for this).

`docs/engineering-guidelines.md`'s coverage marker flips from `**[tooling candidate: coverage threshold in pytest/CI]**` to `**[enforced: pytest-cov, --cov-fail-under=90]**`.

## Logging Convention

Grepping `app/` today finds zero `import logging`, zero `logger.`, zero `print(` — the "Log unexpected failures" line in the Error Handling section is currently unactionable prose with no defined convention and no example to follow. This spec defines one:

- stdlib `logging` module — no new dependency. A structured logging library would be premature at this project's current scale (single-process, no distributed tracing yet); this is noted as a future reconsideration point if/when observability (already on the roadmap) is built out.
- Every module that logs gets its own logger: `logger = logging.getLogger(__name__)` at module level.
- Any `except` block that does not re-raise must call `logger.exception(...)` (captures the traceback automatically) before handling the failure.
- Never log full request/response bodies or secrets — log identifiers (job IDs, filenames) and error messages, not raw payloads.
- This is additive to `CLAUDE.md`'s existing "never use `print()` in application code" rule, not a restatement of it — the guidelines doc will cross-reference `CLAUDE.md` rather than duplicate the rule.

## Retrofit: `app/ingestion/jobs.py`

`run_ingestion_job`'s `except Exception as exc:` block (the one with the pre-existing `# noqa: BLE001` justifying why it's a deliberately blind catch) currently does nothing but store `str(exc)` on the job record — a failed ingestion job produces no log line anywhere. This is the concrete, present-day violation of the "Log unexpected failures" guideline the new convention is meant to fix. Add a module-level logger and a `logger.exception(...)` call in that except block, with a new test asserting the log record is emitted on failure (via pytest's `caplog` fixture).

## Cross-Reference to Architecture Principles

`docs/architecture.md:61-66` already states the routes/service-layer separation principle ("business logic must never exist inside API routes... API routes should only: validate input, call service layer..."). `docs/engineering-guidelines.md`'s Coding Standards section gets one added line pointing to it, rather than restating the principle — avoids the two docs drifting out of sync.

## Verification

- `uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=90` — passes at current ~99% coverage.
- A new test in `tests/ingestion/test_jobs.py` confirms `run_ingestion_job`'s failure path emits a log record (via `caplog`), and that the existing behavior (job marked `FAILED`, error string stored) is unchanged.
- `uv run ruff check .` / `uv run mypy app` — still clean (logging import/usage must satisfy both).
- Deliberately drop coverage below 90% (comment out a test locally, not committed) to confirm the CI command actually fails, then restore — mirrors ERP-007's and ERP-010's guardrail-verification pattern.
