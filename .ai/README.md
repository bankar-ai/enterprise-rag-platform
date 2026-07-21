# .ai/ — Repository AI Engineering Operating System

This directory is the working system that `CLAUDE.md` points to. It complements `docs/`:

- `docs/` holds durable reference documentation (architecture, engineering guidelines, roadmap).
- `.ai/` holds the operating system itself — work management, decisions, session history, and living project context.

## Subdirectories

- `tickets/` — work management, one file per ticket. Framework defined in ERP-002.
- `adr/` — accepted architecture decisions, one file per decision. Framework defined in ERP-003.
- `sessions/` — immutable per-session/milestone summaries. Framework defined in ERP-004.
- `memory/` — living, updated-in-place project context (current state, glossary, known issues, in-progress decisions), distinct from the immutable history in `sessions/`. Framework defined in ERP-005.
- `templates/` — shared templates (ticket, ADR, session) used once the frameworks above exist.

This skeleton was created by ERP-001. Each subdirectory's real content and format is built out by its own dedicated ticket, listed above.
