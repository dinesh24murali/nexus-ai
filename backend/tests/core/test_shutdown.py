import os
import signal
import threading
from collections.abc import Iterator

import pytest
from app.core.shutdown import chain_sigterm


@pytest.fixture(autouse=True)
def _restore_sigterm() -> Iterator[None]:
    original = signal.getsignal(signal.SIGTERM)
    yield
    signal.signal(signal.SIGTERM, original)


def test_the_callback_runs_first_and_the_previous_handler_still_runs() -> None:
    order: list[str] = []
    signal.signal(signal.SIGTERM, lambda *_: order.append("previous"))
    restore = chain_sigterm(lambda: order.append("callback"))

    os.kill(os.getpid(), signal.SIGTERM)

    assert order == ["callback", "previous"]
    restore()


def test_restore_puts_the_previous_handler_back() -> None:
    calls: list[str] = []
    signal.signal(signal.SIGTERM, lambda *_: calls.append("previous"))
    restore = chain_sigterm(lambda: calls.append("callback"))
    restore()

    os.kill(os.getpid(), signal.SIGTERM)

    assert calls == ["previous"]


def test_a_failing_callback_does_not_swallow_the_signal() -> None:
    calls: list[str] = []
    signal.signal(signal.SIGTERM, lambda *_: calls.append("previous"))

    def boom() -> None:
        raise RuntimeError("callback bug")

    chain_sigterm(boom)
    os.kill(os.getpid(), signal.SIGTERM)

    assert calls == ["previous"]


def test_off_the_main_thread_it_is_a_no_op() -> None:
    """Signals can only be installed from the main thread (the app also runs under test threads)."""
    result: list[object] = []
    thread = threading.Thread(target=lambda: result.append(chain_sigterm(lambda: None)))
    thread.start()
    thread.join()

    (restore,) = result
    assert callable(restore)
    restore()  # harmless
