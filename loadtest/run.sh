#!/usr/bin/env bash
# The target load for TASK-031: 20 concurrent streams + 5 plain API requests per second against
# the fake model, through Caddy, with pass/fail thresholds from the PRD's NFR table.
#   make loadtest                      (LOAD_RUN_SECONDS=180 LOAD_USERS=25 to change)
cd "$(dirname "$0")/.."
. loadtest/lib.sh

SECONDS_RUN="${LOAD_RUN_SECONDS:-180}"
USERS="${LOAD_USERS:-25}"   # 20 chat users + 5 API users (the locustfile's weights)

stack_up
make_invites "$USERS" "$RESULTS/invites.txt"
rm -f "$RESULTS"/run_*
run_locust "$USERS" "$SECONDS_RUN" "$RESULTS/run"
python3 loadtest/check.py "$RESULTS/run_stats.csv" "$RESULTS/run_peak.json"
