import json

import pytest
from app.core.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient


def log_lines(capsys: pytest.CaptureFixture[str]) -> list[dict[str, object]]:
    out = capsys.readouterr().out
    return [json.loads(line) for line in out.splitlines() if line.startswith("{")]


def test_access_log_has_request_fields_and_no_bodies_or_query(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    app = create_app(settings)
    client = TestClient(app)
    client.post(
        "/api/v1/nope?invite=SUPERSECRETCODE",
        json={"password": "hunter2-very-secret", "content": "private message"},
        headers={"authorization": "Bearer abc.def.ghi"},
    )
    raw = capsys.readouterr()
    text = raw.out + raw.err
    for forbidden in ("hunter2-very-secret", "private message", "SUPERSECRETCODE", "abc.def.ghi"):
        assert forbidden not in text

    lines = [json.loads(line) for line in raw.out.splitlines() if line.startswith("{")]
    access = [line for line in lines if line.get("event") == "http_request"]
    assert len(access) == 1
    rec = access[0]
    assert rec["method"] == "POST"
    assert rec["path"] == "/api/v1/nope"
    assert rec["status"] == 404
    assert isinstance(rec["latency_ms"], int | float)
    assert str(rec["request_id"]).startswith("req_")
    assert {"ts", "level"} <= set(rec)


def test_unhandled_error_is_logged_without_request_body(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    app = create_app(settings)

    @app.post("/api/v1/_test/boom")
    async def boom() -> None:
        raise RuntimeError("kaboom")

    TestClient(app, raise_server_exceptions=False).post(
        "/api/v1/_test/boom", json={"x": "body-secret"}
    )
    raw = capsys.readouterr()
    assert "body-secret" not in raw.out + raw.err
    assert "kaboom" in raw.out + raw.err  # the exception itself is logged for debugging


def test_third_party_info_logs_are_suppressed_but_warnings_pass(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    """HTTP client libraries log full URLs at INFO; unknown third-party loggers must stay quiet."""
    import logging

    create_app(settings)
    noisy = logging.getLogger("some.future.http.client")
    noisy.info("GET https://api.example.com/x?token=SECRET")
    noisy.warning("retrying connection")
    out = capsys.readouterr().out
    assert "SECRET" not in out
    assert "retrying connection" in out


def test_uvicorn_startup_lines_still_emitted(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    import logging

    create_app(settings)
    logging.getLogger("uvicorn.error").info("Application startup complete.")
    assert "Application startup complete." in capsys.readouterr().out
