# Memory: AI

> Domain knowledge about the AI subsystem: models, prompts, graph, retrieval, evals.
> Update whenever a model, prompt version, graph topology or retrieval setting changes.
> Full design: `docs/02_outputs/08_ai_system_design.md`.

---

## Models in Use

Checked against OpenAI docs on 2026-11-19; re-check at the M2 bake-off (TASK-036).

| Role | Model | Notes |
|------|-------|-------|
| chat (agent) | `gpt-6.1-terra` (alias-only) | effort `low`, max output 4096 incl. reasoning, 60 s timeout; **bake-off** vs pinned `gpt-5.4-mini-2026-03-17` |
| chat fallback | `gpt-6-luna` | effort `none` |
| summarize / title / memory_extract | `gpt-6-luna` | effort `none` (temperature allowed only at `none`); memory_extract uses strict structured output |
| judge (evals only) | `gpt-5.5-2026-04-23` (pinned) | effort `medium`; pinned so judge calibration stays valid |
| embeddings | `text-embedding-3-small` | 1536-d; changing it requires re-index (ADR-0003) |
| moderation | `omni-moderation-latest` | **fail-open** on outage (logged + tagged) |
| router (v1.1) | `gpt-6-luna` | effort `none`, max 150, 5 s, **no retries/fallback model: any failure → `general_chat`**; threshold `min_confidence` in `nexus.route.md` front-matter (starts 0.70, tuned on the first reviewed `router.intents` run) |
| long_form (v1.1) | `gpt-6.1-terra` | effort `medium`, max 4000, 120 s; document drafts + research reports; fallback luna; limits keep worst case ≤ $0.05/run (research plan on `chat` with max 1000) |

- All created via `backend/app/llm/factory.py` (ADR-0005); names and efforts from env. `LLM_MODE=fake` gives the scripted fake model (refused in production).
- **Reasoning effort replaces temperature**: GPT-6 accepts `temperature` only at effort `none`; `gpt-6.2-*` rejects `none`.
- **Responses API everywhere, `store=false`.** GPT-6 tool calling needs it. Open: whether multi-turn tool calls need encrypted reasoning items in checkpoint state (TASK-009/010).
- Reasoning tokens bill as output and add latency.
- Do not use: `gpt-4.1*` (shut down 2027-01-14), `gpt-5`/`-mini`/`-nano` (2026-12-11), `gpt-5.1`, `gpt-5.4-nano` (2027-04-01).
- Prices per 1M (input / cached / output): terra $1 / $0.05 / $5; gpt-5.4-mini $0.75 / $0.075 / $4.50; luna $0.10 / $0.01 / $0.50; gpt-5.5 $5 / $0.50 / $30; gpt-6.2-sol (upgrade path) $2 / $0.10 / $10.

## Prompt Registry

Prompts are canonical in `backend/prompts/<name>.md` (front-matter `name`, `version`), loaded from disk; CI mirrors merged changes to Langfuse (label `production`). Never inline prompt text in code; never edit prompts in the Langfuse UI except for throwaway experiments (ADR-0008).

| Prompt name | Current version | Used by | Notes |
|-------------|-----------------|---------|-------|
| `nexus.system` | not yet created | `agent` node | fixed text (no per-turn variables, for caching); untrusted-data policy, `[n]` citations, capability paragraph + slash commands, scope rule (other users/platform), tool-routing rules, never claim memory writes, (v1.1) never offer PDFs/research |
| `nexus.context` | not yet created | `agent` node (late context message) | `today`, `user_display_name`, `<user_memory>` block, 5 most recent ready files (id, filename, uploaded_at); never stored in state |
| `nexus.summarize` | not yet created | `summarize` node | |
| `nexus.title` | not yet created | `generate_title` job | ≤ 6 words |
| `nexus.memory_extract` | not yet created | `extract_memories` job | sensitive-category deny-list |
| `nexus.route` (v1.1) | not yet created | `route` node | strict JSON `RouteDecision` (4 intents); input = last 3 user messages + previous intent only; front-matter `min_confidence` |
| `nexus.document` (v1.1) | not yet created | `generate_document` | JSON `DocumentDraft` |
| `nexus.research_plan` / `nexus.research_report` (v1.1) | not yet created | `deep_research` | 3–6 queries; cited report ≤ 1,500 words |
| `eval.judge.faithfulness` / `relevance` | not yet created | evals | only faithfulness is calibrated in v1 (30 labels, κ ≥ 0.7) |

## Orchestration Graph

- **Framework:** `LangGraph` 1.x
- **Topology (v1):** `load_context → (summarize?) → agent ⇄ [approval →] tools → agent → END`; max 6 tool calls/turn
- **Topology (v1.1, ADR-0010):** slash-command parser (no model) or `load_context ∥ route` → `dispatch` → {`general_chat` (= v1 agent), `manage_memory`, `generate_document`, `deep_research`}; confidence < `min_confidence` / error → `general_chat`; `/help` is fixed text; resume never re-routes; kill switches `ROUTER_DISABLED`, `INTENTS_DISABLED` (runtime flags). Schedules and `smalltalk_help` were cut in task 003b (schedules → PRD backlog)
- **Prompt layout (cache-friendly):** system → `<summary>` → history → late context message (memories, date) → newest user message
- **Checkpointer / thread-id strategy:** `AsyncPostgresSaver` (psycopg 3), `thread_id = conversation_id`; summary lives only in graph state; regenerate forks from the checkpoint before the last answer
- **Interrupt (HITL) points:** `TOOL_POLICY` — `web_search` every call (a message with mixed calls interrupts before any runs; all run after the decision); v1.1: `research_plan` once per research run (only approved queries run), `forget_memory` before every memory delete by chat (1 clear match or up to 3 candidates); pending interrupt mirrored to `conversations.pending_interrupt`, expires after 24 h
- **Tool cap:** 6 individual calls per turn; calls beyond it get "tool limit reached"
- **Run lease:** `active_run_id` + `run_heartbeat_at` (10 s); worker `reap_stale_runs` (30 s, stale after 60 s) writes a `failed` message (`finish_reason=server_restart`); graceful drain 90 s on SIGTERM
- **Tenant context:** `user_id` comes from `config["configurable"]`, never from model/tool args

## Retrieval

- **Vector DB:** `Qdrant` 1.x, collection alias `documents`, queried with `query_points`
- **Embedding model / dimensions:** `text-embedding-3-small`, 1536, cosine
- **Chunking:** 800 tokens / 120 overlap (`o200k_base`), never across PDF pages
- **Top-k / rerank:** search 8 (score ≥ 0.25) → dedupe → top 5, ≤ 3k tokens; no reranker in v1
- **Tenant isolation filter:** `user_id` payload (tenant index) added in retriever code

## Long-term Memory

- `AsyncPostgresStore`, namespace `("memories", user_id)`, embedding index on `content`
- Read: top 8 relevant + 5 recent preferences, ≤ 600 tokens, as `<user_memory>` data in the late context message
- Write: `extract_memories` job after each turn (≤ 3 ops; user message is the only source; skipped on `manage_memory` turns; `create` dropped at ≥ 0.9 similarity to an existing memory) and, from v1.1, `manage_memory` save/forget. No memory-writing tool in `general_chat` (`remember_fact` and its taint rule removed in task 003b); 200-item cap

## Budgets & Cost

- Per-user daily spend budget $0.50 (env), global daily cap $5 (env), 30 msg/min; background jobs count toward the user's spend
- G4 target < $0.01/message is reachable only with prompt caching working — track `ops.cost_per_turn_usd` and `ops.cached_input_ratio`
- v1.1: G4 covers chat messages only; research (≈ $0.043) and document (≈ $0.037) runs are capped at ≤ $0.05 **by construction** (token limits), pre-checked against the user budget and the global cap, never cut off mid-run; `last_answer` documents ≈ $0; 10 documents/user/day

## Eval Status

Two-tier gate (ADR-0009): `evals/eval-baseline.json` → `targets` (from doc 09 §3) + `metrics` (enforced gates; empty until the first suite in task 006). Injection resistance is scored by deterministic canaries, not a judge.

| Metric | Target | Gate | Last result | Date |
|--------|--------|------|-------------|------|
| `rag.faithfulness` | 0.85 | advisory until judge calibrated | — | — |
| `tools.selection_accuracy` | 0.90 | regression (measured) | — | — |
| `safety.injection_resistance` | 0.95 | absolute | — | — |
| `safety.cross_tenant_leaks` | 0 | absolute | — | — |
| `router.intent_accuracy` (v1.1) | 0.92 | regression (measured) | — | — |
| `documents.external_fetches` / `research.unapproved_queries` (v1.1) | 0 | absolute | — | — |

## Known Failure Modes & Gotchas

- SSE needs unbuffered proxies (Caddy `flush_interval -1`, `X-Accel-Buffering: no`)
- Langfuse SDK v4+ filters non-LLM spans by default — enable per-node spans explicitly (TASK-009)
- Alias-only GPT-6 models can change silently; the **weekly** full eval is the drift alarm (up to a week of lag)
- Eval runs send one trace per case (no per-node spans); the CI Langfuse project is in its own organisation (Hobby quota)
- Threat IDs `LLM-T01…T20` (T19 retired with schedules) are defined in `docs/02_outputs/10_llm_threat_model.md`; red-team cases reference them
