# Session — search-vector-index-and-nonlocking-migration

Date: 2026-09-05
Tickets Touched: ERP-023 (attempted, blocked), ERP-024, ERP-025

## Decisions

- ERP-024 and ERP-025 implemented together since they touch the same file (`app/ingestion/models.py`) — ERP-024's `Index(...)` declaration lands first, ERP-025 builds on it rather than the two edits fighting over `__table_args__`.
- ERP-025's downgrade path was chosen to exactly restore ERP-014's original migration (`GENERATED ALWAYS` column + non-concurrent index) rather than inventing a different rollback shape, keeping the up/down pair symmetric and predictable.
- `search_vector`'s auto-population moved from a `Computed(...)` SQLAlchemy column (which SQLAlchemy translates straight into `GENERATED ALWAYS AS (...) STORED` DDL) to a Postgres trigger, declared identically in both the Alembic migration (for real deployments) and via `event.listen(ChunkRecord.__table__, "after_create", DDL(...))` in the model (so `Base.metadata.create_all`, used by the test suite, reproduces the same behavior without running Alembic).
- ERP-023 (require CI status checks on `main`) could not be completed — the `gh api -X PUT .../branches/main/protection` call was blocked by the auto-mode permission classifier as a repo-settings change. Left as Backlog; needs either the user to do it directly or to grant a permission rule for this action.

## Implementation Summary

- `app/ingestion/models.py`: added `Index("ix_chunks_search_vector", "search_vector", postgresql_using="gin")` to `ChunkRecord.__table_args__` (ERP-024). Removed `Computed(...)` from `search_vector`, now a plain nullable `TSVECTOR` column; added two `event.listen(..., "after_create", DDL(...))` calls creating the `chunks_search_vector_update()` trigger function and `chunks_search_vector_trigger` (ERP-025).
- `alembic/versions/ec9863a88014_convert_search_vector_to_trigger_based_.py`: new migration, chained after `cafac1f26e4f`. `upgrade()`: drops the old index, `ALTER TABLE chunks ALTER COLUMN search_vector DROP EXPRESSION` (converts generated → plain column in place, no table rewrite, existing values kept), creates the trigger function + trigger, runs a batched backfill loop (`WHERE search_vector IS NULL LIMIT 1000`, looped — a no-op today since every row already has a value from the dropped generated expression, but exercises the general-purpose safe pattern), then rebuilds the GIN index with `CREATE INDEX CONCURRENTLY` inside `op.get_context().autocommit_block()` (required since `CONCURRENTLY` cannot run inside a transaction). Steps 1-4 all run in one transaction, so no external session ever observes a trigger-less intermediate state. `downgrade()`: drops trigger/function/index, drops and re-adds `search_vector` as `GENERATED ALWAYS AS (to_tsvector('english', text)) STORED` with a plain (non-concurrent) index — byte-for-byte matching ERP-014's original migration.

## Verification

- `uv run ruff check .` and `uv run mypy app` — both clean (`app/ingestion/models.py`'s two `DDL(...)` calls needed `# type: ignore[no-untyped-call]` — SQLAlchemy 2.0.51's `DDL.__init__` genuinely has no type annotations).
- `alembic/` is excluded from ruff (`extend-exclude` in `pyproject.toml`), consistent with the existing migration files' style.
- Dropped and recreated the local `erp_test` Postgres database (a stray `pytest` run against it earlier had left it in a state inconsistent with Alembic's tracked version — unrelated to this change, just a side effect of running the app's own test suite directly against the same DB Alembic manages), then ran `alembic upgrade head` from base — all five migrations, including the new one, applied cleanly.
- Manually inserted a chunk row via `psql` and confirmed the trigger populates `search_vector` correctly (`'africa':4 'giraff':1 'live':2` for "giraffes live in Africa").
- `alembic downgrade -1` restores the exact `GENERATED ALWAYS`/non-concurrent-index schema, with the trigger-inserted row's `search_vector` value preserved. Re-ran `alembic upgrade head` to return to the new schema.
- `alembic revision --autogenerate` produces an empty diff both immediately after ERP-024's model change and again after ERP-025's — confirms the ORM model and the live DB schema agree exactly (temp autogenerate files deleted after inspection, never committed).
- Full suite: `uv run pytest -q --cov=app --cov-fail-under=90` — 189 passed, `app/ingestion/models.py` at 100% coverage, 99.32% overall.

## Blockers

ERP-023 blocked: modifying `main`'s branch protection via `gh api` was denied by the environment's auto-mode permission classifier (repo-settings changes are treated as high-risk regardless of this project's own pre-authorization notes). The user needs to either apply it directly on GitHub or grant a Bash permission rule for this specific call. Intended settings (preserving the existing `required_pull_request_reviews`/`allow_force_pushes`/`allow_deletions` config, adding only `required_status_checks`):

```
gh api -X PUT repos/bankar-ai/enterprise-rag-platform/branches/main/protection \
  -H "Accept: application/vnd.github+json" \
  -f "required_status_checks[strict]=true" \
  -f "required_status_checks[contexts][]=test" \
  -f "enforce_admins=false" \
  -F "required_pull_request_reviews[required_approving_review_count]=0" \
  -F "required_pull_request_reviews[dismiss_stale_reviews]=false" \
  -F "required_pull_request_reviews[require_code_owner_reviews]=false" \
  -f "restrictions=null" \
  -F "allow_force_pushes=false" \
  -F "allow_deletions=false"
```

(`test` is the CI job's check-run name, confirmed via `gh api repos/.../commits/main/check-runs`.)

## Next Steps

- ERP-023 remains open, blocked as described above.
- Nothing else queued from this session; `.ai/memory/current-state.md`'s "Next Planned Work" list is otherwise empty of ready-to-start items.
