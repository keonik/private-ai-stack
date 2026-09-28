#!/usr/bin/env bash
# Install the agents in launchd/daemons/ (and the gitignored local/launchd/daemons/) as system daemons, so they run from boot with nobody logged in.
#
#   ./scripts/install-daemons.sh          # render, then print the one sudo command to run
#   sudo ./scripts/install-daemons.sh go  # do it (retires the matching user agents first)
#   sudo ./scripts/install-daemons.sh remove
#
# Why: a LaunchAgent only loads once somebody logs in. On 2026-09-28 this Mac rebooted to the login window
# and infer.offbyone.ai was down for three hours while the tunnels and colima — already a daemon — carried
# on. These jobs run as the user whose files they touch, with HOME set, because launchd gives a daemon
# neither; running them as root would read /var/root and write logs nobody reads.
set -euo pipefail
cd "$(dirname "$0")/.."
STACK=$PWD
OWNER=${SUDO_USER:-$(id -un)}
HOME_DIR=$(dscl . -read "/Users/$OWNER" NFSHomeDirectory | awk '{print $2}')
RENDER=${TMPDIR:-/tmp}/pas-daemons
DEST=/Library/LaunchDaemons

render() {
  mkdir -p "$RENDER"
  for f in launchd/daemons/*.plist local/launchd/daemons/*.plist; do
    [ -e "$f" ] || continue
    sed -e "s|__STACK__|$STACK|g" -e "s|__HOME__|$HOME_DIR|g" -e "s|__USER__|$OWNER|g" "$f" > "$RENDER/$(basename "$f")"
    plutil -lint "$RENDER/$(basename "$f")" > /dev/null
  done
}

case "${1:-}" in
  remove)
    [ "$(id -u)" = 0 ] || { echo "needs sudo"; exit 1; }
    for f in launchd/daemons/*.plist local/launchd/daemons/*.plist; do
      [ -e "$f" ] || continue
      label=$(basename "$f" .plist)
      launchctl bootout "system/$label" 2>/dev/null || true
      rm -f "$DEST/$label.plist"
      echo "removed $label"
    done
    echo "the user agents in ~/Library/LaunchAgents are still there; ./scripts/install-launchd.sh reloads them"
    exit 0 ;;
  go)
    [ "$(id -u)" = 0 ] || { echo "run this one with sudo"; exit 1; }
    render
    for f in "$RENDER"/*.plist; do
      label=$(basename "$f" .plist)
      # One job, one copy: retire the user agent of the same name before the daemon takes over.
      sudo -u "$OWNER" launchctl bootout "gui/$(id -u "$OWNER")/$label" 2>/dev/null || true
      agent="$HOME_DIR/Library/LaunchAgents/$label.plist"
      [ -f "$agent" ] && mv "$agent" "$agent.disabled" && echo "retired agent $label"
      launchctl bootout "system/$label" 2>/dev/null || true
      install -m 644 -o root -g wheel "$f" "$DEST/$label.plist"
      launchctl bootstrap system "$DEST/$label.plist"
      echo "installed daemon $label"
    done
    launchctl list | grep private-ai-stack || true
    exit 0 ;;
  *)
    render
    echo "rendered to $RENDER:"; ls "$RENDER" | sed 's/^/  /'
    echo
    echo "then run:"
    echo "  sudo $STACK/scripts/install-daemons.sh go"
    exit 0 ;;
esac
