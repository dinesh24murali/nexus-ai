"""Langfuse tracing for graph runs (ADR-0004, docs/02_outputs/08 section 10).

One `Tracer` per process. `run_config()` returns the LangGraph/LangChain run config fragment
(callbacks, tags, metadata, run name) to merge into `graph.astream(..., config=...)`:

- trace name `chat.turn | chat.regenerate | chat.resume`, `session_id` = conversation id,
  `user_id` = HMAC hash (never the raw id or email), tags `env:*` / `feature:*`, and the name and
  version of every prompt the run uses (ADR-0008) as trace metadata;
- the trace id is derived from the assistant message id (`trace_id_for`), so the run, the stored
  message and a later user rating (`score_feedback`) all find the same trace without a lookup;
- the Langfuse `mask` function (llm/redaction.py) runs on every input, output and metadata value
  before export;
- one span per graph node, not only LLM generations. SDK v5 exports every span created by its own
  tracer by default; the filter below states that explicitly so an SDK default change cannot
  silently drop node spans;
- `mode="eval"` attaches no per-node callbacks: the eval harness records one trace per case.

Tracing is best-effort: no keys means no tracing, and no failure here may reach a run.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

import structlog
from langfuse import Langfuse
from langfuse.langchain import CallbackHandler
from langfuse.span_filter import is_default_export_span, is_langfuse_span
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter

from app.core.config import Settings
from app.llm.prompts import Prompt
from app.llm.redaction import mask

_log = structlog.get_logger("nexus.llm.tracing")

RunName = Literal[
    "chat.turn", "chat.regenerate", "chat.resume", "job.generate_title", "job.extract_memories"
]
TraceMode = Literal["run", "eval"]

FEEDBACK_SCORE_NAME = "user_feedback"


@dataclass(frozen=True)
class RunTrace:
    name: RunName
    conversation_id: str
    user_hash: str
    run_id: str
    assistant_message_id: str | None = None
    prompts: Sequence[Prompt] = ()
    features: Sequence[str] = ("chat",)
    tags: Sequence[str] = ()  # extra trace tags, e.g. `moderation:skipped`


def _export_span(span: ReadableSpan) -> bool:
    return is_langfuse_span(span) or is_default_export_span(span)


class Tracer:
    def __init__(self, settings: Settings, *, span_exporter: SpanExporter | None = None) -> None:
        self._environment = settings.app_env
        self._public_key = settings.langfuse_public_key.get_secret_value()
        self._exporter = span_exporter
        self._client: Langfuse | None = None
        if not settings.langfuse_configured:
            return
        try:
            self._client = Langfuse(
                public_key=self._public_key,
                secret_key=settings.langfuse_secret_key.get_secret_value(),
                base_url=settings.langfuse_host,
                environment=settings.app_env,
                mask=mask,
                should_export_span=_export_span,
                span_exporter=span_exporter,
            )
        except Exception:
            _log.warning("langfuse_init_failed", exc_info=True)

    @property
    def enabled(self) -> bool:
        return self._client is not None

    @property
    def client(self) -> Langfuse | None:
        """The shared Langfuse client (None without keys), for the eval harness."""
        return self._client

    def trace_id_for(self, message_id: str) -> str | None:
        """The Langfuse trace id of the run that wrote `message_id`: 32 hex characters, a pure
        function of the id. None when tracing is off, so nothing is stored for a trace that was
        never exported."""
        if self._client is None:
            return None
        return Langfuse.create_trace_id(seed=message_id)

    def score_feedback(
        self, trace_id: str, *, message_id: str, score: int, comment: str | None
    ) -> bool:
        """Attach the user's rating to the trace of the rated answer (score `user_feedback`, -1 or
        1). The score id is fixed per message, so a second rating overwrites the first instead of
        adding a score. The comment is masked like every other trace field. Best effort: returns
        False, never raises."""
        if self._client is None:
            return False
        try:
            self._client.create_score(
                name=FEEDBACK_SCORE_NAME,
                value=float(score),
                trace_id=trace_id,
                score_id=f"feedback-{message_id}",
                data_type="NUMERIC",
                comment=mask(comment) if comment else None,
            )
        except Exception:
            _log.warning("langfuse_score_failed", exc_info=True)
            return False
        return True

    def run_config(self, trace: RunTrace, *, mode: TraceMode = "run") -> dict[str, Any]:
        config: dict[str, Any] = {
            "run_name": trace.name,
            "tags": [
                f"env:{self._environment}",
                *(f"feature:{f}" for f in trace.features),
                *trace.tags,
            ],
            "metadata": self._metadata(trace),
        }
        trace_id = (
            self.trace_id_for(trace.assistant_message_id) if trace.assistant_message_id else None
        )
        if mode == "run" and (handler := self._handler(trace_id)) is not None:
            config["callbacks"] = [handler]
        return config

    def flush(self) -> None:
        if self._client is None:
            return
        try:
            self._client.flush()
        except Exception:
            _log.warning("langfuse_flush_failed", exc_info=True)

    def shutdown(self) -> None:
        if self._client is None:
            return
        try:
            self._client.shutdown()
        except Exception:
            _log.warning("langfuse_shutdown_failed", exc_info=True)

    # ------------------------------------------------------------------------------------------

    def _handler(self, trace_id: str | None = None) -> CallbackHandler | None:
        if self._client is None:
            return None
        try:
            return CallbackHandler(
                public_key=self._public_key,
                trace_context={"trace_id": trace_id} if trace_id else None,
            )
        except Exception:
            _log.warning("langfuse_handler_failed", exc_info=True)
            return None

    @staticmethod
    def _metadata(trace: RunTrace) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "langfuse_user_id": trace.user_hash,
            "langfuse_session_id": trace.conversation_id,
            "langfuse_trace_name": trace.name,
            "run_id": trace.run_id,
            "prompt_versions": {p.name: p.version for p in trace.prompts},
        }
        if trace.assistant_message_id:
            meta["assistant_message_id"] = trace.assistant_message_id
        return meta
