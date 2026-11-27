#!/usr/bin/env python3
"""After an API restart under load: wait until no conversation is left locked (TASK-031).

    python3 check_locks.py --reaped-before N --expect-reaped

A run holds a lease on its conversation (`active_run_id`). A clean stop drains runs and releases
them; a crash leaves them, and the worker's reaper must free them (60 s stale + a 30 s tick) and
leave a `failed` / `server_restart` assistant message instead of a chat that stays locked. Reads
Postgres through `docker compose exec`, so it needs nothing installed. Standard library only.
"""

import argparse
import subprocess
import sys
import time

DEFAULT_WAIT_S = 240


def psql(sql: str) -> str:
    out = subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres", "sh", "-c",
         'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -c "$0"', sql],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    return out.stdout.strip()


def locked() -> int:
    return int(psql("SELECT count(*) FROM conversations WHERE active_run_id IS NOT NULL"))


def reaped() -> int:
    return int(psql("SELECT count(*) FROM messages WHERE finish_reason = 'server_restart'"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait", type=int, default=DEFAULT_WAIT_S, help="seconds to wait")
    parser.add_argument(
        "--expect-reaped",
        action="store_true",
        help="the API was killed: runs must have been cut off and then reaped, or the scenario "
        "did not test what it claims",
    )
    parser.add_argument("--count-reaped", action="store_true", help="print the count and exit")
    parser.add_argument("--reaped-before", type=int, default=0, help="count before the restart")
    args = parser.parse_args()
    if args.count_reaped:
        print(reaped())
        return 0

    deadline = time.monotonic() + args.wait
    while (n := locked()) and time.monotonic() < deadline:
        print(f"{n} conversation(s) still locked; waiting for the reaper...", flush=True)
        time.sleep(10)
    if n:
        print(f"FAIL: {n} conversation(s) still locked after {args.wait} s")
        return 1
    print("no conversation is locked")
    if args.expect_reaped and not (count := reaped() - args.reaped_before):
        print("FAIL: nothing was reaped, so no run was cut off: the restart missed the load")
        return 1
    elif args.expect_reaped:
        print(f"{count} cut-off run(s) were reaped and ended as failed messages")
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
