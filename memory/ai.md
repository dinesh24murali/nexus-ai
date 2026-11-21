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

- All created via `backend/app/llm/factory.py` (ADR-0005; a ruff `TID251` rule bans constructing provider clients anywhere else); names and efforts from env. `LLM_MODE=fake` gives the scripted fake model (refused in production). `get_model(role, tools=…, model_override=…)` binds tools to both primary and fallback and attaches the fallback last (`with_fallbacks` has no `bind_tools`); fallback triggers only on timeout / connection / 5xx errors; `router` has no retries and no fallback. The `CHAT_MODEL_OVERRIDE` kill switch is read per run from runtime flags by `RunService` and passed as `model_override` (graphs are cached per override value). Embeddings and moderation clients are not built yet (first users: TASK-025 / TASK-014).
- **Fake model** (`app/llm/fake.py`): the fixture is picked by a `[[fixture:<id>]]` marker in the newest human message (default: echo `Fake reply: <text>`); fixtures are JSON in `app/llm/fixtures/` (`long`, `web_search`); the script turn is derived from the message history (AI messages since the newest human message), never from instance state. `summarize`/`title` roles return fixed text (`FAKE-SUMMARY`, `Fake title`). Approval interrupts come from a fixture returning a tool call for a tool that needs approval.
- **Reasoning effort replaces temperature**: GPT-6 accepts `temperature` only at effort `none`; `gpt-6.2-*` rejects `none`.
- **Responses API everywhere, `store=false`.** GPT-6 tool calling needs it.
- **`store=false` + reasoning models: encrypted reasoning items ARE needed (verified 2026-11-21 from `langchain-openai` 1.8.5 source and OpenAI docs/forum; no live API probe, `OPENAI_API_KEY` was empty).** With `store=false` the server keeps no reasoning items, so a tool loop must send the model's reasoning item back with its function call, and only the encrypted form can be replayed. `langchain-openai` drops reasoning blocks that lack `encrypted_content` when `store=False` and does **not** add the `include` itself, so the factory sets `include=["reasoning.encrypted_content"]` for every role with effort ≠ `none`. The default `output_version` (`responses/v1`) keeps those blocks in `AIMessage.content` (a list of blocks, not a string), so they travel in graph state and the checkpoint (test: `tests/graph/test_checkpointer.py`). **Consequences:** read text with `message.text`, never `message.content`; never strip content blocks when trimming/summarising; the first real tool-calling run (M3/M4 evals) must confirm there is no "function_call without its reasoning item" error.
- Reasoning tokens bill as output and add latency.
- Do not use: `gpt-4.1*` (shut down 2027-01-14), `gpt-5`/`-mini`/`-nano` (2026-12-11), `gpt-5.1`, `gpt-5.4-nano` (2027-04-01).
- Prices per 1M (input / cached / output): terra $1 / $0.05 / $5; gpt-5.4-mini $0.75 / $0.075 / $4.50; luna $0.10 / $0.01 / $0.50; gpt-5.5 $5 / $0.50 / $30; gpt-6.2-sol (upgrade path) $2 / $0.10 / $10.

## Prompt Registry

Prompts are canonical in `backend/prompts/<name>.md` (front-matter `name`, `version`), loaded from disk; CI mirrors merged changes to Langfuse (label `production`). Never inline prompt text in code; never edit prompts in the Langfuse UI except for throwaway experiments (ADR-0008).

| Prompt name | Current version | Used by | Notes |
|-------------|-----------------|---------|-------|
| `nexus.system` | **1** | `agent` node | fixed text (no per-turn variables, for caching). v1 covers style, what Nexus can do **today** (chat only: no tools, no files, no web), memory-is-automatic rule, scope rule, untrusted-data policy, no offers for other modes. **Still to add (with a version bump) as features land:** `[n]` citations and tool-routing rules (M3/M4), capability paragraph + slash commands (M7) |
| `nexus.context` | **1** | `agent` node (late context message) | `today`, `user_display_name`, `<user_memory>` block, `<recent_files>` block (renders `(none)` until M3); never stored in state; sent as a human message wrapping data in `<context>` tags |
| `nexus.summarize` | **1** | `summarize` node | variables `existing_summary`, `messages` (a transcript, wrapped in `<messages>` as data); ≤ 400 words out |
| `nexus.title` | **1** | `generate_title` job | variables `first_user_message`, `first_answer` (each cut to 1000 chars, wrapped as data); ≤ 6 words, same language as the user. Output is cleaned in `jobs/handlers.py::clean_title` (quotes, `Title:` prefix, trailing punctuation, control chars, ≤ 100 chars) and only applied while the conversation still has the default title |
| `nexus.memory_extract` | not yet created | `extract_memories` job | sensitive-category deny-list |
| `nexus.route` (v1.1) | not yet created | `route` node | strict JSON `RouteDecision` (4 intents); input = last 3 user messages + previous intent only; front-matter `min_confidence` |
| `nexus.document` (v1.1) | not yet created | `generate_document` | JSON `DocumentDraft` |
| `nexus.research_plan` / `nexus.research_report` (v1.1) | not yet created | `deep_research` | 3–6 queries; cited report ≤ 1,500 words |
| `eval.judge.faithfulness` / `relevance` | not yet created | evals | only faithfulness is calibrated in v1 (30 labels, κ ≥ 0.7) |

## Orchestration Graph

- **Framework:** `LangGraph` 1.x
- **Topology (v1):** `load_context → (summarize?) → agent ⇄ [approval →] tools → agent → END`; max 6 tool calls/turn
- **Topology (v1.1, ADR-0010):** slash-command parser (no model) or `load_context ∥ route` → `dispatch` → {`general_chat` (= v1 agent), `manage_memory`, `generate_document`, `deep_research`}; confidence < `min_confidence` / error → `general_chat`; `/help` is fixed text; resume never re-routes; kill switches `ROUTER_DISABLED`, `INTENTS_DISABLED` (runtime flags). Schedules and `smalltalk_help` were cut in task 003b (schedules → PRD backlog)
- **Prompt layout (cache-friendly):** system → `<summary>` → history → late context message (memories, date) → newest user message. Built in `app/graph/prompting.py`; the summary and context messages are **human** messages wrapping data, so user-derived text never gains system authority. A test asserts the prefix (everything before the late context message) is byte-identical across consecutive turns and after a summary is written. Because the context message sits before the newest user message, a turn's cacheable prefix ends where the previous turn's context began: reuse is one turn behind by design
- **Implemented graph (TASK-010):** `START → load_context → (history > 24k tokens ? summarize) → agent → END`, no tools yet. `load_context` resets `memories` and `tool_calls_this_turn`. `summarize` keeps the most recent ≤ 12k tokens starting on a human message (a turn is never split, so no orphan tool results; the newest user message is always kept) and folds the rest into `summary`; if the summariser fails, chat continues with the full history. **Deviation from doc 08 §3:** the agent sends the state history unmodified (no sliding 12k window): a window would change the prefix every turn between 12k and 24k tokens and defeat caching, so "history ≤ 12k" holds right after summarisation and grows to 24k before the next one. Token counts use tiktoken `o200k_base` (falls back to chars/4 if the encoding cannot be loaded; the Docker image bakes it in)
- **Streaming (TASK-011):** `RunService` iterates `graph.astream(stream_mode="messages")` and keeps only chunks from node `agent` (the summariser is tagged `nostream` as a second line of defence). A model that does not stream is sent as one `token`. Usage and cost come from `llm/usage.py::UsageCollector` attached to the run config: it sees EVERY model call of the run (summariser, fallback model) and prices each per model; a call that reports no model name is billed to the chat model (the fake model reports none)
- **Cancel / reaper and the thread:** cancel appends the partial answer with `aupdate_state(as_node="agent")` (only if there is text). The reaper does NOT touch the checkpoint (deviation from doc 08 §2.3, now corrected there): the text a crashed process had streamed is gone, and an empty AI message in the thread is worse than none; the next turn simply sees two user messages in a row (tested with a real SIGKILL)
- **Checkpointer / thread-id strategy:** `AsyncPostgresSaver` (psycopg 3), `thread_id = conversation_id`; summary lives only in graph state; regenerate forks from the checkpoint before the last answer
- **Interrupt (HITL) points:** `TOOL_POLICY` — `web_search` every call (a message with mixed calls interrupts before any runs; all run after the decision); v1.1: `research_plan` once per research run (only approved queries run), `forget_memory` before every memory delete by chat (1 clear match or up to 3 candidates); pending interrupt mirrored to `conversations.pending_interrupt`, expires after 24 h
- **Tool cap:** 6 individual calls per turn; calls beyond it get "tool limit reached"
- **Run lease:** `active_run_id` + `run_heartbeat_at` (10 s); worker `reap_stale_runs` (30 s, stale after 60 s) writes a `failed` message (`finish_reason=server_restart`, empty content); graceful drain 90 s on SIGTERM (verified on the Compose stack: a stream open at SIGTERM finished and the container exited 0 in ~8 s; uvicorn runs with `--timeout-graceful-shutdown 105`)
- **Tenant context:** `user_id` comes from `config["configurable"]`, never from model/tool args

## Background Jobs (TASK-012)

- Postgres queue in `app/jobs/queue.py`: claim = `SELECT … FOR UPDATE SKIP LOCKED` (oldest `run_after` first); every later transition (`complete`/`fail`/`release`/`heartbeat`) is conditional on `locked_by`, so a worker whose job was requeued cannot finish someone else's run. Retry backoff 10 s, 20 s, 40 s … capped at 2 min; `enqueue` joins the caller's transaction and is idempotent on the key
- Worker (`app/worker.py`): 4 claim loops, 1 s poll, only claims kinds it has a handler for; per-job heartbeat refreshes `locked_at` every 30 s; hard per-kind timeout (`generate_title` 120 s, 2 attempts); a worker that finds it no longer holds its job cancels the handler and writes nothing; on SIGTERM it stops claiming, waits up to 100 s, then releases running jobs back to the queue with the attempt refunded (Compose grace is 120 s)
- Periodic (`jobs/periodic.py`, idempotent, safe in several workers): `reap_stale_runs` 30 s, `requeue_stale_jobs` 30 s (running job without a heartbeat for 2 min; one that used all its attempts fails instead), `cleanup_invites` daily (UNUSED invites expired/revoked > 30 days; used ones stay as the join record), `cleanup_rate_limits` daily (> 1 day old)
- Jobs call models only through the factory (`JobContext.model(role)`), are traced as `job.generate_title`, and record their spend in `usage_daily` with `requests=0`
- Not built yet: `extract_memories`, `ingest_file`, `delete_file_vectors`, `purge_user`, `render_document`, `cleanup_documents`. The `jobs` table is never pruned (one `generate_title` row per conversation so far)
- Tests that wait for time are scaled (0.1 s heartbeat, 0.5 s stale window, 1.5 s job) because a real 5-minute job cannot run in a unit test; `test_without_a_heartbeat_the_same_job_would_run_twice` is the negative control proving that test can fail

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
- Langfuse SDK v5 (5.2.0) default export filter keeps every span created by its own tracer, so LangGraph node spans from `langfuse.langchain.CallbackHandler` are exported; `llm/tracing.py` still states the filter explicitly (`should_export_span`) and a test asserts a node span (`agent`) and a generation both arrive. The client is a per-public-key singleton; metadata values are JSON-encoded once (pass dicts, not JSON strings); `propagate_attributes` limits (ASCII ids ≤ 200 chars, metadata values ≤ 200 chars) apply. Trace fields come from RunnableConfig metadata keys `langfuse_user_id`, `langfuse_session_id`, `langfuse_trace_name`; prompt name + version go in trace metadata `prompt_versions` (not Langfuse prompt linking, since prompts are not fetched from Langfuse)
- Tracing helpers: `app/llm/tracing.py` (`Tracer.run_config(RunTrace, mode="run"|"eval")`; eval mode attaches no per-node callbacks), `app/llm/redaction.py` (`mask` for emails, phones, Luhn-valid cards, IBANs, JWTs, API keys; `hash_user_id` = HMAC-SHA256 keyed with `APP_SECRET`, 32 hex chars), `app/llm/pricing.py` (Decimal; unknown models priced at the highest known rate so budgets never under-count), `scripts/sync_prompts.py` (fails when a prompt's text changed without a `version` bump)
- Alias-only GPT-6 models can change silently; the **weekly** full eval is the drift alarm (up to a week of lag)
- Eval runs send one trace per case (no per-node spans); the CI Langfuse project is in its own organisation (Hobby quota)
- Threat IDs `LLM-T01…T20` (T19 retired with schedules) are defined in `docs/02_outputs/10_llm_threat_model.md`; red-team cases reference them
