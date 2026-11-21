# ADR-0005: OpenAI Behind a Single Model Factory

**Status:** Accepted
**Date:** 2026-11-19
**Revised:** 2026-11-19 (task 003: GPT-5/6 model roles, reasoning effort instead of temperature, Responses API with `store=false`, `LLM_MODE=fake`); 2026-11-21 (task 003b: drift alarm is the weekly full eval)
**Deciders:** Nexus AI Team

---

## Context

v1 uses OpenAI only (developer decision). Model calls need consistent timeouts, retries, fallbacks, tracing callbacks, cost accounting, and the ability to swap models (or providers later) without touching graph code. OpenAI model names change frequently.

Facts that shaped the revision (OpenAI docs, checked 2026-11-19):
- `gpt-4.1` and `gpt-4.1-mini` are deprecated and shut down on 2027-01-14; `gpt-5`, `gpt-5-mini`, `gpt-5-nano` shut down on 2026-12-11.
- GPT-6 models accept `temperature` only at `reasoning_effort: none`; `gpt-6.2-*` models reject `none`. Reasoning tokens are billed as output.
- GPT-6 tool calling requires the Responses API; `langchain-openai` 1.8 switches to it automatically when tools are bound.
- GPT-6 models are alias-only (no dated snapshots); several GPT-5.x models have dated snapshots.

## Decision

All chat/embedding/moderation clients are created by `backend/app/llm/factory.py` (`get_model(role)`, `get_embeddings()`), configured from env. No other module instantiates provider clients (enforced by a ruff banned-import rule).

| Role | Default model | Reasoning effort | Notes |
|------|---------------|------------------|-------|
| `chat` | `gpt-6.1-terra` | `low` | M2 bake-off against pinned `gpt-5.4-mini-2026-03-17`; keep whichever meets G3 (TTFT), G4 (cost) and the tool-selection target |
| `chat` fallback | `gpt-6-luna` | `none` | via `.with_fallbacks()` on 5xx/timeout |
| `summarize`, `title`, `memory_extract` | `gpt-6-luna` | `none` | `memory_extract` uses strict structured output |
| `judge` (evals only) | `gpt-5.5-2026-04-23` | `medium` | pinned snapshot so judge calibration stays valid |
| `embedding` | `text-embedding-3-small` | — | 1536-d |
| `moderation` | `omni-moderation-latest` | — | |

- **Reasoning effort per role replaces temperature.** The factory sets `temperature` only when effort is `none`; otherwise it omits it.
- **Responses API everywhere** (`use_responses_api=True`) with **`store=false`**, so OpenAI does not retain responses. If multi-turn tool use needs reasoning items carried between calls, the factory requests encrypted reasoning content and the graph keeps it in checkpoint state (verify in TASK-009/010).
- **`LLM_MODE=fake`** makes the factory return a scripted fake model that replays responses (text, tool calls, approval interrupts) keyed by fixture id, with a configurable per-token delay. Unit tests, Playwright e2e and the load test all use it. The API refuses to start when `APP_ENV=production` and `LLM_MODE=fake`.
- Env: `LLM_MODE`, `LLM_CHAT_MODEL`, `LLM_CHAT_REASONING_EFFORT`, `LLM_FALLBACK_MODEL`, `LLM_BACKGROUND_MODEL`, `LLM_JUDGE_MODEL`, `LLM_EMBEDDING_MODEL`, `LLM_MODERATION_MODEL`.

## Consequences

### Positive
- One place for reliability policy, tracing, pricing and the fake model
- Model swaps are config + `make eval`, not code changes; a provider swap is a factory change

### Negative
- Alias-only GPT-6 models can change behaviour silently; mitigated by weekly full evals and a drift alert (LLM-T13)
- Model names and prices must be re-checked at the M2 bake-off

### Neutral
- Embedding model changes require re-indexing (versioned Qdrant collection, ADR-0003)

## Alternatives Considered

| Option | Reason rejected |
|--------|----------------|
| Keep `gpt-4.1` / `gpt-4.1-mini` | Shut down 2027-01-14 |
| `gpt-6.2-sol` for chat | OpenAI's default pick, but minimum effort `low` and 2× terra's price; kept as an upgrade path if the bake-off fails |
| Chat Completions API | GPT-6 tool calling is not available there |
| LiteLLM proxy for multi-provider | Out of scope for v1; adds a service |
| Direct client construction per module | Inconsistent timeouts/tracing; hard to swap models |

## References

- `docs/02_outputs/08_ai_system_design.md` §3
- `memory/ai.md`
