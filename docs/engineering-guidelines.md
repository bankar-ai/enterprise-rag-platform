# Engineering Guidelines

> Status note: ERP-007 configured Ruff and Mypy, and ERP-006 added a `pytest-cov` coverage gate and a Logging convention — see `.pre-commit-config.yaml` and `.github/workflows/ci.yml` for what's enforced. Every item that can reasonably be enforced by tooling now is; anything without an `[enforced: ...]` marker is a principle that's inherently a matter of judgment (e.g. "prefer readability"), not a gap awaiting a future ticket.

## Engineering Principles

Always prefer

- readability
- maintainability
- simplicity
- modularity
- testability

Avoid

- unnecessary abstractions
- premature optimization
- duplicated code
- deeply nested logic
- large functions

Every function should ideally have one responsibility.

## Coding Standards

Use

- Python type hints **[enforced: mypy --strict on `app/`]**
- Pydantic v2
- Google-style docstrings **[enforced: ruff `D` rules, google convention]**

API routes must only validate input and call the service layer — see `docs/architecture.md`'s Architecture Principles for the full rule.

Functions should remain small.

Variable names should be descriptive.

Avoid single-character variable names except for loops.

Never suppress warnings unless absolutely necessary. **[enforced: ruff `PGH003`/`PGH004` ban blanket `# type: ignore` / `# noqa`]**

## Error Handling

Never silently ignore exceptions. **[enforced: ruff `BLE001`]**

Raise meaningful exceptions.

Provide actionable error messages.

Log unexpected failures.

## Logging

Use Python's standard `logging` module — no third-party logging library at this project's current scale.

Every module that logs gets its own logger: `logger = logging.getLogger(__name__)` at module level.

Any `except` block that does not re-raise must call `logger.exception(...)` before handling the failure, so the traceback is captured. See `app/ingestion/jobs.py`'s `run_ingestion_job` for the pattern.

Never log full request/response bodies or secrets. Log identifiers (job IDs, filenames) and error messages.

Never use `print()` in application code — see `CLAUDE.md`.

## Testing

Every business logic module should have unit tests. **[enforced: pytest-cov, `--cov-fail-under=90`]**

Prefer pytest.

Mock external dependencies.

Tests should be deterministic.

Avoid flaky tests.

## Performance

Prefer efficient algorithms.

Avoid unnecessary database queries.

Avoid unnecessary LLM calls.

Avoid repeated embedding generation.

Cache expensive operations whenever appropriate.
