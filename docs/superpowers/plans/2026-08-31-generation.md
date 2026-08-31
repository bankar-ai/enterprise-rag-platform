# Generation (ERP-017) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `POST /generation/query`, an endpoint that runs the existing hybrid retrieval pipeline and synthesizes a single grounded, citation-marked answer via a local Ollama-hosted LLM (Qwen3).

**Architecture:** A new `app/generation/` module (config, client, prompt, schemas, service, router) mirrors the shape of `app/retrieval/` and `app/embedding/`. `service.generate` calls `app.retrieval.service.search` unmodified, short-circuits to a fixed answer on empty results, otherwise builds a numbered/truncated prompt and calls an injectable `LLMClient`.

**Tech Stack:** FastAPI, Pydantic v2, `pydantic-settings`, `ollama` (already a dependency — `ollama>=0.6.2`), pytest, `monkeypatch`.

## Global Constraints

- All dependencies via `uv add`/`uv sync` — no `pip install`. This plan introduces **no new dependency**; `ollama` is already in `pyproject.toml`.
- No `print()` in application code — use `logging` with a module-level `logger = logging.getLogger(__name__)`.
- Never hardcode secrets/URLs beyond documented defaults; all config through `pydantic-settings` env vars (`GENERATION_` prefix).
- 90% coverage gate applies (CI `--cov-fail-under=90`) — every new module needs tests exercising its branches.
- Business logic never lives in API routes — routes only validate request, call service, return response (matches `app/retrieval/router.py`'s existing pattern).
- Streaming responses and conversation memory are explicitly out of scope for this ticket (see `docs/superpowers/specs/2026-08-31-generation-design.md`).

---

## Task 1: `GenerationSettings` config

**Files:**
- Create: `app/generation/__init__.py` (empty, makes `app/generation` a package)
- Create: `app/generation/config.py`
- Test: `tests/generation/__init__.py` (empty)
- Test: `tests/generation/test_config.py`

**Interfaces:**
- Produces: `GenerationSettings` (Pydantic `BaseSettings`, `GENERATION_` env prefix) with fields `ollama_host: str = "http://localhost:11434"`, `model: str = "qwen3"`, `max_context_chars: int = 8000`, `temperature: float = 0.1`; `get_generation_settings() -> GenerationSettings` (`lru_cache`-wrapped, mirrors `app/embedding/config.py`'s `get_embedding_settings`).

- [ ] **Step 1: Write the failing test**

Create `tests/generation/__init__.py` (empty file).

Create `tests/generation/test_config.py`:

```python
from app.generation.config import GenerationSettings, get_generation_settings


def test_default_settings():
    settings = GenerationSettings()
    assert settings.ollama_host == "http://localhost:11434"
    assert settings.model == "qwen3"
    assert settings.max_context_chars == 8000
    assert settings.temperature == 0.1


def test_settings_overridable_via_env(monkeypatch):
    monkeypatch.setenv("GENERATION_MODEL", "qwen3:14b")
    monkeypatch.setenv("GENERATION_MAX_CONTEXT_CHARS", "4000")
    settings = GenerationSettings()
    assert settings.model == "qwen3:14b"
    assert settings.max_context_chars == 4000


def test_get_generation_settings_is_cached():
    get_generation_settings.cache_clear()
    first = get_generation_settings()
    second = get_generation_settings()
    assert first is second
    get_generation_settings.cache_clear()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/generation/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.generation'`

- [ ] **Step 3: Write minimal implementation**

Create `app/generation/__init__.py` (empty).

Create `app/generation/config.py`:

```python
"""Generation settings, loaded from environment variables."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class GenerationSettings(BaseSettings):
    """Configuration for LLM-backed answer generation.

    Overridable via `GENERATION_*` env vars.
    """

    model_config = SettingsConfigDict(env_prefix="GENERATION_")

    ollama_host: str = "http://localhost:11434"
    model: str = "qwen3"
    max_context_chars: int = 8000
    temperature: float = 0.1


@lru_cache
def get_generation_settings() -> GenerationSettings:
    """Return the process-wide cached `GenerationSettings` instance."""
    return GenerationSettings()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/generation/test_config.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add app/generation/__init__.py app/generation/config.py tests/generation/__init__.py tests/generation/test_config.py
git commit -m "feat: add GenerationSettings config for ERP-017"
```

---

## Task 2: `LLMClient` protocol + `OllamaLLMClient`

**Files:**
- Create: `app/generation/client.py`
- Test: `tests/generation/test_client.py`

**Interfaces:**
- Consumes: `GenerationSettings` from Task 1 (`app.generation.config`).
- Produces: `LLMClient` (`Protocol` with `generate(self, system_prompt: str, user_prompt: str) -> str`) and `OllamaLLMClient(settings: GenerationSettings)` implementing it via `ollama.Client(host=...).chat(model=..., messages=[...], options={"temperature": ...})`.

- [ ] **Step 1: Write the failing test**

Create `tests/generation/test_client.py`:

```python
from app.generation.client import OllamaLLMClient
from app.generation.config import GenerationSettings


class _FakeOllamaClient:
    def __init__(self, host):
        self.host = host
        self.calls = []

    def chat(self, model, messages, options):
        self.calls.append((model, messages, options))
        return {"message": {"content": "the answer [1]"}}


def test_generate_calls_ollama_with_system_and_user_messages(monkeypatch):
    fake = _FakeOllamaClient(host="http://fake:11434")
    monkeypatch.setattr("app.generation.client.ollama.Client", lambda host: fake)
    settings = GenerationSettings(
        ollama_host="http://fake:11434", model="test-model", temperature=0.2
    )

    client = OllamaLLMClient(settings)
    answer = client.generate("system text", "user text")

    assert answer == "the answer [1]"
    assert fake.calls == [
        (
            "test-model",
            [
                {"role": "system", "content": "system text"},
                {"role": "user", "content": "user text"},
            ],
            {"temperature": 0.2},
        )
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/generation/test_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.generation.client'`

- [ ] **Step 3: Write minimal implementation**

Create `app/generation/client.py`:

```python
"""Answer generation via a local Ollama chat model."""

from typing import Protocol

import ollama

from app.generation.config import GenerationSettings


class LLMClient(Protocol):
    """Anything that can turn a system+user prompt pair into an answer string."""

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Return the model's answer text for the given system/user prompts."""
        ...


class OllamaLLMClient:
    """`LLMClient` backed by a local Ollama chat model."""

    def __init__(self, settings: GenerationSettings) -> None:
        """Build a client bound to `settings.ollama_host`/`settings.model`/`settings.temperature`."""
        self._client = ollama.Client(host=settings.ollama_host)
        self._model = settings.model
        self._temperature = settings.temperature

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Send `system_prompt`/`user_prompt` to Ollama and return the response text."""
        response = self._client.chat(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            options={"temperature": self._temperature},
        )
        return response["message"]["content"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/generation/test_client.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add app/generation/client.py tests/generation/test_client.py
git commit -m "feat: add OllamaLLMClient for ERP-017"
```

---

## Task 3: Prompt construction with char-budget truncation

**Files:**
- Create: `app/generation/prompt.py`
- Test: `tests/generation/test_prompt.py`

**Interfaces:**
- Consumes: `RetrievedChunk` from `app.retrieval.schemas` (fields: `chunk_id`, `document_id`, `text`, `section_path`, `page_start`, `page_end`, `source_filename`, `score`).
- Produces: `SYSTEM_PROMPT: str` constant; `build_prompt(query: str, chunks: list[RetrievedChunk], max_context_chars: int) -> tuple[str, list[RetrievedChunk]]` returning `(user_prompt_text, included_chunks_in_citation_order)`.

- [ ] **Step 1: Write the failing test**

Create `tests/generation/test_prompt.py`:

```python
from app.generation.prompt import build_prompt
from app.retrieval.schemas import RetrievedChunk


def _chunk(chunk_id, text, section_path=None):
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id="doc-1",
        text=text,
        section_path=section_path or ["Intro"],
        page_start=1,
        page_end=1,
        source_filename="doc.pdf",
        score=0.9,
    )


def test_build_prompt_numbers_chunks_and_appends_question():
    chunks = [_chunk("c1", "First chunk text."), _chunk("c2", "Second chunk text.")]

    user_prompt, included = build_prompt("What is X?", chunks, max_context_chars=1000)

    assert "[1] First chunk text." in user_prompt
    assert "[2] Second chunk text." in user_prompt
    assert "(source: doc.pdf, section: Intro)" in user_prompt
    assert user_prompt.endswith("Question: What is X?")
    assert included == chunks


def test_build_prompt_always_includes_first_chunk_even_if_it_alone_exceeds_budget():
    chunks = [_chunk("c1", "x" * 50)]

    user_prompt, included = build_prompt("q", chunks, max_context_chars=10)

    assert included == chunks
    assert "[1]" in user_prompt


def test_build_prompt_truncates_before_chunk_that_would_exceed_budget():
    chunks = [_chunk("c1", "a" * 20), _chunk("c2", "b" * 20), _chunk("c3", "c" * 20)]

    user_prompt, included = build_prompt("q", chunks, max_context_chars=25)

    assert included == chunks[:1]
    assert "[2]" not in user_prompt
    assert "b" * 20 not in user_prompt


def test_build_prompt_with_no_chunks_returns_empty_context():
    user_prompt, included = build_prompt("q", [], max_context_chars=1000)

    assert included == []
    assert user_prompt.endswith("Question: q")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/generation/test_prompt.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.generation.prompt'`

- [ ] **Step 3: Write minimal implementation**

Create `app/generation/prompt.py`:

```python
"""Prompt construction for LLM-backed answer generation."""

from app.retrieval.schemas import RetrievedChunk

SYSTEM_PROMPT = (
    "You are an assistant answering questions using only the provided context. "
    "Cite sources inline using [1], [2], etc. matching the numbered context below. "
    "If the context does not contain enough information to answer, say so explicitly "
    "-- do not use outside knowledge."
)


def build_prompt(
    query: str, chunks: list[RetrievedChunk], max_context_chars: int
) -> tuple[str, list[RetrievedChunk]]:
    """Build the numbered-context user prompt, truncated to `max_context_chars`.

    Walks `chunks` in order, accumulating character count. The first chunk is always
    included even if it alone exceeds the budget (so a single oversized top result
    doesn't produce empty context); every subsequent chunk is included only if adding
    it would not exceed `max_context_chars`. Returns the user-prompt text and the list
    of chunks actually included, in citation-number order.
    """
    included: list[RetrievedChunk] = []
    total_chars = 0
    for chunk in chunks:
        entry_len = len(chunk.text)
        if included and total_chars + entry_len > max_context_chars:
            break
        included.append(chunk)
        total_chars += entry_len

    context_lines = []
    for index, chunk in enumerate(included, start=1):
        section = " > ".join(chunk.section_path) if chunk.section_path else "N/A"
        context_lines.append(
            f"[{index}] {chunk.text}\n(source: {chunk.source_filename}, section: {section})"
        )
    context_block = "\n\n".join(context_lines)

    user_prompt = f"{context_block}\n\nQuestion: {query}" if context_block else f"Question: {query}"
    return user_prompt, included
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/generation/test_prompt.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add app/generation/prompt.py tests/generation/test_prompt.py
git commit -m "feat: add prompt construction with char-budget truncation for ERP-017"
```

---

## Task 4: `GenerationQuery`/`Citation`/`GenerationResponse` schemas

**Files:**
- Create: `app/generation/schemas.py`
- Test: `tests/generation/test_schemas.py`

**Interfaces:**
- Produces: `GenerationQuery` (`query: str = Field(min_length=1)`, `top_k: int = Field(default=5, ge=1, le=50)`, `rerank: bool = Field(default=False)`, `expand_sections: bool = Field(default=False)`); `Citation` (`chunk_id: str`, `document_id: str`, `section_path: list[str]`, `page_start: int`, `page_end: int`, `source_filename: str`); `GenerationResponse` (`answer: str`, `citations: list[Citation]`).

- [ ] **Step 1: Write the failing test**

Create `tests/generation/test_schemas.py`:

```python
import pytest
from pydantic import ValidationError

from app.generation.schemas import Citation, GenerationQuery, GenerationResponse


def test_generation_query_defaults():
    query = GenerationQuery(query="hello")
    assert query.top_k == 5
    assert query.rerank is False
    assert query.expand_sections is False


def test_generation_query_rejects_empty_query():
    with pytest.raises(ValidationError):
        GenerationQuery(query="")


def test_generation_query_rejects_top_k_out_of_bounds():
    with pytest.raises(ValidationError):
        GenerationQuery(query="hello", top_k=0)


def test_generation_response_round_trip():
    citation = Citation(
        chunk_id="c1",
        document_id="doc-1",
        section_path=["Intro"],
        page_start=1,
        page_end=1,
        source_filename="doc.pdf",
    )
    response = GenerationResponse(answer="the answer [1]", citations=[citation])

    assert response.model_dump()["citations"][0]["chunk_id"] == "c1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/generation/test_schemas.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.generation.schemas'`

- [ ] **Step 3: Write minimal implementation**

Create `app/generation/schemas.py`:

```python
"""Pydantic schemas for the generation API's request and response."""

from pydantic import BaseModel, Field


class GenerationQuery(BaseModel):
    """A grounded-answer generation request."""

    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)
    rerank: bool = Field(default=False)
    expand_sections: bool = Field(default=False)


class Citation(BaseModel):
    """Provenance for one chunk that was included in the answer's context."""

    chunk_id: str
    document_id: str
    section_path: list[str]
    page_start: int
    page_end: int
    source_filename: str


class GenerationResponse(BaseModel):
    """A synthesized answer with the citations backing its inline [n] markers."""

    answer: str
    citations: list[Citation]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/generation/test_schemas.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add app/generation/schemas.py tests/generation/test_schemas.py
git commit -m "feat: add generation request/response schemas for ERP-017"
```

---

## Task 5: `service.generate` orchestration

**Files:**
- Create: `app/generation/service.py`
- Test: `tests/generation/test_service.py`

**Interfaces:**
- Consumes: `search` from `app.retrieval.service` (imported as `retrieval_search`, signature `search(query: str, top_k: int, rerank: bool = False, expand_sections: bool = False, ...) -> list[RetrievedChunk]`); `GenerationSettings`/`get_generation_settings` from Task 1; `LLMClient`/`OllamaLLMClient` from Task 2; `SYSTEM_PROMPT`/`build_prompt` from Task 3; `GenerationQuery`/`Citation`/`GenerationResponse` from Task 4.
- Produces: `NO_CONTEXT_ANSWER: str` constant; `generate(query: str, top_k: int, rerank: bool = False, expand_sections: bool = False, settings: GenerationSettings | None = None, llm_client: LLMClient | None = None) -> GenerationResponse`.

- [ ] **Step 1: Write the failing test**

Create `tests/generation/test_service.py`:

```python
from app.generation.config import GenerationSettings
from app.generation.service import NO_CONTEXT_ANSWER, generate
from app.retrieval.schemas import RetrievedChunk


def _chunk(chunk_id):
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id="doc-1",
        text=f"text for {chunk_id}",
        section_path=["Intro"],
        page_start=1,
        page_end=1,
        source_filename="doc.pdf",
        score=0.9,
    )


class _FakeLLMClient:
    def __init__(self, answer):
        self._answer = answer
        self.calls = []

    def generate(self, system_prompt, user_prompt):
        self.calls.append((system_prompt, user_prompt))
        return self._answer


def test_generate_short_circuits_on_empty_retrieval(monkeypatch):
    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: [])
    fake_llm = _FakeLLMClient("should not be used")

    response = generate("what is X?", top_k=5, llm_client=fake_llm)

    assert response.answer == NO_CONTEXT_ANSWER
    assert response.citations == []
    assert fake_llm.calls == []


def test_generate_builds_prompt_and_returns_citations(monkeypatch):
    chunks = [_chunk("c1"), _chunk("c2")]
    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: chunks)
    fake_llm = _FakeLLMClient("the answer [1][2]")

    response = generate(
        "what is X?",
        top_k=5,
        rerank=True,
        expand_sections=False,
        settings=GenerationSettings(),
        llm_client=fake_llm,
    )

    assert response.answer == "the answer [1][2]"
    assert [c.chunk_id for c in response.citations] == ["c1", "c2"]
    assert len(fake_llm.calls) == 1
    system_prompt, user_prompt = fake_llm.calls[0]
    assert "[1]" in user_prompt and "[2]" in user_prompt
    assert "cite sources inline" in system_prompt.lower()


def test_generate_passes_retrieval_params_through(monkeypatch):
    captured = {}

    def _fake_search(query, top_k, rerank=False, expand_sections=False):
        captured["args"] = (query, top_k, rerank, expand_sections)
        return [_chunk("c1")]

    monkeypatch.setattr("app.generation.service.retrieval_search", _fake_search)
    fake_llm = _FakeLLMClient("answer")

    generate("q", top_k=7, rerank=True, expand_sections=True, llm_client=fake_llm)

    assert captured["args"] == ("q", 7, True, True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/generation/test_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.generation.service'`

- [ ] **Step 3: Write minimal implementation**

Create `app/generation/service.py`:

```python
"""Grounded answer generation over hybrid-retrieved chunks."""

from app.generation.client import LLMClient, OllamaLLMClient
from app.generation.config import GenerationSettings, get_generation_settings
from app.generation.prompt import SYSTEM_PROMPT, build_prompt
from app.generation.schemas import Citation, GenerationResponse
from app.retrieval.service import search as retrieval_search

NO_CONTEXT_ANSWER = "I don't have enough information in the ingested documents to answer this question."


def generate(
    query: str,
    top_k: int,
    rerank: bool = False,
    expand_sections: bool = False,
    settings: GenerationSettings | None = None,
    llm_client: LLMClient | None = None,
) -> GenerationResponse:
    """Retrieve context for `query` and synthesize a grounded, citation-marked answer.

    Runs the existing hybrid retrieval pipeline unmodified (`rerank`/`expand_sections`
    passed straight through). If retrieval returns no chunks, short-circuits to
    `NO_CONTEXT_ANSWER` without constructing or calling an `LLMClient`. Otherwise builds
    a numbered, char-budget-truncated prompt from the retrieved chunks and returns the
    LLM's answer alongside `Citation`s for every chunk actually included in the prompt.

    `settings`/`llm_client` are injectable for testing; default to the process-wide
    cached `GenerationSettings` and an `OllamaLLMClient` built from it.
    """
    chunks = retrieval_search(query, top_k, rerank=rerank, expand_sections=expand_sections)
    if not chunks:
        return GenerationResponse(answer=NO_CONTEXT_ANSWER, citations=[])

    settings = settings or get_generation_settings()
    llm_client = llm_client or OllamaLLMClient(settings)

    user_prompt, included_chunks = build_prompt(query, chunks, settings.max_context_chars)
    answer = llm_client.generate(SYSTEM_PROMPT, user_prompt)

    citations = [
        Citation(
            chunk_id=chunk.chunk_id,
            document_id=chunk.document_id,
            section_path=chunk.section_path,
            page_start=chunk.page_start,
            page_end=chunk.page_end,
            source_filename=chunk.source_filename,
        )
        for chunk in included_chunks
    ]
    return GenerationResponse(answer=answer, citations=citations)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/generation/test_service.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add app/generation/service.py tests/generation/test_service.py
git commit -m "feat: add generate() service orchestration for ERP-017"
```

---

## Task 6: `POST /generation/query` router + app registration

**Files:**
- Create: `app/generation/router.py`
- Modify: `app/main.py`
- Test: `tests/generation/test_router.py`

**Interfaces:**
- Consumes: `generate` from `app.generation.service` (Task 5); `GenerationQuery`/`GenerationResponse` from `app.generation.schemas` (Task 4); `OllamaLLMClient` from `app.generation.client` (Task 2, for test monkeypatching only).
- Produces: `router: APIRouter` with `POST /generation/query`, mounted on `app.main.app`.

- [ ] **Step 1: Write the failing test**

Create `tests/generation/test_router.py`:

```python
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _stub_generation_backend(monkeypatch):
    """Stub retrieval and the LLM client so no real Ollama/Postgres call is made."""

    def _fake_search(query, top_k, rerank=False, expand_sections=False):
        return []

    monkeypatch.setattr("app.generation.service.retrieval_search", _fake_search)
    yield


def test_query_returns_no_context_answer_when_retrieval_empty():
    response = client.post("/generation/query", json={"query": "anything"})
    assert response.status_code == 200
    body = response.json()
    assert body["citations"] == []
    assert "don't have enough information" in body["answer"]


def test_query_rejects_empty_query_string():
    response = client.post("/generation/query", json={"query": ""})
    assert response.status_code == 422


def test_query_rejects_top_k_out_of_bounds():
    response = client.post("/generation/query", json={"query": "x", "top_k": 0})
    assert response.status_code == 422


def test_query_returns_answer_with_citations(monkeypatch):
    from app.retrieval.schemas import RetrievedChunk

    chunk = RetrievedChunk(
        chunk_id="c1",
        document_id="doc-1",
        text="some text",
        section_path=["Intro"],
        page_start=1,
        page_end=1,
        source_filename="doc.pdf",
        score=0.9,
    )
    monkeypatch.setattr(
        "app.generation.service.retrieval_search", lambda *a, **k: [chunk]
    )

    from app.generation.client import OllamaLLMClient

    monkeypatch.setattr(
        OllamaLLMClient, "generate", lambda self, system_prompt, user_prompt: "the answer [1]"
    )

    response = client.post("/generation/query", json={"query": "what is X?"})

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "the answer [1]"
    assert body["citations"] == [
        {
            "chunk_id": "c1",
            "document_id": "doc-1",
            "section_path": ["Intro"],
            "page_start": 1,
            "page_end": 1,
            "source_filename": "doc.pdf",
        }
    ]


def test_query_returns_503_when_llm_backend_unavailable(monkeypatch):
    from app.retrieval.schemas import RetrievedChunk

    chunk = RetrievedChunk(
        chunk_id="c1",
        document_id="doc-1",
        text="some text",
        section_path=["Intro"],
        page_start=1,
        page_end=1,
        source_filename="doc.pdf",
        score=0.9,
    )
    monkeypatch.setattr(
        "app.generation.service.retrieval_search", lambda *a, **k: [chunk]
    )

    from app.generation.client import OllamaLLMClient

    def _raise_generate(self, system_prompt, user_prompt):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(OllamaLLMClient, "generate", _raise_generate)

    response = client.post("/generation/query", json={"query": "what is X?"})

    assert response.status_code == 503
    assert response.json() == {"detail": "Generation query failed"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/generation/test_router.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.generation.router'` (or a 404 once the module exists but isn't yet registered)

- [ ] **Step 3: Write minimal implementation**

Create `app/generation/router.py`:

```python
"""Generation API: grounded answer synthesis over retrieved chunks."""

import logging

from fastapi import APIRouter, HTTPException

from app.generation.schemas import GenerationQuery, GenerationResponse
from app.generation.service import generate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/generation", tags=["generation"])


@router.post("/query")
def query(query_request: GenerationQuery) -> GenerationResponse:
    """Run retrieval + LLM synthesis and return a grounded, cited answer."""
    try:
        return generate(
            query_request.query,
            query_request.top_k,
            rerank=query_request.rerank,
            expand_sections=query_request.expand_sections,
        )
    except Exception as exc:
        logger.exception("Generation query failed")
        raise HTTPException(status_code=503, detail="Generation query failed") from exc
```

Modify `app/main.py`:

```python
"""FastAPI application entrypoint."""

from fastapi import FastAPI

from app.generation.router import router as generation_router
from app.ingestion.router import router as ingestion_router
from app.retrieval.router import router as retrieval_router

app = FastAPI(title="Enterprise RAG Platform")
app.include_router(ingestion_router)
app.include_router(retrieval_router)
app.include_router(generation_router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/generation/test_router.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Run the full test suite and coverage gate**

Run: `uv run pytest --cov=app --cov-fail-under=90`
Expected: all tests pass (existing + new `tests/generation/*`), coverage gate holds.

- [ ] **Step 6: Commit**

```bash
git add app/generation/router.py app/main.py tests/generation/test_router.py
git commit -m "feat: add POST /generation/query endpoint for ERP-017"
```

---

## Task 7: Ticket, session log, and current-state updates

**Files:**
- Create: `.ai/tickets/ERP-017.md` (follow `.ai/templates/` ticket template and the shape of `.ai/tickets/ERP-016.md`)
- Create: `.ai/sessions/2026-08-31-generation.md` (follow `.ai/templates/session.md`)
- Modify: `.ai/memory/current-state.md`

**Interfaces:** None (documentation only).

- [ ] **Step 1: Read the existing ERP-016 ticket and the session template to match format**

Run: view `.ai/tickets/ERP-016.md` and `.ai/templates/session.md` for structure/headings to mirror.

- [ ] **Step 2: Write `.ai/tickets/ERP-017.md`**

Follow the ticket template's structure (title, context, scope, acceptance criteria, links to the design spec at `docs/superpowers/specs/2026-08-31-generation-design.md`); mark it `Done` once Task 6 is committed, listing the merge commit once merged.

- [ ] **Step 3: Write `.ai/sessions/2026-08-31-generation.md`**

Summarize what was decided (design choices from the brainstorm: sync-only, new endpoint, inline numbered citations, char-budget truncation, retrieval knobs exposed, empty-retrieval short-circuit) and what was built (the six `app/generation/*` files, the six commits, `main.py` registration).

- [ ] **Step 4: Update `.ai/memory/current-state.md` in place**

Add a bullet under "What Exists" describing the new `POST /generation/query` endpoint (mirroring the existing ERP-012/014/015/016 bullets' style and level of detail), move "Generation code" out of "What Does Not Exist Yet", and update "Next Planned Work" to drop generation and note any real follow-ups surfaced during implementation (if none, leave the existing Redis-cache/CI/GIN-index items as the next work).

- [ ] **Step 5: Commit**

```bash
git add .ai/tickets/ERP-017.md .ai/sessions/2026-08-31-generation.md .ai/memory/current-state.md
git commit -m "docs: close out ERP-017 with session log and current-state update"
```

---

## Post-Plan: PR

Once all tasks are committed on the feature branch, open a PR against `develop` (following this repo's established `develop`-then-promote-to-`main` flow — see `current-state.md`'s PR history) titled around "feat: add generation endpoint (ERP-017)", summarizing the six `app/generation/*` modules and linking the design spec and session log. Do not merge without the user's explicit go-ahead.
