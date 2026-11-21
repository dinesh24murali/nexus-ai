"""Run the real app under uvicorn (in a thread) so schemathesis can fuzz it over HTTP."""

import socket
import threading
import time
from collections.abc import Iterator

import pytest
import uvicorn
from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def live_server_url(settings: Settings, db_url: str) -> Iterator[str]:
    app = create_app(settings.model_copy(update={"database_url": db_url}))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, lifespan="on")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "server did not start"
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}/api/v1"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
