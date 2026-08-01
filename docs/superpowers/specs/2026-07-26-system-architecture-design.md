# System Architecture Design

Date: 2026-07-26

## Context

The repository had `docs/architecture.md` describing project goals, principles, and a technology stack list, but no diagram showing how components fit together, and no explicit statement that all dependencies must be open-source/free. Before writing the first line of application code, this design fills both gaps: it defines the full target-state architecture (which components exist, how data flows through them, how errors are handled, and how the system is tested), to be rendered as a draw.io diagram and used as the reference shape for incremental feature work.

This is a target-state design — most components described here do not exist yet. Components are built incrementally against this shape; nothing here implies all of it ships at once.

## Constraint

All dependencies must be open-source and free to use. Paid or proprietary APIs/services (hosted LLMs, managed vector DBs, paid embedding/reranking APIs) are out of scope unless explicitly approved as an exception. This is now recorded in `docs/architecture.md`'s Architecture Principles section.

## Components

**API Layer** (FastAPI) — routes only validate requests, call the service layer, and return responses; no business logic lives here (existing principle in `docs/architecture.md`). Includes authentication middleware.

**Service Layer** — one feature-oriented module per capability:
- *Ingestion Service* — scoped to **PDF only** for now: parses PDF documents, chunks text, generates embeddings, writes chunk text+metadata to Postgres and vectors to FAISS/BM25. DOCX, PPTX, and OCR support (already listed in `docs/roadmap.md`) are future work, added as separate parser modules behind the same Ingestion Service interface once PDF ingestion is solid.
- *Retrieval Service* — hybrid retrieval: FAISS dense search + BM25 sparse search, merged via Reciprocal Rank Fusion (RRF), refined by a cross-encoder reranker, extended later with PageIndex-inspired retrieval and multi-document retrieval.
- *Generation Service* — builds prompts from retrieved chunks + conversation history, calls Ollama (Qwen3) for inference, supports streaming responses and conversation memory.
- *Evaluation Service* — runs offline/async evaluation of completed conversations (e.g. via an open-source framework such as Ragas), writes scores to Postgres. Framework choice is not decided here.

**Data Layer**:
- *PostgreSQL* — the single primary datastore: users/auth, chat/conversation history, chunk text + metadata (keyed by the same ID used in FAISS), evaluation results.
- *FAISS* — vector index for dense embeddings.
- *BM25 index* — on-disk sparse keyword index, paired with FAISS via shared chunk IDs.
- *Redis* — cache-aside layer, never load-bearing: embedding cache (skip re-embedding duplicate content by hash), retrieval/query result cache (short TTL), session/auth token cache.

**Core/Infra** — Nomic Embed (embedding generation), Ollama runtime (local LLM inference).

**Cross-cutting** — Observability (structured logging, metrics/tracing), Authentication.

**Deployment** — Docker (app + Postgres + Redis containers), GitHub Actions CI/CD (already built, see ERP-008 and ADR-002).

## Data Flow

**Ingestion** (upload → searchable):
1. Client uploads a document → API Layer validates request + auth → calls Ingestion Service.
2. Ingestion Service parses the file (PDF only, for now) and chunks the text.
3. Each chunk is embedded (Nomic Embed), checking the Redis embedding cache first by content hash.
4. Chunk text + metadata are written to Postgres; chunk vectors to FAISS; the BM25 index is updated on disk — all keyed by the same chunk ID.
5. Observability logs parse time, chunk count, and embedding time; the API returns document ID + status.

**Query** (question → answer):
1. Client sends a message → API Layer validates auth/session → calls Retrieval Service.
2. The query is embedded; FAISS top-K (dense) and BM25 top-K (sparse) run in parallel; results are merged via RRF; a cross-encoder reranker produces the final top-N chunk IDs.
3. Chunk text for those IDs is fetched from Redis first (query-result cache), falling back to Postgres on a miss.
4. Generation Service builds a prompt from retrieved chunks + conversation history (from Postgres), calls Ollama, and streams the response back.
5. The new message + response is written to Postgres conversation history.
6. Observability logs per-stage latency (retrieval, rerank, generation) and token usage.
7. Evaluation Service periodically samples completed conversations offline and scores them; this does not sit in the live request path.

## Error Handling

- Redis is cache-aside, never load-bearing: if it's unavailable, every path falls through to Postgres/FAISS directly — degraded latency, not a broken system.
- Ingestion failures (corrupt file, unsupported format) mark the document with a clear failed status in Postgres rather than leaving partially-indexed chunks/vectors.
- Retrieval degrades gracefully: if FAISS is unavailable, fall back to BM25-only (and vice versa) rather than failing the whole query.
- Generation failures/timeouts (Ollama unresponsive) return a clear error to the client rather than hanging or silently retrying an expensive LLM call.

## Testing Strategy

Per `docs/engineering-guidelines.md`: pytest, mock external dependencies. Service-level unit tests mock or use lightweight test doubles for Ollama/FAISS/Postgres (e.g. an in-memory FAISS index, a test Postgres via Docker). One end-to-end integration test exercises the real ingestion → query → generation path against local Ollama, to catch integration issues unit tests can't.

## Open Decisions (not resolved by this design)

- Whether the Service Layer hand-builds the retrieval/ingestion pipeline or uses an open-source RAG framework (LangChain, LlamaIndex, Haystack). Leaning toward hand-built, given `docs/architecture.md`'s emphasis on demonstrating custom modular architecture, but not decided.
- Which open-source evaluation framework the Evaluation Service uses.
- Scope and design of the first vertical slice (which service/module gets built first).

## Diagram

A draw.io diagram (`docs/diagrams/architecture.drawio`, exported to `docs/diagrams/architecture.png`) renders this component/data-flow design visually, with not-yet-built components visually distinguished from what exists today. Creating that diagram file is implementation work that follows this spec, not part of the spec itself.
