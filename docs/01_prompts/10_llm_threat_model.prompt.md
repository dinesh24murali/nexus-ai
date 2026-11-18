# Prompt: Generate LLM Threat Model

> **How to use:** Complete Prompts 03 and 08 first. Paste this file together with both outputs. Save the output to `docs/02_outputs/10_llm_threat_model.md`.

---

## Instructions to AI

You are an application security engineer specialising in LLM systems. Produce a **threat model** for the AI subsystem below, mapped to the OWASP Top 10 for LLM Applications. Assume all user input, uploaded documents, retrieved chunks, web pages and tool outputs are attacker-controlled. Every threat must have a concrete mitigation and a test (which becomes a red-team eval case or an automated test).

---

## Project Input

**Project name:** [[PROJECT_NAME]]
**Auth strategy:** [[AUTH_STRATEGY]]
**Data sensitivity / compliance:** [[Fill from intake 8.3–8.4]]
**Guardrails required:** [[Fill from intake 11.10]]
**Data retention:** [[Fill from intake 11.12]]
**AI system design:** [[Paste or reference docs/02_outputs/08_ai_system_design.md]]

---

## Required Sections

1. **Assets & Trust Boundaries** — Mermaid data-flow diagram marking trust boundaries (browser, API, LLM provider, vector DB, tools, observability)
2. **Threat Table** — columns: ID, OWASP LLM category, threat, entry point, impact, likelihood, mitigation, test. Cover at minimum:
   - Direct and indirect prompt injection (documents, web results, memories)
   - Sensitive information disclosure (system prompt, other users' data, secrets in traces)
   - Excessive agency (tools with side effects, missing approvals)
   - Insecure output handling (markdown/HTML/XSS in rendered answers, links)
   - Cross-tenant data leakage in vector search and memory store
   - Denial of wallet (token abuse, huge uploads, recursive tool loops)
   - Supply chain (model/provider changes, dependency risk)
   - Data poisoning of the knowledge base or memory
3. **Tool Permission Matrix** — each tool × (auth scope, approval required, rate limit, allowed domains/args)
4. **Guardrail Architecture** — where input moderation, output filtering, PII redaction and length caps sit in the request path
5. **Logging & Privacy** — what goes into traces, redaction rules, retention, user deletion flow
6. **Incident Response** — kill switches (disable a tool, switch model, read-only mode), and who is alerted

---

## Output Format

- Use Markdown with Mermaid diagrams
- Threat IDs as `LLM-T01`, `LLM-T02`, … so tasks and evals can reference them
- Target length: 1500–3000 words
