# Engineering Guidelines

> Status note: ERP-007 configured Ruff and Mypy to enforce the items below marked **[tooling candidate: ruff ...]** or **[tooling candidate: mypy]** — see `.pre-commit-config.yaml` and `.github/workflows/ci.yml`. Items still marked **[tooling candidate]** without an enforcing tool remain prose-only, pending ERP-006's broader guidelines review.

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

Functions should remain small.

Variable names should be descriptive.

Avoid single-character variable names except for loops.

Never suppress warnings unless absolutely necessary. **[enforced: ruff `PGH003`/`PGH004` ban blanket `# type: ignore` / `# noqa`]**

## Error Handling

Never silently ignore exceptions. **[enforced: ruff `BLE001`]**

Raise meaningful exceptions.

Provide actionable error messages.

Log unexpected failures.

## Testing

Every business logic module should have unit tests. **[tooling candidate: coverage threshold in pytest/CI]**

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
