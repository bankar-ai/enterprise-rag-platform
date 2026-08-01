# PDF Ingestion Slice (Parse + Chunk) Design

Date: 2026-07-31

## Context

This is the first vertical slice of application code in the repository — everything before this (GitHub setup, the `.ai/` operating system, the target-state architecture diagram) was infrastructure and planning. `docs/superpowers/specs/2026-07-26-system-architecture-design.md` scoped the Ingestion Service to PDF-only for now, but left its internals ("parse PDF → chunk text") undesigned. This spec designs that first slice specifically: PDF upload → parsed → chunked, with the resulting chunks returned to the caller. It deliberately stops short of embedding or persistence (Postgres, FAISS, BM25) — those are the next slice, once this one is solid.

## Scope

In scope: PDF parsing, structure-aware chunking, an async job-based API endpoint, chunk metadata preservation.
Out of scope (explicitly deferred): embedding generation, Postgres/FAISS/BM25 persistence, DOCX/PPTX/OCR-as-a-feature (though the parser choice keeps OCR available later — see below), multi-file batch upload, a real task queue (Celery/RQ/Arq).

## Parser Strategy

Two open-source parsers, chosen after benchmarking current options (not from cached assumptions — see `CLAUDE.md`'s Research Before Recommending rule, added during this design):

- **PyMuPDF4LLM** (fast path) — fastest available option (~0.01s/page) for native/simple text PDFs, produces Markdown. License: AGPL (inherited from PyMuPDF). Accepted as a project-wide constraint: since the platform is already fully open-source end-to-end, AGPL's network-copyleft obligation adds no real additional constraint.
- **Docling** (IBM, MIT license) — best table-extraction accuracy among self-hosted open-source options (0.887 vs. PyMuPDF4LLM's 0.401, per benchmark data), with built-in OCR support that directly covers the roadmap's future scanned-document/image-in-PDF needs under the same dependency. Trade-off: ~4s/page (5-30x slower) and ~1-2GB of model weights on disk.

**Routing (`needs_fallback()`):** every document is parsed with PyMuPDF4LLM first. The whole document (not per-page) is re-parsed with Docling instead if either signal trips:
1. Any page's raw extracted-text length falls below `ocr_text_threshold` (config) — indicates a likely scanned/image page that needs OCR.
2. The fast-path Markdown contains a table block — PyMuPDF4LLM's table fidelity is known-poor (benchmarked), so any detected table is treated as a quality signal requiring Docling's re-parse.

Whole-document (not mixed per-page) fallback was chosen to keep the resulting Markdown's structure internally consistent, rather than stitching two parsers' differing header/section conventions together.

## Module Layout

Feature-oriented, under `app/ingestion/`, following `docs/architecture.md`'s "API routes only validate + call service layer" principle:

- `app/ingestion/config.py` — `IngestionSettings` (pydantic-settings): `chunk_size`, `chunk_overlap`, `ocr_text_threshold`, all overridable via env vars, none hardcoded.
- `app/ingestion/schemas.py` — `Chunk`, `IngestResponse`, job-status models (see Chunk Schema and API below).
- `app/ingestion/parsers.py` — `parse_fast()`, `parse_quality()`, `needs_fallback()`, `parse_pdf()` (orchestrates fast-then-fallback).
- `app/ingestion/chunker.py` — `chunk_markdown()`, using `langchain-text-splitters`' `MarkdownHeaderTextSplitter` (primary split, on headers) then `RecursiveCharacterTextSplitter` (secondary split, only within sections exceeding `chunk_size`).
- `app/ingestion/service.py` — `ingest_pdf()`, the actual Ingestion Service: orchestrates parse → chunk.
- `app/ingestion/jobs.py` — in-memory job store and background execution wiring (see Scaling below).
- `app/ingestion/router.py` — `POST /ingestion/pdf`, `GET /ingestion/jobs/{job_id}`.

## Chunk Schema

```python
class Chunk(BaseModel):
    chunk_id: str            # f"{document_id}-{chunk_index}"
    document_id: str         # stable id for the source document, groups its chunks
    chunk_index: int         # position within the document, for ordering
    text: str
    section_path: list[str]  # Markdown header hierarchy, e.g. ["Chapter 2", "2.3 Methodology"]
    page_start: int
    page_end: int             # a chunk can span pages; track the range
    char_count: int
    parser_used: Literal["fast", "quality"]  # debugging trail: which parser produced this chunk
    source_filename: str
```

This is deliberately designed so these exact fields become the Postgres `chunks` table columns in the next slice (embedding + storage) — `section_path`/`page_start`/`page_end` support answer citation later ("according to page 5, section 2.3"), and `parser_used` gives a debugging trail if a retrieved chunk looks malformed (was it the fast path mangling a table, or something else).

## API & Scaling

Docling's ~4s/page cost means a document that trips the quality fallback could take minutes (a 200-page PDF: ~13 minutes) — too long for a blocking HTTP request without risking client/gateway timeouts. The endpoint is therefore async/job-based, not synchronous:

- `POST /ingestion/pdf` — validates the upload is actually a PDF (content-type + magic bytes, not just filename extension), returns `202 Accepted` with a `job_id` immediately, and schedules the real parse+chunk work via FastAPI's built-in `BackgroundTasks` (no new dependency for this slice).
- `GET /ingestion/jobs/{job_id}` — returns job status (`pending`/`processing`/`done`/`failed`) and the `IngestResponse` once complete.

Job state is in-memory for this slice — a process restart loses in-flight job status. This is a real, acknowledged limitation, but it's consistent with the rest of the slice (nothing here is durable yet; persistence arrives with the next slice). The `job_id` + polling API shape is designed to not need to change when a real task queue and a persisted job table arrive later — only the internals swap out.

**Multi-file upload is explicitly out of scope for this slice.** Parsing has zero cross-document coupling, so a future `POST /ingestion/pdf/batch` endpoint would just loop this same per-file logic — trivially additive later, not worth designing in now.

## Error Handling

- Non-PDF upload → `400`, rejected before any parsing is attempted.
- Corrupt/unreadable PDF (both parsers fail) → job status `failed` with a clear reason recorded; no partial/silent output.
- Docling's model weights not downloaded yet on first run (1-2GB) → logged explicitly as a first-run cost, not a silent hang.

## Testing

Per `docs/engineering-guidelines.md` (pytest, mock external dependencies):
- `chunker.py`: unit tests using known Markdown strings (no PDF needed) verifying header-splitting and overlap behavior precisely.
- `needs_fallback()`: unit tests using synthetic inputs (fake low-text-density page, fake table-containing Markdown) verifying the heuristic triggers correctly without invoking Docling.
- Integration tests using a small number of real fixture PDFs in `tests/fixtures/` (one simple text PDF, one table-heavy PDF, one scanned/image-only PDF), exercising the real parsers end-to-end and confirming the router returns a well-formed `IngestResponse` via the job-polling flow.

## Dependencies

All added via `uv add` (per `CLAUDE.md`'s mandatory-`uv` rule), each already justified above:
- `pymupdf4llm` (fast-path parser, AGPL — accepted)
- `docling` (quality-path parser + OCR, MIT)
- `langchain-text-splitters` (standalone package, not full `langchain` — MIT, depends only on lightweight `langchain-core`)
- `pytest` (dev dependency — already added in this session while fixing CI)

## Open Items (not resolved by this design, deferred to later slices)

- Exact numeric thresholds for `ocr_text_threshold` and `chunk_size`/`chunk_overlap` defaults — set to reasonable starting values during implementation, tunable via config without code changes.
- Embedding generation and Postgres/FAISS/BM25 persistence (next slice).
- A real task queue and persisted job table, if/when in-memory job state proves insufficient.
- DOCX/PPTX/OCR-as-a-user-facing-feature (roadmap items; Docling's presence here makes them cheaper to add later but doesn't implement them now).
