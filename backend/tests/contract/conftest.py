"""The contract tests use the shared `live_server` fixture from tests/conftest.py."""

from collections.abc import Callable

import pytest


@pytest.fixture
def live_server_url(live_server: Callable[..., str]) -> str:
    # Schemathesis sends far more than 30 messages a minute; the per-user rate limit is tested in
    # tests/api/test_chat_limits.py, so here it must not eat the fuzzer's happy-path examples.
    return live_server(chat_rate_limit_per_min=100_000)
