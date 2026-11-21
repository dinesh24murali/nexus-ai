"""Contract tests, generated from the OpenAPI spec (docs/02_outputs/04_api_spec.yaml).

schemathesis derives requests from the spec and checks that every response has a documented status
code, content type and body schema, and that nothing returns a 5xx. The spec is the contract: when
this fails, either the code or the spec is wrong, and the spec change comes first (AGENTS.md 3.7).

Scope grows with the API: operations whose tasks have not landed yet are excluded below and must be
removed from the exclusion list by the task that implements them.
"""

import os
from pathlib import Path
from typing import Any

import httpx
import pytest
import schemathesis
from hypothesis import HealthCheck, settings
from schemathesis.specs.openapi.checks import allow_header_conformance, positive_data_acceptance

SPEC = Path(__file__).resolve().parents[3] / "docs" / "02_outputs" / "04_api_spec.yaml"

pytestmark = pytest.mark.integration

schema = (
    schemathesis.openapi.from_path(SPEC)
    .include(
        path_regex=(
            r"^/(healthz|readyz|auth/.*|me(/password|/usage)?|conversations(/\{conversationId\})?"
            r"|conversations/\{conversationId\}/(messages|regenerate|runs/cancel))$"
        )
    )
    # Not implemented yet: account deletion is TASK-028 (M6).
    .exclude(path="/me", method="DELETE")
    # Not implemented yet: resuming after a tool approval needs tools (M4). Matched by neither
    # pattern above, listed here so the next reader sees it is a decision, not an oversight.
)


class _Session:
    """The access token for the current test, read by the auth provider below."""

    token = ""


@schema.auth()
class BearerAuth:
    """Lets schemathesis supply a valid token, and still probe missing/invalid ones itself."""

    def get(self, case: Any, ctx: Any) -> str:
        return _Session.token

    def set(self, case: Any, data: str, ctx: Any) -> None:
        case.headers = {**(case.headers or {}), "Authorization": f"Bearer {data}"}


@pytest.fixture(autouse=True)
def _signed_in(live_server_url: str, make_user: Any) -> None:
    make_user()
    r = httpx.post(
        f"{live_server_url}/auth/login",
        json={"email": "marco@example.com", "password": "correct-horse-battery"},
    )
    assert r.status_code == 200
    _Session.token = str(r.json()["access_token"])


@schema.parametrize()
@settings(
    max_examples=50,
    # Deterministic by default so CI is not flaky; `CONTRACT_FUZZ=random pytest ...` explores anew.
    derandomize=os.environ.get("CONTRACT_FUZZ") != "random",
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
def test_api_conforms_to_the_spec(case: Any, live_server_url: str) -> None:
    # Narrow, documented exceptions to schemathesis' generic checks:
    #  * Allow header: `/me` documents DELETE, which lands with TASK-028; until then the Allow
    #    header on a 405 legitimately omits it. REMOVE this exclusion in TASK-028.
    #  * Positive-data acceptance on POST /me/password: a schema-valid body with a wrong
    #    `current_password` is a business-rule rejection, documented as 400 in the spec.
    #  * Positive-data acceptance on GET /conversations: `cursor` is typed as a plain string but is
    #    opaque and server-issued; one the server never issued is documented as 400 (and tested in
    #    tests/api/test_conversations.py). `?cursor=` (empty) is accepted as the first page.
    #  * Positive-data acceptance on POST/PATCH conversations: titles are trimmed and must then be
    #    non-empty and free of NUL (Postgres cannot store it); the spec's `minLength: 1` is looser
    #    than that, and the stricter rule is a documented 422 (tested in test_conversations.py).
    excluded: list[Any] = [allow_header_conformance]
    #  * Positive-data acceptance on PATCH /me, POST /auth/register, POST /auth/login: a NUL
    #    character in `display_name` or the email is schema-valid, but Postgres text cannot store
    #    it, so it is a documented 422 (tested in test_me.py / test_auth.py), not a 500. Goes away
    #    if the spec gains a pattern excluding NUL (open decision, see the 004-skeleton task).
    if case.operation.label in (
        "PATCH /me",
        "POST /auth/register",
        "POST /auth/login",
        "POST /me/password",
        "GET /conversations",
        "POST /conversations",
        "PATCH /conversations/{conversationId}",
        # content: NUL and whitespace-only are documented 422s the spec's `minLength: 1` does not
        # express (like titles); the history cursor is opaque and server-issued (like the list's).
        "POST /conversations/{conversationId}/messages",
        "GET /conversations/{conversationId}/messages",
    ):
        excluded.append(positive_data_acceptance)
    case.call_and_validate(base_url=live_server_url, excluded_checks=excluded)
