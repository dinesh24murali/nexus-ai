# Memory: AI

> Domain knowledge about the AI subsystem: models, prompts, graph, retrieval, evals.
> Update whenever a model, prompt version, graph topology or retrieval setting changes.
> Full design: `docs/02_outputs/08_ai_system_design.md`.

---

## Models in Use

| Role | Model | Notes |
|------|-------|-------|
| chat (agent) | `gpt-4.1` | temp 0.3, max 2048 out, 60 s timeout, fallback `gpt-4.1-mini` |
| summarize / title / memory_extract | `gpt-4.1-mini` | temp 0–0.2; memory_extract uses strict structured output |
| judge (evals only) | `gpt-4.1` | temp 0 |
| embeddings | `text-embedding-3-small` | 1536-d; changing it requires re-index (ADR-0003) |
| moderation | `omni-moderation-latest` | fail-open on outage (logged + tagged) |

All created via `backend/app/llm/factory.py` (ADR-0005). Names come from env — verify against OpenAI's current lineup before launch.

## Prompt Registry

Prompts live in Langfuse (label `production`) with a version-pinned fallback in `backend/prompts/<name>.md`. Never inline prompt text in code.

| Prompt name | Current version / label | Used by | Notes |
|-------------|------------------------|---------|-------|
| `nexus.system` | not yet created | `agent` node | untrusted-data policy, `[n]` citations |
| `nexus.summarize` | not yet created | `summarize` node | |
| `nexus.title` | not yet created | `generate_title` job | ≤ 6 words |
| `nexus.memory_extract` | not yet created | `extract_memories` job | sensitive-category deny-list |
| `eval.judge.faithfulness` / `relevance` / `injection` | not yet created | evals | κ ≥ 0.7 vs human labels before gating |

## Orchestration Graph

- **Framework:** `LangGraph`
- **Topology:** `load_context → (summarize?) → agent ⇄ [approval →] tools → agent → END`; max 6 tool calls/turn
- **Checkpointer / thread-id strategy:** `AsyncPostgresSaver`, `thread_id = conversation_id`; regenerate forks from the checkpoint before the last answer
- **Interrupt (HITL) points:** `approval` node before every `web_search` call; pending interrupt mirrored to `conversations.pending_interrupt`, expires after 24 h
- **Tenant context:** `user_id` comes from `config["configurable"]`, never from model/tool args

## Retrieval

- **Vector DB:** `Qdrant`, collection alias `documents`
- **Embedding model / dimensions:** `text-embedding-3-small`, 1536, cosine
- **Chunking:** 800 tokens / 120 overlap (`o200k_base`), never across PDF pages
- **Top-k / rerank:** search 8 (score ≥ 0.25) → dedupe → top 5, ≤ 3k tokens; no reranker in v1
- **Tenant isolation filter:** `user_id` payload (tenant index) added in retriever code

## Long-term Memory

- `AsyncPostgresStore`, namespace `("memories", user_id)`, embedding index on `content`
- Read: top 8 relevant + 5 recent preferences, ≤ 600 tokens, as `<user_memory>` data
- Write: `extract_memories` job after each turn (≤ 3 ops), `remember_fact` tool; 200-item cap

## Eval Status

Thresholds: `evals/eval-baseline.json` (22 metrics from `docs/02_outputs/09_eval_plan.md` §3). No eval suite yet (task 005).

| Metric | Baseline | Last result | Date |
|--------|----------|-------------|------|
| `rag.faithfulness` | 0.85 | — | — |
| `tools.selection_accuracy` | 0.90 | — | — |
| `safety.injection_resistance` | 0.95 | — | — |
| `safety.cross_tenant_leaks` | 0 | — | — |

## Known Failure Modes & Gotchas

- SSE needs unbuffered proxies (Caddy `flush_interval -1`, `X-Accel-Buffering: no`)
- Langfuse first boot takes ~60 s (ClickHouse migrations); tracing must be best-effort
- Threat IDs `LLM-T01…T16` are defined in `docs/02_outputs/10_llm_threat_model.md`; red-team cases reference them
