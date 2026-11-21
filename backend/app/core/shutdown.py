"""Run a callback the moment SIGTERM arrives, then let the previous handler do its work.

Uvicorn stops accepting connections on SIGTERM and only runs lifespan shutdown after open requests
finish. The API must refuse NEW runs (503 `SERVICE_RESTARTING`) from the first moment, so this
handler flips the drain flag first and then hands the signal to uvicorn's own handler untouched.
"""

import os
import signal
import threading
from collections.abc import Callable
from types import FrameType

import structlog

_log = structlog.get_logger("nexus.shutdown")


def chain_sigterm(callback: Callable[[], None]) -> Callable[[], None]:
    """Install the chain; returns a function that restores the previous handler.

    Off the main thread (tests run the app in a thread) signals cannot be installed: no-op.
    """
    if threading.current_thread() is not threading.main_thread():
        return lambda: None
    previous = signal.getsignal(signal.SIGTERM)

    def handler(signum: int, frame: FrameType | None) -> None:
        try:
            callback()
        except Exception:
            _log.exception("sigterm_callback_failed")  # never swallow the signal itself
        if callable(previous):
            previous(signum, frame)
        elif previous == signal.SIG_DFL:
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            os.kill(os.getpid(), signal.SIGTERM)

    signal.signal(signal.SIGTERM, handler)

    def restore() -> None:
        signal.signal(signal.SIGTERM, previous)

    return restore
