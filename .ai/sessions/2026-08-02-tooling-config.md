# Session — Ruff/Mypy/Pytest/Pre-commit Tooling

Date: 2026-08-02
Tickets Touched: ERP-007

## Decisions

- **Line length set to 110, not Ruff's default 88**: the existing codebase (`app/`, `tests/`) has prose-heavy comments and docstrings that would generate a large volume of noisy `E501` violations at 88 with no real readability gain; 110 keeps the check meaningful without forcing a rewrite of existing prose.
- **Tests exempted from Ruff's docstring rules (`D`) and from Mypy `--strict`**: test function names are already self-documenting (e.g. `test_upload_pdf_rejects_missing_filename`), so docstrings add no signal; retrofitting type hints across 30+ existing test functions under `--strict` was judged low-value churn relative to the benefit. Both exemptions apply to `tests/` only — `app/` gets the full, unexempted treatment.
- **Mypy scoped to `app/` only, not the whole repo**: a planning-time trial run against `tests/` surfaced ~79 errors, virtually all "missing type annotation" on test functions — the same tradeoff as the docstring exemption above, so `tests/` was excluded from the Mypy target rather than incrementally suppressed.
- **Ruff wired into both `pre-commit` and CI; Mypy into CI only**: Mypy's `--strict` run is slower than Ruff's lint pass, so it's kept out of the local pre-commit hook to avoid slowing down every commit, while still gating merges via CI.
- **Deviation from the original spec, called out explicitly during planning**: the spec didn't specify whether `tests/` should be in scope for Mypy `--strict`; the scoping decision above was made during plan-writing based on the trial-run evidence, mirroring the same tests-are-different judgment already made for Ruff's docstring rules.

## Implementation Summary

- `pyproject.toml`: Ruff added as a dev dependency, configured (`line-length = 110`, `select = ["E", "F", "I", "B", "D", "BLE", "PGH"]`, google docstring convention, `tests/` exempted from `D`); Mypy added as a dev dependency, configured `--strict`, scoped to `app/`.
- `.pre-commit-config.yaml`: extended with a Ruff hook alongside the existing Gitleaks hook.
- `.github/workflows/ci.yml`: extended with `ruff check` and `mypy app` steps; the earlier no-tests-yet tolerance on the `pytest` step was removed now that real coverage exists.
- `app/` and `tests/` brought into full Ruff/Mypy compliance across a series of per-file fixes (import ordering, docstrings, type annotations, bare-except tightening, etc.).
- **Real bug fix surfaced by Mypy strict-mode typing, not by review**: `upload_pdf` accepted a file with no filename (`UploadFile.filename` is `str | None`); fixed to reject that case explicitly rather than passing `None` further down the pipeline.
- `docs/engineering-guidelines.md`: status note and the relevant `[tooling candidate]` markers updated to `[enforced: ...]` now that the tools are live.
- Guardrail verification (Task 11): a deliberate missing-docstring violation was introduced, confirmed caught by both the local `pre-commit` hook and the CI-equivalent `ruff check` command, then reverted.
- Final full-suite verification: `ruff check .`, `mypy app`, `pytest -q`, and `pre-commit run --all-files` all run clean end-to-end.

**Process incident**: during Task 10, an implementer subagent accidentally committed its work to the main repo checkout (branch `develop`) instead of this task's git worktree (`worktree-erp-007-tooling-config`). Caught by the controller while generating the review package (commit unreachable from the worktree branch). Corrected by cherry-picking the commit onto the correct worktree branch, then reverting it off `develop` via a hard reset to the prior commit — done only after explicit user confirmation, since a hard reset is a destructive operation. The subsequent subagent dispatch (Task 12) was given an explicit, upfront working-directory verification instruction in its controller prompt (`git rev-parse --show-toplevel` / `git branch --show-current`, checked both at start and immediately before committing) to prevent a repeat; this instruction lives in the controller's dispatch prompts, not in the plan's task-brief text itself.

## Blockers

None.

## Next Steps

- ERP-006 (review/strengthen engineering guidelines) — now unblocked, since real application code and real tooling both exist to review guidelines against.
- Consider requiring CI status checks in `main`'s branch protection, now that ERP-007 gives CI real signal (ruff, mypy, pytest, gitleaks).
- Embedding generation + Postgres/FAISS/BM25 persistence remains the next vertical slice of application work.
