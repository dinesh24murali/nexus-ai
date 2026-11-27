# Scratch: 036-chat-model-bakeoff

## Plan

1. Names checked against the API with the project key (`models.list()`): `gpt-6.1-terra`, `gpt-5.4-mini-2026-03-17`, `gpt-6-luna`, `gpt-5.5-2026-04-23` all exist. Prices re-checked on OpenAI's pricing page (search, 2026-11-21): terra $1 / $0.05 / $5, luna $0.10 / $0.01 / $0.50, gpt-5.4-mini $0.75 / $0.075 / $4.50 (standard tier) match `app/llm/pricing.py`: no change to the table.
2. Run the same full eval (n=3) per candidate by setting `LLM_CHAT_MODEL` for the process, on `chat.golden`, `perf.latency` and `safety.redteam` (summary uses the background model, which does not change): `gpt-6.1-terra` (effort low, the current default) and `gpt-5.4-mini-2026-03-17` (effort low).
3. Compare TTFT p95 (G3 <= 2000 ms), cost per turn (G4 <= $0.01), format compliance, refusal accuracy, relevance, leaks. Pick the model that meets G3 and G4 and the quality gates; record in `memory/ai.md`, write `evals/results/bakeoff-<date>.md`.
4. Change `.env.example` / the default only if the winner differs from the current default.

## Files
`evals/results/bakeoff-2026-11-21.md` (gitignored folder), `memory/ai.md`, `CHANGELOG.md`, maybe `.env.example`.

## Uncertainties
- n=3 over 30 cases is a small sample: differences of a few points are noise. TTFT is one machine, one network, one afternoon.
- The tool-routing part cannot run (no tools yet): re-run in M4.
- Whether `gpt-5.4-mini` accepts effort `low` through the Responses API: the run will show.

## Skipped
Tool-routing and RAG suites (features missing); changing gates (the decision may keep the default).

## Risks
Cost: a few cents per candidate. A model that errors on some prompts shows up as `errors` in the run report.
