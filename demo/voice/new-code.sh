#!/usr/bin/env bash
# Mint an access code for the voice demo and put it live.
#
#   ./new-code.sh gray            # make a code for "gray"
#   ./new-code.sh gray K7QF3M2X   # or choose one
#   ./new-code.sh --list          # who has a code
#   ./new-code.sh --revoke gray   # take one away
#
# A code lifts the rate limit for whoever types it (300 requests per 5 minutes instead of 45, and no share
# of the daily budget). It is stored in the deployment's env file, never in the repo, and the demo is
# redeployed so it works within about half a minute. Codes are compared with case, spaces and hyphens
# ignored, so "k7qf-3m2x" and "K7QF 3M2X" are the same code.
set -euo pipefail
ENV_FILE=${DEMO_ENV_FILE:-$HOME/.config/deployer/env/voice-demo.env}
DEPLOYER=${DEPLOYER:-$HOME/private-ai-stack/stack/deployer/deployer.py}
SITE=${DEMO_URL:-https://voice.jfay.dev}

current() { grep -E '^DEMO_PASSES=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- || true; }
write() {  # $1 = the new DEMO_PASSES value
  local tmp; tmp=$(mktemp)
  grep -v -E '^DEMO_PASSES=' "$ENV_FILE" > "$tmp" || true
  echo "DEMO_PASSES=$1" >> "$tmp"
  install -m 600 "$tmp" "$ENV_FILE"; rm -f "$tmp"
}
deploy() { /usr/bin/python3 "$DEPLOYER" deploy voice-demo >/dev/null && echo "  live on $SITE"; }

case "${1:-}" in
  --list|-l)
    echo "codes in $ENV_FILE:"
    current | tr ',' '\n' | awk -F: 'NF==2 {printf "  %-12s %s…\n", $1, substr($2,1,4)}'
    exit 0 ;;
  --revoke|-r)
    name=${2:?which code?}
    left=$(current | tr ',' '\n' | grep -v -E "^$name:" | paste -sd, -)
    write "$left"; echo "revoked $name"; deploy; exit 0 ;;
  "" ) sed -n '3,9p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac

name=$(echo "$1" | tr -cd 'A-Za-z0-9_-')
[ -n "$name" ] || { echo "a name is needed, e.g. ./new-code.sh gray"; exit 2; }
# Four-and-four from an alphabet without look-alikes (no O/0, I/1), so it survives being read out loud.
code=${2:-$(LC_ALL=C tr -dc 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789' < /dev/urandom | head -c 8)}
existing=$(current | tr ',' '\n' | grep -v -E "^$name:" | paste -sd, -)
write "$(echo "$existing,$name:$code" | sed 's/^,//')"
echo "code for $name:"
echo
echo "    ${code:0:4}-${code:4:4}"
echo
echo "  they can type it into \"have a code?\" on $SITE"
echo "  or open $SITE/?pass=$code"
deploy
