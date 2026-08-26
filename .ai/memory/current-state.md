# Current State

Living summary of what exists in this repository right now. Update in place as state changes — do not append history here (that belongs in `.ai/sessions/`).

## What Exists

- The `.ai/` AI Engineering Operating System is fully built out:
  - `tickets/` — ticket template + lifecycle (ERP-002), tickets ERP-001–012
  - `adr/` — ADR template + lifecycle (ERP-003), ADR-001 ("Adopt Architecture Decision Records"), ADR-002 ("Branch Strategy & CI Approach"), ADR-003 ("Data Layer & Caching Architecture"), ADR-004 ("Automated Secrets Scanning")
  - `sessions/` — session template + lifecycle (ERP-004), entries: 2026-07-22, 2026-08-01, 2026-08-02 (strengthen-engineering-guidelines), 2026-08-02 (tooling-config), 2026-08-06 (embedding-persistence), 2026-08-08 (retrieval-endpoint), 2026-08-26 (bm25-hybrid-retrieval)
  - `memory/` — this framework (ERP-005)
- `docs/architecture.md`, `docs/engineering-guidelines.md`, `docs/roadmap.md` describe the intended project, philosophy, stack, and standards. `docs/architecture.md` also has the target-state architecture diagram (`docs/diagrams/architecture.drawio`/`.png`) and an explicit open-source/free-only dependency constraint.
- `CLAUDE.md` is a short operational guide pointing into `docs/` and `.ai/`; also documents the mandatory `uv` workflow, the research-before-recommending rule, the automated secrets-scanning guardrail, and the session-log/current-state maintenance habit.
- GitHub repository setup is done (ERP-008): `main` (protected, default branch) + `develop` branch model, CI (`.github/workflows/ci.yml`: gitleaks scan + `pytest`, verified actually green — not just present), PR template.
- Automated secrets-scanning guardrail is live (ERP-010): Gitleaks via `pre-commit`, local hook + CI backstop, verified to actually block a real secret.
- Ruff (lint) and Mypy (`--strict`, scoped to `app/`) are configured (ERP-007): Ruff runs via `pre-commit` and CI, Mypy via CI only. `app/` and `tests/` are fully compliant. `docs/engineering-guidelines.md` marks the now-enforced items accordingly.
- Pytest coverage gate is enforced (ERP-006): `pytest-cov`, `--cov-fail-under=90` in CI. A logging convention (stdlib `logging`, module-level loggers, `logger.exception` on non-re-raising excepts) is defined in `docs/engineering-guidelines.md` and applied to `app/ingestion/jobs.py`'s previously-silent failure path.
- **First vertical slice of application code exists and is live on `main`**: `app/ingestion/` — a PDF ingestion pipeline (`POST /ingestion/pdf` job-based upload, `GET /ingestion/jobs/{job_id}` polling). PyMuPDF4LLM fast-path parsing with automatic Docling fallback (tables/OCR). Structure-aware Markdown chunking with full provenance metadata (page range, section path, parser used). Full test suite under `tests/ingestion/` (33 tests, ~98.6% coverage), all passing in CI.
- **Embedding generation and durable persistence are wired into the ingestion job pipeline (ERP-011)**: Postgres persistence via SQLAlchemy 2.0 + Alembic (`app/core/db.py`, `app/ingestion/models.py`, `app/ingestion/repository.py` — document + chunks saved in one transaction), a FAISS index persisted to local disk (`app/embedding/`), and an Ollama-backed embedding client (Nomic Embed). `run_ingestion_job` calls the new `embed_and_persist` orchestration after chunking, inside the same try/except as the existing parse/chunk stage, so a `DONE` job now means chunks are embedded and durably persisted, not just held in memory. `docker-compose.yml` provides a local Postgres service; CI runs the full suite against a real Postgres service container. Merged to `develop` via PR #4 (merge commit `a019e35`).
- **A synchronous retrieval endpoint exists (ERP-012)**: `POST /retrieval/query` (new `app/retrieval/` module: `schemas.py`, `service.py`, `router.py`) embeds the query text and searches the FAISS index (via `FaissIndex.search` in `app/embedding/index.py`) for the nearest vectors, hydrating matches from Postgres (via `get_chunks_by_vector_ids` in `app/ingestion/repository.py`). `top_k` defaults to 5, bounded 1-50; an empty query is rejected with `422`; an empty index returns `200` with `results: []`. Vector-only ranking has since been superseded by hybrid ranking — see the ERP-014 bullet below (contract unchanged). Merged to `develop` via PR #5 (merge commit `2792e90`).
- **`POST /retrieval/query` is hybrid (vector + BM25) retrieval (ERP-014)**: `chunks` gained a generated `search_vector` (`tsvector`) column and a GIN index (new Alembic migration `a97a8780506f`), and `app/ingestion/repository.py` gained `search_chunks_by_text` (Postgres full-text search via `plainto_tsquery`/`ts_rank`). `app/retrieval/service.py` fuses BM25 and FAISS vector rank-ordered results via Reciprocal Rank Fusion (RRF, `k=60` — see `.ai/adr/ADR-005.md`) into one ranked list; `RetrievedChunk.score` is now the fused RRF score, not `1/(1+distance)`. Endpoint request/response contract unchanged (additive only); still no per-document filtering, reranking, or PageIndex-style traversal. On branch `erp-014-bm25-hybrid-retrieval`, PR open into `develop`, not yet merged.
- PR #1 (`develop` → `main`) merged 2026-08-01. PR #2 (`develop` → `main`, secrets-scanning guardrail + ruff/mypy tooling) merged 2026-08-02. ERP-006 (pytest coverage gate + logging convention, see item above) merged `develop` → `main` 2026-08-02. ERP-011 merged to `develop` via PR #4 (merge commit `a019e35`). ERP-012 merged to `develop` via PR #5 (merge commit `2792e90`). PR #6 (`develop` → `main`, promoting both ERP-011 and ERP-012) merged 2026-08-26 (merge commit `6471a4f`); `develop` and `main` are in sync with a clean working tree as of that merge. ERP-014's PR into `develop` (from `erp-014-bm25-hybrid-retrieval`) is open, not yet merged.

## What Does Not Exist Yet

- Redis embedding cache — named in ADR-003 alongside Postgres/FAISS, but explicitly deferred out of ERP-011's scope to a follow-up ticket. It's cache-aside and non-load-bearing, so its absence affects latency, not correctness.
- Reranking and PageIndex-style structure-aware retrieval — deferred out of ERP-012's scope; BM25/hybrid retrieval (the first of the three deferred items) now exists as of ERP-014 (see "What Exists" above).
- Generation code (no LLM-backed answer synthesis over retrieved chunks yet — retrieval only).
- Branch protection on `main` does not yet require CI status checks to pass (no `required_status_checks` configured) — CI now enforces ruff, mypy, pytest+coverage, gitleaks, and (as of ERP-011) a real Postgres service container, so this could be made required; not yet done.

## Next Planned Work

- Redis embedding cache (deferred from ERP-011) — cache-aside lookups ahead of Ollama calls.
- Reranking as a post-processing step over the fused retrieval candidates ERP-014 now produces (ERP-015).
- PageIndex-style structure-aware retrieval using chunks' existing `section_path` metadata (deferred from ERP-012).
- Consider requiring CI status checks in `main`'s branch protection, now that CI gives real signal (ruff, mypy, pytest+coverage, gitleaks, Postgres-backed tests).
