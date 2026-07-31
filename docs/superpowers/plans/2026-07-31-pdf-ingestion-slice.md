# PDF Ingestion Slice (Parse + Chunk) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first vertical slice of application code: a `POST /ingestion/pdf` endpoint that parses an uploaded PDF (fast path via PyMuPDF4LLM, quality/OCR fallback via Docling) into structure-aware chunks with full provenance metadata, returned via a job-polling API (`GET /ingestion/jobs/{job_id}`).

**Architecture:** Feature-oriented module at `app/ingestion/`, following the existing "API routes only validate + call service layer" principle. Six focused files, each independently testable: `config.py` (settings), `schemas.py` (data models), `chunker.py` (pure function: marked-markdown -> chunks), `parsers.py` (PDF -> per-page markdown, with fallback routing), `service.py` (orchestrates parse + chunk into an `IngestResponse`), `jobs.py` (in-memory async job tracking), `router.py` (FastAPI endpoints).

**Tech Stack:** FastAPI, Pydantic v2 / pydantic-settings, PyMuPDF4LLM, Docling, `langchain-text-splitters`, pytest, all managed via `uv`.

## Global Constraints

- Every dependency is added via `uv add` (or `uv add --dev` for test-only deps) — never edit `pyproject.toml`'s dependency lists by hand, never use `pip`.
- No `print()` in application code (structured logging only, per `docs/engineering-guidelines.md`) — this slice has no logging setup yet, so omit logging rather than reaching for `print()`; a follow-up slice adds structured logging.
- Config values (`chunk_size`, `chunk_overlap`, `ocr_text_threshold`) must be settings-driven (env-var overridable via `pydantic-settings`), never hardcoded in logic.
- Chunk schema fields, verbatim from the spec: `chunk_id`, `document_id`, `chunk_index`, `text`, `section_path: list[str]`, `page_start`, `page_end`, `char_count`, `parser_used: Literal["fast", "quality"]`, `source_filename`.
- Whole-document fallback only (never mix parsers within one document) — if any page trips a fallback signal, the entire document is re-parsed with Docling.
- **On library-API uncertainty:** the exact code below is grounded in verified documentation (cited during design), but PyMuPDF4LLM/Docling/`langchain-text-splitters` are fast-moving libraries. If the RED step of a task fails with an error that doesn't match what's described (e.g. a KeyError on a dict key, an unexpected return type), that's a signal the real installed version differs from what's documented — inspect the actual object (e.g. `print(repr(x))` in a scratch script, then remove it) and adapt the code to match reality, not the other way around. Note any such deviation in your task report.

---

### Task 1: Dependencies and settings

**Files:**
- Modify: `pyproject.toml` (via `uv add`, not hand-edited)
- Create: `app/ingestion/__init__.py`
- Create: `app/ingestion/config.py`
- Test: `tests/ingestion/test_config.py`

**Interfaces:**
- Produces: `IngestionSettings` (pydantic-settings model with `chunk_size: int`, `chunk_overlap: int`, `ocr_text_threshold: int`, env prefix `INGESTION_`) and `get_settings() -> IngestionSettings` (cached factory), both from `app.ingestion.config`. Every later task that needs settings imports `get_settings` from here.

- [ ] **Step 1: Add all dependencies for this slice**

```bash
uv add fastapi pydantic-settings python-multipart uvicorn pymupdf4llm docling langchain-text-splitters
uv add --dev httpx pymupdf
```

Rationale for each (all open-source/free, per `docs/architecture.md`'s constraint):
- `fastapi` — the project's chosen backend framework (already named in `docs/architecture.md`), needed for the first time here.
- `pydantic-settings` — env-var-driven config (pulls in pydantic v2, already the chosen validation library).
- `python-multipart` — required by FastAPI/Starlette to parse `multipart/form-data` file uploads; without it, `UploadFile` endpoints raise a runtime error.
- `uvicorn` — ASGI server to actually run the app.
- `pymupdf4llm` — fast-path PDF parser (AGPL, accepted per ADR-level discussion — ties to the platform's fully-open-source status).
- `docling` — quality-path PDF parser + built-in OCR (MIT).
- `langchain-text-splitters` — standalone package (not full `langchain`), structure-aware Markdown chunking (MIT).
- `httpx` (dev) — required by FastAPI's `TestClient`.
- `pymupdf` (dev) — used directly in test fixtures to generate synthetic PDFs (`fitz` module); already a transitive dependency of `pymupdf4llm`, but declared explicitly here since test code imports it directly.

- [ ] **Step 2: Write the failing test for settings**

Create `tests/ingestion/__init__.py` (empty file) and `tests/ingestion/test_config.py`:

```python
import os

from app.ingestion.config import IngestionSettings, get_settings


def test_default_settings():
    settings = IngestionSettings()
    assert settings.chunk_size == 1500
    assert settings.chunk_overlap == 200
    assert settings.ocr_text_threshold == 20


def test_settings_overridable_via_env(monkeypatch):
    monkeypatch.setenv("INGESTION_CHUNK_SIZE", "500")
    settings = IngestionSettings()
    assert settings.chunk_size == 500


def test_get_settings_returns_cached_instance():
    assert get_settings() is get_settings()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/ingestion/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.ingestion.config'` (or similar import error).

- [ ] **Step 4: Write `app/ingestion/__init__.py`**

```python
```

(Empty file — marks `app/ingestion` as a package.)

- [ ] **Step 5: Write `app/ingestion/config.py`**

```python
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class IngestionSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INGESTION_")

    chunk_size: int = 1500
    chunk_overlap: int = 200
    ocr_text_threshold: int = 20


@lru_cache
def get_settings() -> IngestionSettings:
    return IngestionSettings()
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/ingestion/test_config.py -v`
Expected: PASS (3 passed)

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock app/ingestion/__init__.py app/ingestion/config.py tests/ingestion/__init__.py tests/ingestion/test_config.py
git commit -m "feat: add ingestion slice dependencies and settings"
```

---

### Task 2: Chunk and response schemas

**Files:**
- Create: `app/ingestion/schemas.py`
- Test: `tests/ingestion/test_schemas.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `Chunk`, `IngestResponse`, `JobStatus` (enum: `PENDING`, `PROCESSING`, `DONE`, `FAILED`), `JobStatusResponse` — all from `app.ingestion.schemas`. Task 3 uses none of these (pure function, dict-based). Task 5 (`service.py`) builds `Chunk`/`IngestResponse` instances. Task 6 (`jobs.py`) uses `JobStatus`. Task 7 (`router.py`) uses `JobStatusResponse`.

- [ ] **Step 1: Write the failing test**

```python
# tests/ingestion/test_schemas.py
import pytest
from pydantic import ValidationError

from app.ingestion.schemas import Chunk, IngestResponse, JobStatus, JobStatusResponse


def test_chunk_requires_all_fields():
    chunk = Chunk(
        chunk_id="doc1-0",
        document_id="doc1",
        chunk_index=0,
        text="hello world",
        section_path=["Intro"],
        page_start=1,
        page_end=1,
        char_count=11,
        parser_used="fast",
        source_filename="test.pdf",
    )
    assert chunk.parser_used == "fast"


def test_chunk_rejects_invalid_parser_used():
    with pytest.raises(ValidationError):
        Chunk(
            chunk_id="doc1-0",
            document_id="doc1",
            chunk_index=0,
            text="hello",
            section_path=[],
            page_start=1,
            page_end=1,
            char_count=5,
            parser_used="turbo",
            source_filename="test.pdf",
        )


def test_ingest_response_holds_chunks():
    chunk = Chunk(
        chunk_id="doc1-0", document_id="doc1", chunk_index=0, text="hi",
        section_path=[], page_start=1, page_end=1, char_count=2,
        parser_used="quality", source_filename="test.pdf",
    )
    response = IngestResponse(document_id="doc1", chunks=[chunk])
    assert response.chunks[0].chunk_id == "doc1-0"


def test_job_status_response_defaults():
    response = JobStatusResponse(status=JobStatus.PENDING)
    assert response.result is None
    assert response.error is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingestion/test_schemas.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.ingestion.schemas'`

- [ ] **Step 3: Write `app/ingestion/schemas.py`**

```python
from enum import Enum
from typing import Literal

from pydantic import BaseModel


class Chunk(BaseModel):
    chunk_id: str
    document_id: str
    chunk_index: int
    text: str
    section_path: list[str]
    page_start: int
    page_end: int
    char_count: int
    parser_used: Literal["fast", "quality"]
    source_filename: str


class IngestResponse(BaseModel):
    document_id: str
    chunks: list[Chunk]


class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class JobStatusResponse(BaseModel):
    status: JobStatus
    result: IngestResponse | None = None
    error: str | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ingestion/test_schemas.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/schemas.py tests/ingestion/test_schemas.py
git commit -m "feat: add ingestion Chunk, IngestResponse, and job schemas"
```

---

### Task 3: Structure-aware chunker

**Files:**
- Create: `app/ingestion/chunker.py`
- Test: `tests/ingestion/test_chunker.py`

**Interfaces:**
- Consumes: `IngestionSettings` from `app.ingestion.config` (Task 1).
- Produces: `chunk_markdown(pages: list[dict], settings: IngestionSettings) -> list[dict]` from `app.ingestion.chunker`, where each input page dict has `{"text": str, "page_number": int}` and each output dict has `{"text": str, "section_path": list[str], "page_start": int, "page_end": int, "char_count": int}`. Task 5 (`service.py`) calls this directly with the normalized output of `parse_pdf` (Task 4).

This task is pure-function and needs no PDF files — it operates on plain page dicts, so it's testable before the parser (Task 4) exists.

- [ ] **Step 1: Write the failing test**

```python
# tests/ingestion/test_chunker.py
from app.ingestion.chunker import chunk_markdown
from app.ingestion.config import IngestionSettings


def _settings(**overrides):
    return IngestionSettings(**{"chunk_size": 1500, "chunk_overlap": 200, "ocr_text_threshold": 20, **overrides})


def test_splits_on_headers_and_tracks_page_range():
    pages = [
        {"text": "# Title\nIntro text.\n## Section One\nBody of section one.", "page_number": 1},
        {"text": "## Section Two\nBody of section two.", "page_number": 2},
    ]
    chunks = chunk_markdown(pages, _settings())

    assert len(chunks) >= 2
    section_one = next(c for c in chunks if "section one" in c["text"].lower())
    assert section_one["page_start"] == 1
    assert section_one["page_end"] == 1
    assert section_one["section_path"] == ["Title", "Section One"]

    section_two = next(c for c in chunks if "section two" in c["text"].lower())
    assert section_two["page_start"] == 2
    assert section_two["page_end"] == 2


def test_no_page_marker_leaks_into_chunk_text():
    pages = [{"text": "# Title\nSome body text here.", "page_number": 1}]
    chunks = chunk_markdown(pages, _settings())
    for chunk in chunks:
        assert "page:" not in chunk["text"]
        assert "<!--" not in chunk["text"]


def test_large_section_is_split_by_char_limit_with_overlap():
    long_body = "word " * 800  # ~4000 chars, well over a small chunk_size
    pages = [{"text": f"# Title\n{long_body}", "page_number": 1}]
    chunks = chunk_markdown(pages, _settings(chunk_size=500, chunk_overlap=50))

    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk["char_count"] <= 500 + 50  # allow overlap slack
        assert chunk["char_count"] == len(chunk["text"])


def test_empty_page_produces_no_chunks():
    pages = [{"text": "", "page_number": 1}]
    chunks = chunk_markdown(pages, _settings())
    assert chunks == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingestion/test_chunker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.ingestion.chunker'`

- [ ] **Step 3: Write `app/ingestion/chunker.py`**

```python
import re

from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

from app.ingestion.config import IngestionSettings

_PAGE_MARKER_RE = re.compile(r"<!-- page:(\d+) -->\n?")
_HEADERS_TO_SPLIT_ON = [("#", "h1"), ("##", "h2"), ("###", "h3")]


def _mark_pages(pages: list[dict]) -> str:
    return "\n\n".join(f"<!-- page:{page['page_number']} -->\n{page['text']}" for page in pages)


def _page_range(text: str) -> tuple[int, int] | None:
    page_numbers = [int(match) for match in _PAGE_MARKER_RE.findall(text)]
    if not page_numbers:
        return None
    return min(page_numbers), max(page_numbers)


def chunk_markdown(pages: list[dict], settings: IngestionSettings) -> list[dict]:
    marked_document = _mark_pages(pages)

    header_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=_HEADERS_TO_SPLIT_ON)
    header_sections = header_splitter.split_text(marked_document)

    char_splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )

    chunks: list[dict] = []
    for section in header_sections:
        section_path = list(section.metadata.values())
        section_range = _page_range(section.page_content) or (1, 1)

        for piece in char_splitter.split_text(section.page_content):
            clean_text = _PAGE_MARKER_RE.sub("", piece).strip()
            if not clean_text:
                continue

            piece_range = _page_range(piece) or section_range
            chunks.append(
                {
                    "text": clean_text,
                    "section_path": section_path,
                    "page_start": piece_range[0],
                    "page_end": piece_range[1],
                    "char_count": len(clean_text),
                }
            )

    return chunks
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ingestion/test_chunker.py -v`
Expected: PASS (4 passed). If `test_splits_on_headers_and_tracks_page_range` fails because `section.metadata.values()` doesn't preserve header order as `["Title", "Section One"]` (e.g. if `MarkdownHeaderTextSplitter`'s metadata dict key order differs), inspect the actual `section.metadata` dict returned and adjust `section_path` construction to sort by header level (h1, h2, h3) explicitly rather than relying on dict insertion order.

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/chunker.py tests/ingestion/test_chunker.py
git commit -m "feat: add structure-aware markdown chunker"
```

---

### Task 4: PDF parsers (fast path, quality fallback, routing)

**Files:**
- Create: `app/ingestion/parsers.py`
- Create: `tests/ingestion/conftest.py`
- Test: `tests/ingestion/test_parsers.py`

**Interfaces:**
- Consumes: `IngestionSettings` from `app.ingestion.config` (Task 1).
- Produces: `parse_pdf(pdf_path: str, settings: IngestionSettings) -> tuple[list[dict], Literal["fast", "quality"]]` from `app.ingestion.parsers`, where the returned list contains `{"text": str, "page_number": int}` dicts (the exact shape Task 3's `chunk_markdown` consumes). Task 5 (`service.py`) calls this directly. Also produces `needs_fallback(fast_pages: list[dict], ocr_text_threshold: int) -> bool` (unit-tested independently with synthetic data, no real parsing).

- [ ] **Step 1: Write fixture generators (no test framework needed for these — they're fixtures, not tests)**

```python
# tests/ingestion/conftest.py
import fitz
import pytest


@pytest.fixture
def simple_text_pdf(tmp_path):
    """A one-page PDF with a heading and body text — should stay on the fast path."""
    path = tmp_path / "simple.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Introduction", fontsize=18)
    page.insert_text((72, 100), "This is a simple paragraph of body text for testing extraction.", fontsize=11)
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture
def table_pdf(tmp_path):
    """A one-page PDF with a drawn grid (table) — should trigger the quality fallback."""
    path = tmp_path / "table.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Data Table", fontsize=18)
    x0, y0, cell_w, cell_h = 72, 100, 100, 30
    for row in range(2):
        for col in range(2):
            rect = fitz.Rect(x0 + col * cell_w, y0 + row * cell_h, x0 + (col + 1) * cell_w, y0 + (row + 1) * cell_h)
            page.draw_rect(rect)
            page.insert_text((rect.x0 + 5, rect.y0 + 20), f"R{row}C{col}", fontsize=10)
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture
def scanned_pdf(tmp_path):
    """A one-page PDF containing only an image, no extractable text — should trigger the OCR fallback."""
    path = tmp_path / "scanned.pdf"
    doc = fitz.open()
    page = doc.new_page()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 200))
    pix.set_rect(pix.irect, (255, 255, 255))
    page.insert_image(fitz.Rect(0, 0, page.rect.width, page.rect.height), pixmap=pix)
    doc.save(str(path))
    doc.close()
    return str(path)
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/ingestion/test_parsers.py
from app.ingestion.config import IngestionSettings
from app.ingestion.parsers import needs_fallback, parse_pdf


def _settings():
    return IngestionSettings(chunk_size=1500, chunk_overlap=200, ocr_text_threshold=20)


# --- needs_fallback: pure unit tests, no real parsing ---

def test_needs_fallback_true_for_low_text_page():
    fast_pages = [{"text": " ", "tables": [], "metadata": {"page_number": 1}}]
    assert needs_fallback(fast_pages, ocr_text_threshold=20) is True


def test_needs_fallback_true_when_table_detected():
    fast_pages = [
        {"text": "plenty of readable text here to pass the threshold check", "tables": [{"bbox": [0, 0, 1, 1]}], "metadata": {"page_number": 1}}
    ]
    assert needs_fallback(fast_pages, ocr_text_threshold=20) is True


def test_needs_fallback_false_for_normal_text_page():
    fast_pages = [
        {"text": "plenty of readable text here to pass the threshold check", "tables": [], "metadata": {"page_number": 1}}
    ]
    assert needs_fallback(fast_pages, ocr_text_threshold=20) is False


# --- parse_pdf: real parsing against generated fixture PDFs ---

def test_parse_pdf_uses_fast_path_for_simple_text(simple_text_pdf):
    pages, parser_used = parse_pdf(simple_text_pdf, _settings())
    assert parser_used == "fast"
    assert len(pages) == 1
    assert "introduction" in pages[0]["text"].lower()
    assert pages[0]["page_number"] == 1


def test_parse_pdf_falls_back_to_quality_for_table(table_pdf):
    pages, parser_used = parse_pdf(table_pdf, _settings())
    assert parser_used == "quality"
    assert len(pages) >= 1


def test_parse_pdf_falls_back_to_quality_for_scanned_page(scanned_pdf):
    pages, parser_used = parse_pdf(scanned_pdf, _settings())
    assert parser_used == "quality"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/ingestion/test_parsers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.ingestion.parsers'`

- [ ] **Step 4: Write `app/ingestion/parsers.py`**

```python
from typing import Literal

import pymupdf4llm
from docling.document_converter import DocumentConverter

from app.ingestion.config import IngestionSettings

_PAGE_BREAK = "\n\n<!-- docling-page-break -->\n\n"


def parse_fast(pdf_path: str) -> list[dict]:
    """Raw PyMuPDF4LLM page_chunks output — used for both extraction and fallback routing."""
    return pymupdf4llm.to_markdown(pdf_path, page_chunks=True)


def needs_fallback(fast_pages: list[dict], ocr_text_threshold: int) -> bool:
    for page in fast_pages:
        if len(page["text"].strip()) < ocr_text_threshold:
            return True
        if len(page.get("tables", [])) > 0:
            return True
    return False


def parse_quality(pdf_path: str) -> list[dict]:
    """Docling parse (quality path: better tables + OCR). Returns {"text", "page_number"} dicts."""
    converter = DocumentConverter()
    result = converter.convert(pdf_path)
    markdown = result.document.export_to_markdown(page_break_placeholder=_PAGE_BREAK)
    return [
        {"text": text, "page_number": index + 1}
        for index, text in enumerate(markdown.split(_PAGE_BREAK))
    ]


def parse_pdf(pdf_path: str, settings: IngestionSettings) -> tuple[list[dict], Literal["fast", "quality"]]:
    fast_pages = parse_fast(pdf_path)

    if needs_fallback(fast_pages, settings.ocr_text_threshold):
        return parse_quality(pdf_path), "quality"

    normalized = [
        {"text": page["text"], "page_number": page["metadata"]["page_number"]}
        for page in fast_pages
    ]
    return normalized, "fast"
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/ingestion/test_parsers.py -v`
Expected: PASS (6 passed). This task's real-PDF tests invoke Docling on first run, which downloads ~1-2GB of model weights — expect this run to take significantly longer than other tasks' tests (potentially several minutes). This is a known, accepted cost per the design spec.

If `pymupdf4llm.to_markdown(..., page_chunks=True)` returns dicts with different key names than `text`/`tables`/`metadata.page_number` in the installed version, adjust `parse_fast`/`needs_fallback`/`parse_pdf` to match the actual keys (inspect via `print(fast_pages[0].keys())` in a scratch script, then remove the print). Likewise for Docling's `export_to_markdown(page_break_placeholder=...)` — if that parameter doesn't exist in the installed version, check `docling`'s installed version's `DoclingDocument.export_to_markdown` signature and adapt.

- [ ] **Step 6: Commit**

```bash
git add app/ingestion/parsers.py tests/ingestion/conftest.py tests/ingestion/test_parsers.py
git commit -m "feat: add PDF parsers with fast-path/quality-fallback routing"
```

---

### Task 5: Ingestion service orchestration

**Files:**
- Create: `app/ingestion/service.py`
- Test: `tests/ingestion/test_service.py`

**Interfaces:**
- Consumes: `parse_pdf` from `app.ingestion.parsers` (Task 4), `chunk_markdown` from `app.ingestion.chunker` (Task 3), `Chunk`/`IngestResponse` from `app.ingestion.schemas` (Task 2), `IngestionSettings` from `app.ingestion.config` (Task 1).
- Produces: `ingest_pdf(pdf_path: str, source_filename: str, settings: IngestionSettings) -> IngestResponse` from `app.ingestion.service`. Task 6 (`jobs.py`) calls this directly.

- [ ] **Step 1: Write the failing test**

```python
# tests/ingestion/test_service.py
from app.ingestion.config import IngestionSettings
from app.ingestion.service import ingest_pdf


def _settings():
    return IngestionSettings(chunk_size=1500, chunk_overlap=200, ocr_text_threshold=20)


def test_ingest_pdf_returns_chunks_with_full_provenance(simple_text_pdf):
    response = ingest_pdf(simple_text_pdf, "simple.pdf", _settings())

    assert response.document_id
    assert len(response.chunks) >= 1

    first = response.chunks[0]
    assert first.document_id == response.document_id
    assert first.chunk_id == f"{response.document_id}-0"
    assert first.chunk_index == 0
    assert first.source_filename == "simple.pdf"
    assert first.parser_used == "fast"
    assert first.page_start == 1


def test_ingest_pdf_chunk_indices_are_sequential(simple_text_pdf):
    response = ingest_pdf(simple_text_pdf, "simple.pdf", _settings())
    indices = [chunk.chunk_index for chunk in response.chunks]
    assert indices == list(range(len(response.chunks)))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingestion/test_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.ingestion.service'`

- [ ] **Step 3: Write `app/ingestion/service.py`**

```python
import uuid

from app.ingestion.chunker import chunk_markdown
from app.ingestion.config import IngestionSettings
from app.ingestion.parsers import parse_pdf
from app.ingestion.schemas import Chunk, IngestResponse


def ingest_pdf(pdf_path: str, source_filename: str, settings: IngestionSettings) -> IngestResponse:
    document_id = str(uuid.uuid4())
    pages, parser_used = parse_pdf(pdf_path, settings)
    raw_chunks = chunk_markdown(pages, settings)

    chunks = [
        Chunk(
            chunk_id=f"{document_id}-{index}",
            document_id=document_id,
            chunk_index=index,
            text=raw["text"],
            section_path=raw["section_path"],
            page_start=raw["page_start"],
            page_end=raw["page_end"],
            char_count=raw["char_count"],
            parser_used=parser_used,
            source_filename=source_filename,
        )
        for index, raw in enumerate(raw_chunks)
    ]

    return IngestResponse(document_id=document_id, chunks=chunks)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ingestion/test_service.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/service.py tests/ingestion/test_service.py
git commit -m "feat: add ingestion service orchestrating parse and chunk"
```

---

### Task 6: In-memory async job tracking

**Files:**
- Create: `app/ingestion/jobs.py`
- Test: `tests/ingestion/test_jobs.py`

**Interfaces:**
- Consumes: `ingest_pdf` from `app.ingestion.service` (Task 5), `JobStatus` from `app.ingestion.schemas` (Task 2), `IngestionSettings` from `app.ingestion.config` (Task 1).
- Produces: `create_job() -> str` (returns a new job id), `get_job(job_id: str) -> JobRecord | None`, `run_ingestion_job(job_id: str, pdf_path: str, filename: str, settings: IngestionSettings) -> None`, and the `JobRecord` class (`status: JobStatus`, `result: IngestResponse | None`, `error: str | None`) — all from `app.ingestion.jobs`. Task 7 (`router.py`) calls all three functions.

- [ ] **Step 1: Write the failing test**

```python
# tests/ingestion/test_jobs.py
from app.ingestion.config import IngestionSettings
from app.ingestion.jobs import create_job, get_job, run_ingestion_job
from app.ingestion.schemas import JobStatus


def _settings():
    return IngestionSettings(chunk_size=1500, chunk_overlap=200, ocr_text_threshold=20)


def test_create_job_starts_pending():
    job_id = create_job()
    record = get_job(job_id)
    assert record.status == JobStatus.PENDING
    assert record.result is None
    assert record.error is None


def test_get_job_returns_none_for_unknown_id():
    assert get_job("does-not-exist") is None


def test_run_ingestion_job_marks_done_on_success(simple_text_pdf):
    job_id = create_job()
    run_ingestion_job(job_id, simple_text_pdf, "simple.pdf", _settings())

    record = get_job(job_id)
    assert record.status == JobStatus.DONE
    assert record.result is not None
    assert record.error is None


def test_run_ingestion_job_marks_failed_on_bad_path():
    job_id = create_job()
    run_ingestion_job(job_id, "/no/such/file.pdf", "missing.pdf", _settings())

    record = get_job(job_id)
    assert record.status == JobStatus.FAILED
    assert record.result is None
    assert record.error is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingestion/test_jobs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.ingestion.jobs'`

- [ ] **Step 3: Write `app/ingestion/jobs.py`**

```python
import threading
import uuid

from app.ingestion.config import IngestionSettings
from app.ingestion.schemas import IngestResponse, JobStatus
from app.ingestion.service import ingest_pdf

_jobs: dict[str, "JobRecord"] = {}
_lock = threading.Lock()


class JobRecord:
    def __init__(self) -> None:
        self.status: JobStatus = JobStatus.PENDING
        self.result: IngestResponse | None = None
        self.error: str | None = None


def create_job() -> str:
    job_id = str(uuid.uuid4())
    with _lock:
        _jobs[job_id] = JobRecord()
    return job_id


def get_job(job_id: str) -> JobRecord | None:
    with _lock:
        return _jobs.get(job_id)


def run_ingestion_job(job_id: str, pdf_path: str, filename: str, settings: IngestionSettings) -> None:
    with _lock:
        _jobs[job_id].status = JobStatus.PROCESSING

    try:
        result = ingest_pdf(pdf_path, filename, settings)
    except Exception as exc:  # noqa: BLE001 - job failure is reported via status, not raised
        with _lock:
            _jobs[job_id].status = JobStatus.FAILED
            _jobs[job_id].error = str(exc)
        return

    with _lock:
        _jobs[job_id].status = JobStatus.DONE
        _jobs[job_id].result = result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ingestion/test_jobs.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/jobs.py tests/ingestion/test_jobs.py
git commit -m "feat: add in-memory async ingestion job tracking"
```

---

### Task 7: FastAPI router and app wiring

**Files:**
- Create: `app/ingestion/router.py`
- Modify: `app/main.py`
- Test: `tests/ingestion/test_router.py`

**Interfaces:**
- Consumes: `jobs.create_job`, `jobs.get_job` from `app.ingestion.jobs` (Task 6), `get_settings` from `app.ingestion.config` (Task 1), `JobStatusResponse` from `app.ingestion.schemas` (Task 2).
- Produces: `router: APIRouter` from `app.ingestion.router`, mounted into the FastAPI app in `app/main.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/ingestion/test_router.py
import io
import time

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _read_fixture_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _poll_until_done(job_id: str, timeout_seconds: float = 60.0) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = client.get(f"/ingestion/jobs/{job_id}")
        assert response.status_code == 200
        body = response.json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.1)
    raise TimeoutError(f"job {job_id} did not finish within {timeout_seconds}s")


def test_upload_pdf_rejects_non_pdf_content_type():
    response = client.post(
        "/ingestion/pdf",
        files={"file": ("notes.txt", io.BytesIO(b"just text"), "text/plain")},
    )
    assert response.status_code == 400


def test_upload_pdf_rejects_file_without_pdf_header():
    response = client.post(
        "/ingestion/pdf",
        files={"file": ("fake.pdf", io.BytesIO(b"not really a pdf"), "application/pdf")},
    )
    assert response.status_code == 400


def test_upload_and_poll_simple_pdf(simple_text_pdf):
    pdf_bytes = _read_fixture_bytes(simple_text_pdf)
    response = client.post(
        "/ingestion/pdf",
        files={"file": ("simple.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    final = _poll_until_done(job_id)
    assert final["status"] == "done"
    assert final["result"]["chunks"]
    assert final["result"]["chunks"][0]["source_filename"] == "simple.pdf"


def test_get_job_status_404_for_unknown_job():
    response = client.get("/ingestion/jobs/does-not-exist")
    assert response.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingestion/test_router.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.ingestion.router'` (or an import error from `app.main` since the router isn't wired in yet).

- [ ] **Step 3: Write `app/ingestion/router.py`**

```python
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile, status

from app.ingestion import jobs
from app.ingestion.config import get_settings
from app.ingestion.schemas import JobStatusResponse

router = APIRouter(prefix="/ingestion", tags=["ingestion"])

_PDF_MAGIC = b"%PDF-"


@router.post("/pdf", status_code=status.HTTP_202_ACCEPTED)
async def upload_pdf(file: UploadFile = File(...), background_tasks: BackgroundTasks = None) -> dict:
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="File must be a PDF (content-type application/pdf)")

    header = await file.read(5)
    if header != _PDF_MAGIC:
        raise HTTPException(status_code=400, detail="File is not a valid PDF (missing %PDF- header)")
    await file.seek(0)

    tmp_dir = Path(tempfile.mkdtemp())
    tmp_path = tmp_dir / file.filename
    with tmp_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    job_id = jobs.create_job()
    settings = get_settings()
    background_tasks.add_task(jobs.run_ingestion_job, job_id, str(tmp_path), file.filename, settings)

    return {"job_id": job_id}


@router.get("/jobs/{job_id}")
def get_job_status(job_id: str) -> JobStatusResponse:
    record = jobs.get_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatusResponse(status=record.status, result=record.result, error=record.error)
```

- [ ] **Step 4: Wire the router into `app/main.py`**

Read the current contents of `app/main.py` first (it's an empty stub file per the repository's current state). Replace its contents with:

```python
from fastapi import FastAPI

from app.ingestion.router import router as ingestion_router

app = FastAPI(title="Enterprise RAG Platform")
app.include_router(ingestion_router)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/ingestion/test_router.py -v`
Expected: PASS (4 passed). The third test polls a background task and may take a few seconds; it uses the fast path only (simple text PDF), so it should not trigger Docling's slow path.

- [ ] **Step 6: Run the full test suite**

Run: `uv run pytest -v`
Expected: All tests across every task pass. Note the Docling-backed tests in `test_parsers.py` (table/scanned fixtures) will be the slowest in the suite — this is expected, not a regression.

- [ ] **Step 7: Commit**

```bash
git add app/ingestion/router.py app/main.py tests/ingestion/test_router.py
git commit -m "feat: add ingestion PDF upload and job-status endpoints"
```

---

## Manual Verification (for the user, after implementation)

Start the server and confirm the endpoint works end-to-end with a real PDF:

```bash
uv run uvicorn app.main:app --reload
```

Then, in another terminal:

```bash
curl -F "file=@/path/to/some/real.pdf" http://127.0.0.1:8000/ingestion/pdf
# note the job_id from the response, then:
curl http://127.0.0.1:8000/ingestion/jobs/<job_id>
```

Confirm the final response contains chunks with sensible `text`, `section_path`, `page_start`/`page_end`, and the expected `parser_used` for that document.
