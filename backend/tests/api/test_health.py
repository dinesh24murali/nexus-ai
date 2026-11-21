from collections.abc import Awaitable, Callable

import pytest
from app.core.config import Settings
from app.core.health import Check
from app.main import create_app
from fastapi.testclient import TestClient


async def up() -> bool:
    return True


async def down() -> bool:
    return False


async def raises() -> bool:
    raise ConnectionError("nope")


def client_with(settings: Settings, **checks: Callable[[], Awaitable[bool]]) -> TestClient:
    app = create_app(settings)
    app.state.readiness_checks = [
        Check(name=n, fn=fn, required=n != "langfuse") for n, fn in checks.items()
    ]
    return TestClient(app)


def test_healthz_is_always_ok(settings: Settings) -> None:
    r = TestClient(create_app(settings)).get("/api/v1/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_readyz_ok_when_all_up(settings: Settings) -> None:
    r = client_with(settings, postgres=up, qdrant=up, langfuse=up).get("/api/v1/readyz")
    assert r.status_code == 200
    assert r.json() == {
        "status": "ok",
        "checks": {"postgres": "ok", "qdrant": "ok", "langfuse": "ok"},
    }


def test_readyz_degraded_but_200_when_only_langfuse_down(settings: Settings) -> None:
    r = client_with(settings, postgres=up, qdrant=up, langfuse=down).get("/api/v1/readyz")
    assert r.status_code == 200
    assert r.json()["status"] == "degraded"
    assert r.json()["checks"]["langfuse"] == "unavailable"


@pytest.mark.parametrize("dead", ["postgres", "qdrant"])
def test_readyz_503_when_required_dependency_down(settings: Settings, dead: str) -> None:
    checks = {"postgres": up, "qdrant": up, "langfuse": up} | {dead: down}
    r = client_with(settings, **checks).get("/api/v1/readyz")
    assert r.status_code == 503
    assert r.json()["status"] == "unavailable"
    assert r.json()["checks"][dead] == "unavailable"


def test_readyz_treats_raising_check_as_unavailable(settings: Settings) -> None:
    r = client_with(settings, postgres=raises, qdrant=up).get("/api/v1/readyz")
    assert r.status_code == 503
    assert r.json()["checks"]["postgres"] == "unavailable"


@pytest.mark.parametrize("path", ["/api/v1/healthz", "/api/v1/readyz"])
def test_probes_need_no_auth(settings: Settings, path: str) -> None:
    assert client_with(settings, postgres=up, qdrant=up).get(path).status_code == 200
