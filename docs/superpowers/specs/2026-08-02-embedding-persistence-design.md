# Embedding Generation & Persistence Design

Date: 2026-08-02

## Context

`app/ingestion/` currently parses a PDF and chunks it into provenance-tagged `Chunk` objects, entirely in memory — nothing is embedded and nothing survives a process restart (`current-state.md`: "No embedding generation, no Postgres/FAISS/BM25 persistence — the ingestion slice stops at chunking, by design"). ADR-003 already decided the target data layer: Postgres for chunk text/metadata, FAISS for vectors keyed by the same ID, Redis as a cache-aside layer for embeddings. `docs/architecture.md` specifies Nomic Embed via Ollama for embeddings and FAISS for the vector store.

This spec covers the next vertical slice: turning chunked-but-ephemeral output into embedded, durably-persisted chunks. Redis caching is explicitly deferred to a follow-up ticket — per ADR-003 it's cache-aside and never load-bearing, so adding it later changes nothing about correctness, only latency.

## Scope

In scope: embedding generation via Ollama (Nomic Embed), a Postgres schema + migrations for documents and chunks, a FAISS index persisted to local disk, and wiring all three into the existing async ingestion job pipeline so a `DONE` job means the chunks are embedded and durably stored.

Out of scope (explicitly deferred): Redis embedding cache, any retrieval/query endpoint (reading persisted chunks/vectors back out), BM25 indexing, authentication, and re-embedding/versioning of already-ingested documents.

## Module Layout

Following the existing feature-oriented structure:

- `app/core/db.py` — new. SQLAlchemy engine + session factory. Lives under `core/` rather than `ingestion/` because the database connection itself is shared infrastructure, not an ingestion concern.
- `app/ingestion/models.py` — new. SQLAlchemy ORM models: `DocumentRecord` (id, filename, created_at) and `ChunkRecord` (mirrors the existing `Chunk` pydantic schema's fields, plus a new autoincrement integer `vector_id` column — FAISS requires int64 IDs, which the existing string `chunk_id` isn't).
- `app/ingestion/repository.py` — new. `save_document_and_chunks(document_id, chunks, ...)`, writing one `DocumentRecord` and its `ChunkRecord`s in a single transaction.
- `app/embedding/` — new feature module, parallel to `app/ingestion/`:
  - `client.py` — wraps the `ollama` Python client's embeddings call: `embed_texts(texts: list[str], settings) -> list[list[float]]`.
  - `index.py` — `FaissIndex` wrapper: load-from-disk-or-create, `add(vector_ids, vectors)`, `save()`. Backed by `faiss.IndexIDMap(faiss.IndexFlatL2(dim))`.
  - `service.py` — `embed_and_persist(document_id, chunks, settings)`: calls `embed_texts`, persists chunks via `repository.save_document_and_chunks` (capturing each row's `vector_id`), adds the vectors to the FAISS index, saves the index.
- `alembic/` — new. Migration environment plus one initial revision creating the `documents` and `chunks` tables.
- `app/ingestion/jobs.py` — `run_ingestion_job` extended to call `embed_and_persist` after `ingest_pdf` (chunking), inside the same try/except that already exists — so a failure at the embedding or persistence stage is still caught, logged via `logger.exception`, and reported as `FAILED`, exactly like a parsing failure is today.

## Data Flow

`POST /ingestion/pdf` → job created → parse → chunk (unchanged, existing `ingest_pdf`) → **new:** `embed_texts` batch-embeds every chunk's text via Ollama (`nomic-embed-text`, 768-dim) → **new:** `save_document_and_chunks` writes the `DocumentRecord` + `ChunkRecord` rows to Postgres in one transaction, yielding each chunk's `vector_id` → **new:** the embedding vectors are added to the FAISS index under those `vector_id`s, and the index is saved to disk → job marked `DONE` with the same `IngestResponse` shape as today. `GET /ingestion/jobs/{id}` polling is unchanged; a `DONE` response now additionally means the data is durably persisted, not just held in the job record.

## Config

New settings, loaded the same pydantic-settings way as `IngestionSettings` today:

- `DATABASE_URL` — Postgres connection string.
- `OLLAMA_HOST` — Ollama server address (default `http://localhost:11434`).
- `EMBEDDING_MODEL` — default `nomic-embed-text`.
- `FAISS_INDEX_PATH` — local file path for the persisted index.

## Infra

`docker-compose.yml` gets a `postgres` service for local dev. CI's test job adds a Postgres service container (GitHub Actions `services:`) and runs `alembic upgrade head` before `pytest`. FAISS needs no service of its own — it's an in-process library backed by a local file, so no infra change beyond ensuring `FAISS_INDEX_PATH`'s directory exists.

## Testing

- `embedding/client.py`, `embedding/service.py`: unit tests mock the Ollama call with fixed-dimension fake vectors — no live Ollama required, consistent with CI having no Ollama instance.
- `embedding/index.py`: unit tests against a temp-directory FAISS file — fully local, no external service.
- `ingestion/repository.py`, `ingestion/models.py`: tests run against a real Postgres (docker-compose locally, GH Actions service container in CI) — consistent with ADR-003 explicitly rejecting SQLite as a stand-in for Postgres.
- `run_ingestion_job`: existing test extended to assert a `DocumentRecord`/`ChunkRecord` land in Postgres and a vector lands in the FAISS index after a `DONE` job, plus a new failure-path test for the embedding/persistence stage (mirroring the existing parse-failure test).
- The 90%-coverage CI gate (ERP-006) applies to all new modules.

## New Dependencies

Requires approval per the dependency policy — none of these are satisfiable via stdlib or an existing dependency:

- `sqlalchemy` — ORM/engine, the standard choice for a production FastAPI + Postgres stack.
- `alembic` — versioned schema migrations, the standard companion to SQLAlchemy.
- `psycopg[binary]` — Postgres driver.
- `ollama` — official Python client for the local Ollama server, already the project's chosen LLM/embedding runtime.
- `faiss-cpu` — already named as the project's vector store in `docs/architecture.md`; `faiss-cpu` is the correct PyPI package for a CPU-only, non-GPU deployment target.
