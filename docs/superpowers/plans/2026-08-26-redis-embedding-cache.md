# Redis Embedding Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cache-aside Redis in front of Ollama embedding calls, so re-embedding identical `(model, text)` pairs hits Redis instead of Ollama, with zero visible interface change to `app/ingestion/jobs.py` or `app/retrieval/service.py`.

**Architecture:** Two changes inside `app/embedding/` — a new `cache.py` (`EmbeddingCache` protocol + `RedisEmbeddingCache`) and a modified `client.py` (`OllamaEmbeddingClient` does cache-aside lookups internally, defaulting its `cache` param to `RedisEmbeddingCache`). `config.py` gains two new settings. No other module changes.

**Tech Stack:** `redis` (official Python client), pytest against a real Redis service container (mirroring ERP-011's Postgres precedent).

## Global Constraints

- Only touch `app/embedding/`, `docker-compose.yml`, `.github/workflows/ci.yml`, `.ai/`, `docs/superpowers/`, `tests/embedding/`, `pyproject.toml`/`uv.lock`. Never touch `app/retrieval/` or `app/ingestion/` — the caching must be transparent to both without editing either.
- Never use `pip install`/`pip uninstall` — always `uv add`/`uv remove`/`uv sync`/`uv run` (`CLAUDE.md`).
- Never use `print()`; use the existing stdlib `logging` convention (module-level `logger = logging.getLogger(__name__)`).
- `--strict` Mypy applies to `app/`; `redis` ships its own type stubs (`py.typed`), so no `[[tool.mypy.overrides]]` entry should be needed — verify during Task 1.
- `pytest --cov=app --cov-fail-under=90` gates CI.
- Never hardcode secrets; the local Redis has no auth (matching the existing local-only Postgres `postgres`/`postgres` precedent — not a production credential).
- Redis must never be load-bearing: every Redis call in `cache.py` is wrapped so a Redis outage degrades to a cache miss, never an exception propagating to the caller.

---

## File Structure

- `pyproject.toml` — modify: add `redis` dependency (done: `uv add redis`).
- `app/embedding/config.py` — modify: add `redis_url`, `cache_ttl_seconds`.
- `app/embedding/cache.py` — create: `EmbeddingCache` protocol, `RedisEmbeddingCache`.
- `app/embedding/client.py` — modify: `OllamaEmbeddingClient` does cache-aside internally.
- `docker-compose.yml` — modify: add `redis` service.
- `.github/workflows/ci.yml` — modify: add Redis service container.
- `tests/embedding/test_config.py` — modify: cover the two new settings.
- `tests/embedding/conftest.py` — create: points `EMBEDDING_REDIS_URL` at a dedicated test logical DB, flushes it around each test.
- `tests/embedding/test_cache.py` — create: `RedisEmbeddingCache` against real Redis.
- `tests/embedding/test_client.py` — modify: inject a fake `EmbeddingCache`, cover hit/partial-hit/miss.

---

### Task 1: Dependency

- [x] **Step 1:** `uv add redis` — done; `pyproject.toml` gained `redis>=8.1.0`.
- [ ] **Step 2:** Confirm no mypy override is needed: `uv run mypy app` after Task 3 (client change) touches `redis` imports — if `redis` has no inline types recognized, add:
  ```toml
  [[tool.mypy.overrides]]
  module = "redis"
  ignore_missing_imports = true
  ```
- [ ] **Step 3:** Commit dependency (already staged from `uv add`; commit alongside Task 2 or standalone): `git commit -m "chore: add redis dependency"`

---

### Task 2: Embedding settings

**Files:** Modify `app/embedding/config.py`; modify `tests/embedding/test_config.py`.

- [ ] **Step 1: Write the failing tests.** Append to `tests/embedding/test_config.py`:
  ```python
  def test_redis_defaults():
      settings = EmbeddingSettings()
      assert settings.redis_url.startswith("redis://")
      assert settings.cache_ttl_seconds > 0


  def test_redis_reads_env_vars(monkeypatch):
      monkeypatch.setenv("EMBEDDING_REDIS_URL", "redis://cache-host:6379/2")
      monkeypatch.setenv("EMBEDDING_CACHE_TTL_SECONDS", "60")
      settings = EmbeddingSettings()
      assert settings.redis_url == "redis://cache-host:6379/2"
      assert settings.cache_ttl_seconds == 60
  ```
- [ ] **Step 2:** Run: `uv run pytest tests/embedding/test_config.py -v` — expect `AttributeError`/`ValidationError` failures on the two new tests.
- [ ] **Step 3: Implement.** In `app/embedding/config.py`, add to `EmbeddingSettings`:
  ```python
  redis_url: str = "redis://localhost:6379/0"
  cache_ttl_seconds: int = 86400
  ```
- [ ] **Step 4:** Run: `uv run pytest tests/embedding/test_config.py -v` — expect PASS (4 tests).
- [ ] **Step 5:** Commit: `git commit -m "feat: add redis cache settings to EmbeddingSettings"`

---

### Task 3: Redis-backed embedding cache

**Files:** Create `app/embedding/cache.py`, `tests/embedding/conftest.py`, `tests/embedding/test_cache.py`.

**Interfaces:** Produces `EmbeddingCache` (`Protocol`: `get(model: str, text: str) -> list[float] | None`, `set(model: str, text: str, vector: list[float]) -> None`), `RedisEmbeddingCache(settings: EmbeddingSettings)` implementing it.

- [ ] **Step 1: Add the test-Redis fixture.** Create `tests/embedding/conftest.py`:
  ```python
  """Shared fixtures for embedding tests: points Redis at a dedicated test-only logical DB."""

  import os

  os.environ.setdefault("EMBEDDING_REDIS_URL", "redis://localhost:6379/1")

  import pytest  # noqa: E402
  import redis  # noqa: E402

  from app.embedding.config import EmbeddingSettings  # noqa: E402


  @pytest.fixture
  def redis_settings() -> EmbeddingSettings:
      """`EmbeddingSettings` pointed at the dedicated test Redis logical DB."""
      return EmbeddingSettings(redis_url=os.environ["EMBEDDING_REDIS_URL"])


  @pytest.fixture(autouse=True)
  def _flush_test_redis_db(redis_settings: EmbeddingSettings):
      """Flush the test-only Redis logical DB before and after every embedding test."""
      client = redis.Redis.from_url(redis_settings.redis_url)
      client.flushdb()
      yield
      client.flushdb()
  ```
  This requires a Redis reachable at `localhost:6379` (docker-compose locally, service container in CI) — logical DB 1 is used so cache tests never touch db 0 (the app default).

- [ ] **Step 2: Write the failing tests.** Create `tests/embedding/test_cache.py`:
  ```python
  from app.embedding.cache import RedisEmbeddingCache
  from app.embedding.config import EmbeddingSettings


  def test_get_returns_none_on_miss(redis_settings):
      cache = RedisEmbeddingCache(redis_settings)
      assert cache.get("model-a", "hello world") is None


  def test_set_then_get_round_trips_vector(redis_settings):
      cache = RedisEmbeddingCache(redis_settings)
      cache.set("model-a", "hello world", [0.1, 0.2, 0.3])
      assert cache.get("model-a", "hello world") == [0.1, 0.2, 0.3]


  def test_same_text_different_model_does_not_collide(redis_settings):
      cache = RedisEmbeddingCache(redis_settings)
      cache.set("model-a", "hello world", [0.1, 0.2])
      assert cache.get("model-b", "hello world") is None


  def test_unreachable_redis_degrades_to_miss_and_noop_set():
      settings = EmbeddingSettings(redis_url="redis://localhost:1/0")
      cache = RedisEmbeddingCache(settings)
      assert cache.get("model-a", "text") is None
      cache.set("model-a", "text", [0.1])  # must not raise
  ```
- [ ] **Step 3:** Run: `uv run pytest tests/embedding/test_cache.py -v` — expect `ModuleNotFoundError: No module named 'app.embedding.cache'`.
- [ ] **Step 4: Implement `app/embedding/cache.py`:**
  ```python
  """Cache-aside storage for embedding vectors, backed by Redis. Never load-bearing (ADR-003):
  every Redis failure degrades to a cache miss/no-op instead of raising.
  """

  import hashlib
  import json
  import logging
  from typing import Protocol

  import redis

  from app.embedding.config import EmbeddingSettings

  logger = logging.getLogger(__name__)


  class EmbeddingCache(Protocol):
      """Anything that can cache-aside an embedding vector for a `(model, text)` pair."""

      def get(self, model: str, text: str) -> list[float] | None:
          """Return the cached vector for `(model, text)`, or `None` on a miss."""
          ...

      def set(self, model: str, text: str, vector: list[float]) -> None:
          """Cache `vector` for `(model, text)`."""
          ...


  class RedisEmbeddingCache:
      """`EmbeddingCache` backed by Redis, keyed by a hash of `(model, text)`."""

      def __init__(self, settings: EmbeddingSettings) -> None:
          """Build a cache bound to `settings.redis_url`, TTL from `settings.cache_ttl_seconds`."""
          self._client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
          self._ttl_seconds = settings.cache_ttl_seconds

      @staticmethod
      def _key(model: str, text: str) -> str:
          digest = hashlib.sha256(f"{model}:{text}".encode()).hexdigest()
          return f"embedding:{digest}"

      def get(self, model: str, text: str) -> list[float] | None:
          """Return the cached vector for `(model, text)`, or `None` on a miss or Redis error."""
          try:
              raw = self._client.get(self._key(model, text))
          except redis.RedisError:
              logger.exception("Redis GET failed; treating as a cache miss")
              return None
          if raw is None:
              return None
          vector: list[float] = json.loads(raw)
          return vector

      def set(self, model: str, text: str, vector: list[float]) -> None:
          """Cache `vector` for `(model, text)` with the configured TTL. No-op on Redis error."""
          try:
              self._client.set(self._key(model, text), json.dumps(vector), ex=self._ttl_seconds)
          except redis.RedisError:
              logger.exception("Redis SET failed; continuing without caching this vector")
  ```
- [ ] **Step 5:** Run: `uv run pytest tests/embedding/test_cache.py -v` — expect PASS (4 tests). Requires local Redis: `docker compose up -d redis` first if not already running.
- [ ] **Step 6:** Commit: `git commit -m "feat: add redis-backed embedding cache"`

---

### Task 4: Wire cache-aside lookups into `OllamaEmbeddingClient`

**Files:** Modify `app/embedding/client.py`, `tests/embedding/test_client.py`.

**Interfaces:** `OllamaEmbeddingClient.__init__` gains `cache: EmbeddingCache | None = None`, defaulting to `RedisEmbeddingCache(settings)`. `embed()` signature unchanged.

- [ ] **Step 1: Write the failing tests.** Replace `tests/embedding/test_client.py` with a version that adds a fake cache and cache-behavior tests, keeping the two existing tests (updated to pass an explicit no-op fake cache so they stay pure-unit and don't touch real Redis):
  ```python
  from app.embedding.client import OllamaEmbeddingClient
  from app.embedding.config import EmbeddingSettings


  class _FakeOllamaClient:
      def __init__(self, host):
          self.host = host
          self.calls = []

      def embed(self, model, input):
          self.calls.append((model, input))
          return {"embeddings": [[0.1, 0.2, 0.3] for _ in input]}


  class _FakeCache:
      def __init__(self):
          self.store = {}

      def get(self, model, text):
          return self.store.get((model, text))

      def set(self, model, text, vector):
          self.store[(model, text)] = vector


  def test_embed_calls_ollama_with_model_and_texts(monkeypatch):
      fake = _FakeOllamaClient(host="http://fake:11434")
      monkeypatch.setattr("app.embedding.client.ollama.Client", lambda host: fake)
      settings = EmbeddingSettings(ollama_host="http://fake:11434", model="test-model")

      client = OllamaEmbeddingClient(settings, cache=_FakeCache())
      vectors = client.embed(["a", "b"])

      assert vectors == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]
      assert fake.calls == [("test-model", ["a", "b"])]


  def test_embed_empty_list_returns_empty_without_calling_ollama(monkeypatch):
      fake = _FakeOllamaClient(host="http://fake:11434")
      monkeypatch.setattr("app.embedding.client.ollama.Client", lambda host: fake)
      client = OllamaEmbeddingClient(EmbeddingSettings(), cache=_FakeCache())

      assert client.embed([]) == []
      assert fake.calls == []


  def test_embed_skips_ollama_entirely_on_full_cache_hit(monkeypatch):
      fake = _FakeOllamaClient(host="http://fake:11434")
      monkeypatch.setattr("app.embedding.client.ollama.Client", lambda host: fake)
      settings = EmbeddingSettings(model="test-model")
      cache = _FakeCache()
      cache.set("test-model", "a", [9.0, 9.0])

      client = OllamaEmbeddingClient(settings, cache=cache)
      vectors = client.embed(["a"])

      assert vectors == [[9.0, 9.0]]
      assert fake.calls == []


  def test_embed_only_sends_cache_misses_to_ollama_and_preserves_order(monkeypatch):
      fake = _FakeOllamaClient(host="http://fake:11434")
      monkeypatch.setattr("app.embedding.client.ollama.Client", lambda host: fake)
      settings = EmbeddingSettings(model="test-model")
      cache = _FakeCache()
      cache.set("test-model", "cached", [9.0, 9.0])

      client = OllamaEmbeddingClient(settings, cache=cache)
      vectors = client.embed(["cached", "miss"])

      assert vectors == [[9.0, 9.0], [0.1, 0.2, 0.3]]
      assert fake.calls == [("test-model", ["miss"])]


  def test_embed_writes_misses_back_to_cache(monkeypatch):
      fake = _FakeOllamaClient(host="http://fake:11434")
      monkeypatch.setattr("app.embedding.client.ollama.Client", lambda host: fake)
      settings = EmbeddingSettings(model="test-model")
      cache = _FakeCache()

      client = OllamaEmbeddingClient(settings, cache=cache)
      client.embed(["new-text"])

      assert cache.get("test-model", "new-text") == [0.1, 0.2, 0.3]


  def test_default_cache_is_redis_backed(monkeypatch):
      fake = _FakeOllamaClient(host="http://fake:11434")
      monkeypatch.setattr("app.embedding.client.ollama.Client", lambda host: fake)
      client = OllamaEmbeddingClient(EmbeddingSettings())

      from app.embedding.cache import RedisEmbeddingCache

      assert isinstance(client._cache, RedisEmbeddingCache)
  ```
- [ ] **Step 2:** Run: `uv run pytest tests/embedding/test_client.py -v` — expect failures (`TypeError: unexpected keyword argument 'cache'`).
- [ ] **Step 3: Implement.** In `app/embedding/client.py`:
  ```python
  """Embedding generation via a local Ollama server, cache-aside in front of Redis."""

  from typing import Protocol

  import ollama

  from app.embedding.cache import EmbeddingCache, RedisEmbeddingCache
  from app.embedding.config import EmbeddingSettings


  class EmbeddingClient(Protocol):
      """Anything that can turn a batch of texts into embedding vectors."""

      def embed(self, texts: list[str]) -> list[list[float]]:
          """Return one embedding vector per input text, in the same order."""
          ...


  class OllamaEmbeddingClient:
      """`EmbeddingClient` backed by Ollama, with a cache-aside lookup ahead of each call.

      Cache misses are batched into a single Ollama call and written back to the cache;
      cache hits never reach Ollama. Callers see no difference from a plain Ollama client.
      """

      def __init__(self, settings: EmbeddingSettings, cache: EmbeddingCache | None = None) -> None:
          """Build a client bound to `settings.ollama_host`/`settings.model`, cache-aside via `cache`.

          `cache` defaults to a `RedisEmbeddingCache` built from `settings` when not given.
          """
          self._client = ollama.Client(host=settings.ollama_host)
          self._model = settings.model
          self._cache = cache if cache is not None else RedisEmbeddingCache(settings)

      def embed(self, texts: list[str]) -> list[list[float]]:
          """Return one embedding vector per text in `texts`, in the same order.

          Each text is looked up in the cache first; only cache misses are sent to Ollama,
          in a single batched call, and their results are written back to the cache.
          """
          if not texts:
              return []

          vectors: list[list[float] | None] = [self._cache.get(self._model, text) for text in texts]
          miss_indices = [i for i, vector in enumerate(vectors) if vector is None]

          if miss_indices:
              miss_texts = [texts[i] for i in miss_indices]
              response = self._client.embed(model=self._model, input=miss_texts)
              new_vectors = list(response["embeddings"])
              for i, vector in zip(miss_indices, new_vectors, strict=True):
                  vectors[i] = vector
                  self._cache.set(self._model, texts[i], vector)

          return [vector for vector in vectors if vector is not None]
  ```
  Note: the final list comprehension's `if vector is not None` filter is a mypy-narrowing formality, not a real filter — every element is guaranteed non-`None` at that point since all misses were just filled in. Verify mypy accepts this without complaint; if it flags the return type, use an explicit `cast(list[list[float]], vectors)` instead, matching the `cast` precedent already used in `app/embedding/index.py`.
- [ ] **Step 4:** Run: `uv run pytest tests/embedding/test_client.py -v` — expect PASS (7 tests).
- [ ] **Step 5:** Run: `uv run mypy app` — fix any typing gaps (see the note in Step 3).
- [ ] **Step 6:** Commit: `git commit -m "feat: cache-aside embedding lookups in OllamaEmbeddingClient"`

---

### Task 5: Local dev and CI Redis infra

**Files:** Modify `docker-compose.yml`, `.github/workflows/ci.yml`.

- [ ] **Step 1: Add the local-dev Redis service.** Edit `docker-compose.yml`:
  ```yaml
  services:
    postgres:
      image: postgres:16-alpine
      environment:
        POSTGRES_USER: postgres
        POSTGRES_PASSWORD: postgres
        POSTGRES_DB: erp
      ports:
        - "5432:5432"
      volumes:
        - postgres_data:/var/lib/postgresql/data

    redis:
      image: redis:7-alpine
      ports:
        - "6379:6379"

  volumes:
    postgres_data:
  ```
  Run: `docker compose up -d redis` — expect the container starts and reports healthy.

- [ ] **Step 2: Add a Redis service container to CI.** Edit `.github/workflows/ci.yml`, adding a `redis` entry alongside the existing `postgres` service:
  ```yaml
  services:
    postgres:
      image: postgres:16-alpine
      env:
        POSTGRES_USER: postgres
        POSTGRES_PASSWORD: postgres
        POSTGRES_DB: erp_test
      ports:
        - 5432:5432
      options: >-
        --health-cmd pg_isready
        --health-interval 10s
        --health-timeout 5s
        --health-retries 5
    redis:
      image: redis:7-alpine
      ports:
        - 6379:6379
      options: >-
        --health-cmd "redis-cli ping"
        --health-interval 10s
        --health-timeout 5s
        --health-retries 5
  ```
  No new env var is needed — `tests/embedding/conftest.py` (Task 3) sets `EMBEDDING_REDIS_URL` itself, pointed at `localhost:6379/1`, which resolves correctly against the CI service container's published port.

- [ ] **Step 3: Verify locally.** With `docker compose up -d postgres redis` running:
  ```bash
  uv run ruff check .
  uv run mypy app
  uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=90
  ```
  Expected: all pass, coverage at or above 90%.

- [ ] **Step 4:** Commit: `git commit -m "ci: run embedding-cache tests against a real redis service container"`
- [ ] **Step 5: Push and confirm CI is green.**
  ```bash
  git push -u origin erp-013-redis-embedding-cache
  ```
  Check the GitHub Actions run for this push; confirm all steps pass, including the new Redis-backed ones.

---

### Task 6: Close out ERP-013

**Files:** Modify `.ai/tickets/ERP-013.md`, create `.ai/sessions/2026-08-26-redis-embedding-cache.md`, modify `.ai/memory/current-state.md`.

- [ ] **Step 1:** Mark ERP-013's acceptance criteria complete; `Status: Done`.
- [ ] **Step 2:** Write the session log per `.ai/templates/session.md`.
- [ ] **Step 3:** Update `current-state.md`: remove the Redis-embedding-cache line from "What Does Not Exist Yet"/"Next Planned Work", add a summary bullet under "What Exists" matching the ERP-011/012 style.
- [ ] **Step 4:** Commit: `git commit -m "docs: close out ERP-013 ticket, session log, and current-state"`

---

## Self-Review Notes

- **Spec coverage:** settings (Task 2), Redis-backed cache module with graceful degradation (Task 3), transparent wiring into the existing client with no call-site changes (Task 4), local/CI infra (Task 5). All spec sections have a corresponding task.
- **Transparency verified structurally, not just asserted:** Task 4's design keeps `OllamaEmbeddingClient(settings)`'s single-positional-arg call signature valid (new `cache` param is optional and trailing), so `app/embedding/service.py` and `app/retrieval/service.py` — both out of scope to touch — need no changes and were confirmed (by reading both files during design) to already call the constructor that way.
- **Redis never load-bearing:** `RedisEmbeddingCache.get`/`set` both catch `redis.RedisError` and degrade rather than raise, covered by `test_unreachable_redis_degrades_to_miss_and_noop_set` (Task 3).
