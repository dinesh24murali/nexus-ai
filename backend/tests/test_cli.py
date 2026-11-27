"""Admin CLI (`python -m app.cli`) against a real database, end to end with the API."""

from typing import Any

import psycopg
import pytest
from app.cli import main
from app.core.config import Settings
from fastapi.testclient import TestClient

from .api.test_auth import API, PASSWORD, login, refresh_cookie, register_body, with_cookie

pytestmark = pytest.mark.integration


@pytest.fixture
def cli(settings: Settings, db_url: str, capsys: pytest.CaptureFixture[str]) -> Any:
    cfg = settings.model_copy(update={"database_url": db_url})

    def run(*argv: str) -> tuple[int, str]:
        capsys.readouterr()
        code = main(list(argv), settings=cfg)
        return code, capsys.readouterr().out

    return run


def created_code(output: str) -> str:
    return next(tok for tok in output.split() if tok.startswith("nx_"))


# ───────────────────────────── invites ─────────────────────────────


def test_invite_create_yields_a_code_that_registers_a_user(client: TestClient, cli: Any) -> None:
    code, out = cli("invite", "create", "--expires", "7d", "--note", "Priya")
    assert code == 0
    invite = created_code(out)
    assert f"register?invite={invite}" in out
    r = client.post(f"{API}/auth/register", json=register_body(invite))
    assert r.status_code == 201


def test_invite_codes_are_not_stored_in_plaintext(cli: Any, db: psycopg.Connection[Any]) -> None:
    _, out = cli("invite", "create")
    invite = created_code(out)
    stored = [r[0] for r in db.execute("SELECT code_hash FROM invites").fetchall()]
    assert stored and invite not in stored


def test_invite_expiry_is_honoured(
    client: TestClient, cli: Any, db: psycopg.Connection[Any]
) -> None:
    cli("invite", "create", "--expires", "2h")
    (hours,) = db.execute(
        "SELECT round(extract(epoch FROM (expires_at - now())) / 3600) FROM invites"
    ).fetchone() or (None,)
    assert hours == 2


@pytest.mark.parametrize("bad", ["soon", "0d", "-1d", "7", "7w"])
def test_invite_create_rejects_bad_expiry(cli: Any, bad: str) -> None:
    code, _ = cli("invite", "create", "--expires", bad)
    assert code != 0


def test_invite_list_shows_status_and_hides_codes(client: TestClient, cli: Any) -> None:
    _, a = cli("invite", "create", "--note", "unused-one")
    _, b = cli("invite", "create", "--note", "will-be-used")
    client.post(f"{API}/auth/register", json=register_body(created_code(b)))
    code, out = cli("invite", "list")
    assert code == 0
    rows = {line.split()[-1]: line for line in out.splitlines()}
    assert " active " in rows["unused-one"]
    assert " used " in rows["will-be-used"]
    assert created_code(a) not in out, "codes are only shown at creation"


def test_invite_revoke_blocks_registration(
    client: TestClient, cli: Any, db: psycopg.Connection[Any]
) -> None:
    _, out = cli("invite", "create")
    invite = created_code(out)
    (invite_id,) = db.execute("SELECT id FROM invites").fetchone() or (None,)
    code, _ = cli("invite", "revoke", str(invite_id))
    assert code == 0
    r = client.post(f"{API}/auth/register", json=register_body(invite))
    assert r.status_code == 403 and r.json()["error"]["code"] == "INVITE_INVALID"


def test_invite_revoke_accepts_the_code_itself(client: TestClient, cli: Any) -> None:
    """FS-001: `invite revoke <code>`. Handy right after creating one, or when the code was sent."""
    _, out = cli("invite", "create")
    invite = created_code(out)
    code, _ = cli("invite", "revoke", invite)
    assert code == 0
    r = client.post(f"{API}/auth/register", json=register_body(invite))
    assert r.status_code == 403


def test_invite_revoke_unknown_code_fails(cli: Any) -> None:
    code, out = cli("invite", "revoke", "nx_" + "0" * 32)
    assert code == 1 and "not found" in out.lower()


def test_invite_revoke_rejects_garbage_argument(cli: Any) -> None:
    code, _ = cli("invite", "revoke", "whatever")
    assert code != 0


def test_invite_revoke_unknown_id_fails(cli: Any) -> None:
    code, out = cli("invite", "revoke", "00000000-0000-0000-0000-000000000000")
    assert code == 1 and "not found" in out.lower()


# ───────────────────────────── users ─────────────────────────────


def test_reset_password_changes_credentials_and_signs_out_everywhere(
    client: TestClient, cli: Any, make_user: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_user()
    session = refresh_cookie(login(client))
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("a-brand-new-passphrase\n"))
    code, _ = cli("reset-password", "marco@example.com", "--password-stdin")
    assert code == 0
    assert login(client, password=PASSWORD).status_code == 401
    assert login(client, password="a-brand-new-passphrase").status_code == 200
    assert client.post(f"{API}/auth/refresh", headers=with_cookie(session)).status_code == 401


def test_reset_password_enforces_minimum_length(
    cli: Any, make_user: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_user()
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("short\n"))
    code, _ = cli("reset-password", "marco@example.com", "--password-stdin")
    assert code != 0


def test_reset_password_unknown_user(cli: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("a-brand-new-passphrase\n"))
    code, out = cli("reset-password", "ghost@example.com", "--password-stdin")
    assert code == 1 and "not found" in out.lower()


def test_disable_user_locks_out_immediately_and_enable_restores(
    client: TestClient, cli: Any, make_user: Any
) -> None:
    make_user()
    r = login(client)
    access, session = r.json()["access_token"], refresh_cookie(r)

    assert cli("disable-user", "marco@example.com")[0] == 0
    assert client.get(f"{API}/me", headers={"authorization": f"Bearer {access}"}).status_code == 401
    assert client.post(f"{API}/auth/refresh", headers=with_cookie(session)).status_code == 401
    blocked = login(client)
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "ACCOUNT_DISABLED"

    assert cli("enable-user", "marco@example.com")[0] == 0
    assert login(client).status_code == 200


def test_disable_unknown_user_fails(cli: Any) -> None:
    code, out = cli("disable-user", "ghost@example.com")
    assert code == 1 and "not found" in out.lower()


# ───────────────────────────── flags ─────────────────────────────


def test_flags_set_and_list(cli: Any) -> None:
    assert cli("flags", "set", "TOOLS_DISABLED=web_search", "READ_ONLY_MODE=1")[0] == 0
    code, out = cli("flags", "list")
    assert code == 0
    assert "TOOLS_DISABLED" in out and "web_search" in out
    assert "READ_ONLY_MODE" in out and "1" in out
    assert "CHAT_MODEL_OVERRIDE" in out, "unset flags are listed too, with their default"


@pytest.mark.parametrize("arg", ["NOT_A_FLAG=1", "READ_ONLY_MODE=maybe", "READ_ONLY_MODE"])
def test_flags_set_rejects_bad_input(cli: Any, arg: str) -> None:
    code, _ = cli("flags", "set", arg)
    assert code != 0
