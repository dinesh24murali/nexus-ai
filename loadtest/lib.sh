# Shared by run.sh and restart.sh. Run from the repository root.
set -euo pipefail

COMPOSE="docker compose -f compose.yml -f loadtest/compose.loadtest.yml"
HOST="${LOAD_HOST:-http://localhost:${CADDY_HTTP_PORT:-8080}}"
RESULTS="loadtest/results"
mkdir -p "$RESULTS"

# The stack in fake mode (the overlay) and healthy.
stack_up() {
  $COMPOSE up -d --build --wait
}

# make_invites <count> <file>: one real invite code per line, minted with the admin CLI.
make_invites() {
  local count="$1" file="$2"
  seq "$count" | xargs -P 6 -I{} sh -c \
    "$COMPOSE exec -T api python -m app.cli invite create --note loadtest | sed -n 's/^  code:  //p'" \
    >"$file"
  [ "$(wc -l <"$file")" -ge "$count" ] || { echo "could not create $count invites" >&2; exit 1; }
}

# run_locust <users> <seconds> <csv prefix> [extra locust args]: headless, then writes the stats.
run_locust() {
  local users="$1" seconds="$2" prefix="$3"
  shift 3
  LOAD_USERS="$users" LOAD_RUN_SECONDS="$seconds" \
    LOAD_INVITES_FILE="${INVITES_FILE:-$RESULTS/invites.txt}" LOAD_PEAK_FILE="${prefix}_peak.json" \
    uv run --project loadtest locust -f loadtest/locustfile.py --headless --host "$HOST" \
    --csv "$prefix" "$@"
}
