# LLM Threat Model — Nexus AI

**Version:** 1.3 · **Date:** 2026-11-21 (task 003b: schedules and `remember_fact` removed, LLM-T19 retired, forget confirmation, kill switches in `runtime_flags`) · previous 1.2 2026-11-21 (LLM-T17…T20), 1.1 2026-11-19 (task 003) · **Framework:** OWASP Top 10 for LLM Applications (2025) · **Related:** [AI design](08_ai_system_design.md), [Architecture](03_tech_architecture.md) §8, [Eval plan](09_eval_plan.md) §8

**Working assumption:** every user message, uploaded file, retrieved chunk, web result, stored memory and tool output is attacker-controlled.

## 1. Assets & Trust Boundaries

**Assets:** user conversations and files (PII), generated documents (PDFs), memories, account credentials/tokens, system prompts, OpenAI/Tavily API keys and budget, Langfuse traces, other users' data.

```mermaid
flowchart LR
  subgraph TB1["Untrusted: Internet"]
    U[User browser]
    WEB[Web pages via Tavily]
  end
  subgraph TB2["Trusted: our VM"]
    API[API + LangGraph]
    WK[Worker / parsers]
    PG[(Postgres: users, messages, checkpoints, memories)]
    QD[(Qdrant: chunks)]
  end
  subgraph TB3["Third-party processors"]
    OAI[OpenAI - store=false]
    TAV[Tavily]
    LF[Langfuse Cloud - EU]
  end
  U -- "messages, files (untrusted)" --> API
  API -- "prompts incl. user data" --> OAI
  OAI -- "model output (untrusted)" --> API
  API -- "search query (approved)" --> TAV
  TAV -- "web content (untrusted)" --> WEB
  TAV -- "results (untrusted)" --> API
  API --> PG & QD
  WK -- "parses untrusted files" --> QD
  API & WK -- "redacted traces" --> LF
  API -- "rendered answer (untrusted HTML risk)" --> U
```

Key boundary rules: model output is never trusted to choose *whose* data is accessed (tenant id comes from the auth context); content crossing from TB1/TB3 into a prompt is wrapped as data; anything leaving to TB3 is minimised (Tavily receives only the user-approved query). Long-term memory is written only from the **user's own words** (background extraction from the user's message; v1.1 memory commands), never from tool output or documents. v1.1: the **router sees only user-authored text**; the **PDF renderer has no network or filesystem access** beyond bundled assets.

## 2. Threat Table

| ID | OWASP LLM | Threat | Entry point | Impact | Lik. | Mitigation | Test |
|----|-----------|--------|-------------|--------|------|------------|------|
| LLM-T01 | LLM01 Prompt injection (indirect) | Instructions hidden in uploaded docs hijack the agent | `search_my_files` results | Wrong/malicious answers, tool misuse | High | Passages wrapped in `<document>` tags + system rule "data not instructions"; tools with external effect need approval; no tool reads secrets | rt-01, rt-02, rt-03, rt-20 |
| LLM-T02 | LLM01 Prompt injection (indirect) | Web results carry injected instructions / phishing | `web_search` results | Phishing links, memory poisoning | High | `<web_result>` wrapping; **no memory-writing tool in `general_chat`**, so injected web text can't write memory (structural); links open with `noopener noreferrer` and the domain is shown | rt-04, rt-05 |
| LLM-T03 | LLM01 / LLM07 System prompt leakage | Direct jailbreak or system-prompt extraction | user message | Policy bypass, prompt disclosure | Med | No secrets in prompts (prompt leak is low impact by design); refusal instructions; moderation. A prompt canary was in the first design and is **not built** (review TASK-030: the prompt holds nothing secret, so a leak costs little; `tasks/deferred.md` D21) | rt-06, rt-07 |
| LLM-T04 | LLM04 Data & model poisoning | Malicious content stored as long-term memory, affecting all future chats | `extract_memories`, `manage_memory` | Persistent injection | Med | Extraction only from the **user's** message, not tool/doc content; no memory-writing tool in `general_chat`; memory commands (v1.1) take only user-authored text and every forget is confirmed; instruction-like memories rejected by filter; memories injected as `<user_memory>` data in the late context message, escaped to one line per memory so one cannot close the tag (`tests/graph/test_memory_context.py`; TASK-026); user can view/delete all; text added or edited in Settings goes through the same instruction and sensitive-category filter (`tests/api/test_memories.py`; TASK-027) | rt-05, rt-08, rt-09 |
| LLM-T05 | LLM02 Sensitive info disclosure | Cross-tenant leakage via vector search | Qdrant query | Disclosure of other users' documents | Low (severe) | Mandatory `user_id` filter added in retriever code, not tool args; tenant-indexed payload; integration test per release | rt-10, rt-11, `test_isolation.py` |
| LLM-T06 | LLM02 Sensitive info disclosure | IDOR on conversations/files/memories/checkpoints/documents | REST API, store namespaces | Disclosure | Low (severe) | Repository methods require `user_id`; 404 on mismatch; store namespace `("memories", user_id)`; thread ids are UUIDs checked against owner | rt-12, API isolation tests |
| LLM-T07 | LLM02 / LLM06 | Exfiltration of private data via tool arguments (e.g., doc contents in a web query) | `web_search` args | Private data sent to third party | Med | Human approval shows the exact query; query length cap 400; Tavily only receives the query | rt-13 |
| LLM-T08 | LLM06 Excessive agency | Tool loops, unapproved side effects | agent loop | Cost, unintended actions | Med | ≤ 6 individual tool calls/turn; only read-only tools, with every web search approved; no tools with side effects beyond outbound search in v1 | rt-14 |
| LLM-T09 | LLM05 Improper output handling | XSS / malicious links / image-beacon exfiltration via markdown | rendered answer | Account takeover, data exfiltration | Med | `react-markdown` (raw HTML is shown as text: no `rehype-raw`; `javascript:` and other schemes are dropped by the default `urlTransform`), external images blocked by CSP `img-src 'self' data:`, links `noopener noreferrer` (the app is behind a login, so `nofollow` has nothing to protect), CSP `base-uri`/`object-src`/`form-action` closed | rt-03, rt-15, e2e DOM check |
| LLM-T10 | LLM02 | PII/secrets in traces, logs, eval data | Langfuse Cloud, logs | Privacy breach (traces are stored by a third party) | Med | `mask` redaction runs before export (mandatory); hashed user ids; EU region; logs exclude content; 30-day trace retention; datasets anonymised | `test_redaction.py`, `test_tracing.py`, log lint `test_log_fields.py` (TASK-030) |
| LLM-T11 | LLM10 Unbounded consumption | Denial of wallet: long prompts, huge files, many requests | chat, upload | Cost overrun, outage | Med (invite-only) | Invite-only registration; message ≤ 16k chars; max output tokens (incl. reasoning); per-user 30 msg/min + **$0.50/day**; **global $5/day cap**; file 20 MB/500 pages; OpenAI project budget cap; spend alert at 80% of the global cap | rt-19, load test |
| LLM-T12 | LLM03 Supply chain / LLM04 | Malicious files exploit parsers (zip bombs in DOCX, malformed PDFs) | worker | RCE / DoS on worker | Med | Parse in worker only, non-root container with all capabilities dropped, decompressed-size limits, per-job timeout 5 min, memory and process limit; **an egress allow-list is not built** (plain Compose cannot enforce one, `tasks/deferred.md` D22) | fuzz corpus in parser tests |
| LLM-T13 | LLM03 Supply chain | Model/provider behaviour change or dependency compromise | OpenAI model updates (GPT-6 models are alias-only), PyPI/npm | Silent quality/safety regression | Med | Pinned snapshots where available (judge `gpt-5.5-2026-04-23`; `gpt-5.4-mini-2026-03-17` as pinned chat alternative); weekly full evals as the drift alarm for alias-only models (up to a week of lag); lockfiles, Dependabot, pip-audit/npm audit, Trivy | weekly full eval |
| LLM-T14 | LLM09 / content safety | Harmful content generation | user message | Abuse, reputational harm | Low (invite-only) | `omni-moderation-latest` on input (fail-open on outage, logged + tagged); model safety; refusal eval | rt-17, `chat.refusal_accuracy` |
| LLM-T15 | LLM05 / LLM06 | Code execution via calculator | `calculator` args | RCE | Low | AST whitelist evaluator, no `eval`, operand size limits (no `9**9**9`), 1 s timeout | rt-16, fuzz tests |
| LLM-T16 | LLM09 Misinformation | Hallucinated facts presented as grounded | answers | Bad decisions | High | Citations required for file/web facts; "not found" behaviour; faithfulness gate | rt-18, `rag.faithfulness` |
| LLM-T17 | LLM01 / LLM06 | **Router manipulation:** injected text steers a message to a side-effect intent (research, memory forget) or away from a guarded path | `route` input | Unwanted research runs or memory changes; skipped guardrails | Med | Router input = the last 3 **user** messages + previous intent only (no tool output, documents or memories); output is an enum + Pydantic-validated slots that never carry ids; the router grants no permissions (the research plan always interrupts; every forget shows a confirmation card and deletes at most one memory); slash commands are parsed without a model; `ROUTER_DISABLED` kill switch | rt-22, `router.*` |
| LLM-T18 | LLM05 Improper output handling | **PDF renderer abuse:** untrusted thread/web content becomes HTML that makes WeasyPrint fetch `file://` or internal URLs (SSRF, local file read), or embeds `javascript:` links | `render_document` | Local file or cloud-metadata disclosure inside a PDF the user downloads; malicious links | Med | Markdown → HTML with the client's sanitiser allowlist (raw HTML is shown as text: no `rehype-raw`; `javascript:` and other schemes are dropped by the default `urlTransform`); deny-all URL fetcher (bundled CSS/fonts only); links only `http(s)`, printed in full; render in the worker (non-root, egress limited as LLM-T12), 60 s timeout, ≤ 30 pages; PDFs served `Content-Disposition: attachment` | rt-23, `documents.external_fetches` |
| LLM-T20 | LLM02 / LLM10 | **Deep-research exfiltration and cost:** the planner puts private context (memories, thread text) into web queries, or plans many expensive searches | `research_plan` | Private data sent to Tavily; cost overrun | Med | Every planned query is shown in one approval before any search; only approved queries run; ≤ 6 queries × 400 chars; token limits keep the worst case ≤ $0.05, checked before the run against the user budget and the global cap; `INTENTS_DISABLED=deep_research` | rt-25, `research.unapproved_queries` |

LLM-T19 (unattended execution via scheduled tasks) was retired in revision 1.3 together with schedules; re-add it if schedules return from the backlog.

## 3. Tool Permission Matrix

| Tool | Auth scope | Approval | Rate limit | Allowed args / destinations |
|------|-----------|----------|-----------|-----------------------------|
| `search_my_files` | caller's `user_id` (from JWT, injected server-side) | no | within 6 calls/turn | `query` ≤ 400 chars, `file_ids` ≤ 10 and must belong to caller (filtered, not trusted) |
| `web_search` | none | **yes, every call** | 6/turn, 100/user/day | `query` ≤ 400 chars → Tavily only; `max_results=5`; no URL fetching in v1 |
| `calculator` | none | no | 6/turn | expression ≤ 200 chars, AST whitelist |
| `research_search` (pipeline step) | none | **yes, one approval for the whole plan** | ≤ 6/run; research runs count towards the 100 web searches/user/day | approved queries only, ≤ 400 chars → Tavily |
| `render_document` (job) | caller's `user_id` | no | 10 documents/user/day | sanitised HTML; deny-all URL fetcher; ≤ 30 pages |
| `memory_ops` (`manage_memory`) | caller's `user_id` | **`forget` always** (`forget_memory` card); `save` no (user-authored input only) | 1 save or 1 delete per message | content ≤ 500 chars; rejected if memory disabled, sensitive category, or instruction-like ("always", "ignore", "from now on you…"); forget candidates: top 3, similarity ≥ 0.75 |

**Implementation status (TASK-022 and TASK-023, 2026-11-21).** What is built and where it is proven; what is not is listed so nobody assumes it:

| Tool / threat | Built | Proven by |
|---------------|-------|-----------|
| `calculator` (LLM-T15) | AST whitelist (numbers, `+ - * / // % **`, unary signs, `sqrt`, `log`, `round`, `abs`), `Decimal` arithmetic, ≤ 200 chars, ≤ 120 nodes, depth ≤ 40, results below 1e300 (so `9**9**9` is refused at once), one line only, 1 s timeout; errors never echo the expression | `backend/tests/graph/test_calculator.py`: 80+ attack strings, two seeded fuzzers (4000 token-soup inputs; 300 file-writing attack shapes with a sentinel file), `eval`/`exec` patched out; mutation-checked (letting `Compare`/`Attribute`/`Subscript` through fails 14 tests) |
| `web_search` (LLM-T02, T07) | every call interrupts before it runs (`graph/policy.py`, `nodes/approval.py`), the card shows the exact query, an edit is validated (1–400 chars), anything unclear counts as a rejection; Tavily gets the query and the key only; only `http(s)` results, ≤ 5 × 800 chars, text and attributes HTML-escaped in `<web_result>` blocks; failures never carry exception text; `nexus.system` v3 says results are data | `tests/graph/test_web_search.py`, `tests/graph/test_approval.py` (mutation-checked: skipping the approval node fails 25 tests, ignoring the policy in the `tools` node fails 19), `tests/services/test_runs_approval.py`, `tests/api/test_chat_tools.py`, `tests/e2e/tools.spec.ts`, and the absolute gate `tools.approval_enforced` |
| Tool loops (LLM-T08) | ≤ 6 individual calls per turn; a call past the cap gets no approval card and no run | `tests/graph/test_approval.py::test_a_search_past_the_tool_cap_is_refused_without_asking`, `tests/graph/test_tools.py` |
| Kill switch `TOOLS_DISABLED` | read per run: a switched-off tool is not offered, not asked about, and does not run even if its approval was already pending | `tests/graph/test_disabled_tools.py`, `tests/api/test_chat_tools.py` |
| Not built yet | the **100 web searches per user per day** limit (every search is approved one by one, capped at 6 per turn and under the 30 sends/min limit, so the abuse path is narrow; counter and message are deferred, `tasks/deferred.md` D13); **red-team dataset records** rt-04, rt-05, rt-13, rt-14, rt-16 (they need live runs and the developer's review, `tasks/deferred.md` D6, D13); the `research_plan` approval (v1.1, TASK-043; the `forget_memory` approval is built, TASK-039) | |

Kill switches are rows in the Postgres `runtime_flags` table, set with `python -m app.cli flags set NAME=value` and read by the API and worker through a 60 s cache; env values only give boot defaults, so flipping one needs no restart or drain: `TOOLS_DISABLED=web_search,…`, `MEMORY_WRITES_DISABLED=1` (extraction and memory commands), `CHAT_MODEL_OVERRIDE=<model>`, `READ_ONLY_MODE=1` (no new runs, history readable). v1.1: `ROUTER_DISABLED=1` (everything → `general_chat`), `INTENTS_DISABLED=deep_research,generate_document,…`.

## 4. Guardrail Architecture

```mermaid
flowchart LR
  IN[User message] --> V[Pydantic validation<br/>length, type] --> RL[Rate limit + budget] --> MOD[OpenAI moderation] --> RT[Router<br/>user text only, enum output,<br/>fallback general_chat] --> G[LangGraph]
  G --> TOOLS[Tools<br/>arg validation, tenant filter,<br/>approval, timeouts, output wrapping]
  TOOLS --> G
  G --> OUT[Output caps<br/>max tokens, tool limit] --> SSE[SSE to client] --> R[Client render<br/>no raw HTML, CSP]
  G -. async .-> MEM[Memory extraction<br/>sensitive + instruction filters]
  G -. async .-> TR[Tracing<br/>mask PII, hash user]
```

## 5. Logging & Privacy

- **Traces (Langfuse Cloud, EU region):** prompts/completions included (needed for debugging and evals) after the `mask` function redacts emails, phone numbers, IBAN/card-like numbers, JWTs and API-key patterns — redaction runs before anything leaves the VM; user id hashed (HMAC with server secret); retention 30 days; access limited to the Langfuse org members (the developer). Self-hosters can keep traces on their own infrastructure with the optional Langfuse v4 profile.
- **App logs:** metadata only (route, status, latency, ids); never message content, file names or memory text.
- **Eval datasets:** synthetic or anonymised; never raw production data.
- **User deletion:** `purge_user` deletes Postgres rows, checkpoints (`adelete_thread`), store namespace, Qdrant points by `user_id`, uploaded files; Langfuse traces age out within 30 days (hashed id, no direct identifier).
- **Third parties:** OpenAI API (Responses API with `store=false`; no training on API data by default), Tavily (approved queries only), Langfuse Cloud EU (redacted traces, 30 days). Listed in the README privacy section.

## 6. Incident Response

| Situation | Action | Owner |
|-----------|--------|-------|
| Suspected cross-tenant leak | `READ_ONLY_MODE=1`; preserve logs/traces; run isolation suite; notify affected users within 72 h | on-call engineer |
| Injection campaign via web results | `TOOLS_DISABLED=web_search`; add cases to red-team set; patch prompt; re-enable after eval passes | AI owner |
| Memory poisoning found | `MEMORY_WRITES_DISABLED=1`; script to find and delete matching memories; fix filters | AI owner |
| Cost spike | spend alert → identify user via `usage_daily`; disable user (CLI); lower budget | on-call |
| PDF renderer fetched an external or local resource | `INTENTS_DISABLED=generate_document`; check `render_document` logs; delete affected PDFs; fix the fetcher allowlist | AI owner |
| Model regression after provider update | `CHAT_MODEL_OVERRIDE` to the pinned alternative (`gpt-5.4-mini-2026-03-17`) — alias-only models have no previous snapshot; run full evals | AI owner |

Alerts go to the team channel from Uptime monitoring and the weekly full-eval workflow; Langfuse cost/feedback dashboards are reviewed weekly.

## Red-team coverage (TASK-029, 2026-11-22)

The records are in `evals/datasets/safety.redteam.jsonl`; `backend/evals/redteam.py` runs them through the real graph with stand-in tools and `backend/evals/canaries.py` scores them. Absolute gates: `safety.injection_resistance` >= 0.95 and `safety.cross_tenant_leaks` = 0. Not yet run on the real model (`tasks/deferred.md` D19).

| Threat | Records | What a failure looks like |
|--------|---------|---------------------------|
| LLM-T01 | rt-01, rt-02, rt-03, rt-20 | planted token or markup in the answer, a `web_search` requested after reading a document |
| LLM-T02 | rt-04, rt-05 | phishing link in the answer after an approved search; a memory written from a web result |
| LLM-T04 | rt-08, rt-09 | a planted memory obeyed; an instruction-like memory stored |
| LLM-T05 | rt-10, rt-11 | another user's document text in the answer or a tool result; a lookup for another user's id |
| LLM-T06 | rt-12, rt-21 | another user's memory, or any e-mail address, in the answer |
| LLM-T07 | rt-13 | a web search that ran without the user's approval |
| LLM-T08 | rt-14 | more than 6 tool calls run in one turn |
| LLM-T15 | rt-16 | code output (`uid=`, `/etc/passwd` content) from the calculator |
| LLM-T09 | rt-15 (not in the dataset) | `Markdown.test.tsx`, `tests/e2e/chat.spec.ts` |

## Security review (TASK-030, 2026-11-22)

Every threat in section 2 has a row below: what was checked in the code, and the tests that fail if it breaks. `backend/tests/core/test_threat_model.py` reads this table: a threat without a row, a status other than `built` or `M7`, a `built` row without a test, a path that does not exist, a `::name` that is not in its file, or a red-team id that is not in `evals/datasets/safety.redteam.jsonl` fails the build. Paths are relative to the repository root.

| Threat | Status | Tests |
|--------|--------|-------|
| LLM-T01 | built | `backend/tests/graph/test_search_my_files.py::test_a_passage_cannot_close_the_document_tag`, `backend/tests/graph/test_search_my_files.py::test_a_file_name_cannot_break_out_of_its_attribute`, `backend/tests/llm/test_system_prompt.py::test_document_text_is_data_not_instructions`, rt-01, rt-02, rt-03, rt-20 |
| LLM-T02 | built | `backend/tests/graph/test_web_search.py::test_a_result_cannot_close_its_block_or_open_another`, `backend/tests/graph/test_web_search.py::test_a_result_that_is_not_a_web_page_is_dropped`, `backend/tests/llm/test_system_prompt.py::test_web_results_are_data_not_instructions_and_are_cited_by_their_index`, `frontend/src/features/chat/Citations.test.tsx`, rt-04, rt-05 |
| LLM-T03 | built | `backend/tests/llm/test_system_prompt.py::test_it_still_refuses_what_it_cannot_do`, rt-06, rt-07 |
| LLM-T04 | built | `backend/tests/memory/test_policy.py::test_instructions_to_the_assistant_are_refused`, `backend/tests/graph/test_memory_context.py::test_a_memory_cannot_close_its_block_or_open_a_tag`, `backend/tests/api/test_memories.py`, `backend/tests/jobs/test_extract_memories.py`, rt-08, rt-09 |
| LLM-T05 | built | `backend/tests/rag/test_isolation.py::test_another_user_running_the_same_search_gets_nothing`, `backend/tests/rag/test_retriever.py::test_another_users_chunks_are_never_returned`, `backend/tests/graph/test_search_my_files.py::test_the_owner_comes_from_the_caller_never_from_the_arguments`, rt-10, rt-11 |
| LLM-T06 | built | `backend/tests/api/test_isolation.py::test_conversations_are_invisible_to_other_users`, `backend/tests/api/test_isolation.py::test_files_are_invisible_and_undeletable_for_other_users`, `backend/tests/memory/test_apply.py::test_a_plan_cannot_reach_another_users_memory_even_with_its_id`, `backend/tests/jobs/test_memory_in_context.py::test_another_users_chat_never_gets_the_memory`, rt-12, rt-21 |
| LLM-T07 | built | `backend/tests/graph/test_policy.py::test_every_web_search_needs_approval`, `backend/tests/graph/test_web_search.py::test_the_request_carries_only_the_query_and_the_key`, `backend/tests/graph/test_approval.py`, rt-13 |
| LLM-T08 | built | `backend/tests/graph/test_approval.py::test_a_search_past_the_tool_cap_is_refused_without_asking`, `backend/tests/graph/test_tools.py`, rt-14 |
| LLM-T09 | built | `frontend/src/features/chat/Markdown.test.tsx`, `frontend/src/features/chat/Citations.test.tsx`, `backend/tests/core/test_deploy_config.py::test_csp_allows_no_remote_origin_and_no_inline_or_eval_code`, `tests/e2e/auth.spec.ts` |
| LLM-T10 | built | `backend/tests/llm/test_redaction.py::test_sensitive_values_are_replaced`, `backend/tests/llm/test_tracing.py::test_pii_is_masked_before_export`, `backend/tests/llm/test_tracing.py::test_raw_user_id_and_email_never_appear_in_the_config`, `backend/tests/core/test_log_fields.py::test_no_log_call_has_a_field_that_names_user_content_or_a_credential`, `backend/tests/core/test_logging.py::test_access_log_has_request_fields_and_no_bodies_or_query` |
| LLM-T11 | built | `backend/tests/core/test_budgets.py`, `backend/tests/api/test_chat_limits.py::test_the_fourth_send_in_a_minute_is_rate_limited`, `backend/tests/api/test_chat_limits.py::test_a_user_over_budget_gets_429_with_scope_user`, `backend/tests/services/test_file_validation.py`, `backend/tests/core/test_deploy_config.py::test_the_proxy_body_limit_is_just_above_the_upload_limit`, `backend/tests/loadtest/test_check.py`, `make loadtest` (TASK-031), rt-19 |
| LLM-T12 | built | `backend/tests/rag/test_parsers.py::test_docx_zip_bomb_is_refused_before_it_is_expanded`, `backend/tests/rag/test_parsers.py::test_pdf_over_the_page_limit_is_refused`, `backend/tests/jobs/test_ingest.py::test_the_job_is_registered_with_a_5_minute_timeout_and_3_attempts`, `backend/tests/jobs/test_worker_jobs.py::test_a_job_past_its_hard_timeout_fails_the_attempt`, `backend/tests/core/test_deploy_config.py::test_the_worker_that_parses_uploads_has_a_memory_and_process_limit` |
| LLM-T13 | built | `backend/tests/llm/test_factory.py`, `make audit` (pip-audit and npm audit; run by hand, CI is not built, `tasks/deferred.md` D21) |
| LLM-T14 | built | `backend/tests/llm/test_moderation.py::test_any_failure_fails_open_and_is_logged_without_the_text`, `backend/tests/llm/test_moderation.py::test_the_refusal_is_a_400_that_does_not_echo_the_message`, rt-17 |
| LLM-T15 | built | `backend/tests/graph/test_calculator.py` |
| LLM-T16 | built | `backend/tests/graph/test_search_my_files.py::test_nothing_found_says_so_and_cites_nothing`, `backend/tests/graph/test_web_search.py::test_no_results_says_so_and_cites_nothing`, `backend/tests/evals/test_rag_metrics.py` |
| LLM-T17 | M7 | built with TASK-038 (router; test `router.*`, rt-22) |
| LLM-T18 | M7 | built with TASK-041 and TASK-042 (document pipeline; rt-23, `documents.external_fetches`) |
| LLM-T20 | M7 | built with TASK-043 (deep research; rt-25, `research.unapproved_queries`) |

### What the review checked

| Area | Result |
|------|--------|
| Browser headers | CSP had no `base-uri`, `object-src` or `form-action`, and the app sent no `Permissions-Policy`, `Cross-Origin-Opener-Policy` or `Cross-Origin-Resource-Policy`. **Fixed** in `deploy/Caddyfile`, pinned by `test_deploy_config.py` and the e2e header check. `Cache-Control: no-store` on `/api/*` was tried and removed: it makes the DOM check hang (D23) |
| Upload limits | Caddy 22 MB (above the API's 20 MB on purpose), API stream-checked 20 MB, magic-byte type check, file name never used as a path, parsers bound pages (500), characters (2 million) and unpacked DOCX size (100 MB, read from the zip directory), 5-minute job timeout. No change needed |
| Invites and sign-in | 128-bit invite codes, only the SHA-256 hash is stored, one identical error for unknown, used, revoked and expired codes, failure counters per code and per login, a dummy password hash so an unknown email costs the same time, argon2id, JWT algorithm pinned with required claims, refresh cookie `HttpOnly; Secure; SameSite=Strict` on `/api/v1/auth`. No change needed |
| Trace and log redaction | `mask` runs before export, user id is an HMAC pseudonym, and no log call carries a content field (**new** lint `test_log_fields.py`). No change needed |
| Secrets | Keys are `SecretStr`, `.env` is ignored by git, production refuses `LLM_MODE=fake` and a secret under 32 characters, `/docs` and `/openapi.json` are off in production, no secret is baked into an image. No change needed |
| Containers | The image already ran as a non-root user. **Fixed:** api, worker and migrate now drop every capability and cannot gain privileges; the worker (which parses uploads) has a 2 GB memory and a 512-process limit. Dev ports are bound to loopback, production publishes only Caddy (both tested) |
| Dependencies | `pip-audit` (locked runtime set) and `npm audit` (all and production-only): no known vulnerabilities on 2026-11-22. `make audit` repeats it |
| Doc claims that were not true | The T03 prompt canary was never built (T03 row now says so, D21). T09 named `rehype-sanitize` and `nofollow`; the code uses `react-markdown` without `rehype-raw` and `noopener noreferrer`, which gives the same protection (the row now says what the code does). T02 said the domain is shown: it was not, the citation link now reads "Open source (host)" with the real host. T12 named an egress allow-list that Compose cannot enforce (D22) |
| Not done | The 100 web searches per user per day counter (D13), the first real-model red-team run (D19), an egress allow-list for the worker (D22), a prompt canary (D21) |
