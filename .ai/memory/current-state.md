# Current State

Living summary of what exists in this repository right now. Update in place as state changes — do not append history here (that belongs in `.ai/sessions/`).

## What Exists

- The `.ai/` AI Engineering Operating System is fully built out:
  - `tickets/` — ticket template + lifecycle (ERP-002), tickets ERP-001–005, ERP-008, ERP-009, ERP-010
  - `adr/` — ADR template + lifecycle (ERP-003), ADR-001 ("Adopt Architecture Decision Records"), ADR-002 ("Branch Strategy & CI Approach"), ADR-003 ("Data Layer & Caching Architecture"), ADR-004 ("Automated Secrets Scanning")
  - `sessions/` — session template + lifecycle (ERP-004), entries: 2026-07-22, 2026-08-01
  - `memory/` — this framework (ERP-005)
- `docs/architecture.md`, `docs/engineering-guidelines.md`, `docs/roadmap.md` describe the intended project, philosophy, stack, and standards. `docs/architecture.md` also has the target-state architecture diagram (`docs/diagrams/architecture.drawio`/`.png`) and an explicit open-source/free-only dependency constraint.
- `CLAUDE.md` is a short operational guide pointing into `docs/` and `.ai/`; also documents the mandatory `uv` workflow, the research-before-recommending rule, the automated secrets-scanning guardrail, and the session-log/current-state maintenance habit.
- GitHub repository setup is done (ERP-008): `main` (protected, default branch) + `develop` branch model, CI (`.github/workflows/ci.yml`: gitleaks scan + `pytest`, verified actually green — not just present), PR template.
- Automated secrets-scanning guardrail is live (ERP-010): Gitleaks via `pre-commit`, local hook + CI backstop, verified to actually block a real secret.
- **First vertical slice of application code exists and is live on `main`**: `app/ingestion/` — a PDF ingestion pipeline (`POST /ingestion/pdf` job-based upload, `GET /ingestion/jobs/{job_id}` polling). PyMuPDF4LLM fast-path parsing with automatic Docling fallback (tables/OCR). Structure-aware Markdown chunking with full provenance metadata (page range, section path, parser used). Scoped to parse + chunk only — no embedding, no persistence yet. Full test suite under `tests/ingestion/` (31 tests), all passing in CI.
- PR #1 (`develop` → `main`) merged 2026-08-01.

## What Does Not Exist Yet

- No embedding generation, no Postgres/FAISS/BM25 persistence — the ingestion slice stops at chunking, by design (next vertical slice).
- No retrieval or generation code at all yet.
- No lint/type-check tooling (ruff, mypy) configured — pending ERP-007 (Gitleaks is configured; ERP-007 extends the same `.pre-commit-config.yaml`).
- Engineering guidelines are prose-only, not yet enforced by tooling beyond secrets-scanning — pending ERP-006.
- Branch protection on `main` does not yet require CI status checks to pass (no `required_status_checks` configured) — worth revisiting once ERP-007 lands.

## Next Planned Work

- Embedding generation + Postgres/FAISS/BM25 persistence (the next vertical slice).
- ERP-006 (review/strengthen engineering guidelines) and ERP-007 (ruff/mypy/pytest config) — now unblocked, since real application code exists to review/configure against.
