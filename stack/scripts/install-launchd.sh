#!/usr/bin/env bash
# Install (or reinstall) the launchd agents: nightly backup at 03:15 and a 5-minute keepalive that
# runs `docker compose up -d` whenever a service is missing (e.g. after Docker restarts).
#   ./scripts/install-launchd.sh          # install
#   ./scripts/install-launchd.sh remove   # uninstall
set -euo pipefail
cd "$(dirname "$0")/.."
STACK=$PWD; DEST=$HOME/Library/LaunchAgents; UID_=$(id -u)
for f in launchd/*.plist; do
  label=$(basename "$f" .plist)
  launchctl bootout "gui/$UID_/$label" 2>/dev/null || true
  if [ "${1:-}" = remove ]; then rm -f "$DEST/$label.plist"; echo "removed $label"; continue; fi
  sed -e "s|__STACK__|$STACK|g" -e "s|__HOME__|$HOME|g" "$f" > "$DEST/$label.plist"
  launchctl bootstrap "gui/$UID_" "$DEST/$label.plist"
  echo "installed $label"
done
[ "${1:-}" = remove ] || launchctl list | grep private-ai-stack
