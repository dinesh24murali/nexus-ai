"""The eval suite (docs/02_outputs/09_eval_plan.md): runs the app against `evals/datasets/*.jsonl`,
scores the answers and writes `evals/results/latest.json` for `evals/check_baseline.py` to gate.

Run from `backend/`: `uv run python -m evals.run --suite smoke|full`. It lives outside `app/` so
nothing in the product imports it.
"""
