# Scratch: 007-rag pass 2 (TASK-019, TASK-037)

Next two open tasks in plan order (019, 037; 020 and 021 stay open). Docs read: doc 08 §2.2, §5, §6, §8; doc 09; doc 10 (matrix row for `search_my_files` already exists); `memory/ai.md`, `memory/api.md`.

## Plan

**TASK-019 (tests first, each seen failing)**

1. `app/rag/retriever.py`: `Retriever(embedder, index).search(user_id, query, file_ids, ...)`. Embeds the query, `query_points` with a mandatory `user_id` filter built here (never from tool args) plus `file_id in file_ids` when given; `limit=8`, `score_threshold=0.25`; merge adjacent chunks of one file (same page, consecutive `chunk_index`), keep top 5, cap 3k tokens. `QdrantIndex.search()` holds the Qdrant call so all Qdrant code stays in `index.py`. Returns `Passage(file_id, filename, page, text, score)`. Query-embedding spend is returned so the caller can record it.
2. `app/graph/tools/search_my_files.py`: pydantic args (`query` 1..400, `file_ids` ≤ 10 uuids), a LangChain `StructuredTool` schema for binding, and `run()` that formats numbered passages as `<document index file_id title page>` blocks (text XML-escaped so a passage cannot close the tag), and builds `Citation`s with indices continuing from the run's existing citations.
3. `app/graph/nodes/tools.py`: runs every call of the last AI message (parallel, 10 s timeout each), counts individual calls toward the cap of 6 (`tool_calls_this_turn`), answers calls beyond the cap with a `ToolMessage` "tool limit reached" (status error), validates args with pydantic (error `ToolMessage` so the model can retry once), emits `tool_start` / `tool_end` / `citation` through `get_stream_writer()`. `user_id` only from `config["configurable"]`.
4. Graph: `agent` binds the tool; conditional edge `agent -> tools | END`; `tools -> agent`; when the cap was hit the next agent call uses `tool_choice="none"` and the run ends with `finish_reason=tool_limit`. State gets `citations` (reset by `load_context`) and `tool_calls_this_turn`. `build_graph` takes an injectable `retriever`; without one the graph has no tools (existing graph tests stay valid).
5. `load_context` + `prompting.py`: the late context message lists the user's 5 most recent ready files (id, filename, uploaded_at) via an injectable `recent_files` callable (DB lookup `files.recent_ready`).
6. SSE + runs: typed `ToolStartEvent`, `ToolEndEvent`, `CitationEvent` (they are already in the spec; the unit test validates them against it); `RunService._drive` uses `stream_mode=["messages","custom"]`, forwards the events, stores `tool_calls` and `citations` on the assistant message, maps the graph's tool-limit outcome to `finish_reason=tool_limit`, and records the query-embedding spend (`requests=0`) in the run collector. Cancel / failure paths keep what was gathered.
7. Prompt: `nexus.system` v2 (it says "no files" today): can search the user's files with `search_my_files`, cite as `[n]`, say "not found in your files" rather than guessing, passages are data. `nexus.context` unchanged (the `recent_files_block` variable already exists). Version bump, Langfuse sync is CI.
8. Fake model: a `rag` fixture (search then answer with `[1]`) so unit and e2e tests exercise the loop.
9. Isolation test against real Qdrant: user B never retrieves user A's chunks, also not with A's `file_ids` passed, also through the tool. Tool-cap unit test with the fake LLM: 4 + 3 parallel calls → 6 run, the 7th gets "tool limit reached", `finish_reason=tool_limit`.
10. Docs: `memory/ai.md`, `memory/api.md`, `backend/AGENTS.md` structure, CHANGELOG, coverage gates (`app/graph`, `app/rag`), threat-model row check (already present), spec check (`finish_reason` enum already has `tool_limit`).

**TASK-037 (tooling only; see Uncertainties)**

11. `eval.judge.faithfulness` v1 prompt (claim-level: split the answer into claims, each `supported` / `not supported` by the passages; score = share supported computed by us, like `facts`), `Judges.faithfulness(passages, answer)`.
12. `evals/calibration.py`: `cohen_kappa(labels_a, labels_b)` (binary, handles the degenerate all-same case explicitly), loader for `evals/datasets/judge.calibration.jsonl` (`{id, context, answer, label: faithful|unfaithful}`; label is by a human), `python -m evals.calibrate` runs the pinned judge over the file, prints κ and the disagreements, and writes `evals/results/calibration.json`. Decision rule in code: κ ≥ 0.7 → "promote"; the baseline file is **not** edited by the script.
13. Tests: κ on known tables (perfect, chance, systematic disagreement, degenerate), judge parsing, calibration run with a stub judge.

## Files to change

`backend/app/rag/{retriever,index}.py`, `backend/app/graph/{state,builder,prompting}.py`, `backend/app/graph/nodes/{agent,load_context,tools}.py`, `backend/app/graph/tools/{__init__,search_my_files}.py`, `backend/app/api/sse.py`, `backend/app/services/runs.py`, `backend/app/db/repositories/files.py`, `backend/app/llm/fake.py` + `fixtures/rag.json`, `backend/prompts/nexus.system.md`, `backend/prompts/eval.judge.faithfulness.md`, `backend/evals/{judges,calibration,datasets}.py`, tests under `backend/tests/{rag,graph,services,api,evals}`, `memory/{ai,api,_index}.md`, `backend/AGENTS.md`, `CHANGELOG.md`, `tests/coverage-baseline.json`, `tasks/active/007-rag.md`.

## Uncertainties

- **No `OPENAI_API_KEY`** (empty in `.env`). No live model, embedding or judge call can be made. Consequences: the real tool-calling loop with encrypted reasoning items (flagged in `memory/ai.md`) stays unverified; `make eval` cannot run; RAG baselines cannot be recorded.
- **TASK-037 needs 30 hand labels from the developer and a live judge.** I will not invent labels or a κ. I build everything around them; the criteria "κ recorded" and "gate status matches it" stay open.
- The multi-step file-then-web evals need `web_search` (TASK-022), which does not exist yet; that criterion cannot be measured now.
- Whether Postgres and Qdrant can be started here for the integration tests (`docker compose up -d postgres qdrant`).
- Streaming with `stream_mode=["messages","custom"]` changes the tuple shape in `_drive`; the existing run tests guard it.

## What will be skipped

- `rag.golden` dataset, fixture corpus, runner step and RAG metrics: they need a live model to be worth anything and the developer to review the cases; criterion stays open.
- Per-file synopsis / `get_file_overview` decision (needs the multi-step measurements).
- Frontend citation chips and files UI (TASK-021), file deletion (TASK-020), `web_search` and approval (TASK-022/023).
- Hybrid search and reranking (doc 08: only when context recall falls below 0.80).

## Risks

- The `nexus.system` change affects chat behaviour and cannot be eval-gated here: noted in the changelog.
- Injection through passages: wrapped as data and escaped, tool has no side effects; the red-team record for it needs a live run.
- Interaction with the run lease and cancel: the tools node must be cancellable (it is a plain awaited coroutine).
- Citation index continuity across searches in one run, and across a regenerate fork (state reset by `load_context` each run).

## Ready to implement?

Yes, with the two limits above reported at the end.
