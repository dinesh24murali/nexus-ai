"""Best-effort Langfuse logging for eval runs (doc 09 sections 2 and 6).

Datasets are mirrored as Langfuse datasets of the same name (item id `<dataset>:<case id>`), and
each case produces ONE trace carrying its scores: no per-node spans, so an eval run uses few
Langfuse units (ADR-0004). Everything here is optional: no keys means no logging, and a Langfuse
outage must never fail a run. Case text is masked by the client's `mask` before export.

Not wired: linking a trace to its dataset item (the "runs" comparison view). SDK 5 replaced the
dataset-run-items API with experiments; use it once a real key is available to verify the call.
"""

from collections.abc import Mapping, Sequence
from typing import Any

import structlog
from langfuse import Langfuse, propagate_attributes

_log = structlog.get_logger("nexus.evals.langfuse")

MAX_TAG_CHARS = 200


class EvalLogger:
    def __init__(self, client: Langfuse | None, *, run_name: str, environment: str) -> None:
        self._client = client
        self._run_name = run_name
        self._environment = environment

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def sync_dataset(
        self, name: str, version: str, items: Sequence[tuple[str, Any, Any, Mapping[str, Any]]]
    ) -> None:
        """`items`: (case id, input, expected output, metadata). Idempotent: same ids upsert."""
        if self._client is None:
            return
        try:
            self._client.create_dataset(name=name, metadata={"version": version})
            for case_id, case_input, expected, metadata in items:
                self._client.create_dataset_item(
                    dataset_name=name,
                    id=f"{name}:{case_id}",
                    input=case_input,
                    expected_output=expected,
                    metadata={**metadata, "dataset_version": version},
                )
        except Exception:
            _log.warning("langfuse_dataset_sync_failed", dataset=name, exc_info=True)

    def log_case(
        self,
        dataset: str,
        case_id: str,
        *,
        case_input: Any,
        output: str,
        scores: Mapping[str, float],
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        if self._client is None:
            return
        try:
            with (
                propagate_attributes(
                    trace_name=f"eval.{dataset}",
                    session_id=self._run_name[:MAX_TAG_CHARS],
                    tags=[f"env:{self._environment}", "feature:eval", f"dataset:{dataset}"],
                    metadata={"case_id": case_id, "dataset": dataset},
                ),
                self._client.start_as_current_observation(
                    name=f"eval.{dataset}.{case_id}",
                    as_type="span",
                    input=case_input,
                    output=output,
                    metadata=dict(metadata or {}),
                ) as span,
            ):
                for key, value in scores.items():
                    span.score_trace(name=key, value=float(value))
        except Exception:
            _log.warning("langfuse_case_log_failed", dataset=dataset, exc_info=True)

    def flush(self) -> None:
        if self._client is None:
            return
        try:
            self._client.flush()
        except Exception:
            _log.warning("langfuse_flush_failed", exc_info=True)
