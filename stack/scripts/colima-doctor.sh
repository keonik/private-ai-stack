#!/usr/bin/env bash
# Colima can die from the outside in: the VM runs, `colima status` says "running", every container is up
# inside it — and yet the host can reach none of them. The tunnels and the key gateway proxy to
# 127.0.0.1:<published port>, so when that plumbing dies every public service 502s at once while nothing
# looks broken. Seen 2026-09-15; the fix is `colima restart`.
#
# This finds that state and fixes it. It deliberately does nothing when colima is stopped: stopping it is
# an operator decision, not a fault. A restart is never repeated inside the cooldown, so a machine that
# cannot recover reports instead of thrashing.
#
#   colima-doctor.sh            # check, and repair if broken (run from keepalive)
#   colima-doctor.sh --check    # say what it sees and change nothing
set -uo pipefail
cd "$(dirname "$0")/.."

STATE=${COLIMA_DOCTOR_STATE:-$HOME/.local/state/private-ai-stack}
COOLDOWN=${COLIMA_DOCTOR_COOLDOWN:-1800}   # seconds between restarts; a loop of restarts helps nobody
MIN_PORTS=${COLIMA_DOCTOR_MIN_PORTS:-2}    # "everything is unreachable" needs more than one thing
mkdir -p "$STATE"
LAST=$STATE/colima-doctor.last
DRY=${COLIMA_DOCTOR_DRY:-0}
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

# NTFY_URL from the environment wins, so a drill can run without buzzing anyone.
NTFY=${NTFY_URL-$(sed -n 's/^NTFY_URL=//p' .env 2>/dev/null | head -1 | sed 's/#.*//' | tr -d " \"'")}
say() { echo "$(date -u +%FT%TZ) colima-doctor: $1"; }
notify() {
  say "$2"
  [ -n "$NTFY" ] && curl -s -m 10 -A colima-doctor/1.0 -H "Title: $1" -H "Priority: ${3:-default}" -H "Tags: whale" -d "$2" "$NTFY" >/dev/null
}

# The host-side symptoms, in the order they are cheap to test.
diagnose() {
  # A drill: COLIMA_DOCTOR_FORCE="why" makes it believe the plumbing is dead. With COLIMA_DOCTOR_DRY=1 it
  # stops before the restart, which is how the repair path gets exercised without an outage.
  [ -n "${COLIMA_DOCTOR_FORCE:-}" ] && { echo "$COLIMA_DOCTOR_FORCE"; return; }
  docker info >/dev/null 2>&1 || { echo "the host cannot reach docker.sock"; return; }
  local ports dead=0 total=0 p
  ports=$(docker ps --format '{{.Ports}}' 2>/dev/null | grep -oE '(127\.0\.0\.1|0\.0\.0\.0):[0-9]+' | cut -d: -f2 | sort -un)
  for p in $ports; do
    total=$((total + 1))
    nc -z -G 2 127.0.0.1 "$p" >/dev/null 2>&1 || dead=$((dead + 1))
  done
  # One dead port is an app's problem; every published port at once is the plumbing.
  [ "$total" -ge "$MIN_PORTS" ] && [ "$dead" = "$total" ] && echo "all $total published ports refuse connections from the host"
}

# Not piped into grep: `grep -q` exits at the first match, colima dies of SIGPIPE, and with `pipefail` the
# whole check reads as "not running" — the watchdog would then sit out the very failure it is here for.
STATUS=$(colima status 2>&1)
case "$STATUS" in
  *"colima is running"*) ;;
  *) [ "$CHECK_ONLY" = 1 ] && say "colima is not running; nothing to do"; exit 0 ;;
esac

BROKEN=$(diagnose)
if [ -n "$BROKEN" ] && [ "$CHECK_ONLY" = 0 ]; then
  sleep 5                      # a restarting container or a busy moment should not count
  BROKEN=$(diagnose)
fi
if [ -z "$BROKEN" ]; then
  [ "$CHECK_ONLY" = 1 ] && say "healthy: docker.sock answers and published ports accept connections"
  exit 0
fi
if [ "$CHECK_ONLY" = 1 ]; then
  say "BROKEN: $BROKEN"
  exit 1
fi

now=$(date +%s); last=$(cat "$LAST" 2>/dev/null || echo 0)
if [ $((now - last)) -lt "$COOLDOWN" ]; then
  notify "colima still broken" "$BROKEN — last restart $(( (now - last) / 60 )) min ago, inside the ${COOLDOWN}s cooldown. Needs a person." high
  exit 1
fi

# Containers without a restart policy (a scratch database, say) will not come back on their own, so note
# what was running inside the VM — that still works when the host side does not — and start them after.
WERE=$(colima ssh -- docker ps --format '{{.Names}}' 2>/dev/null | tr '\n' ' ')
notify "colima restarting" "$BROKEN. Restarting colima; services will be down for about a minute." high
[ "$DRY" = 1 ] && { say "dry run: would restart colima, then restore: $WERE"; exit 0; }

echo "$now" > "$LAST"
colima restart 2>&1 | tail -3
for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 2; done

docker compose up -d --remove-orphans 2>&1 | tail -3
for c in $WERE; do
  [ "$(docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null)" = true ] || { say "starting $c"; docker start "$c" >/dev/null 2>&1; }
done
# Deployed apps live outside compose; their own agent would get there within a minute, but not waiting is free.
[ -x /usr/bin/python3 ] && /usr/bin/python3 deployer/deployer.py tick 2>&1 | tail -2

sleep 5
AFTER=$(diagnose)
if [ -z "$AFTER" ]; then
  notify "colima recovered" "Restarted and the host can reach containers again. Restored: ${WERE:-nothing}"
else
  notify "colima restart did not fix it" "$AFTER — needs a person." urgent
  exit 1
fi
