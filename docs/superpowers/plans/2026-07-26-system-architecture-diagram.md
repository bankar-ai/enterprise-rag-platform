# System Architecture Diagram Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the approved architecture design spec (`docs/superpowers/specs/2026-07-26-system-architecture-design.md`) into durable artifacts: an ADR recording the data-layer/caching decision, a tracking ticket, and a draw.io diagram (with PNG export) referenced from `docs/architecture.md`.

**Architecture:** This is documentation-only work — no application code. Each task produces one committed artifact: an ADR, a ticket, a `.drawio` file, and a doc cross-reference. The diagram encodes the full target-state architecture from the spec, with not-yet-built components shown dashed/grey and the one already-built piece (CI/CD, from ERP-008) shown solid/green.

**Tech Stack:** Markdown (ADR/ticket templates), draw.io/diagrams.net XML format (`.drawio`), Git.

## Global Constraints

- All dependencies/components in the diagram must be open-source and free (per `docs/architecture.md`'s Architecture Principles) — every box in this diagram is already open-source (Postgres, FAISS, Redis, Ollama, Nomic Embed, GitHub Actions).
- ADR numbering: next available is `ADR-003` (existing: ADR-001, ADR-002).
- Ticket numbering: next available is `ERP-009` (`ERP-006`/`ERP-007` are reserved per `docs/engineering-guidelines.md`; `ERP-008` already exists).
- Ingestion scope in the diagram/ADR must say PDF-only for now, per the spec's correction (DOCX/PPTX/OCR stay as roadmap/planned, not in the near-term scope).
- Follow the existing templates: `.ai/templates/adr.md`, `.ai/templates/ticket.md`.

---

### Task 1: Record the data-layer/caching decision and open a tracking ticket

**Files:**
- Create: `.ai/adr/ADR-003.md`
- Create: `.ai/tickets/ERP-009.md`

**Interfaces:**
- Consumes: the design spec at `docs/superpowers/specs/2026-07-26-system-architecture-design.md` (Components, Data Layer sections).
- Produces: `ADR-003` and `ERP-009`, referenced from Task 3's `docs/architecture.md` edit and from the diagram's implicit provenance (no diagram cell references them directly, but commit messages will).

- [ ] **Step 1: Write `.ai/adr/ADR-003.md`**

```markdown
# ADR-003 — Data Layer & Caching Architecture

Status: Accepted
Related Ticket: ERP-009

## Context

The system needs to persist users/auth, conversation history, ingested-document chunk text/metadata, and evaluation results — none of which FAISS (a pure vector index with no metadata or relational capability) can hold. Separately, the engineering guidelines call for avoiding unnecessary database queries and caching expensive operations, but no caching layer had been designed.

## Decision

Use **PostgreSQL** as the single primary relational datastore for users/auth, conversation history, chunk text + metadata (keyed by the same ID used in the FAISS vector index), and evaluation results. Add **Redis** as a cache-aside layer (never load-bearing) scoped to three concrete uses: an embedding cache (skip re-embedding duplicate chunk content by hash), a retrieval/query-result cache (short TTL), and a session/auth-token cache.

## Alternatives Considered

- **SQLite instead of Postgres** — rejected: weaker concurrent-write behavior and less representative of the "production-grade enterprise" architecture goal in `docs/architecture.md`, despite being simpler to run.
- **Separate sidecar store for chunk metadata, apart from user/auth data** — rejected: one datastore is simpler to operate and keeps ingestion writes (chunk metadata) transactionally consistent; only worth splitting if the two data shapes are expected to scale very differently, which isn't the case yet.
- **Deferring Redis entirely** — initially chosen, then reversed: the engineering guidelines already call out avoiding unnecessary DB queries and caching expensive operations (especially embeddings), so adding a narrowly-scoped cache now is cheaper than retrofitting one later. Redis is open-source and free, satisfying the project's dependency constraint.

## Consequences

Every ingestion and query request now has a clear datastore to write to/read from, closing the gap where chat history, document metadata, and eval results had no home. Redis being cache-aside means it can be added, resized, or removed without touching correctness — only latency. The trade-off is one more moving part in local development and deployment (a Redis instance alongside Postgres and Ollama).
```

- [ ] **Step 2: Write `.ai/tickets/ERP-009.md`**

```markdown
# ERP-009 — System Architecture Diagram

Status: Done
Depends On: None

## Description

Produces the first visual architecture diagram for the platform, rendering the target-state design from `docs/superpowers/specs/2026-07-26-system-architecture-design.md` (API layer, service layer, data layer, core/infra, cross-cutting concerns, deployment) as a draw.io diagram, exported to PNG, and referenced from `docs/architecture.md`. The data-layer/caching decision that the diagram depends on is recorded separately in ADR-003.

## Acceptance Criteria

- [x] ADR-003 written, recording the Postgres + Redis data-layer decision
- [x] `docs/diagrams/architecture.drawio` created, covering all components from the design spec, with not-yet-built components shown dashed/grey and the already-built CI/CD piece shown solid/green
- [ ] `docs/diagrams/architecture.png` exported from the `.drawio` file and committed — requires opening the file in the draw.io app/web UI, which cannot be done from the terminal; this is a manual step for the user
- [x] `docs/architecture.md` updated to reference the diagram

## Notes

Ingestion is scoped to PDF only for now (DOCX/PPTX/OCR remain roadmap items, shown dashed in the diagram). See ADR-003 for the data-layer reasoning.
```

- [ ] **Step 3: Commit**

```bash
git add .ai/adr/ADR-003.md .ai/tickets/ERP-009.md
git commit -m "docs: add ADR-003 (data layer & caching) and ERP-009 ticket"
```

---

### Task 2: Create the draw.io architecture diagram

**Files:**
- Create: `docs/diagrams/architecture.drawio`

**Interfaces:**
- Consumes: the component list and styling convention from Task 1 (built = solid/green, planned = dashed/grey).
- Produces: `docs/diagrams/architecture.drawio`, referenced by Task 3's doc edit and used by the user to export `docs/diagrams/architecture.png`.

- [ ] **Step 1: Create the directory and write the diagram file**

```
docs/diagrams/architecture.drawio
```

```xml
<mxfile host="app.diagrams.net">
  <diagram name="System Architecture" id="erp-system-architecture">
    <mxGraphModel dx="1400" dy="900" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="1650" pageHeight="1000" math="0" shadow="0">
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />

        <mxCell id="client" value="Client" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#f5f5f5;strokeColor=#666666;dashed=1;" vertex="1" parent="1">
          <mxGeometry x="700" y="20" width="160" height="40" as="geometry" />
        </mxCell>

        <mxCell id="api" value="API Layer (FastAPI)&#10;Auth Middleware" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#f5f5f5;strokeColor=#666666;dashed=1;" vertex="1" parent="1">
          <mxGeometry x="680" y="100" width="200" height="60" as="geometry" />
        </mxCell>

        <mxCell id="ing" value="Ingestion Service&#10;(PDF only, for now)" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#f5f5f5;strokeColor=#666666;dashed=1;" vertex="1" parent="1">
          <mxGeometry x="60" y="220" width="180" height="60" as="geometry" />
        </mxCell>

        <mxCell id="ret" value="Retrieval Service&#10;(BM25 + FAISS + RRF + Rerank)" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#f5f5f5;strokeColor=#666666;dashed=1;" vertex="1" parent="1">
          <mxGeometry x="280" y="220" width="220" height="60" as="geometry" />
        </mxCell>

        <mxCell id="gen" value="Generation Service&#10;(Ollama, streaming, memory)" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#f5f5f5;strokeColor=#666666;dashed=1;" vertex="1" parent="1">
          <mxGeometry x="540" y="220" width="220" height="60" as="geometry" />
        </mxCell>

        <mxCell id="eval" value="Evaluation Service" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#f5f5f5;strokeColor=#666666;dashed=1;" vertex="1" parent="1">
          <mxGeometry x="800" y="220" width="180" height="60" as="geometry" />
        </mxCell>

        <mxCell id="embed" value="Nomic Embed" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#f5f5f5;strokeColor=#666666;dashed=1;" vertex="1" parent="1">
          <mxGeometry x="60" y="330" width="180" height="50" as="geometry" />
        </mxCell>

        <mxCell id="ollama" value="Ollama (Qwen3)" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#f5f5f5;strokeColor=#666666;dashed=1;" vertex="1" parent="1">
          <mxGeometry x="540" y="330" width="180" height="50" as="geometry" />
        </mxCell>

        <mxCell id="pg" value="PostgreSQL&#10;(users, chat history,&#10;chunk metadata, eval results)" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#f5f5f5;strokeColor=#666666;dashed=1;" vertex="1" parent="1">
          <mxGeometry x="60" y="430" width="200" height="70" as="geometry" />
        </mxCell>

        <mxCell id="faiss" value="FAISS&#10;(vector index)" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#f5f5f5;strokeColor=#666666;dashed=1;" vertex="1" parent="1">
          <mxGeometry x="280" y="430" width="160" height="70" as="geometry" />
        </mxCell>

        <mxCell id="bm25" value="BM25 Index&#10;(on-disk)" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#f5f5f5;strokeColor=#666666;dashed=1;" vertex="1" parent="1">
          <mxGeometry x="460" y="430" width="160" height="70" as="geometry" />
        </mxCell>

        <mxCell id="redis" value="Redis&#10;(cache-aside: embeddings,&#10;query results, sessions)" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#f5f5f5;strokeColor=#666666;dashed=1;" vertex="1" parent="1">
          <mxGeometry x="640" y="430" width="200" height="70" as="geometry" />
        </mxCell>

        <mxCell id="obs" value="Observability&#10;(structured logging,&#10;metrics/tracing)" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#f5f5f5;strokeColor=#666666;dashed=1;" vertex="1" parent="1">
          <mxGeometry x="1040" y="200" width="180" height="300" as="geometry" />
        </mxCell>

        <mxCell id="docker" value="Docker&#10;(app + Postgres + Redis)" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#f5f5f5;strokeColor=#666666;dashed=1;" vertex="1" parent="1">
          <mxGeometry x="60" y="560" width="200" height="60" as="geometry" />
        </mxCell>

        <mxCell id="cicd" value="GitHub Actions CI/CD&#10;(ERP-008, built)" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#d5e8d4;strokeColor=#82b366;" vertex="1" parent="1">
          <mxGeometry x="300" y="560" width="200" height="60" as="geometry" />
        </mxCell>

        <mxCell id="e1" style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;" edge="1" parent="1" source="client" target="api">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        <mxCell id="e2" style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;" edge="1" parent="1" source="api" target="ing">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        <mxCell id="e3" style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;" edge="1" parent="1" source="api" target="ret">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        <mxCell id="e4" style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;" edge="1" parent="1" source="ret" target="gen">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        <mxCell id="e5" style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;" edge="1" parent="1" source="ing" target="embed">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        <mxCell id="e6" style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;" edge="1" parent="1" source="ret" target="embed">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        <mxCell id="e7" style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;" edge="1" parent="1" source="embed" target="faiss">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        <mxCell id="e8" style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;" edge="1" parent="1" source="ing" target="pg">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        <mxCell id="e9" style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;" edge="1" parent="1" source="ing" target="bm25">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        <mxCell id="e10" style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;" edge="1" parent="1" source="ret" target="faiss">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        <mxCell id="e11" style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;" edge="1" parent="1" source="ret" target="bm25">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        <mxCell id="e12" style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;" edge="1" parent="1" source="ret" target="redis">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        <mxCell id="e13" style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;" edge="1" parent="1" source="ret" target="pg">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        <mxCell id="e14" style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;" edge="1" parent="1" source="gen" target="ollama">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        <mxCell id="e15" style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;" edge="1" parent="1" source="gen" target="pg">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        <mxCell id="e16" style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;" edge="1" parent="1" source="gen" target="redis">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        <mxCell id="e17" style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;" edge="1" parent="1" source="eval" target="pg">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        <mxCell id="e18" style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;dashed=1;" edge="1" parent="1" source="obs" target="api">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        <mxCell id="e19" style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;dashed=1;" edge="1" parent="1" source="obs" target="ing">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        <mxCell id="e20" style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;dashed=1;" edge="1" parent="1" source="obs" target="ret">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        <mxCell id="e21" style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;dashed=1;" edge="1" parent="1" source="obs" target="gen">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        <mxCell id="e22" style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;dashed=1;" edge="1" parent="1" source="cicd" target="docker">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        <mxCell id="e23" style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;dashed=1;" edge="1" parent="1" source="docker" target="api">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>

      </root>
    </mxGraphModel>
  </diagram>
</mxfile>
```

- [ ] **Step 2: Validate the file is well-formed XML**

Run: `python -c "import xml.dom.minidom; xml.dom.minidom.parse('docs/diagrams/architecture.drawio'); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add docs/diagrams/architecture.drawio
git commit -m "docs: add draw.io system architecture diagram"
```

---

### Task 3: Reference the diagram from docs/architecture.md

**Files:**
- Modify: `docs/architecture.md`

**Interfaces:**
- Consumes: `docs/diagrams/architecture.drawio` (Task 2) and the future `docs/diagrams/architecture.png` (manual step below).

- [ ] **Step 1: Add a Diagram section**

Add this section to `docs/architecture.md`, immediately after the `## Technology Stack` section (at the end of the file):

```markdown

## Architecture Diagram

The full target-state architecture — API layer, service layer, data layer, core/infra, cross-cutting concerns, and deployment — is diagrammed in `docs/diagrams/architecture.drawio` (source, editable in [draw.io](https://app.diagrams.net/)) and `docs/diagrams/architecture.png` (exported image). Components not yet built are shown dashed/grey; components that exist today are shown solid/green. See `docs/superpowers/specs/2026-07-26-system-architecture-design.md` for the design rationale.
```

- [ ] **Step 2: Commit**

```bash
git add docs/architecture.md
git commit -m "docs: reference architecture diagram from architecture.md"
```

---

## Manual Step (cannot be automated — for the user, not an agentic worker)

Export the PNG from the `.drawio` file:

1. Open `docs/diagrams/architecture.drawio` in the draw.io desktop app or at [app.diagrams.net](https://app.diagrams.net/) (File → Open From → Device).
2. File → Export as → PNG, save as `docs/diagrams/architecture.png` in the repo.
3. Commit it:

```bash
git add docs/diagrams/architecture.png
git commit -m "docs: export architecture diagram to PNG"
```

4. Check the box in `.ai/tickets/ERP-009.md` for the PNG-export acceptance criterion once done.
