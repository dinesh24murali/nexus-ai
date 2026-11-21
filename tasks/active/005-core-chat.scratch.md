# Scratch: 005-core-chat (TASK-009 + TASK-010)

> Written before any code, per AGENTS.md §3.1. The developer asked to implement "the next 2 tasks" and stop, so I proceed after writing this; open questions below are decided with a stated default and flagged in the final report.

---

## Plan

TDD, one slice per step; each ends green on `make lint` + `make test`.

**TASK-009**
1. **Deps + config.** `uv add langchain-openai langchain-core langfuse tiktoken pyyaml` (+ dev `types-pyyaml`). Add the `LLM_*` settings from `.env.example` to `core/config.py` (models, efforts, timeout, retries, router/long_form, `openai_api_key`). Ruff `TID251` banned-api rule: provider clients (`openai`, `langchain_openai.ChatOpenAI`/`OpenAIEmbeddings`) only importable in `app/llm/factory.py`.
2. **Pricing** (`llm/pricing.py`): per-1M table (input / cached / output; output includes reasoning), `cost_usd(model, usage)`. Unknown model → most expensive known rate + warning (budgets must never under-count).
3. **Redaction** (`llm/redaction.py`): `mask()` for emails, phone numbers, card/IBAN-like numbers, JWTs, API keys; recursive over dict/list/str; never raises. `hash_user_id()` = HMAC-SHA256(`APP_SECRET`, user_id).
4. **Prompt loader** (`llm/prompts.py`): `backend/prompts/<name>.md`, front-matter `name` + integer `version` (+ extra keys such as `min_confidence`), strict `{{var}}` rendering (missing or unused variable raises), cached. Prompts: `nexus.system`, `nexus.context`, `nexus.summarize`.
5. **Fake model** (`llm/fake.py`): `FakeChatModel(BaseChatModel)`; stateless script selection (turn index = AI messages since the last human message, so agent→tool→agent loops and resumes work), fixture chosen by a `[[fixture:<id>]]` marker in the newest human message (default `default` = echo), JSON fixtures in `app/llm/fixtures/` (text, tool calls, usage), per-token delay, streaming + `bind_tools` + `with_structured_output`-compatible, deterministic usage metadata.
6. **Factory** (`llm/factory.py`): `get_model(role, *, tools=None, settings=None)`; Responses API, `store=False`, `include=["reasoning.encrypted_content"]` (if verified), per-role effort / max output / timeout / retries; `temperature` only when effort `none`; fallback via `.with_fallbacks()` (applied after tool binding); router has no retries/fallback; `LLM_MODE=fake` returns the fake model. `get_embeddings()` / moderation client are **not** built here (M3 / TASK-014 are their first users).
7. **Tracing** (`llm/tracing.py`): Langfuse v5 client with `mask`, callback handler factory, per-node spans enabled explicitly, run metadata (`name`, `session_id`, hashed `user_id`, tags, prompt name + version), eval mode = no per-node callbacks. No keys → no-op; export errors never reach a run.
8. **`scripts/sync_prompts.py`**: pushes prompts whose version changed to Langfuse (label `production`); dry-run mode; unit-tested against a stub client.
9. **Verify `store=false` question** from OpenAI docs + `langchain-openai` source (no API key available for a live probe); answer recorded in `memory/ai.md`.

**TASK-010**
10. **Token counting** (`llm/tokens.py`): `o200k_base` via tiktoken; if the encoding cannot be loaded (offline) fall back to chars/4 with one warning.
11. **State + nodes** (`graph/state.py`, `graph/nodes/{load_context,summarize,agent}.py`, `graph/builder.py`): `ChatState` (messages, summary, memories, tool_calls_this_turn); `load_context` resets per-run fields (memories stay empty until M5); `summarize` runs when history > 24k tokens, keeps the most recent ≤ 12k tokens aligned on a human message, never splits tool-call/ToolMessage pairs, removes the rest with `RemoveMessage`; `agent` assembles the request in the cache-friendly order and calls the chat model; `build_graph(checkpointer, *, chat_model, summarize_model)` with injectable models.
12. **Tests:** multi-turn thread, summarisation triggering, prefix byte-identical across consecutive turns (also across a summary), tool-call pair safety, plus one `integration` test with `AsyncPostgresSaver` (strict msgpack on).
13. **Wrap-up:** `memory/ai.md` (prompt registry, factory/tracing facts, `store=false` answer, deviations), `CHANGELOG.md`, `tests/coverage-baseline.json` (`app/llm`, `app/graph` gates = measured), `backend/AGENTS.md` structure, Dockerfile (`COPY prompts`, tiktoken cache baked at build), `.env.example` if new vars.

## Files to Create or Modify

| File | Action |
|------|--------|
| `backend/pyproject.toml`, `backend/uv.lock` | modify (deps, ruff banned-api, per-file ignore) |
| `backend/app/core/config.py` | modify (LLM settings) |
| `backend/app/llm/{__init__,pricing,redaction,prompts,fake,factory,tracing,tokens}.py` | create |
| `backend/app/llm/fixtures/*.json` | create |
| `backend/prompts/{nexus.system,nexus.context,nexus.summarize}.md` | create |
| `backend/app/graph/{__init__,state,builder}.py`, `backend/app/graph/nodes/{__init__,load_context,summarize,agent}.py` | create |
| `scripts/sync_prompts.py` | create |
| `backend/tests/llm/*`, `backend/tests/graph/*`, `backend/tests/test_sync_prompts.py` | create |
| `backend/Dockerfile` | modify (copy prompts, bake tiktoken cache) |
| `memory/ai.md`, `memory/_index.md` (only if wording changes), `CHANGELOG.md`, `tests/coverage-baseline.json`, `backend/AGENTS.md`, task file | modify |

## Uncertainties and the defaults I'm taking

- **U1 — Summarise at 24k vs history ≤ 12k (doc 08 §2.2 vs §3).** Trimming the model's view with a sliding window at 12k would change the prefix every turn between 12k and 24k and defeat caching (G4). Default: the agent sends the state's history unmodified (bounded by the 24k trigger); `summarize` keeps the most recent ≤ 12k tokens. So "history ≤ 12k" holds right after summarisation, not at every turn. Recorded in `memory/ai.md`; the developer may prefer a lower trigger.
- **U2 — Late context / summary message roles.** Both are `HumanMessage`s wrapping data in tags, not system messages: memories and summaries derive from user-authored text and must not gain system authority (LLM-T-untrusted-data rule).
- **U3 — Cache prefix is one turn behind by design.** Because the late context message sits before the newest user message, turn N's cached prefix ends where context_N began; turn N+1 reuses everything before it. The test asserts exactly that property (prefix up to the late context message is a byte-prefix of the next turn's).
- **U4 — `nexus.system` content.** Doc 08 lists tool-routing, PDF/research, `/memory` capabilities that don't exist yet. A prompt that advertises them would make the model claim missing capabilities, so v1 describes only what exists (chat; no tools; memory is saved automatically; scope rule; untrusted-data policy; no offers for other modes). Later tasks add sections with a version bump.
- **U5 — Fake-model fixture selection** via a marker in the user message (`[[fixture:id]]`). It works through the real HTTP path for Playwright without extra plumbing, and the fake mode is refused in production.
- **U6 — `store=false` + reasoning items.** Expected: required (reasoning items must travel with function calls, via `include=["reasoning.encrypted_content"]`, preserved in the AIMessage content, hence in the checkpoint). Will confirm from docs/source; cannot live-probe (empty `OPENAI_API_KEY` in `.env`).
- **U7 — Langfuse v5 API** is newer than my reference knowledge; I'll read the installed package rather than assume. If the per-node-span behaviour differs from the docs I'll record it.
- **U8 — `CHAT_MODEL_OVERRIDE` kill switch** is a runtime flag (async, DB-backed). The factory stays sync/pure: it accepts a `model_override` argument; the caller (graph/run service, TASK-011) passes the flag value. Not wired in this pass.

## What Will Be Skipped (and why)

- SSE endpoint, run lease, regenerate/cancel — TASK-011. Messages read model and per-message usage persistence — TASK-011 (pricing helper exists, nothing calls it yet).
- Tools, approval node, tool cap, citations state — M3/M4. The builder keeps a single seam (`agent → END`).
- `route` state field and intent router — M7.
- Embeddings and moderation clients — first users are TASK-025 / TASK-014.
- Eval harness and datasets — TASK-015; `make eval` is not runnable, noted honestly in the task file.
- Real model calls, live Langfuse traces — no API key; Langfuse keys exist locally but no trace is sent from tests.
- `nexus.title` prompt — TASK-012.

## Risks

- **langchain-openai API drift** (`use_responses_api`, `reasoning`, `max_tokens` → `max_output_tokens`, `include`): read the installed source and assert the exact request payload in tests, so wrong parameters fail locally rather than at the first real call.
- **tiktoken downloads its encoding on first use** → offline CI/containers. Mitigated by the fallback counter and baking the cache into the image.
- **Strict msgpack** may reject message types in the Postgres checkpointer; the integration test covers it.
- **Prefix stability** is easy to break silently (timestamps, dict ordering); the byte-identity test guards it.
- Blast radius: new modules only; existing API/auth code is untouched apart from `config.py`.

## Ready to implement?

Yes (per the developer's instruction); defaults above are flagged for review.

## Outcome (2026-11-21)

Implemented as planned. Resolutions: U1 kept as stated (agent sends unmodified state; documented in `memory/ai.md` and doc 08 §3). U3 confirmed by test. U6 answered yes (source + docs, no live probe). U7: Langfuse v5 exports node spans by default; made explicit and tested. U8 not wired (as planned). Dockerfile also copies `prompts/` and bakes the tiktoken cache. Embeddings, moderation, title prompt, tools, route state remain skipped as listed.
