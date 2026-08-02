# Session — GitHub Setup, Architecture Diagram, PDF Ingestion Slice, Secrets Guardrail

Date: 2026-08-01
Tickets Touched: ERP-008, ERP-009, ERP-010

## Decisions

- **Branch strategy & CI** (ADR-002): `main` (protected, default) + `develop` (integration) model; CI introduced as a minimal skeleton (`uv sync` + `pytest`) ahead of real lint/type-check tooling, which is ERP-007's job. `gh` CLI installed and authenticated to automate default-branch/branch-protection setup.
- **CI was broken from the start and went unnoticed for days**: every push since ERP-008 failed because `pytest` was referenced in the workflow but never actually added as a project dependency (`uv sync` installed nothing). Fixed by `uv add --dev pytest`; also removed an invalid `setup-uv` input that was silently warning on every run. Lesson: a CI workflow file existing is not the same as it being verified to pass — should have watched the first real run.
- **`.gitignore` was empty since repo init** — a real latent risk (nothing stopped `.venv`/secrets from being swept into a commit). Caught by the user, not by process; populated with standard Python/venv/secrets/editor ignores.
- **Data layer & caching architecture** (ADR-003): PostgreSQL as the single primary datastore (users, chat history, chunk metadata, eval results) paired with FAISS/BM25 via shared chunk IDs; Redis added as a cache-aside layer (embeddings, query results, sessions) — initially deferred, then reversed after the user pushed back citing the engineering guidelines' "avoid unnecessary DB queries" principle.
- **PDF parser selection researched, not assumed** (per the "Research Before Recommending" rule this session added to `CLAUDE.md`): benchmarked PyMuPDF4LLM (fastest, AGPL) against Docling (best table accuracy of self-hosted options, MIT, built-in OCR) against MarkItDown — landed on PyMuPDF4LLM as the fast path with automatic Docling fallback (low-text-density or table-detected pages), accepting AGPL's network-copyleft obligation since the platform is already fully open-source.
- **Chunking**: structure-aware via `langchain-text-splitters` (the standalone package, not full `langchain`) rather than hand-rolled, given the library's maturity on header-hierarchy tracking and recursive fallback splitting.
- **Ingestion API is job-based (202 + poll), not synchronous**: Docling's ~4s/page cost makes a blocking HTTP request risk timeouts on large documents; in-memory job tracking is an acknowledged limitation for this slice (nothing persists yet).
- **Automated secrets scanning** (ADR-004): Gitleaks via `pre-commit`, run locally and in CI — deliberately not using the `gitleaks-action` Marketplace Action, which requires a paid license for org-owned repos; the raw open-source binary via `pre-commit` stays free regardless.
- **This session log itself is overdue**: `.ai/sessions/` and `.ai/memory/current-state.md` (ERP-004/005's own frameworks) went unmaintained through all of the above work — `current-state.md` still said "no application code" after a full feature slice had shipped. Fixed going forward via new `CLAUDE.md` rules (see Next Steps).

## Implementation Summary

**GitHub setup (ERP-008, plus the CI break/fix and `.gitignore` fix that followed):**
- `main` branch pushed, set as GitHub default, branch protection applied (PR required, no force-push/deletion).
- `.github/workflows/ci.yml`, `.github/PULL_REQUEST_TEMPLATE.md` added; later fixed (missing `pytest` dependency) and hardened (`.gitignore`).
- Ticket renumbered `ERP-006` → `ERP-008` mid-flight after discovering `ERP-006`/`ERP-007` were already reserved in `docs/engineering-guidelines.md`.

**System architecture diagram (ERP-009):**
- `docs/superpowers/specs/2026-07-26-system-architecture-design.md` — target-state design (API/service/data layers, PDF-only ingestion scope, error handling, testing strategy).
- `docs/diagrams/architecture.drawio` + exported `.png` — reworked once after the user flagged unreadable line routing (lines cutting through unrelated boxes); fixed with explicit per-edge anchor points and margin-routed long-distance connections.
- `docs/architecture.md` updated with an open-source/free-only dependency constraint and a diagram reference.

**PDF ingestion slice (first vertical application code, no ticket number assigned — tracked via the plan/spec docs directly):**
- `docs/superpowers/specs/2026-07-31-pdf-ingestion-slice-design.md` + 7-task implementation plan, executed via subagent-driven development with per-task spec/quality review.
- `app/ingestion/{config,schemas,chunker,parsers,service,jobs,router}.py`, `app/main.py`, full test suite under `tests/ingestion/`.
- Three tasks needed fix rounds after review found real bugs: chunker page-misattribution then a silent-fallback path (2 rounds); a dead code path in parser table-detection covered only by a synthetic test using a shape that never occurs in real output (1 round).
- **Final whole-branch review caught a Critical bug no individual task review could**: every task's tests used single-line text fixtures, masking that the chunker's page-tracking broke on any multi-paragraph section (i.e., nearly all real PDFs) because `MarkdownHeaderTextSplitter` reformats multi-line sections internally. Fixed via a line-based tracking redesign; also added an upload size limit that wasn't in the original plan. Verified independently against the installed library's actual source and via a live manual end-to-end test (real server, real multi-page PDF).

**Secrets guardrail (ERP-010):**
- `.pre-commit-config.yaml` (Gitleaks hook), CI backstop step, `CLAUDE.md` documentation, verified to actually block a real fake secret (not just "pass" trivially).
- Preceded by a manual full-history PII/secrets audit (clean) before the `develop` → `main` merge.

**Release:**
- PR #1 (`develop` → `main`) opened and merged (merge commit, history preserved) after user review and a green CI run on the PR itself.

## Blockers

None outstanding. ERP-006 (guidelines review) and ERP-007 (ruff/mypy/pytest/pre-commit tooling) remain blocked-turned-partially-unblocked: real application code now exists to review guidelines against, and the `pre-commit` framework + one hook (Gitleaks) already exists for ERP-007 to extend.

## Next Steps

- Embedding generation + Postgres/FAISS/BM25 persistence (the next vertical slice, per the deferred scope in the PDF ingestion design spec).
- ERP-006/ERP-007 are now unblocked and worth picking up given real code exists to configure tooling against.
- Going forward: write a session log at natural checkpoints (end of a feature, before a merge) rather than letting `.ai/sessions/`/`.ai/memory/` drift — formalized as a `CLAUDE.md` rule in this same session.
