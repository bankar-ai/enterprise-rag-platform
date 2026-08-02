# ERP-007 — Ruff, Mypy, Pytest, Pre-commit Tooling Design

Date: 2026-08-02

## Context

`docs/engineering-guidelines.md` currently describes coding standards in prose only, with several items marked `[tooling candidate]` — a note that they're meant to eventually be enforced by tooling rather than relying on people remembering to follow documentation. ERP-010 (Gitleaks secrets scanning) already established the `pre-commit` + CI pattern for automated guardrails in this repo, and explicitly left its `.pre-commit-config.yaml` open for ERP-007 to extend. Real application code now exists (`app/ingestion/`, 869 lines across `app/` and `tests/`), so there's something concrete to configure and validate tooling against — this ticket was blocked on that until now (per ERP-002's `Depends On` notes and the 2026-08-01 session log).

This spec designs ERP-007: configuring Ruff (linting), Mypy (type checking), confirming Pytest's existing config is sufficient, and wiring all of it into `pre-commit` and CI.

## Scope

In scope: Ruff config + pre-commit hook + CI step, Mypy config + CI step (not pre-commit), confirming Pytest config, fixing existing `app/`/`tests/` code to pass the new checks, updating `docs/engineering-guidelines.md`'s status note and `[tooling candidate]` markers, ticket/session/memory bookkeeping.

Out of scope (explicit follow-ups, not implemented here): branch-protection `required_status_checks` on `main` (a GitHub settings change flagged in `current-state.md`, separate from tooling config itself), pytest coverage thresholds, ERP-006's broader guidelines review/rewrite.

## Ruff Configuration

Added as a dev dependency (`uv add --dev ruff`), configured under `[tool.ruff]` / `[tool.ruff.lint]` in `pyproject.toml`. Rule selection is "balanced" — mapped deliberately to specific lines in `docs/engineering-guidelines.md` rather than turning on everything:

| Rule group | Guideline it enforces |
|---|---|
| `E`, `F` (pycodestyle/pyflakes) | baseline correctness, unused code |
| `I` (isort) | import ordering |
| `B` (bugbear) | common bug patterns, implicit footguns |
| `D`, `convention = "google"` | "Google-style docstrings" |
| `BLE` (blind-except) | "Never silently ignore exceptions" |
| `PGH` (blanket `# type: ignore` / `# noqa` bans) | "Never suppress warnings unless absolutely necessary" |

Runs as a local `pre-commit` hook (fast — no dependency install needed to lint) and as a CI step (`uv run ruff check .`).

## Mypy Configuration

Added as a dev dependency (`uv add --dev mypy`), configured under `[tool.mypy]` with `strict = true` — enforces the "Python type hints" guideline comprehensively (no untyped defs, no implicit `Optional`, etc.). Third-party libraries without type stubs (`docling`, `pymupdf4llm`) get targeted `[[tool.mypy.overrides]]` entries with `ignore_missing_imports = true`, scoped to just those modules rather than a blanket project-wide disable.

**CI-only, not a pre-commit hook.** Mypy needs the full project dependency set installed to resolve imports and check them meaningfully; pre-commit's isolated hook environments make that slow and awkward. Ruff and Gitleaks (both fast, self-contained) stay in pre-commit; Mypy runs as a CI step (`uv run mypy .`) that blocks merges but not local commits.

## Pytest Configuration

No new tool. This ticket confirms the existing `[tool.pytest.ini_options]` (`pythonpath = ["."]`) is sufficient for the current test suite. No coverage threshold is added — `docs/engineering-guidelines.md` flags that as its own future tooling candidate, not explicitly part of ERP-007's stated scope (ruff/mypy/pytest/pre-commit *config*, not new enforcement policy). Called out as a follow-up.

## Pre-commit Config

`.pre-commit-config.yaml` gains a Ruff hook (both `ruff check` and `ruff format --check`, or a single `ruff` hook covering lint — exact hook IDs decided at implementation time from Ruff's official pre-commit repo) alongside the existing Gitleaks hook. Same file, per ERP-010's note.

## CI Changes

`.github/workflows/ci.yml` gains two new steps, after `uv sync` and before/alongside the existing gitleaks/pytest steps:
- `uv run ruff check .`
- `uv run mypy .`

Both are real blocking steps (no `|| true` tolerance) — unlike the original `pytest || [ $? -eq 5 ]` skeleton tolerance for "no tests yet," these checks are landing with passing code from the start.

## Existing Code Fixes

`app/` and `tests/` (869 lines) are brought into compliance as part of this ticket — not deferred — so the gate is meaningful immediately rather than landing already-broken. Expected fix categories: missing/non-Google-style docstrings, missing type hints on some functions, possibly a bare/blind except. Scope is bounded by whatever Ruff and Mypy actually flag; no speculative refactoring beyond what's needed to pass.

## Documentation Updates

`docs/engineering-guidelines.md`: remove the `[tooling candidate]` markers for items now actually enforced (docstrings, type hints, blind-except, blanket-suppression bans), and update the top status note to reflect that ERP-007 has landed. The coverage-threshold candidate stays marked, since it's out of scope here.

## Verification

- `uv run ruff check .` — clean, zero errors
- `uv run mypy .` — clean, zero errors
- `uv run pytest` — existing 31+ tests still passing
- `uv run pre-commit run --all-files` — all hooks (gitleaks + ruff) pass
- A deliberately-introduced violation (e.g. a missing docstring) is confirmed to be caught by both the local hook and CI, then reverted — mirroring how ERP-010 verified Gitleaks with a fake secret.
