# ERP-006 Strengthen Engineering Guidelines Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the last `[tooling candidate]` gap in `docs/engineering-guidelines.md` (a pytest coverage threshold), define a logging convention the codebase currently has none of, retrofit the one concrete violation of it, and cross-reference the existing routes/service-layer principle instead of duplicating it.

**Architecture:** No new production dependency beyond `pytest-cov` (dev-only, CI-only). Logging uses stdlib `logging`, module-level loggers. All changes are additive — no existing test or endpoint behavior changes except the new log line.

**Tech Stack:** `pytest-cov` (new dev dependency), stdlib `logging`.

## Global Constraints

- Coverage threshold: 90% (`--cov-fail-under=90`), CI-only, not pre-commit.
- Logging: stdlib `logging` only, no new library. Module-level `logger = logging.getLogger(__name__)`. Any non-re-raising `except` logs via `logger.exception(...)`.
- No duplication of `docs/architecture.md`'s routes/service-layer principle — cross-reference it.
- Ruff line-length 110, docstring convention google, mypy `--strict` on `app/` (from ERP-007) must stay clean throughout.

---

## Task 1: Add `pytest-cov`, wire the coverage gate into CI

**Files:**
- Modify: `pyproject.toml`
- Modify: `.github/workflows/ci.yml`

**Interfaces:** none — dependency + CI config only.

- [ ] **Step 1: Add the dev dependency**

```bash
uv add --dev pytest-cov
```

- [ ] **Step 2: Update the CI test step**

In `.github/workflows/ci.yml`, change:
```yaml
      - name: Run tests
        run: uv run pytest
```
to:
```yaml
      - name: Run tests (with coverage gate)
        run: uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=90
```

- [ ] **Step 3: Verify locally**

```bash
uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=90
```
Expected: passes, reports ~99% (206 statements, 3 missed in `chunker.py`), well above the 90% floor.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock .github/workflows/ci.yml
git commit -m "ci: add pytest-cov and enforce a 90% coverage floor"
```

---

## Task 2: Add a logging convention and retrofit `app/ingestion/jobs.py`

**Files:**
- Modify: `app/ingestion/jobs.py`
- Modify: `tests/ingestion/test_jobs.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `run_ingestion_job` keeps its exact signature and return behavior (job record still gets `FAILED` status + `error` string on failure) — this task only adds a log line, it does not change what callers observe via `get_job`.

**Diagnostics being fixed:** none from Ruff/Mypy — this is a real behavior gap (no log line ever emitted on ingestion failure), not a lint finding.

- [ ] **Step 1: Add logging to `jobs.py`**

Change:
```python
"""In-memory async ingestion job tracking (no persistent queue; single-process only)."""

import threading
import uuid

from app.ingestion.config import IngestionSettings
from app.ingestion.schemas import IngestResponse, JobStatus
from app.ingestion.service import ingest_pdf

_jobs: dict[str, "JobRecord"] = {}
_lock = threading.Lock()
```
to:
```python
"""In-memory async ingestion job tracking (no persistent queue; single-process only)."""

import logging
import threading
import uuid

from app.ingestion.config import IngestionSettings
from app.ingestion.schemas import IngestResponse, JobStatus
from app.ingestion.service import ingest_pdf

logger = logging.getLogger(__name__)

_jobs: dict[str, "JobRecord"] = {}
_lock = threading.Lock()
```

Change:
```python
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
to:
```python
def run_ingestion_job(job_id: str, pdf_path: str, filename: str, settings: IngestionSettings) -> None:
    """Run ingestion for `job_id`, recording DONE + result or FAILED + error on the job record."""
    with _lock:
        _jobs[job_id].status = JobStatus.PROCESSING

    try:
        result = ingest_pdf(pdf_path, filename, settings)
    except Exception as exc:  # noqa: BLE001 - job failure is reported via status, not raised
        logger.exception("Ingestion job %s failed for file %r", job_id, filename)
        with _lock:
            _jobs[job_id].status = JobStatus.FAILED
            _jobs[job_id].error = str(exc)
        return

    with _lock:
        _jobs[job_id].status = JobStatus.DONE
        _jobs[job_id].result = result
```

- [ ] **Step 2: Write the failing test first**

Add to `tests/ingestion/test_jobs.py`, after `test_run_ingestion_job_marks_failed_on_bad_path`:

```python
def test_run_ingestion_job_logs_on_failure(caplog):
    job_id = create_job()
    with caplog.at_level("ERROR"):
        run_ingestion_job(job_id, "/no/such/file.pdf", "missing.pdf", _settings())

    assert any(job_id in record.message for record in caplog.records)
    assert any(record.levelname == "ERROR" for record in caplog.records)
```

- [ ] **Step 3: Run it to confirm it fails before the code change**

If Step 1 hasn't been applied yet in your working copy, run:
```bash
uv run pytest tests/ingestion/test_jobs.py::test_run_ingestion_job_logs_on_failure -v
```
Expected: FAIL (`caplog.records` is empty — no logger configured yet). If Step 1 was already applied, skip this and go straight to Step 4 — the important thing is confirming the test actually exercises new behavior, not the exact order you touched files in.

- [ ] **Step 4: Run it to confirm it passes after the code change**

```bash
uv run pytest tests/ingestion/test_jobs.py -v
```
Expected: all 5 tests in the file pass (4 existing + 1 new).

- [ ] **Step 5: Verify ruff/mypy still clean**

```bash
uv run ruff check app/ingestion/jobs.py tests/ingestion/test_jobs.py
uv run mypy app/ingestion/jobs.py
```
Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git add app/ingestion/jobs.py tests/ingestion/test_jobs.py
git commit -m "feat: log ingestion job failures instead of silently swallowing them"
```

---

## Task 3: Update `docs/engineering-guidelines.md`

**Files:**
- Modify: `docs/engineering-guidelines.md`

**Interfaces:** none — documentation only.

- [ ] **Step 1: Flip the coverage marker to enforced**

Change:
```markdown
Every business logic module should have unit tests. **[tooling candidate: coverage threshold in pytest/CI]**
```
to:
```markdown
Every business logic module should have unit tests. **[enforced: pytest-cov, `--cov-fail-under=90`]**
```

- [ ] **Step 2: Add a one-line cross-reference to the routes/service-layer principle**

In the `## Coding Standards` section, after the `Use` bullet list (after the `Google-style docstrings` line and before `Functions should remain small.`), add:

```markdown
API routes must only validate input and call the service layer — see `docs/architecture.md`'s Architecture Principles for the full rule.
```

- [ ] **Step 3: Add a new Logging section**

Add a new `## Logging` section, placed after `## Error Handling` and before `## Testing`:

```markdown
## Logging

Use Python's standard `logging` module — no third-party logging library at this project's current scale.

Every module that logs gets its own logger: `logger = logging.getLogger(__name__)` at module level.

Any `except` block that does not re-raise must call `logger.exception(...)` before handling the failure, so the traceback is captured. See `app/ingestion/jobs.py`'s `run_ingestion_job` for the pattern.

Never log full request/response bodies or secrets. Log identifiers (job IDs, filenames) and error messages.

Never use `print()` in application code — see `CLAUDE.md`.
```

- [ ] **Step 4: Commit**

```bash
git add docs/engineering-guidelines.md
git commit -m "docs: enforce coverage marker, add logging convention, cross-reference architecture principles"
```

---

## Task 4: Verify the coverage guardrail actually blocks a regression

**Files:** none permanently modified — temporary edit, verified, reverted. Mirrors ERP-007's Task 11 and ERP-010's guardrail verification.

- [ ] **Step 1: Temporarily disable enough tests to drop coverage below 90%**

Rename `tests/ingestion/test_jobs.py` to `tests/ingestion/test_jobs.py.bak` (or comment out its contents) to remove ~30 covered statements' worth of exercised code from the run.

- [ ] **Step 2: Confirm the coverage command fails**

```bash
uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=90
```
Expected: exits non-zero, reports coverage below 90% and `FAIL Required test coverage of 90% not reached`.

- [ ] **Step 3: Restore**

```bash
mv tests/ingestion/test_jobs.py.bak tests/ingestion/test_jobs.py
```
(or revert the comment-out, whichever method Step 1 used)

- [ ] **Step 4: Confirm clean state is restored**

```bash
git status
uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=90
```
Expected: `git status` clean, coverage command passes at ~99% again.

(No commit for this task — it's a verification exercise, not a code change.)

---

## Task 5: Ticket, session, and memory bookkeeping

**Files:**
- Create: `.ai/tickets/ERP-006.md`
- Create: `.ai/sessions/2026-08-02-strengthen-engineering-guidelines.md`
- Modify: `.ai/memory/current-state.md`

**Interfaces:** none — repo process documentation only.

- [ ] **Step 1: Create the ticket file**

```markdown
# ERP-006 — Strengthen Engineering Guidelines

Status: Done
Depends On: ERP-007

## Description

Closes the last `[tooling candidate]` gap in `docs/engineering-guidelines.md` (a pytest coverage threshold), defines a logging convention the codebase previously had none of, retrofits the one concrete violation of it (`app/ingestion/jobs.py`'s silent exception swallowing), and cross-references `docs/architecture.md`'s routes/service-layer principle instead of duplicating it. See `docs/superpowers/specs/2026-08-02-strengthen-engineering-guidelines-design.md` for the full design and `docs/superpowers/plans/2026-08-02-strengthen-engineering-guidelines.md` for the implementation plan.

## Acceptance Criteria

- [x] `pytest-cov` added as a dev dependency; CI enforces `--cov-fail-under=90`
- [x] Logging convention defined in `docs/engineering-guidelines.md` (stdlib `logging`, module-level loggers, `logger.exception(...)` on non-re-raising excepts)
- [x] `app/ingestion/jobs.py`'s `run_ingestion_job` logs on failure; regression test added (`caplog`)
- [x] `docs/engineering-guidelines.md`'s coverage marker flipped to `[enforced: ...]`
- [x] Routes/service-layer principle cross-referenced from `docs/engineering-guidelines.md`, not duplicated
- [x] Coverage guardrail verified to actually block a regression (temporarily dropped coverage, confirmed the CI command fails, restored)

## Notes

Structured logging library (structlog/loguru) explicitly deferred — stdlib `logging` is sufficient at this project's current single-process scale; reconsider once observability (roadmap item) is built out.
```

- [ ] **Step 2: Create the session log entry**

Read `.ai/templates/session.md` and the two most recent files in `.ai/sessions/` (`2026-08-01-...md` and `2026-08-02-tooling-config.md`) to match their exact section structure, then write an entry covering: what was built (coverage gate, logging convention + retrofit, guidelines updates), key decisions with their "why" (90% threshold chosen from measured 99% baseline with slack for defensive branches; stdlib logging over a library, deferred until observability work; retrofit-in-code vs. doc-only, per user's explicit choice), and verification performed (Task 4's guardrail exercise, full green `pytest --cov=app --cov-fail-under=90` / `ruff check .` / `mypy app` run).

- [ ] **Step 3: Update `.ai/memory/current-state.md` in place**

In "What Exists", add a bullet after the ERP-007 bullet:
```markdown
- Pytest coverage gate is enforced (ERP-006): `pytest-cov`, `--cov-fail-under=90` in CI. A logging convention (stdlib `logging`, module-level loggers, `logger.exception` on non-re-raising excepts) is defined in `docs/engineering-guidelines.md` and applied to `app/ingestion/jobs.py`'s previously-silent failure path.
```

In "What Does Not Exist Yet", remove:
```markdown
- Engineering guidelines are prose-only, not yet enforced by tooling beyond secrets-scanning — pending ERP-006.
```

In "Next Planned Work", remove the ERP-006 line (now done) and keep the rest:
```markdown
- Embedding generation + Postgres/FAISS/BM25 persistence (the next vertical slice).
- Consider requiring CI status checks in `main`'s branch protection, now that ERP-007 gives CI real signal (ruff, mypy, pytest, gitleaks).
```

- [ ] **Step 4: Commit**

```bash
git add .ai/tickets/ERP-006.md .ai/sessions/2026-08-02-strengthen-engineering-guidelines.md .ai/memory/current-state.md
git commit -m "docs: close out ERP-006 ticket, session log, and current-state"
```

---

## Self-Review Notes (from plan-writing pass)

- **Spec coverage:** every spec section (coverage gate, logging convention, jobs.py retrofit, cross-reference, out-of-scope items) maps to a task above.
- **Placeholder scan:** no TBDs; every step has literal file content or an exact command with expected output.
- **Type/name consistency:** `run_ingestion_job`, `JobRecord`, `logger` all match across Task 2's steps and the retrofit description in Task 5's ticket text.
