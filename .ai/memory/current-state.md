# Current State

Living summary of what exists in this repository right now. Update in place as state changes — do not append history here (that belongs in `.ai/sessions/`).

## What Exists

- The `.ai/` AI Engineering Operating System is fully built out:
  - `tickets/` — ticket template + lifecycle (ERP-002), tickets ERP-001 through ERP-005, ERP-008
  - `adr/` — ADR template + lifecycle (ERP-003), ADR-001 ("Adopt Architecture Decision Records"), ADR-002 ("Branch Strategy & CI Approach")
  - `sessions/` — session template + lifecycle (ERP-004), one entry so far (2026-07-22)
  - `memory/` — this framework (ERP-005)
- `docs/architecture.md`, `docs/engineering-guidelines.md`, `docs/roadmap.md` describe the intended project, philosophy, stack, and standards.
- `CLAUDE.md` is a short operational guide pointing into `docs/` and `.ai/`.
- GitHub repository setup is done (ERP-008): `main` (protected, default branch) + `develop` branch model, minimal CI skeleton (`.github/workflows/ci.yml` running `uv sync` + `pytest`), and a PR template requiring a linked ticket.

## What Does Not Exist Yet

- No application code. No FastAPI app, no ingestion pipeline, no retrieval logic, no tests.
- No tooling configuration (ruff, mypy, pytest, pre-commit) — pending ERP-007.
- Engineering guidelines are prose-only, not yet enforced by tooling — pending ERP-006.
- CI does not yet enforce lint/type-check, and branch protection on `main` does not yet require CI status checks to pass — both depend on ERP-007 landing real tooling.

## Next Planned Work

- ERP-006: review and strengthen `docs/engineering-guidelines.md` once real code exists to review against.
- ERP-007: configure tooling (ruff, mypy, pytest, pre-commit) to enforce guideline items marked `[tooling candidate]`.
- Both are blocked until actual application code is written.
