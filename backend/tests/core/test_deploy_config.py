"""The deployment files carry security properties (TASK-030): headers, upload limit, container
hardening, published ports. A plain read of the files, so a change that drops one fails a test.

Caddy and Docker are not started here; `caddy validate` and the e2e header check (tests/e2e/
auth.spec.ts) cover that the files work.
"""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from app.core.config import Settings

ROOT = Path(__file__).resolve().parents[3]
CADDYFILE = ROOT / "deploy" / "Caddyfile"
COMPOSE = ROOT / "compose.yml"
COMPOSE_PROD = ROOT / "compose.prod.yml"

pytestmark = pytest.mark.skipif(not CADDYFILE.is_file(), reason="repository root not available")


class _Loader(yaml.SafeLoader):
    """Reads the Compose-only tags (`!reset`, `!override`) as plain values."""


def _ignore_tag(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.Node) -> Any:
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    return loader.construct_scalar(node)  # type: ignore[arg-type]


_Loader.add_multi_constructor("!", _ignore_tag)


def _compose(path: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.load(path.read_text(encoding="utf-8"), Loader=_Loader)  # noqa: S506
    return loaded


def _caddy_header_block() -> str:
    text = CADDYFILE.read_text(encoding="utf-8")
    match = re.search(r"\theader \{\n(.*?)\n\t\}", text, re.DOTALL)
    assert match, "the site block has no header { ... } section"
    return match.group(1)


def _csp() -> dict[str, str]:
    line = next(
        ln for ln in _caddy_header_block().splitlines() if ln.strip().startswith("Content-Security")
    )
    policy = re.search(r'"(.*)"', line)
    assert policy
    directives = {}
    for part in policy.group(1).split(";"):
        name, _, value = part.strip().partition(" ")
        directives[name] = value
    return directives


# ----------------------------------------------------------------------------- Caddy headers


def test_csp_denies_everything_by_default_and_closes_the_gaps_default_src_leaves() -> None:
    csp = _csp()
    assert csp["default-src"] == "'self'"
    assert csp["frame-ancestors"] == "'none'"
    # default-src does not cover these three.
    assert csp["base-uri"] == "'none'"
    assert csp["object-src"] == "'none'"
    assert csp["form-action"] == "'self'"


def test_csp_allows_no_remote_origin_and_no_inline_or_eval_code() -> None:
    for name, value in _csp().items():
        assert "http:" not in value and "https:" not in value and "*" not in value, name
        assert "'unsafe-inline'" not in value and "'unsafe-eval'" not in value, name
    # Images: our own origin and data: URIs only (a model-chosen image URL is a beacon).
    assert _csp()["img-src"] == "'self' data:"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("Strict-Transport-Security", "max-age=31536000; includeSubDomains"),
        ("X-Content-Type-Options", "nosniff"),
        ("Referrer-Policy", "no-referrer"),
        ("Cross-Origin-Opener-Policy", "same-origin"),
        ("Cross-Origin-Resource-Policy", "same-origin"),
    ],
)
def test_security_headers_are_set(name: str, value: str) -> None:
    assert re.search(rf"^\s*{name}\s+\"?{re.escape(value)}\"?\s*$", _caddy_header_block(), re.M)


def test_powerful_browser_features_are_switched_off() -> None:
    line = next(
        ln for ln in _caddy_header_block().splitlines() if ln.strip().startswith("Permissions-")
    )
    for feature in ("camera", "microphone", "geolocation"):
        assert f"{feature}=()" in line


def test_the_server_banner_is_removed() -> None:
    assert re.search(r"^\s*-Server\s*$", _caddy_header_block(), re.M)


def test_the_api_handler_never_compresses_or_buffers_the_sse_stream() -> None:
    text = CADDYFILE.read_text(encoding="utf-8")
    api = text.split("handle /api/* {", 1)[1].split("\n\t}\n", 1)[0]
    assert "encode" not in api
    assert "flush_interval -1" in api


def test_the_proxy_body_limit_is_just_above_the_upload_limit() -> None:
    text = CADDYFILE.read_text(encoding="utf-8")
    match = re.search(r"max_size (\d+)MB", text)
    assert match
    limit = int(match.group(1)) * 1024 * 1024
    upload = Settings().max_upload_bytes
    assert upload < limit <= upload + 5 * 1024 * 1024


# --------------------------------------------------------------------------------- containers


@pytest.mark.parametrize("service", ["migrate", "api", "worker"])
def test_backend_containers_drop_capabilities_and_cannot_gain_privileges(service: str) -> None:
    spec = _compose(COMPOSE)["services"][service]
    assert spec["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in spec["security_opt"]


def test_caddy_container_is_hardened_too() -> None:
    spec = _compose(COMPOSE)["services"]["caddy"]
    assert spec["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in spec["security_opt"]


def test_the_worker_that_parses_uploads_has_a_memory_and_process_limit() -> None:
    spec = _compose(COMPOSE)["services"]["worker"]
    assert spec["mem_limit"]
    assert spec["pids_limit"] >= 1


def test_the_backend_image_runs_as_a_non_root_user() -> None:
    dockerfile = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    runtime = dockerfile.split("AS runtime", 1)[1]
    assert re.search(r"^USER nexus$", runtime, re.M)


def test_in_dev_every_published_port_is_bound_to_loopback() -> None:
    for name, spec in _compose(COMPOSE)["services"].items():
        for port in spec.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), f"{name} publishes {port} to every interface"


def test_in_production_only_caddy_publishes_ports() -> None:
    services = _compose(COMPOSE_PROD)["services"]
    for name, spec in services.items():
        if name != "caddy":
            assert not spec.get("ports"), f"{name} must not publish a port in production"
    assert sorted(services["caddy"]["ports"]) == ["443:443", "80:80"]
