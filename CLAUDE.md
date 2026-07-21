# CLAUDE.md

Operational guide for Claude Code in this repository. This file stays short and is read every turn — durable knowledge lives in `docs/` and `.ai/`.

## Repository Map

- `docs/architecture.md` — project overview, goals, philosophy, architecture principles, technology stack
- `docs/engineering-guidelines.md` — coding standards, error handling, testing, performance
- `docs/roadmap.md` — planned features
- `.ai/` — the repository's AI Engineering Operating System: `tickets/` (work management), `adr/` (accepted architecture decisions), `sessions/` (immutable session history), `memory/` (living project context), `templates/`. See `.ai/README.md`.

## Mandatory Development Rules

Always use `uv add`, `uv remove`, `uv sync`, `uv run`.

Never use `pip install` / `pip uninstall`. Never assume globally installed Python packages. All dependencies are managed through uv.

## Security

Never hardcode API keys, passwords, secrets, or tokens. Always load configuration from environment variables. Never commit secrets to Git.

## Logging

Use structured logging. Never use `print()` inside application code — it's acceptable only for local debugging and must be removed before commit.

## Dependency Policy

Before introducing a new dependency:

1. Check whether the Python standard library is sufficient.
2. Check whether an existing project dependency can solve the problem.
3. Introduce a new dependency only if it provides significant value — explain why, and wait for approval.

Avoid dependency bloat.

## Git Workflow

Commit frequently. Each commit represents one logical change. Use Conventional Commits (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`). Never commit broken code.

## Tool Usage Rules

When solving tasks:

- Only use tools that Claude Code explicitly exposes for the current session.
- Never assume the existence of tools, MCP servers, workflows, plugins, or IDE integrations.
- If a required tool is unavailable, explain why instead of inventing one.

During planning:

- Do not create, modify, or delete files.
- Do not execute notebook code or IDE helper code unless explicitly requested.

During implementation:

- Only modify files explicitly requested by the task.
- Never create temporary files unless explicitly requested.
- Never generate JavaScript workflow files (e.g. `workflow.js`) unless the project specifically requires them.
- Do not introduce new dependencies without explaining why and waiting for approval.

When uncertain, state assumptions clearly and ask for clarification instead of making architectural assumptions.

## Planning & Workflow

Start most work in Plan Mode. Before implementing any feature:

1. Read this file and the relevant `docs/` and `.ai/` content.
2. Understand the current repository structure.
3. Interview the user to resolve ambiguity before proposing an approach.
4. Explain the implementation plan and wait for approval.
5. Implement only the approved scope.
6. Make verification explicit — state how the change was checked, don't just assert it works.
7. Summarize every modified file after implementation.
8. Suggest further improvements, but do not implement them without approval.

## AI Assistant Behaviour

Always: produce production-quality code, explain architectural decisions, use modern Python with type hints, keep functions focused, minimize dependencies, write maintainable code.

Never: generate tutorial-style code, introduce unnecessary complexity, create placeholder implementations, create unused files, duplicate logic, invent APIs or library behavior.

If requirements are ambiguous, ask questions before implementing.

---

End of file.
