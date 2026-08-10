# Current State

Living summary of what exists in this repository right now. Update in place as state changes — do not append history here (that belongs in `.ai/sessions/`).

## What Exists

- The `.ai/` AI Engineering Operating System is fully built out:
  - `tickets/` — ticket template + lifecycle (ERP-002), tickets ERP-001–012
  - `adr/` — ADR template + lifecycle (ERP-003), ADR-001 ("Adopt Architecture Decision Records"), ADR-002 ("Branch Strategy & CI Approach"), ADR-003 ("Data Layer & Caching Architecture"), ADR-004 ("Automated Secrets Scanning")
  - `sessions/` — session template + lifecycle (ERP-004), entries: 2026-07-22, 2026-08-01, 2026-08-02 (strengthen-engineering-guidelines), 2026-08-02 (tooling-config), 2026-08-06 (embedding-persistence), 2026-08-08 (retrieval-endpoint)
  - `memory/` — this framework (ERP-005)
- `docs/architecture.md`, `docs/engineering-guidelines.md`, `docs/roadmap.md` describe the intended project, philosophy, stack, and standards. `docs/architecture.md` also has the target-state architecture diagram (`docs/diagrams/architecture.drawio`/`.png`) and an explicit open-source/free-only dependency constraint.
- `CLAUDE.md` is a short operational guide pointing into `docs/` and `.ai/`; also documents the mandatory `uv` workflow, the research-before-recommending rule, the automated secrets-scanning guardrail, and the session-log/current-state maintenance habit.
- GitHub repository setup is done (ERP-008): `main` (protected, default branch) + `develop` branch model, CI (`.github/workflows/ci.yml`: gitleaks scan + `pytest`, verified actually green — not just present), PR template.
- Automated secrets-scanning guardrail is live (ERP-010): Gitleaks via `pre-commit`, local hook + CI backstop, verified to actually block a real secret.
- Ruff (lint) and Mypy (`--strict`, scoped to `app/`) are configured (ERP-007): Ruff runs via `pre-commit` and CI, Mypy via CI only. `app/` and `tests/` are fully compliant. `docs/engineering-guidelines.md` marks the now-enforced items accordingly.
- Pytest coverage gate is enforced (ERP-006): `pytest-cov`, `--cov-fail-under=90` in CI. A logging convention (stdlib `logging`, module-level loggers, `logger.exception` on non-re-raising excepts) is defined in `docs/engineering-guidelines.md` and applied to `app/ingestion/jobs.py`'s previously-silent failure path.
- **First vertical slice of application code exists and is live on `main`**: `app/ingestion/` — a PDF ingestion pipeline (`POST /ingestion/pdf` job-based upload, `GET /ingestion/jobs/{job_id}` polling). PyMuPDF4LLM fast-path parsing with automatic Docling fallback (tables/OCR). Structure-aware Markdown chunking with full provenance metadata (page range, section path, parser used). Full test suite under `tests/ingestion/` (33 tests, ~98.6% coverage), all passing in CI.
- **Embedding generation and durable persistence are now wired into the ingestion job pipeline (ERP-011, on branch `erp-011-embedding-persistence`)**: Postgres persistence via SQLAlchemy 2.0 + Alembic (`app/core/db.py`, `app/ingestion/models.py`, `app/ingestion/repository.py` — document + chunks saved in one transaction), a FAISS index persisted to local disk (`app/embedding/`), and an Ollama-backed embedding client (Nomic Embed). `run_ingestion_job` calls the new `embed_and_persist` orchestration after chunking, inside the same try/except as the existing parse/chunk stage, so a `DONE` job now means chunks are embedded and durably persisted, not just held in memory. `docker-compose.yml` provides a local Postgres service; CI runs the full suite against a real Postgres service container. Merged to `develop` via PR #4 (merge commit `a019e35`).
- **A synchronous semantic retrieval endpoint exists (ERP-012)**: `POST /retrieval/query` (new `app/retrieval/` module: `schemas.py`, `service.py`, `router.py`) embeds the query text, searches the FAISS index (via a new `FaissIndex.search` in `app/embedding/index.py`) for the nearest `top_k` vectors, hydrates the matching chunk rows from Postgres (via a new `get_chunks_by_vector_ids` in `app/ingestion/repository.py`), and returns them ranked by similarity (`score = 1 / (1 + distance)`). Vector-only (semantic) search — no BM25/hybrid retrieval, no reranking, no PageIndex-style traversal, no per-document filtering, no response caching, and no authentication. `top_k` defaults to 5, bounded 1-50; an empty query is rejected with `422`; an empty index returns `200` with `results: []`. Merged to `develop` via PR #5 (merge commit `2792e90`).
- PR #1 (`develop` → `main`) merged 2026-08-01. PR #2 (`develop` → `main`, secrets-scanning guardrail + ruff/mypy tooling) merged 2026-08-02. ERP-006 (pytest coverage gate + logging convention, see item above) merged `develop` → `main` 2026-08-02; `develop` and `main` were in sync with a clean working tree as of that merge. ERP-011 merged to `develop` via PR #4 (merge commit `a019e35`). ERP-012 merged to `develop` via PR #5 (merge commit `2792e90`). Neither ERP-011 nor ERP-012 has been merged to `main` yet.

## What Does Not Exist Yet

- Redis embedding cache — named in ADR-003 alongside Postgres/FAISS, but explicitly deferred out of ERP-011's scope to a follow-up ticket. It's cache-aside and non-load-bearing, so its absence affects latency, not correctness.
- BM25/hybrid retrieval, reranking, and PageIndex-style structure-aware retrieval — all explicitly deferred out of ERP-012's scope as additive follow-ups on top of the vector-only endpoint that now exists (see item above).
- Generation code (no LLM-backed answer synthesis over retrieved chunks yet — retrieval only).
- Branch protection on `main` does not yet require CI status checks to pass (no `required_status_checks` configured) — CI now enforces ruff, mypy, pytest+coverage, gitleaks, and (as of ERP-011) a real Postgres service container, so this could be made required; not yet done.

## Next Planned Work

- Redis embedding cache (deferred from ERP-011) — cache-aside lookups ahead of Ollama calls.
- BM25 retrieval via Postgres full-text search (`tsvector`), fused with today's vector results as a second retriever (deferred from ERP-012).
- Reranking as a post-processing step over retrieval candidates (deferred from ERP-012).
- PageIndex-style structure-aware retrieval using chunks' existing `section_path` metadata (deferred from ERP-012).
- Consider requiring CI status checks in `main`'s branch protection, now that CI gives real signal (ruff, mypy, pytest+coverage, gitleaks, Postgres-backed tests).
