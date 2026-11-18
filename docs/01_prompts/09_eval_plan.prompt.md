# Prompt: Generate LLM Evaluation Plan

> **How to use:** Complete Prompt 08 first. Paste this file together with `docs/02_outputs/08_ai_system_design.md`. Save the output to `docs/02_outputs/09_eval_plan.md`. Seed `evals/eval-baseline.json` and `evals/datasets/` from it.

---

## Instructions to AI

You are an ML evaluation engineer. Produce an **evaluation plan** that turns "the AI seems to work" into measurable, regression-gated quality. LLM outputs are nondeterministic: every metric must define how it is computed, the dataset it runs on, the pass threshold, and how flakiness is controlled (temperature, repetitions, tolerance).

---

## Project Input

**Project name:** [[PROJECT_NAME]]
**AI features:** [[Fill from intake 11.4]]
**Quality targets:** [[Fill from intake 11.9]]
**Observability / eval platform:** [[LLM_OBSERVABILITY]]
**AI system design:** [[Paste or reference docs/02_outputs/08_ai_system_design.md]]

---

## Required Sections

1. **Eval Strategy Overview** — the test pyramid for AI: deterministic unit tests (fake LLM) → component evals → end-to-end conversation evals → online evals on production traces
2. **Datasets** — for each dataset: name (`evals/datasets/<name>.jsonl`), purpose, size for full run vs. smoke run, record schema, how it is built (hand-written, synthetic, mined from traces), how it is versioned
3. **Metrics per Feature** — table: feature, metric, method (exact match / heuristic / LLM-as-judge / human), threshold, smoke-run threshold. Cover at minimum:
   - Chat: answer relevance, instruction following, refusal correctness
   - RAG: context recall, context precision, faithfulness/groundedness, citation correctness
   - Tools: tool-selection accuracy, argument validity, unnecessary-call rate
   - Memory: extraction precision/recall, no cross-user leakage
   - Safety: prompt-injection resistance, jailbreak resistance, PII leakage
   - Operational: p95 time-to-first-token, tokens and cost per conversation
4. **LLM-as-Judge Rubrics** — full judge prompts with scoring scale, judge model, and how judge bias is checked against human labels
5. **Baseline & Regression Gate** — exact JSON shape of `evals/eval-baseline.json`, tolerance rules, when the baseline may be raised or lowered (requires ADR or task note)
6. **CI Integration** — which datasets run on every PR (smoke), nightly (full), and before release; budget per run
7. **Online Evaluation** — user feedback capture, sampled trace scoring, dashboards and alerts
8. **Red-Team Set** — at least 15 adversarial test cases (injection via documents, via web results, tool abuse, data exfiltration, cross-tenant probing)

---

## Output Format

- Use Markdown
- Include the `eval-baseline.json` content and 3 example records per dataset
- Target length: 2000–3500 words
