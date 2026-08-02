# ERP-007 — Ruff/Mypy/Pytest/Pre-commit Tooling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Configure Ruff and Mypy, wire them into `pre-commit` and CI, and bring `app/`/`tests/` into compliance, so `docs/engineering-guidelines.md`'s `[tooling candidate]` items are enforced by tooling instead of prose.

**Architecture:** Ruff (lint) runs locally via `pre-commit` and in CI. Mypy (`--strict`, scoped to `app/` only) runs in CI only. Both are configured in `pyproject.toml`. Existing code is fixed in place, file by file, verified against the exact diagnostics gathered below (not against guesses).

**Tech Stack:** Ruff (linter), Mypy (type checker) — both added as dev dependencies via `uv add --dev`. No other new dependencies.

## Global Constraints

- Always use `uv add` / `uv sync` / `uv run` — never `pip`. (`CLAUDE.md`)
- Never use `print()` in application code. (`CLAUDE.md`) — not touched by this plan, but any new code added while fixing files must not introduce one.
- Ruff line-length: 110 (chosen during planning — default 88 produced 60 noisy `E501` hits against this codebase's existing prose-heavy comments; 110 eliminates nearly all of them without loosening anything else).
- Ruff docstring convention: `google` (matches `docs/engineering-guidelines.md`'s "Google-style docstrings" line).
- Ruff selected rule groups: `E`, `F`, `I`, `B`, `D`, `BLE`, `PGH` — no `preview = true` needed (verified: same 39-error result with and without it).
- Docstring rules (`D100`, `D101`, `D103`, `D104`) are relaxed for `tests/**` via `per-file-ignores` — test function names are already self-documenting; this was verified against the repo (relaxing it drops the diagnostic count from 83 to 39, all in `app/`/genuinely-shared test helpers).
- Mypy `--strict` is scoped to `app/` only, not `tests/`. Verified: `mypy --strict tests` produces 79 errors, nearly all "function/fixture is missing a type annotation" on individual test functions — low-value churn that mirrors the same tests-are-different judgment already applied to Ruff's docstring rules. `docs/engineering-guidelines.md`'s type-hint guideline is about production code; `pyproject.toml`'s `[tool.mypy]` targets `app/` and CI invokes `uv run mypy app`.
- No blanket `# type: ignore` / `# noqa` — every suppression needs a specific rule code (already enforced by `PGH003`/`PGH004`, which is part of the selected rule set).

---

## Task 1: Add Ruff + Mypy, configure `pyproject.toml`

**Files:**
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: a `[tool.ruff]` / `[tool.ruff.lint]` config and a `[tool.mypy]` config that every later task's fixes must satisfy.

- [ ] **Step 1: Add the dev dependencies**

```bash
uv add --dev ruff mypy
```

- [ ] **Step 2: Add Ruff and Mypy config blocks to `pyproject.toml`**

Add these sections after the existing `[tool.pytest.ini_options]` block:

```toml
[tool.ruff]
line-length = 110
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "D", "BLE", "PGH"]

[tool.ruff.lint.pydocstyle]
convention = "google"

[tool.ruff.lint.flake8-bugbear]
extend-immutable-calls = ["fastapi.File", "fastapi.Depends", "fastapi.Query"]

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["D100", "D101", "D103", "D104"]

[tool.mypy]
python_version = "3.12"
strict = true

[[tool.mypy.overrides]]
module = "pymupdf4llm"
ignore_missing_imports = true
```

- [ ] **Step 3: Confirm the baseline diagnostic counts match what this plan expects**

Run:
```bash
uv run ruff check .
```
Expected: `Found 39 errors.` (the exact set fixed in Tasks 2–7 below).

Run:
```bash
uv run mypy app
```
Expected: `Found 14 errors in 3 files` (fixed in Tasks 3, 4, and 6 below).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add ruff and mypy dev dependencies and config"
```

---

## Task 2: Fix docstring-only files

**Files:**
- Modify: `app/__init__.py`, `app/ingestion/__init__.py`, `app/main.py`, `app/ingestion/config.py`, `app/ingestion/schemas.py`, `app/ingestion/service.py`

**Interfaces:**
- Consumes: Ruff config from Task 1.
- Produces: no behavior change — docstrings only.

- [ ] **Step 1: `app/__init__.py`** (currently empty — `D104 Missing docstring in public package`)

```python
"""Enterprise RAG Platform application package."""
```

- [ ] **Step 2: `app/ingestion/__init__.py`** (currently empty — `D104`)

```python
"""PDF ingestion: parsing and structure-aware chunking."""
```

- [ ] **Step 3: `app/main.py`** (`D100 Missing docstring in public module`)

Add a module docstring as the first line:

```python
"""FastAPI application entrypoint."""

from fastapi import FastAPI

from app.ingestion.router import router as ingestion_router

app = FastAPI(title="Enterprise RAG Platform")
app.include_router(ingestion_router)
```

- [ ] **Step 4: `app/ingestion/config.py`** (`D100` module, `D101` class, `D103` function)

```python
"""Ingestion settings, loaded from environment variables."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class IngestionSettings(BaseSettings):
    """Configuration for the PDF ingestion pipeline, overridable via `INGESTION_*` env vars."""

    model_config = SettingsConfigDict(env_prefix="INGESTION_")

    chunk_size: int = 1500
    chunk_overlap: int = 200
    ocr_text_threshold: int = 20
    max_upload_size_bytes: int = 50_000_000


@lru_cache
def get_settings() -> IngestionSettings:
    """Return the process-wide cached `IngestionSettings` instance."""
    return IngestionSettings()
```

- [ ] **Step 5: `app/ingestion/schemas.py`** (`D100` module, `D101` x4 classes)

```python
"""Pydantic schemas for ingestion API requests, responses, and job status."""

from enum import Enum
from typing import Literal

from pydantic import BaseModel


class Chunk(BaseModel):
    """A single chunk of parsed document text, with full provenance metadata."""

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
    """The completed result of ingesting one document: its ID and resulting chunks."""

    document_id: str
    chunks: list[Chunk]


class JobStatus(str, Enum):
    """Lifecycle status of an async ingestion job."""

    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class JobStatusResponse(BaseModel):
    """Polled status of an ingestion job, with its result or error once finished."""

    status: JobStatus
    result: IngestResponse | None = None
    error: str | None = None
```

- [ ] **Step 6: `app/ingestion/service.py`** (`D100` module, `D103` function)

```python
"""The ingestion service: orchestrates parsing and chunking a PDF into `Chunk`s."""

import uuid

from app.ingestion.chunker import chunk_markdown
from app.ingestion.config import IngestionSettings
from app.ingestion.parsers import parse_pdf
from app.ingestion.schemas import Chunk, IngestResponse


def ingest_pdf(pdf_path: str, source_filename: str, settings: IngestionSettings) -> IngestResponse:
    """Parse and chunk a PDF at `pdf_path`, returning provenance-tagged chunks."""
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

- [ ] **Step 7: Verify**

```bash
uv run ruff check app/__init__.py app/ingestion/__init__.py app/main.py app/ingestion/config.py app/ingestion/schemas.py app/ingestion/service.py
```
Expected: `All checks passed!`

```bash
uv run pytest -q
```
Expected: `31 passed`

- [ ] **Step 8: Commit**

```bash
git add app/__init__.py app/ingestion/__init__.py app/main.py app/ingestion/config.py app/ingestion/schemas.py app/ingestion/service.py
git commit -m "docs: add missing module/class docstrings for ruff D-rule compliance"
```

---

## Task 3: Fix `app/ingestion/parsers.py` (Ruff + Mypy)

**Files:**
- Modify: `app/ingestion/parsers.py`

**Interfaces:**
- Consumes: `IngestionSettings` from `app/ingestion/config.py` (unchanged).
- Produces: `parse_fast`, `needs_fallback`, `parse_quality`, `parse_pdf` — same names/behavior, now with `dict[str, Any]` instead of bare `dict`, and mypy-clean.

**Diagnostics being fixed:** `D100` (module docstring), `D103` x2 (functions), 4x mypy `Missing type parameters for generic type "dict"`, 1x mypy `Returning Any from function declared to return "list[dict[Any, Any]]"`, 1x mypy `import-untyped` for `pymupdf4llm` (fixed by the override added in Task 1).

- [ ] **Step 1: Rewrite the file**

```python
"""PDF parsing: a fast native-text path with a slower quality (tables/OCR) fallback."""

from typing import Any, Literal, cast

import pymupdf4llm
from docling.document_converter import DocumentConverter

from app.ingestion.config import IngestionSettings

_PAGE_BREAK = "\n\n<!-- docling-page-break -->\n\n"


def parse_fast(pdf_path: str) -> list[dict[str, Any]]:
    """Raw PyMuPDF4LLM page_chunks output — used for both extraction and fallback routing."""
    return cast(list[dict[str, Any]], pymupdf4llm.to_markdown(pdf_path, page_chunks=True))


def needs_fallback(fast_pages: list[dict[str, Any]], ocr_text_threshold: int) -> bool:
    """Decide whether a document needs the quality (Docling) parse instead of the fast path."""
    for page in fast_pages:
        if len(page["text"].strip()) < ocr_text_threshold:
            return True
        # pymupdf4llm defaults to its layout-analysis engine (no config in this
        # codebase switches it to legacy/non-layout mode), whose page_chunks
        # output represents detected regions as a "page_boxes" list of dicts
        # with a "class" field (e.g. "table", "section-header") rather than a
        # dedicated "tables" key. Table presence is therefore detected here.
        if any(box.get("class") == "table" for box in (page.get("page_boxes") or [])):
            return True
    return False


def parse_quality(pdf_path: str) -> list[dict[str, Any]]:
    """Docling parse (quality path: better tables + OCR). Returns {"text", "page_number"} dicts."""
    converter = DocumentConverter()
    result = converter.convert(pdf_path)
    markdown = result.document.export_to_markdown(page_break_placeholder=_PAGE_BREAK)
    return [
        {"text": text, "page_number": index + 1}
        for index, text in enumerate(markdown.split(_PAGE_BREAK))
    ]


def parse_pdf(
    pdf_path: str, settings: IngestionSettings
) -> tuple[list[dict[str, Any]], Literal["fast", "quality"]]:
    """Parse a PDF, using the fast path unless `needs_fallback` routes to the quality path."""
    fast_pages = parse_fast(pdf_path)

    if needs_fallback(fast_pages, settings.ocr_text_threshold):
        return parse_quality(pdf_path), "quality"

    normalized = [
        {"text": page["text"], "page_number": page["metadata"]["page_number"]}
        for page in fast_pages
    ]
    return normalized, "fast"
```

- [ ] **Step 2: Verify**

```bash
uv run ruff check app/ingestion/parsers.py
```
Expected: `All checks passed!`

```bash
uv run mypy app/ingestion/parsers.py
```
Expected: `Success: no issues found in 1 source file`

```bash
uv run pytest tests/ingestion/test_parsers.py -q
```
Expected: `6 passed`

- [ ] **Step 3: Commit**

```bash
git add app/ingestion/parsers.py
git commit -m "refactor: type-annotate parsers.py for mypy strict compliance"
```

---

## Task 4: Fix `app/ingestion/chunker.py` (Ruff + Mypy)

**Files:**
- Modify: `app/ingestion/chunker.py`

**Interfaces:**
- Consumes: `IngestionSettings` (unchanged).
- Produces: `chunk_markdown(pages: list[dict[str, Any]], settings: IngestionSettings) -> list[dict[str, Any]]` — same name, same behavior, now typed. Internal helpers (`_build_document`, `_normalize_line`, `_build_line_index`, `_locate_line`, `_section_page_range`) keep their names and logic; only their `dict` annotations and docstring formatting change.

**Diagnostics being fixed:** `D100` (module docstring), `D103` (`chunk_markdown`), 4x `D205` (blank line required after docstring summary), `D301` (needs `r"""` — docstring contains `\n`), `E501` (line 105, over 110 chars), 4x mypy `Missing type parameters for generic type "dict"`.

- [ ] **Step 1: Rewrite the file**

```python
"""Structure-aware Markdown chunking: split on headers, then by character limit."""

from typing import Any

from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

from app.ingestion.config import IngestionSettings

_HEADERS_TO_SPLIT_ON = [("#", "h1"), ("##", "h2"), ("###", "h3")]
# Sort section_path by header level explicitly (h1, h2, h3) rather than relying on dict
# insertion order, which is an implementation detail of MarkdownHeaderTextSplitter, not a
# documented contract.
_HEADER_LEVEL_ORDER = {key: index for index, (_, key) in enumerate(_HEADERS_TO_SPLIT_ON)}


def _build_document(pages: list[dict[str, Any]]) -> str:
    """Concatenate page texts (no inline markers) into one document for the header splitter."""
    return "\n\n".join(page["text"] for page in pages)


def _normalize_line(line: str) -> str:
    """Apply the exact transform MarkdownHeaderTextSplitter applies to each line before.

    Uses it as page_content: `.strip()`, then drop non-printable characters. Applying the
    same transform to our reference line index lets us match lines by equality instead of
    by verbatim substring search.
    """
    return "".join(filter(str.isprintable, line.strip()))


def _build_line_index(pages: list[dict[str, Any]]) -> list[tuple[str, int]]:
    r"""Build an ordered, flat list of (normalized_line_text, page_number) for every line.

    Covers every non-blank line of every page, in document order. This is what the
    reference MarkdownHeaderTextSplitter's reconstructed section.page_content lines are
    matched against, since page_content is no longer a verbatim substring of the original
    document once a section spans more than one line (aggregate_lines_to_chunks joins
    lines with "  \n" and split_text strips every line).
    """
    line_index: list[tuple[str, int]] = []
    for page in pages:
        for raw_line in page["text"].split("\n"):
            normalized = _normalize_line(raw_line)
            if normalized:
                line_index.append((normalized, page["page_number"]))
    return line_index


def _locate_line(line: str, line_index: list[tuple[str, int]], search_from: int) -> int:
    """Find `line` in `line_index` at or after `search_from`, by equality.

    Not substring search, since `line_index` entries are already normalized whole lines.

    Fails loudly rather than silently defaulting a page number: MarkdownHeaderTextSplitter
    never merges, reorders, or invents lines, so every normalized line coming out of a
    section should exist in `line_index` at or after the cursor. If it doesn't, page
    tracking can no longer be trusted, and guessing (e.g. falling back to page 1) would
    reintroduce the exact silent-misattribution bug class earlier review rounds eliminated.
    """
    for index in range(search_from, len(line_index)):
        if line_index[index][0] == line:
            return index
    raise ValueError(
        "chunker: could not locate expected line in the page line index at or after the "
        "expected position; the header splitter may have produced a line that doesn't "
        "trace back to any input page, so page tracking can no longer be trusted"
    )


def _section_page_range(
    section_content: str, line_index: list[tuple[str, int]], cursor: int
) -> tuple[tuple[int, int], int]:
    """Determine the (page_start, page_end) of a header section.

    Matches its constituent lines (split back out of the reconstructed page_content, then
    normalized the same way as `line_index`) against the reference line index with a
    forward-advancing cursor. Returns the page range plus the cursor position to resume
    from for the next section, preserving document order while tolerating duplicate lines
    elsewhere in the document.
    """
    section_lines = [_normalize_line(line) for line in section_content.split("\n")]
    section_lines = [line for line in section_lines if line]
    if not section_lines:
        raise ValueError("chunker: header section has no content lines to locate")

    pages_found: list[int] = []
    for line in section_lines:
        index = _locate_line(line, line_index, cursor)
        pages_found.append(line_index[index][1])
        cursor = index + 1

    return (min(pages_found), max(pages_found)), cursor


def chunk_markdown(pages: list[dict[str, Any]], settings: IngestionSettings) -> list[dict[str, Any]]:
    """Split `pages` into structure-aware chunks: by header first, then by character limit."""
    full_text = _build_document(pages)
    if not full_text.strip():
        return []

    line_index = _build_line_index(pages)

    header_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=_HEADERS_TO_SPLIT_ON)
    header_sections = header_splitter.split_text(full_text)

    char_splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )

    chunks: list[dict[str, Any]] = []
    line_cursor = 0
    for section in header_sections:
        section_path = [
            section.metadata[key]
            for key in sorted(section.metadata, key=lambda key: _HEADER_LEVEL_ORDER[key])
        ]

        page_range, line_cursor = _section_page_range(section.page_content, line_index, line_cursor)

        for piece in char_splitter.split_text(section.page_content):
            # MarkdownHeaderTextSplitter joins separate paragraphs within a section with
            # "  \n" (aggregate_lines_to_chunks) rather than the original document's blank
            # line; normalize that internal joiner back to a plain newline so it doesn't
            # leak into chunk text as a splitter implementation artifact.
            clean_text = piece.replace("  \n", "\n").strip()
            if not clean_text:
                continue

            # Character-level pieces within a section can fragment mid-line, so they can't
            # be cleanly mapped back to individual lines/pages the way whole sections can.
            # Falling back to the section-level page range for every piece within it is an
            # already-accepted tradeoff from an earlier review round.
            chunks.append(
                {
                    "text": clean_text,
                    "section_path": section_path,
                    "page_start": page_range[0],
                    "page_end": page_range[1],
                    "char_count": len(clean_text),
                }
            )

    return chunks
```

- [ ] **Step 2: Verify**

```bash
uv run ruff check app/ingestion/chunker.py
```
Expected: `All checks passed!`

```bash
uv run mypy app/ingestion/chunker.py
```
Expected: `Success: no issues found in 1 source file`

```bash
uv run pytest tests/ingestion/test_chunker.py -q
```
Expected: `6 passed`

- [ ] **Step 3: Commit**

```bash
git add app/ingestion/chunker.py
git commit -m "refactor: type-annotate and reformat chunker.py docstrings for tooling compliance"
```

---

## Task 5: Fix `app/ingestion/jobs.py` (Ruff)

**Files:**
- Modify: `app/ingestion/jobs.py`

**Interfaces:**
- Consumes: `IngestionSettings`, `IngestResponse`, `JobStatus` (unchanged).
- Produces: `JobRecord`, `create_job() -> str`, `get_job(job_id: str) -> JobRecord | None`, `run_ingestion_job(...) -> None` — unchanged signatures.

**Diagnostics being fixed:** `D100` (module), `D101` (`JobRecord` class), `D107` (`__init__`), `D103` x3 (functions). No mypy errors here — already correctly typed.

- [ ] **Step 1: Rewrite the file**

```python
"""In-memory async ingestion job tracking (no persistent queue; single-process only)."""

import threading
import uuid

from app.ingestion.config import IngestionSettings
from app.ingestion.schemas import IngestResponse, JobStatus
from app.ingestion.service import ingest_pdf

_jobs: dict[str, "JobRecord"] = {}
_lock = threading.Lock()


class JobRecord:
    """Mutable state for one tracked ingestion job."""

    def __init__(self) -> None:
        """Initialize a new job in PENDING status with no result or error yet."""
        self.status: JobStatus = JobStatus.PENDING
        self.result: IngestResponse | None = None
        self.error: str | None = None


def create_job() -> str:
    """Register a new PENDING job and return its ID."""
    job_id = str(uuid.uuid4())
    with _lock:
        _jobs[job_id] = JobRecord()
    return job_id


def get_job(job_id: str) -> JobRecord | None:
    """Look up a job by ID, or None if it doesn't exist."""
    with _lock:
        return _jobs.get(job_id)


def run_ingestion_job(job_id: str, pdf_path: str, filename: str, settings: IngestionSettings) -> None:
    """Run ingestion for `job_id`, recording DONE + result or FAILED + error on the job record."""
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

- [ ] **Step 2: Verify**

```bash
uv run ruff check app/ingestion/jobs.py
```
Expected: `All checks passed!`

```bash
uv run mypy app/ingestion/jobs.py
```
Expected: `Success: no issues found in 1 source file`

```bash
uv run pytest tests/ingestion/test_jobs.py -q
```
Expected: `4 passed`

- [ ] **Step 3: Commit**

```bash
git add app/ingestion/jobs.py
git commit -m "docs: add missing docstrings to jobs.py for ruff D-rule compliance"
```

---

## Task 6: Fix `app/ingestion/router.py` (Ruff + Mypy + a real bug)

**Files:**
- Modify: `app/ingestion/router.py`

**Interfaces:**
- Consumes: `jobs.create_job`, `jobs.get_job`, `jobs.run_ingestion_job`, `get_settings` (unchanged).
- Produces: `router` (APIRouter, unchanged prefix/tags), `upload_pdf`, `get_job_status` — same routes; `upload_pdf`'s return type narrows from `dict` to `dict[str, str]`.

**Diagnostics being fixed:** `D100` (module), `D103` x2 (functions), `B008` (fixed via the `extend-immutable-calls` config from Task 1 — no code change needed for `File(...)`), and 3 real mypy errors:
1. `background_tasks: BackgroundTasks = None` — the `None` default is invalid (`BackgroundTasks` isn't `Optional`) *and* unnecessary: FastAPI injects `BackgroundTasks` automatically without needing a default.
2. `file.filename` is typed `str | None` by Starlette (a client could send a part with no filename) but was used directly as a `Path` join and as `add_task`'s `str` argument with no None-check — a genuine unhandled-edge-case bug, not just a type-checker false positive.

- [ ] **Step 1: Rewrite the file**

```python
"""Ingestion API: PDF upload (async job) and job-status polling."""

import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile, status

from app.ingestion import jobs
from app.ingestion.config import get_settings
from app.ingestion.schemas import JobStatusResponse

router = APIRouter(prefix="/ingestion", tags=["ingestion"])

_PDF_MAGIC = b"%PDF-"
_COPY_CHUNK_SIZE = 1024 * 1024


@router.post("/pdf", status_code=status.HTTP_202_ACCEPTED)
async def upload_pdf(
    background_tasks: BackgroundTasks, file: UploadFile = File(...)
) -> dict[str, str]:
    """Validate and stream an uploaded PDF to disk, then schedule an async ingestion job."""
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="File must be a PDF (content-type application/pdf)")
    if file.filename is None:
        raise HTTPException(status_code=400, detail="Uploaded file must have a filename")

    header = await file.read(5)
    if header != _PDF_MAGIC:
        raise HTTPException(status_code=400, detail="File is not a valid PDF (missing %PDF- header)")
    await file.seek(0)

    settings = get_settings()
    max_size = settings.max_upload_size_bytes

    tmp_dir = Path(tempfile.mkdtemp())
    tmp_path = tmp_dir / file.filename
    total_bytes = 0
    try:
        with tmp_path.open("wb") as f:
            while chunk := await file.read(_COPY_CHUNK_SIZE):
                total_bytes += len(chunk)
                if total_bytes > max_size:
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail=f"File exceeds maximum upload size of {max_size} bytes",
                    )
                f.write(chunk)
    except HTTPException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    job_id = jobs.create_job()
    background_tasks.add_task(jobs.run_ingestion_job, job_id, str(tmp_path), file.filename, settings)

    return {"job_id": job_id}


@router.get("/jobs/{job_id}")
def get_job_status(job_id: str) -> JobStatusResponse:
    """Return the current status (and result or error, once finished) of an ingestion job."""
    record = jobs.get_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatusResponse(status=record.status, result=record.result, error=record.error)
```

Note: `background_tasks` moved before `file` in the signature because a parameter without a default (`background_tasks`) can't follow one with a default (`file: UploadFile = File(...)`) in Python. FastAPI resolves both by type/dependency, not by position, so this reordering doesn't change routing behavior.

- [ ] **Step 2: Add a regression test for the new filename validation**

Add to `tests/ingestion/test_router.py`, after `test_upload_pdf_rejects_file_without_pdf_header`:

```python
def test_upload_pdf_rejects_missing_filename():
    response = client.post(
        "/ingestion/pdf",
        files={"file": ("", io.BytesIO(_PDF_MAGIC + b"rest"), "application/pdf")},
    )
    assert response.status_code == 400
```

- [ ] **Step 3: Run the new test first and confirm it exercises the new code path**

```bash
uv run pytest tests/ingestion/test_router.py::test_upload_pdf_rejects_missing_filename -v
```
Expected: `PASSED` (an empty-string filename triggers the same `if file.filename is None` guard's sibling case — Starlette normalizes a missing filename to `""`, not `None`, in multipart uploads from `TestClient`; this test documents that the guard's real-world trigger is an empty/falsy filename, not just `None`). If it fails because `""` doesn't trip the check, change the guard in Step 1 to `if not file.filename:` instead of `if file.filename is None:` and re-run.

- [ ] **Step 4: Verify everything else**

```bash
uv run ruff check app/ingestion/router.py tests/ingestion/test_router.py
```
Expected: `All checks passed!`

```bash
uv run mypy app/ingestion/router.py
```
Expected: `Success: no issues found in 1 source file`

```bash
uv run pytest tests/ingestion/test_router.py -q
```
Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/router.py tests/ingestion/test_router.py
git commit -m "fix: reject uploads with no filename, type-annotate router.py"
```

---

## Task 7: Fix remaining Ruff findings in `tests/`

**Files:**
- Modify: `tests/ingestion/conftest.py`, `tests/ingestion/test_chunker.py`, `tests/ingestion/test_config.py`

**Interfaces:** none — purely formatting/import fixes, no signature changes.

**Diagnostics being fixed:** `E501` x3 (one each in `conftest.py` lines 13 and 48, and `test_chunker.py` line 6), `D205` (blank line after summary in `conftest.py`'s `multi_paragraph_pdf` docstring), `F401` (unused `import os` in `test_config.py`).

- [ ] **Step 1: `tests/ingestion/conftest.py` — wrap the two long lines and fix the docstring**

Line 13, change:
```python
    page.insert_text((72, 100), "This is a simple paragraph of body text for testing extraction.", fontsize=11)
```
to:
```python
    page.insert_text(
        (72, 100), "This is a simple paragraph of body text for testing extraction.", fontsize=11
    )
```

Lines 20–25, change:
```python
def multi_paragraph_pdf(tmp_path):
    """A one-page PDF with a heading and several lines of body text spanning multiple
    paragraphs (with a blank-line gap) — realistic input that a single-line fixture can't
    exercise, since MarkdownHeaderTextSplitter reconstructs multi-line section content
    rather than preserving it verbatim.
    """
```
to:
```python
def multi_paragraph_pdf(tmp_path):
    """A one-page PDF with a heading and several lines of body text spanning multiple paragraphs.

    Includes a blank-line gap — realistic input that a single-line fixture can't exercise,
    since MarkdownHeaderTextSplitter reconstructs multi-line section content rather than
    preserving it verbatim.
    """
```

Line 48, change:
```python
            rect = fitz.Rect(x0 + col * cell_w, y0 + row * cell_h, x0 + (col + 1) * cell_w, y0 + (row + 1) * cell_h)
```
to:
```python
            rect = fitz.Rect(
                x0 + col * cell_w, y0 + row * cell_h, x0 + (col + 1) * cell_w, y0 + (row + 1) * cell_h
            )
```

- [ ] **Step 2: `tests/ingestion/test_chunker.py` — wrap the long line**

Change:
```python
def _settings(**overrides):
    return IngestionSettings(**{"chunk_size": 1500, "chunk_overlap": 200, "ocr_text_threshold": 20, **overrides})
```
to:
```python
def _settings(**overrides):
    defaults = {"chunk_size": 1500, "chunk_overlap": 200, "ocr_text_threshold": 20}
    return IngestionSettings(**{**defaults, **overrides})
```

- [ ] **Step 3: `tests/ingestion/test_config.py` — remove the unused import**

Change:
```python
import os

from app.ingestion.config import IngestionSettings, get_settings
```
to:
```python
from app.ingestion.config import IngestionSettings, get_settings
```

- [ ] **Step 4: Verify**

```bash
uv run ruff check .
```
Expected: `All checks passed!`

```bash
uv run mypy app
```
Expected: `Success: no issues found in 10 source files`

```bash
uv run pytest -q
```
Expected: `32 passed` (31 original + the new filename-rejection test from Task 6).

- [ ] **Step 5: Commit**

```bash
git add tests/ingestion/conftest.py tests/ingestion/test_chunker.py tests/ingestion/test_config.py
git commit -m "style: fix remaining ruff findings in tests (line length, unused import)"
```

---

## Task 8: Add Ruff to pre-commit

**Files:**
- Modify: `.pre-commit-config.yaml`

**Interfaces:** none — config only.

- [ ] **Step 1: Add the Ruff hook**

Current content:
```yaml
repos:
  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.21.2
    hooks:
      - id: gitleaks
```

New content:
```yaml
repos:
  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.21.2
    hooks:
      - id: gitleaks
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.14.5
    hooks:
      - id: ruff-check
```

- [ ] **Step 2: Verify**

```bash
uv run pre-commit run --all-files
```
Expected: both `Detect hardcoded secrets` and `ruff-check` hooks show `Passed`.

- [ ] **Step 3: Commit**

```bash
git add .pre-commit-config.yaml
git commit -m "chore: add ruff pre-commit hook"
```

---

## Task 9: Add Ruff and Mypy to CI

**Files:**
- Modify: `.github/workflows/ci.yml`

**Interfaces:** none — CI config only.

- [ ] **Step 1: Update the workflow**

Current content (steps section):
```yaml
      - name: Scan for secrets (gitleaks)
        run: uv run pre-commit run gitleaks --all-files

      - name: Run tests
        # exit code 5 = no tests collected yet; tolerate until tests/ has content
        run: uv run pytest || [ $? -eq 5 ]
```

New content:
```yaml
      - name: Scan for secrets (gitleaks)
        run: uv run pre-commit run gitleaks --all-files

      - name: Lint (ruff)
        run: uv run ruff check .

      - name: Type check (mypy)
        run: uv run mypy app

      - name: Run tests
        run: uv run pytest
```

Note: the `pytest || [ $? -eq 5 ]` tolerance for "no tests collected yet" is removed — real tests have existed since the ingestion slice landed, so a bare `uv run pytest` is correct now and a collection failure should fail CI like any other test failure.

- [ ] **Step 2: Verify locally** (mirrors what CI will run)

```bash
uv run ruff check . && uv run mypy app && uv run pytest -q
```
Expected: all three succeed, ending in `32 passed`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add ruff and mypy checks, drop no-tests-yet pytest tolerance"
```

- [ ] **Step 4: Push and confirm CI is green**

```bash
git push
```

Then check the Actions run for this push (via `gh run watch` or the GitHub UI) and confirm all steps pass, including the two new ones.

---

## Task 10: Update `docs/engineering-guidelines.md`

**Files:**
- Modify: `docs/engineering-guidelines.md`

**Interfaces:** none — documentation only.

- [ ] **Step 1: Update the status note**

Change:
```markdown
> Status note: this document currently describes standards in prose only. ERP-006 will review and strengthen these guidelines once the repository has real code and tooling in place. ERP-007 will configure the tooling (ruff, mypy, pytest, pre-commit) that can enforce the items marked **[tooling candidate]** below, rather than relying on documentation alone.
```
to:
```markdown
> Status note: ERP-007 configured Ruff and Mypy to enforce the items below marked **[tooling candidate: ruff ...]** or **[tooling candidate: mypy]** — see `.pre-commit-config.yaml` and `.github/workflows/ci.yml`. Items still marked **[tooling candidate]** without an enforcing tool remain prose-only, pending ERP-006's broader guidelines review.
```

- [ ] **Step 2: Update the now-enforced markers**

Change:
```markdown
- Python type hints **[tooling candidate: mypy]**
```
to:
```markdown
- Python type hints **[enforced: mypy --strict on `app/`]**
```

Change:
```markdown
- Google-style docstrings **[tooling candidate: ruff docstring rules]**
```
to:
```markdown
- Google-style docstrings **[enforced: ruff `D` rules, google convention]**
```

Change:
```markdown
Never suppress warnings unless absolutely necessary. **[tooling candidate: ruff/mypy config disallowing blanket suppressions]**
```
to:
```markdown
Never suppress warnings unless absolutely necessary. **[enforced: ruff `PGH003`/`PGH004` ban blanket `# type: ignore` / `# noqa`]**
```

Change:
```markdown
Never silently ignore exceptions. **[tooling candidate: ruff bare-except rules]**
```
to:
```markdown
Never silently ignore exceptions. **[enforced: ruff `BLE001`]**
```

Leave the coverage-threshold candidate (`Every business logic module should have unit tests. **[tooling candidate: coverage threshold in pytest/CI]**`) unchanged — explicitly out of scope for ERP-007.

- [ ] **Step 3: Commit**

```bash
git add docs/engineering-guidelines.md
git commit -m "docs: mark ruff/mypy-enforced guidelines as enforced, not just candidates"
```

---

## Task 11: Verify the guardrail actually blocks a violation

**Files:** none permanently modified — this task introduces a temporary violation, confirms it's caught, then reverts it. Mirrors how ERP-010 verified Gitleaks with a fake secret.

- [ ] **Step 1: Introduce a deliberate violation**

Temporarily remove the docstring from `get_job_status` in `app/ingestion/router.py`:
```python
@router.get("/jobs/{job_id}")
def get_job_status(job_id: str) -> JobStatusResponse:
    record = jobs.get_job(job_id)
```

- [ ] **Step 2: Confirm the local pre-commit hook catches it**

```bash
git add -A
uv run pre-commit run ruff-check --all-files
```
Expected: hook reports `Failed`, showing `D103 Missing docstring in public function`.

- [ ] **Step 3: Confirm the CI-equivalent command catches it**

```bash
uv run ruff check .
```
Expected: exits non-zero, reporting the same `D103` error.

- [ ] **Step 4: Revert the deliberate violation**

```bash
git checkout -- app/ingestion/router.py
git reset
```

- [ ] **Step 5: Confirm clean state is restored**

```bash
uv run ruff check .
```
Expected: `All checks passed!`

(No commit for this task — it's a verification exercise, not a code change.)

---

## Task 12: Ticket, session, and memory bookkeeping

**Files:**
- Create: `.ai/tickets/ERP-007.md`
- Create: `.ai/sessions/2026-08-02-tooling-config.md`
- Modify: `.ai/memory/current-state.md`

**Interfaces:** none — repo process documentation only.

- [ ] **Step 1: Create the ticket file, using `.ai/templates/ticket.md`'s structure**

```markdown
# ERP-007 — Ruff, Mypy, Pytest, Pre-commit Tooling

Status: Done
Depends On: None

## Description

Configures Ruff (linting) and Mypy (`--strict`, scoped to `app/`) per the `[tooling candidate]` items in `docs/engineering-guidelines.md`, wires both into `pre-commit` (Ruff) and CI (both), and brings `app/`/`tests/` into compliance. See `docs/superpowers/specs/2026-08-02-tooling-config-design.md` for the full design and `docs/superpowers/plans/2026-08-02-tooling-config.md` for the implementation plan.

## Acceptance Criteria

- [x] Ruff added as a dev dependency, configured in `pyproject.toml` (`line-length = 110`, `select = ["E", "F", "I", "B", "D", "BLE", "PGH"]`, google docstring convention, tests exempted from docstring rules)
- [x] Mypy added as a dev dependency, configured `--strict`, scoped to `app/`
- [x] `.pre-commit-config.yaml` extended with a Ruff hook
- [x] `.github/workflows/ci.yml` extended with `ruff check` and `mypy app` steps; the no-tests-yet `pytest` tolerance removed
- [x] `app/` and `tests/` brought into full compliance with both tools (including one real bug fix: `upload_pdf` no longer accepts a file with no filename)
- [x] `docs/engineering-guidelines.md` status note and relevant `[tooling candidate]` markers updated to `[enforced: ...]`
- [x] Guardrail verified to actually catch a violation (deliberate missing docstring caught by both the local hook and the CI-equivalent command, then reverted)

## Notes

Pytest coverage thresholds, `tests/` under mypy `--strict`, and branch-protection `required_status_checks` on `main` were considered and explicitly deferred — see the design spec's Scope section.
```

- [ ] **Step 2: Create the session log entry, using `.ai/templates/session.md`'s structure**

Read `.ai/templates/session.md` and the two existing entries in `.ai/sessions/` first to match their exact section structure, then write an entry covering: what was built (Ruff + Mypy config, pre-commit/CI wiring, the `app/`/`tests/` fixes, the `upload_pdf` filename bug fix), key decisions (line-length 110, tests exempted from docstring rules and from mypy strict, mypy scoped to `app/` only — each with the "why" already captured in this plan's Global Constraints section), and verification performed (Task 11's deliberate-violation exercise, plus the full green `ruff check` / `mypy app` / `pytest` / `pre-commit run --all-files` run).

- [ ] **Step 3: Update `.ai/memory/current-state.md` in place**

In the "What Exists" section, add a bullet after the GitHub-setup bullet:
```markdown
- Ruff (lint) and Mypy (`--strict`, scoped to `app/`) are configured (ERP-007): Ruff runs via `pre-commit` and CI, Mypy via CI only. `app/` and `tests/` are fully compliant. `docs/engineering-guidelines.md` marks the now-enforced items accordingly.
```

In the "What Does Not Exist Yet" section, remove this line (now done):
```markdown
- No lint/type-check tooling (ruff, mypy) configured — pending ERP-007 (Gitleaks is configured; ERP-007 extends the same `.pre-commit-config.yaml`).
```

Update this line:
```markdown
- Branch protection on `main` does not yet require CI status checks to pass (no `required_status_checks` configured) — worth revisiting once ERP-007 lands.
```
to:
```markdown
- Branch protection on `main` does not yet require CI status checks to pass (no `required_status_checks` configured) — ERP-007 has landed with real CI checks (ruff, mypy, pytest, gitleaks) that could now be made required; not yet done.
```

In the "Next Planned Work" section, update:
```markdown
- ERP-006 (review/strengthen engineering guidelines) and ERP-007 (ruff/mypy/pytest config) — now unblocked, since real application code exists to review/configure against.
```
to:
```markdown
- ERP-006 (review/strengthen engineering guidelines) — now unblocked (ERP-007 is done), since real application code exists to review guidelines against.
- Consider requiring CI status checks in `main`'s branch protection, now that ERP-007 gives CI real signal (ruff, mypy, pytest, gitleaks).
```

- [ ] **Step 4: Commit**

```bash
git add .ai/tickets/ERP-007.md .ai/sessions/2026-08-02-tooling-config.md .ai/memory/current-state.md
git commit -m "docs: close out ERP-007 ticket, session log, and current-state"
```

---

## Self-Review Notes (from plan-writing pass)

- **Spec coverage:** every spec section (Ruff config, Mypy config, Pytest confirmation, pre-commit, CI, existing-code fixes, docs update, verification) maps to a task above. Pytest's "no new tool, config already sufficient" is covered by Task 1 Step 3 confirming a clean baseline rather than a dedicated task — no separate task needed since nothing changes.
- **Deviation from spec, called out explicitly:** the spec didn't specify whether `tests/` is in scope for Mypy `--strict`. Planning-time investigation (79 errors, virtually all "missing type annotation" on test functions) led to scoping Mypy to `app/` only, documented in Global Constraints. This mirrors the same tests-are-different judgment the spec already made for Ruff's docstring rules per the brainstorming session's "fix existing code now" + "balanced, not maximum" decisions.
- **Placeholder scan:** no TBDs; every step has literal file content or an exact command with expected output.
- **Type/name consistency:** `IngestionSettings`, `Chunk`, `IngestResponse`, `JobStatus`, `JobStatusResponse`, `JobRecord`, `chunk_markdown`, `parse_pdf`, `ingest_pdf`, `create_job`/`get_job`/`run_ingestion_job` all keep identical names and signatures across every task that touches or references them — verified by cross-checking each task's code against the files actually read from the repo before writing this plan.
