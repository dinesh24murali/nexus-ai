# Scratch: 038-router-dispatch

> Written before any code (AGENTS.md 3.1). Scope: TASK-038 only; TASK-039 has its own scratch file and builds on this.

## Plan

1. **Slash-command parser** `backend/app/graph/commands.py` (pure, no I/O). `parse_command(text)` returns `ParsedCommand(name, intent, slots)`, `StaticReply(kind, text)` (for `/help` and usage replies) or `None` (not a command). Rules: only at the very start; case-insensitive; the command word must end at whitespace or the end (`/pdfx`, `/etc/hosts` are not commands and go to the model); a known command with a bad shape (`/pdf foo`, `/brief` with no topic, `/memory foo`, topic > 200 or memory text > 500 chars) gets a short usage reply. Table: doc 08 §2.6.
2. **Router** `backend/app/graph/router.py`: `RouteSlots`/`RouteDecision` (strict-schema friendly: `extra=forbid`, every field present, nullable), `route_input()` (last 3 user messages + previous intent, nothing else), `classify()` (model call with a 5 s `wait_for`, strict structured output), `clean_slots()` (per-intent validation, drops slots that do not belong), and `make_router_node()` which writes `state["route"]` and emits a `route` custom event. Order: command from config (no model) → `ROUTER_DISABLED` → model → low confidence (`min_confidence` from `nexus.route` front-matter, 0.70) / error / timeout / invalid output / unavailable or disabled intent → `general_chat` with source `fallback`. The node is called `router` (a node may not share a name with the state key `route`).
3. **Prompt** `backend/prompts/nexus.route.md` v1: the 4 intents, slot rules, the compound-request precedence, follow-ups (`make that a PDF` → `last_answer`), casual mention vs explicit memory request, "when unsure choose general_chat"; the user messages are data inside tags.
4. **Graph** `builder.py`: `START → load_context` and `START → router` in parallel, both join in a no-op `dispatch` node whose conditional edge sends `manage_memory` to its subgraph (TASK-039) and everything else to the existing summarize/agent loop. `ChatState` gains `route`. The existing regenerate lookup (checkpoint whose next is `agent`) still works. Resume never re-routes (LangGraph resumes inside the subgraph).
5. **Pipelines that exist**: `graph/intents/__init__.py::PIPELINES = {general_chat, manage_memory}`. `generate_document` / `deep_research` are named by the router but unavailable until TASK-041/043: they fall back to `general_chat` (source `fallback`); a slash command for them or for an intent in `INTENTS_DISABLED` gets a short explanation (no model call).
6. **Run service** (`services/runs.py`): parse the command in `start_send` (after moderation, before the lease); `/help` and usage/explanation replies run through the normal run machinery with `static_reply` (lease, user + assistant message stored, title job, `usage` + `done`) but no graph, no checkpoint, no router, no model; read `ROUTER_DISABLED` and `INTENTS_DISABLED` per run and pass `router_disabled`, `disabled_intents`, `command` in the trusted run config; turn the `route` custom event into the `route_selected` SSE event and remember `intent` / `route_source` on the run; store them on the assistant message (also after a resume: read from the state); skip `extract_memories` for `manage_memory` turns and static replies; regenerate is refused (409 `REGENERATE_NOT_SUPPORTED`) when the last answer is not a `general_chat` one; add `nexus.route` to the traced prompts; trace tags `route:command|disabled` and `intent:<x>` where known before the graph runs (a router decision shows in the `router` node span: the Langfuse tags are fixed when the run starts).
7. **SSE + API**: `RouteSelectedEvent` (and `MemoryUpdatedEvent` for TASK-039) in `api/sse.py`, validated against the spec by the existing `test_sse.py`; `MessageOut.intent` from the new column. Migration `0003_message_route`: `messages.intent`, `messages.route_source` (nullable, CHECK on the allowed values).
8. **Fake model**: `role == "router"` in `llm/fake.py`: default `general_chat` 0.95; `[[route:<intent>]]` / `[[route:<intent>:<conf>]]` in the newest user message picks the intent, `[[slot:key=value]]` fills slots, `[[route:invalid]]` returns invalid JSON, `[[route:slow]]` outlives the timeout.
9. **Evals**: `router.intents` dataset (120 records, 24 smoke, 4 intents; AI-drafted, developer to review) + `RouterCase` in `backend/evals/datasets.py` + `backend/evals/router.py` (component eval: one router call per case; `router.intent_accuracy`, `router.macro_f1`, `router.slot_accuracy`, `router.fallback_rate`, `router.latency_p95_ms`; confusion matrix and mean confidence for correct vs wrong in the case notes) wired into `evals/run.py`; `evals/eval-baseline.json` gets the 5 `targets` rows (informational, no gates: ADR-0009 records gates only from a reviewed real run). rt-22 record in `safety.redteam` + the canaries it needs (`must_route`, `forbidden_interrupts`) and the tool runner reading the route.
10. **Docs / memory**: `memory/ai.md` (router, commands, fallback rules, flags), `memory/api.md` (route_selected, static replies, regenerate rule, new columns), backend `AGENTS.md` structure lines, doc 10 / threat model reference rows if needed, `CHANGELOG.md`, `tasks/deferred.md` (D29), `tests/coverage-baseline.json` unchanged (no new package: `graph` gate 95 must hold).

## Files to Create or Modify

| File | Action | Notes |
|------|--------|-------|
| `backend/app/graph/commands.py` | create | parser |
| `backend/app/graph/router.py` | create | models, input, classify, node |
| `backend/app/graph/intents/__init__.py` | create | `PIPELINES` |
| `backend/app/graph/builder.py`, `state.py` | modify | parallel start, dispatch, `route` |
| `backend/prompts/nexus.route.md` | create | v1 |
| `backend/app/services/runs.py` | modify | commands, flags, events, storage, regenerate rule |
| `backend/app/api/sse.py`, `api/schemas.py`, `api/chat.py` | modify | events, `intent` |
| `backend/app/db/models.py`, `db/repositories/messages.py`, `alembic/versions/0003_*.py` | modify/create | columns |
| `backend/app/llm/fake.py` | modify | router role |
| `backend/evals/{datasets,router,run,canaries,redteam,tools}.py` | modify/create | suite, rt-22 |
| `evals/datasets/router.intents.jsonl`, `safety.redteam.jsonl`, `evals/eval-baseline.json` | create/modify | data |
| `backend/tests/graph/test_commands.py`, `test_router.py`, `test_dispatch.py`, `backend/tests/services/test_runs_routing.py`, `backend/tests/api/...`, `backend/tests/evals/test_router_eval.py` | create | tests |
| `memory/ai.md`, `memory/api.md`, `backend/AGENTS.md`, `CHANGELOG.md`, `tasks/deferred.md` | modify | docs |

## AI Impact & Eval Plan

- Prompts / models: new `nexus.route` v1 on the existing `router` role (gpt-6-luna, effort none, 150 tokens, 5 s, no retries). `nexus.system` unchanged (v4 already says "never offer a PDF or research yourself").
- Evals: `router.intents` is new. **No real-model run in this task** (paid, not authorised; same reason as D10/D15/D19): offline only (fake mode end to end, unit tests with scripted routers). First real run, review, tuning of `min_confidence`, TTFT end to end and the baselines are deferred as **D29**. Until then the router's `targets` stay informational.
- New dataset records: 120 `router.intents`, rt-22.
- Tracing: the router call is inside the run trace (callbacks in the run config). Router input is user text only (asserted in a unit test that feeds a state holding a tool message and an assistant message). No PII added to tags.

## Uncertainties

- `min_confidence` 0.70 is the doc's starting value; it cannot be tuned offline (D29).
- Whether the real model reliably fills strict structured output on `gpt-6-luna` at effort `none` within 150 output tokens: only the call shape is tested offline (as with `memory_extract`).
- A dataset of 120 AI-drafted records cannot be hand-verified by me; the developer reviews it (D29).
- `INTENTS_DISABLED` is applied after the model call (an intent in the list falls back to `general_chat`) rather than by removing it from the schema enum (the strict schema is built once); the user-visible effect is the same.
- Langfuse tags are fixed when the run starts, so a router-chosen intent is visible in the `router` span, not as a tag.

## What Will Be Skipped (and why)

- The keyword pre-gate (only if TTFT misses on a real run: D29).
- `generate_document` / `deep_research` subgraphs (TASK-041/043); their router intents fall back to `general_chat` until then.
- Mode chip, slash menu, hiding Regenerate in the UI (TASK-040): the API refuses a regenerate of a non-chat answer in the meantime.
- Any paid run, `min_confidence` tuning, baselines (D29).

## Risks

- The parallel start changes the graph shape: checkpoints written by older graphs have no `route`; the state key is optional, and the regenerate lookup is covered by the existing regenerate tests.
- Static replies skip the checkpoint, so the model never sees `/help` turns (intended) and regenerate must not pick an older answer: guarded by `route_source`.
- A router bug would affect every message: every router failure mode falls back to `general_chat` and has a test.

---

## Progress log (written at the end, 2026-11-22)

**Done as planned:** steps 1-10 of the plan. Verification: backend `ruff`, `mypy --strict`, unit + integration + contract tests green (1960+ tests), frontend lint/typecheck/301 unit tests, `make verify` (38 Playwright tests + DOM checks) against the Compose stack, a fake-mode `python -m evals.run --suite smoke --no-rag`, and mutation checks (below).

**Where the implementation differs from the plan**
- The graph node is called `router`, not `route`: LangGraph refuses a node with the name of a state key.
- `build_graph(router_model=None)` keeps the node but it stays silent (no `route`, no event), so every older test graph and the tool/RAG eval graphs behave exactly as before; production (`graph_factory_for`) and the chat/perf eval graph pass the real router.
- `/help`, usage replies and "not available yet / switched off" explanations store `intent` NULL and `route_source = 'command'` (the `route_selected` enum has no value for them, and the API sends no `route_selected` for them). That pair is what makes a regenerate of them a 409; a test found that without it a regenerate after a `/help` would have replaced an OLDER chat answer.
- The three existing stream-contract tests hard-coded the old event order; they now expect `route_selected` right after `run_started` (a failing router still ends in the same fallback + error). Four new contract tests cover `/memory`, `/help`, `memory_updated` and the forget card.
- A `memory_content` slot is a `User ...` sentence written by the router; slash commands pass the user's text verbatim (`ops.third_person` words it when it is saved).
- `Command(resume=None)` crashes inside LangGraph (`UnboundLocalError`); the API never sends `None` (it always builds a dict), so no guard was added.

**Mutation checks** (each mutation made a test fail, then was reverted): router shown non-user messages; confidence threshold ignored; regenerate guard removed (first survived: added `test_a_later_memory_or_help_answer_does_not_regenerate_an_older_chat_answer`).

**Not measured (D29):** the real router's accuracy, `min_confidence` tuning, TTFT with the router, baselines.
