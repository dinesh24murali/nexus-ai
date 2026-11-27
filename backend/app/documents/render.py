"""Render a document to PDF in a child process that can be killed (ADR-0011, doc 10 LLM-T18).

WeasyPrint is CPU-bound and cannot be interrupted from inside the process, so a pathological
document would keep a thread busy after any timeout. The parent starts `worker_main` instead, feeds
it the HTML on stdin, and kills it at the time limit. The child gets a minimal environment (no API
keys, no database URL: the renderer needs none of them) and writes to a `.part` file that is moved
into place only after it succeeded, so a killed render leaves no half-written PDF.

Failures are `RenderError` with a fixed, user-safe reason; the child's stderr is discarded (it can
echo parts of the document).
"""

import asyncio
import contextlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import structlog

from app.documents.worker_main import EXIT_TOO_LONG

_log = structlog.get_logger("nexus.documents")

RENDER_TIMEOUT_S = 50.0  # the job's hard limit is 60 s (doc 08 section 9)
MAX_PAGES = 30
REASON_TIMEOUT = "Rendering took too long."
REASON_TOO_LONG = f"The document is longer than {MAX_PAGES} pages."
REASON_FAILED = "Couldn't create the PDF."

# What the child needs to find Python, Pango and fonts; nothing else is passed on.
_ENV_KEEP = (
    "PATH", "LANG", "LC_ALL", "HOME", "TMPDIR", "XDG_CACHE_HOME", "XDG_DATA_DIRS",
    "FONTCONFIG_FILE", "FONTCONFIG_PATH", "PYTHONPATH", "VIRTUAL_ENV",
)  # fmt: skip


class RenderError(Exception):
    """The PDF was not made. `reason` is safe to show the user."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Rendered:
    pages: int
    size_bytes: int
    denied: int  # requests for resources the page was not allowed to load (a metric, gate = 0)


def child_env() -> dict[str, str]:
    return {k: os.environ[k] for k in _ENV_KEEP if k in os.environ}


async def render_pdf(
    page_html: str, out: Path, *, timeout_s: float = RENDER_TIMEOUT_S, max_pages: int = MAX_PAGES
) -> Rendered:
    """Render `page_html` to `out` (parent directory must exist)."""
    part = out.with_name(out.name + ".part")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.documents.worker_main",
        str(part),
        str(max_pages),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=child_env(),
    )
    try:
        stdout, _ = await asyncio.wait_for(
            process.communicate(page_html.encode("utf-8")), timeout=timeout_s
        )
    except TimeoutError:
        process.kill()
        await process.wait()
        part.unlink(missing_ok=True)
        _log.warning("render_timeout", timeout_s=timeout_s)
        raise RenderError(REASON_TIMEOUT) from None
    except BaseException:  # cancelled (the job's own timeout): do not leave the child running
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()
        part.unlink(missing_ok=True)
        raise
    if process.returncode == EXIT_TOO_LONG:
        raise RenderError(REASON_TOO_LONG)
    try:
        if process.returncode != 0:
            raise ValueError("render failed")
        result = json.loads(stdout.decode("utf-8").strip().splitlines()[-1])
        pages, denied = int(result["pages"]), int(result["denied"])
        os.replace(part, out)
        size = os.stat(out).st_size
    except ValueError, KeyError, IndexError, OSError:
        part.unlink(missing_ok=True)
        _log.warning("render_failed", exit_code=process.returncode)
        raise RenderError(REASON_FAILED) from None
    return Rendered(pages=pages, size_bytes=size, denied=denied)
