#!/usr/bin/env bash
# Restart the API in the middle of the load and check that nothing stays locked (TASK-031).
#   loadtest/restart.sh graceful   SIGTERM: the API drains its open runs (ADR-0006), then restarts
#   loadtest/restart.sh kill       SIGKILL: runs are cut off, the worker's reaper must free them
cd "$(dirname "$0")/.."
. loadtest/lib.sh

MODE="${1:?usage: restart.sh graceful|kill}"
USERS="${LOAD_USERS:-25}"
LOAD_SECONDS="${LOAD_RUN_SECONDS:-150}"
RESTART_AT="${RESTART_AT_SECONDS:-45}"   # after the ramp, with every stream open

stack_up
make_invites "$USERS" "$RESULTS/invites.txt"
make_invites 5 "$RESULTS/invites_recovery.txt"   # a used code is refused: the recovery run needs its own
rm -f "$RESULTS"/restart_*
REAPED_BEFORE="$(python3 loadtest/check_locks.py --count-reaped)"

# Failures are expected while the API is away: the load run's own pass/fail does not count here.
run_locust "$USERS" "$LOAD_SECONDS" "$RESULTS/restart_load" --exit-code-on-error 0 &
LOCUST_PID=$!

sleep "$RESTART_AT"
echo ">> $MODE restart of the API at +${RESTART_AT}s"
case "$MODE" in
  graceful) $COMPOSE restart api ;;
  kill)     $COMPOSE kill -s SIGKILL api && $COMPOSE up -d --wait api ;;
  *) echo "unknown mode $MODE" >&2; kill "$LOCUST_PID"; exit 2 ;;
esac
wait "$LOCUST_PID" || true

echo ">> waiting for locks to clear"
if [ "$MODE" = kill ]; then
  python3 loadtest/check_locks.py --reaped-before "$REAPED_BEFORE" --expect-reaped
else
  python3 loadtest/check_locks.py
fi

echo ">> recovery: a short run against the restarted API must be clean"
INVITES_FILE="$RESULTS/invites_recovery.txt" run_locust 5 30 "$RESULTS/restart_after"
python3 - <<'PY'
import csv, sys
rows = list(csv.DictReader(open("loadtest/results/restart_after_stats.csv")))
failures = sum(int(r["Failure Count"]) for r in rows)
count = sum(int(r["Request Count"]) for r in rows if r["Name"] != "Aggregated")
print(f"recovery run: {count} requests, {failures} failures")
sys.exit(1 if failures or not count else 0)
PY
echo PASS
