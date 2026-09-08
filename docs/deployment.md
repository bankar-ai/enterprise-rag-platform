# Deployment Notes

Operational guidance discovered through live deployment-readiness testing (2026-09-08) that
doesn't belong in `docs/architecture.md`'s design-level content. See ERP-034, ERP-035, ERP-037.

## Model tags must match exactly what's installed

Ollama resolves an untagged model name (e.g. `"qwen3"`) to an implicit `:latest` tag only -- it
does **not** fall back to whatever tag you actually pulled (e.g. `"qwen3:8b"`). If
`GENERATION_MODEL`/`EMBEDDING_MODEL` don't match an installed tag exactly, generation/embedding
calls fail on the first real request, not at startup.

Before deploying (or after installing/changing a model on the target host), run:

```
uv run python -m app.core.check_models
```

This checks both `GenerationSettings.model` and `EmbeddingSettings.model` against `ollama list` on
their configured hosts and exits non-zero if either is missing. Wire this into any deployment
script as a pre-flight check (see ERP-037) rather than discovering a mismatch from a live 500.

## Hardware / VRAM sizing

There is no universal safe number -- it depends on the exact model tag, its quantization, and the
context length in use. What's been verified live on a 6GB-VRAM / 16GB-system-RAM laptop:

- **`nomic-embed-text`** (137M params, ~274MB on disk): negligible footprint, runs comfortably
  alongside anything else.
- **`gemma3:4b`** (~3.3GB on disk, Q4_K_M): loads and runs reliably even under moderate concurrent
  system load. **Recommended default for constrained or shared hardware** -- verified live to give
  faithful, correctly-cited, non-hallucinated answers (see the 2026-09-08 session log).
- **`qwen3:8b`** (~5.2GB on disk, Q4_K_M): works on a 6GB-VRAM GPU, but only with real headroom
  free. Verified live to intermittently fail with out-of-memory errors -- both a CUDA VRAM
  allocation failure and a CPU-pinned-buffer allocation failure were observed on the same machine,
  at different times, purely as a function of how much RAM other running processes (IDE, browser,
  WSL/Docker backend, etc.) were using at that moment. The same model loaded and ran correctly once
  system RAM pressure eased -- this is **not deterministic given "enough total RAM" on paper**; it
  depends on actual concurrent load at call time.

**Practical implication**: don't assume a model that fits a GPU's VRAM on a spec sheet will reliably
load on a machine that's also running other things. If deploying on genuinely dedicated hardware
(nothing else running), sizing is closer to the spec-sheet numbers; if sharing hardware with a dev
environment or other workloads, prefer a smaller model with real headroom (`gemma3:4b` over
`qwen3:8b` on a 6GB card) or move generation to dedicated/serverless GPU hosting (see
`D:\github-projects\infrastructure-options.md`) rather than fighting for local resources.

## Cross-project infrastructure options

Hosting/compute/database/GPU choices for making this platform (and future sibling projects)
reachable for a bounded test window are tracked outside this repo, at
`D:\github-projects\infrastructure-options.md` (see ADR-008) -- not duplicated here.
