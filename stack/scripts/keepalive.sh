#!/usr/bin/env bash
# Bring the stack back up after Docker (colima / Docker Desktop) restarts. Idempotent and quiet
# when everything is running; meant for launchd every few minutes (see launchd/ and install-launchd.sh).
# If Docker itself is down we do nothing: stopping Docker is an operator decision, not ours.
set -uo pipefail
cd "$(dirname "$0")/.."
docker info >/dev/null 2>&1 || exit 0
want=$(docker compose config --services 2>/dev/null | sort)
have=$(docker compose ps --status running --services 2>/dev/null | sort)
missing=$(comm -23 <(echo "$want") <(echo "$have"))
[ -z "$missing" ] && exit 0
echo "$(date -u +%FT%TZ) keepalive: starting $(echo $missing)"
docker compose up -d --remove-orphans 2>&1 | tail -n 5
