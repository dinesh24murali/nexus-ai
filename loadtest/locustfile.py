"""Load scenario for TASK-031: 20 concurrent chat streams and 5 plain API requests per second.

    ChatUser (x20): signs in, then keeps one answer streaming at a time (`[[fixture:long]]`, word
                    by word at the fake model's delay). Records the time to the first token
                    ("chat ttft") and the whole answer ("chat total").
    ApiUser  (x5):  one request per second each, mixed reads of the kind the UI makes.

The model is the scripted fake, so what these numbers measure is everything else: Caddy, the API,
Postgres, the run lease and the SSE path. The thresholds are in `check.py`.

Needs one invite code per simulated user in the file named by `LOAD_INVITES_FILE` (one per line;
`run.sh` makes them). Each user registers once, with its own address.
"""

import json
import os
import threading
import time
import uuid
from pathlib import Path

from requests.exceptions import RequestException
from locust import HttpUser, LoadTestShape, constant, constant_throughput, events, task

PASSWORD = "load-test-password-1"
STREAM_PROMPT = "[[fixture:long]] Tell me something long."
# A new conversation after this many sends keeps the history (and so the cost per turn) bounded.
SENDS_PER_CONVERSATION = 4

_lock = threading.Lock()
_invites: list[str] | None = None
_open_streams = 0
_peak_streams = 0


def _stream_opened() -> None:
    global _open_streams, _peak_streams
    with _lock:
        _open_streams += 1
        _peak_streams = max(_peak_streams, _open_streams)


def _stream_closed() -> None:
    global _open_streams
    with _lock:
        _open_streams -= 1


@events.quitting.add_listener
def _write_peak(environment: object, **_: object) -> None:
    """The most streams that were open at once, for `check.py` (the target is 20)."""
    if path := os.environ.get("LOAD_PEAK_FILE"):
        Path(path).write_text(json.dumps({"peak_streams": _peak_streams}))


def next_invite() -> str:
    global _invites
    with _lock:
        if _invites is None:
            path = Path(os.environ["LOAD_INVITES_FILE"])
            _invites = [ln.strip() for ln in path.read_text().splitlines() if ln.strip()]
        if not _invites:
            raise RuntimeError("out of invite codes: create more (run.sh INVITES=...)")
        return _invites.pop()


class Account(HttpUser):
    abstract = True

    token = ""
    conversation_id = ""

    def on_start(self) -> None:
        try:
            invite = next_invite()
        except RuntimeError as exc:
            self.give_up(str(exc))
            return
        email = f"load-{uuid.uuid4().hex[:12]}@example.com"
        with self.client.post(
            "/api/v1/auth/register",
            json={
                "invite_code": invite,
                "email": email,
                "password": PASSWORD,
                "display_name": "Load",
            },
            name="setup register",
            catch_response=True,
        ) as response:
            if response.status_code != 201:
                response.failure(f"register: {response.status_code} {response.text[:200]}")
                self.give_up("could not register")
                return
            self.token = response.json()["access_token"]
        # The refresh cookie is not needed again: a run is far shorter than the token's 15 minutes.
        self.client.cookies.clear()
        if not self.new_conversation():
            self.give_up("could not create a conversation")

    def give_up(self, reason: str) -> None:
        """A user that cannot set up ends the run: Locust would replace it, burning one invite
        per try, and the numbers would describe a load that never happened."""
        events.request.fire(
            request_type="SETUP",
            name="setup",
            response_time=0,
            response_length=0,
            exception=RuntimeError(reason),
            context={},
        )
        self.environment.runner.quit()

    @property
    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def new_conversation(self) -> bool:
        """False when it failed; the caller decides whether that ends the run (only setup does:
        mid-run, the API may be restarting on purpose and the old conversation still works)."""
        with self.client.post(
            "/api/v1/conversations",
            json={},
            headers=self.auth,
            name="setup conversation",
            catch_response=True,
        ) as response:
            if response.status_code != 201:
                response.failure(f"{response.status_code} {response.text[:200]}")
                return False
            self.conversation_id = response.json()["id"]
            return True


class ChatUser(Account):
    weight = 20
    wait_time = constant(0.5)
    sends = 0

    @task
    def stream_an_answer(self) -> None:
        if self.sends and self.sends % SENDS_PER_CONVERSATION == 0:
            self.new_conversation()
        self.sends += 1
        started = time.perf_counter()
        first_token: float | None = None
        done = False
        error = ""
        with self.client.post(
            f"/api/v1/conversations/{self.conversation_id}/messages",
            json={"content": STREAM_PROMPT},
            headers={**self.auth, "Accept": "text/event-stream"},
            stream=True,
            name="chat send",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"{response.status_code} {response.text[:200]}")
                return
            response.encoding = "utf-8"
            _stream_opened()
            try:
                for line in response.iter_lines(decode_unicode=True):
                    if line.startswith("event: "):
                        kind = line[7:].strip()
                        if kind == "token" and first_token is None:
                            first_token = time.perf_counter()
                        elif kind == "done":
                            done = True
                        elif kind == "error":
                            error = "error event"
            except RequestException as exc:  # the connection died mid-stream (API restarted)
                response.failure(f"stream broke: {type(exc).__name__}")
                return
            finally:
                _stream_closed()
            if error or not done or first_token is None:
                response.failure(error or "stream ended without done")
                return
        ended = time.perf_counter()
        for name, seconds in (("chat ttft", first_token - started), ("chat total", ended - started)):
            events.request.fire(
                request_type="SSE",
                name=name,
                response_time=seconds * 1000,
                response_length=0,
                exception=None,
                context={},
            )


class ApiUser(Account):
    weight = 5
    wait_time = constant_throughput(1)  # one request per second per user

    @task(4)
    def me(self) -> None:
        self.client.get("/api/v1/me", headers=self.auth, name="api me")

    @task(3)
    def list_conversations(self) -> None:
        self.client.get("/api/v1/conversations?limit=20", headers=self.auth, name="api conversations")

    @task(2)
    def usage(self) -> None:
        self.client.get("/api/v1/me/usage", headers=self.auth, name="api usage")

    @task(1)
    def messages(self) -> None:
        self.client.get(
            f"/api/v1/conversations/{self.conversation_id}/messages?limit=20",
            headers=self.auth,
            name="api messages",
        )


class Steady(LoadTestShape):
    """All users at once after a short ramp (registration is slow: argon2), then steady."""

    def tick(self) -> tuple[int, float] | None:
        run_time = self.get_run_time()
        if run_time > float(os.environ.get("LOAD_RUN_SECONDS", "120")):
            return None
        users = int(os.environ.get("LOAD_USERS", "25"))
        return users, max(1.0, users / 10)
