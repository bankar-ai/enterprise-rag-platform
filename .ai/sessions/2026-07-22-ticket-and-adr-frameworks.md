# Session — Ticket and ADR Frameworks

Date: 2026-07-22
Tickets Touched: ERP-001, ERP-002, ERP-003, ERP-004

## Decisions

- Established the `.ai/` skeleton (`tickets/`, `adr/`, `sessions/`, `memory/`, `templates/`) and rewrote `CLAUDE.md` into a short operational guide pointing into `docs/` and `.ai/` (ERP-001).
- Ticket lifecycle: `Backlog / In Progress / Done`, with a `Depends On` field for cross-ticket ordering (ERP-002).
- ADR lifecycle: `Accepted / Superseded`, with a `Related Ticket` field linking each decision back to the ticket that prompted it (ERP-003).
- ERP-001 was backfilled retroactively as a Done ticket since the ticket framework didn't exist when that work happened; ADRs, by contrast, are intentionally not backfilled for the tech stack choices already listed in `docs/architecture.md` — those weren't decisions made with consciously evaluated alternatives at the time.
- Session files are named by date (`YYYY-MM-DD-<slug>.md`) rather than sequential numbers, since sessions are a chronological log rather than a dependency-ordered artifact like tickets or ADRs (ERP-004).

## Implementation Summary

- ERP-001: `.ai/README.md` + five subdirectory READMEs; `docs/architecture.md`, `docs/engineering-guidelines.md`, `docs/roadmap.md`; shortened `CLAUDE.md`.
- ERP-002: `.ai/templates/ticket.md`; `.ai/tickets/ERP-001.md` (backfilled) and `.ai/tickets/ERP-002.md`; updated `.ai/tickets/README.md` and `.ai/templates/README.md`.
- ERP-003: `.ai/templates/adr.md`; `.ai/adr/ADR-001.md` ("Adopt Architecture Decision Records"); `.ai/tickets/ERP-003.md`; updated `.ai/adr/README.md` and `.ai/templates/README.md`.
- ERP-004: `.ai/templates/session.md`; this session file; `.ai/tickets/ERP-004.md`; updated `.ai/sessions/README.md` and `.ai/templates/README.md`.

## Blockers

None.

## Next Steps

ERP-005 (project memory framework) next, since it — like ERP-002/003/004 — has no dependency on real application code. ERP-006 (strengthen engineering guidelines) and ERP-007 (configure ruff/mypy/pytest/pre-commit tooling) remain blocked until actual application code exists to review and configure tooling against.
