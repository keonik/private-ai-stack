#!/usr/bin/env bash
# At login: make sure the Docker engine is running, then bring the stack up.
#
# The 5-minute keepalive deliberately does nothing when Docker is down, because stopping Docker mid-session
# is an operator decision. A reboot is not, so this runs once at login. It starts colima only if the
# engine is unreachable, and never fights a deliberate `colima stop` later in the day.
set -uo pipefail
cd "$(dirname "$0")/.."
say() { echo "$(date -u +%FT%TZ) docker-boot: $*"; }

if ! docker info >/dev/null 2>&1; then
  if command -v colima >/dev/null 2>&1; then
    say "engine down, starting colima"
    colima start 2>&1 | tail -n 3
  else
    say "engine down and no colima on PATH (Docker Desktop? start it manually)"
  fi
  for i in $(seq 1 60); do
    docker info >/dev/null 2>&1 && break
    sleep 5
  done
fi

if ! docker info >/dev/null 2>&1; then
  say "engine still unreachable, giving up"
  exit 1
fi
say "engine up, starting the stack"
docker compose up -d --remove-orphans 2>&1 | tail -n 5
