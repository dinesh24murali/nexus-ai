import re

import pytest
from app.core.config import Settings
from app.core.errors import ApiError
from app.main import create_app
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

REQ_ID = re.compile(r"^req_[0-9a-f]{16,}$")


class Body(BaseModel):
    email: str
    password: str


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    app = create_app(settings)

    @app.post("/api/v1/_test/validate")
    async def validate(body: Body) -> dict[str, str]:  # pragma: no cover - never reached
        return {"ok": body.email}

    @app.get("/api/v1/_test/api-error")
    async def api_error() -> None:
        raise ApiError(409, "EMAIL_TAKEN", "Email already registered.", details={"field": "email"})

    @app.get("/api/v1/_test/boom")
    async def boom() -> None:
        raise RuntimeError("secret internal detail")

    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def assert_envelope(resp_json: dict[str, object], code: str) -> dict[str, object]:
    assert set(resp_json) == {"error"}
    err = resp_json["error"]
    assert isinstance(err, dict)
    assert err["code"] == code
    assert isinstance(err["message"], str)
    assert isinstance(err["request_id"], str)
    assert REQ_ID.match(err["request_id"])
    return err


def test_unknown_route_returns_envelope_with_request_id(client: TestClient) -> None:
    r = client.get("/api/v1/nope")
    assert r.status_code == 404
    err = assert_envelope(r.json(), "NOT_FOUND")
    assert r.headers["x-request-id"] == err["request_id"]


def test_wrong_method_returns_envelope(client: TestClient) -> None:
    r = client.delete("/api/v1/healthz")
    assert r.status_code == 405
    assert_envelope(r.json(), "METHOD_NOT_ALLOWED")


def test_api_error_uses_status_code_and_details(client: TestClient) -> None:
    r = client.get("/api/v1/_test/api-error")
    assert r.status_code == 409
    err = assert_envelope(r.json(), "EMAIL_TAKEN")
    assert err["details"] == {"field": "email"}


def test_validation_error_is_422_and_never_echoes_input(client: TestClient) -> None:
    r = client.post("/api/v1/_test/validate", json={"email": "a@b.co", "password": 12345})
    assert r.status_code == 422
    err = assert_envelope(r.json(), "VALIDATION_FAILED")
    text = r.text
    assert "12345" not in text, "submitted values must not be echoed back"
    details = err["details"]
    assert isinstance(details, dict)
    fields = details["fields"]
    assert isinstance(fields, list)
    assert fields[0]["loc"] == ["body", "password"]


def test_malformed_json_is_422_envelope(client: TestClient) -> None:
    r = client.post(
        "/api/v1/_test/validate", content=b"{not json", headers={"content-type": "application/json"}
    )
    assert r.status_code == 422
    assert_envelope(r.json(), "VALIDATION_FAILED")


def test_unhandled_exception_is_500_without_leaking_detail(client: TestClient) -> None:
    r = client.get("/api/v1/_test/boom")
    assert r.status_code == 500
    assert_envelope(r.json(), "INTERNAL_ERROR")
    assert "secret internal detail" not in r.text


def test_incoming_request_id_is_honoured_when_well_formed(client: TestClient) -> None:
    r = client.get("/api/v1/nope", headers={"x-request-id": "req_0123456789abcdef"})
    assert r.json()["error"]["request_id"] == "req_0123456789abcdef"


def test_malformed_incoming_request_id_is_replaced(client: TestClient) -> None:
    r = client.get("/api/v1/nope", headers={"x-request-id": "bad id\nwith newline"})
    assert REQ_ID.match(r.json()["error"]["request_id"])


def test_undecodable_json_body_is_422_like_other_malformed_bodies(client: TestClient) -> None:
    """json.loads sniffs UTF-16/32 from the first bytes; a bad payload there makes FastAPI answer
    with a generic 400 instead of a validation error. The contract says 422 for every bad body."""
    r = client.post(
        "/api/v1/_test/validate",
        content=b"\x00\x00\x00\x00\xff\xff\xff\xff",
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 422
    err = assert_envelope(r.json(), "VALIDATION_FAILED")
    details = err["details"]
    assert isinstance(details, dict) and details["fields"][0]["loc"] == ["body"]
