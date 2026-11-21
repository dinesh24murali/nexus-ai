"""Fixtures shared by the API tests."""

from collections.abc import Callable, Generator
from typing import Any

import pytest
from app.core.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient


@pytest.fixture
def make_client(settings: Settings, db_url: str) -> Generator[Callable[..., TestClient]]:
    """A client with settings overrides (small limits make the limit tests cheap)."""
    clients: list[TestClient] = []

    def make(**overrides: Any) -> TestClient:
        app = create_app(settings.model_copy(update={"database_url": db_url, **overrides}))
        c = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
        c.__enter__()
        clients.append(c)
        return c

    yield make
    for c in clients:
        c.__exit__(None, None, None)
