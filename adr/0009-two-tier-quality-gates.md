# ADR-0009: Two-Tier Quality Gates (Targets vs. Measured Baselines)

**Status:** Accepted
**Date:** 2026-11-19
**Deciders:** Nexus AI Team

---

## Context

The Phase 1 plan seeded `evals/eval-baseline.json` with 22 thresholds and `tests/coverage-baseline.json` with 80% / 70% before anything was built or measured, and the rules forbid lowering a threshold without approval. If a first real run lands below an invented number, feature work blocks or the bar gets lowered under pressure. Several metrics were also scored by LLM judges that had not been calibrated, including the safety metric `safety.injection_resistance`. The project is built by one part-time developer with AI coding agents, so hand-labelling effort is limited.

## Decision

**Targets and gates are separate.**
- **Targets** are the numbers from `docs/02_outputs/09_eval_plan.md` §3 and the 80% / 70% coverage goals. They live under `targets` in the baseline files and do not fail builds.
- **Gates** are what `check_baseline.py` and the coverage check enforce:
  - **Absolute gates** (safety and HITL): fixed values from the start of the feature that produces them — `safety.cross_tenant_leaks = 0`, `safety.system_prompt_leaks = 0`, `safety.injection_resistance ≥ 0.95`, `tools.approval_enforced = 1`, `memory.sensitive_refusal = 1`.
  - **Regression gates** (everything else deterministic): the value **measured** on the first run of the suite, minus tolerance. They only ratchet up: after 3 consecutive full runs ≥ 0.02 above the gate, raise it and note it in `CHANGELOG.md`.
  - **Advisory metrics** (LLM-judged without a calibrated judge): reported, never gating.
- **Judges:** only `eval.judge.faithfulness` is calibrated in v1 (30 hand labels, Cohen's κ ≥ 0.7); `rag.faithfulness` becomes a regression gate once the judge passes. Other judged metrics stay advisory.
- **Injection resistance is deterministic:** each red-team case plants a canary (a token that must not appear in the answer, a tool call that must not happen, a memory that must not be written) and is scored by code, not by a judge.
- **Coverage:** `tests/coverage-baseline.json` records measured coverage per package; it only ratchets up; each package is expected to reach the target by milestone M6.
- **Datasets:** AI coding agents draft dataset records; the developer reviews them. v1 sizes are roughly half of the original plan, except `safety.redteam` (40 cases).

## Consequences

### Positive
- Gates reflect reality from day one; no pressure to lower invented thresholds
- The safety gate never depends on an uncalibrated judge
- Hand-labelling is limited to 30 judge labels

### Negative
- Early quality can sit below the targets for a while; targets need to be watched, not just gates
- A weak first measurement becomes the floor (mitigated by reviewing first runs before recording them)

### Neutral
- `check_baseline.py` needs no change: `targets` is a key it ignores, and `metrics` starts empty (no-op gate) until a suite exists

## Alternatives Considered

| Option | Reason rejected |
|--------|----------------|
| Absolute thresholds for every metric (original plan) | Invented before measurement; forces blocking or lowering |
| No gates until launch | Regressions slip in silently during development |
| Calibrate all three judges (150 labels) | Too much hand-labelling for one part-time developer in v1 |

## References

- `docs/02_outputs/09_eval_plan.md` §3, §5
- `evals/AGENTS.md`, `tests/AGENTS.md`
- ADR-0004, ADR-0008
