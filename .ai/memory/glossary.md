# Glossary

Living reference for terms used across this repository. Update in place as terms are introduced or redefined.

## Operating System Terms

- **ERP-NNN** — a ticket in `.ai/tickets/`, the unit of work management. Sequentially numbered.
- **ADR-NNN** — an Architecture Decision Record in `.ai/adr/`, capturing a real decision, its alternatives, and its consequences. Written only after a decision is actually made — not backfilled for choices stated without evaluated alternatives.
- **Session** — an immutable, date-named entry in `.ai/sessions/` summarizing what happened in a work session: decisions, implementation, blockers, next steps. Never edited after being written.
- **Memory** (this directory) — living, updated-in-place project context: current state, glossary, known issues, decisions in progress. Distinct from sessions, which are an append-only historical log.
- **`docs/`** — durable reference documentation (architecture, engineering guidelines, roadmap). Contrast with `.ai/`, the operating system that manages work against that reference.

## Domain Terms (RAG Platform)

- **RAG (Retrieval-Augmented Generation)** — architecture pattern where an LLM's response is grounded by retrieving relevant context from a document store before generation.
- **Hybrid Retrieval** — combining multiple retrieval strategies (e.g. dense semantic search + sparse keyword search) to improve recall/precision over either alone.
- **PageIndex Retrieval** — a PageIndex-inspired retrieval approach referenced in `docs/architecture.md`; not yet implemented.
- **BM25** — a sparse, term-frequency-based ranking function used for keyword retrieval, planned per `docs/roadmap.md`.
- **Reranking** — a second-pass scoring step that re-orders initially retrieved candidates using a more expensive/accurate model.
- **FAISS** — Facebook AI Similarity Search, the vector database used for dense retrieval (see `docs/architecture.md`).
- **Ollama** — local LLM inference runtime used for development, primary model Qwen3 (see `docs/architecture.md`).
