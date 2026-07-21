# Engineering Guidelines

> Status note: this document currently describes standards in prose only. ERP-006 will review and strengthen these guidelines once the repository has real code and tooling in place. ERP-007 will configure the tooling (ruff, mypy, pytest, pre-commit) that can enforce the items marked **[tooling candidate]** below, rather than relying on documentation alone.

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

- Python type hints **[tooling candidate: mypy]**
- Pydantic v2
- Google-style docstrings **[tooling candidate: ruff docstring rules]**

Functions should remain small.

Variable names should be descriptive.

Avoid single-character variable names except for loops.

Never suppress warnings unless absolutely necessary. **[tooling candidate: ruff/mypy config disallowing blanket suppressions]**

## Error Handling

Never silently ignore exceptions. **[tooling candidate: ruff bare-except rules]**

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
