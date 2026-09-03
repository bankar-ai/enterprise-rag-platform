# Session — Streaming Generation

Date: 2026-09-03
Tickets Touched: ERP-020

## Decisions

New `POST /generation/query/stream` endpoint (not a `stream` flag on the existing endpoint) keeps `POST /generation/query` completely untouched. Citations are sent as the first SSE event, before any answer tokens, since they're known from retrieval/`build_prompt` before generation starts. Streaming supports full conversation-memory parity with the sync endpoint from the start (not deferred). Partial answers are never persisted on disconnect or mid-generation failure — persistence happens only as the last step before the terminal `done` event, so `GeneratorExit` (disconnect) or an exception (failure) both skip it naturally, with no special-case code needed.

## Implementation Summary

- `app/generation/client.py`: `LLMClient.generate_stream` Protocol method; `OllamaLLMClient.generate_stream` via `ollama.Client.chat(..., stream=True)`.
- `app/generation/service.py`: `generate_stream(...)` generator, yielding `("citations", ...)`, `("token", ...)`×n, then `("done", ...)` or `("error", ...)`. Shares `generate()`'s stateless/stateful branching, rewrite, and persistence logic; `generate()` itself is unchanged.
- `app/generation/router.py`: `POST /generation/query/stream`, formatting each yielded tuple as one SSE frame (`event: <name>\ndata: <json>\n\n`) via `StreamingResponse`.
- Design spec: `docs/superpowers/specs/2026-09-03-streaming-generation-design.md`. Implementation plan: `docs/superpowers/plans/2026-09-03-streaming-generation.md`.
- Full suite: 171 tests, 99% coverage, mypy clean, ruff clean.

## Blockers

None.

## Next Steps

Push the branch, open a PR against `develop`, wait for CI, then merge. A separate `develop` → `main` promotion PR follows later, following the PR #6/#11/#15 pattern. Remaining `docs/roadmap.md` gaps after this: DOCX ingestion, PPTX ingestion, Authentication, Evaluation, Observability, and a real deployment story (Dockerfile + CD) beyond local-dev `docker-compose.yml`.
