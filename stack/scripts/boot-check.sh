#!/usr/bin/env bash
# Did the stack come back without anybody logging in? Safe to run over ssh: an ssh login is a Background
# session, so it does not load the Aqua LaunchAgents and does not spoil the test.
#   ~/private-ai-stack/stack/scripts/boot-check.sh
echo "uptime:     $(uptime | sed 's/.*up //; s/,.*users.*//')"
echo "console:    $(stat -f%Su /dev/console)   <- root means nobody is logged in at the screen"
echo
echo "daemons:"
for l in com.offbyone.colima dev.private-ai-stack.omlx dev.private-ai-stack.infer-gateway \
         dev.private-ai-stack.host-metrics dev.private-ai-stack.keepalive dev.private-ai-stack.deployer \
         dev.private-ai-stack.backup; do
  s=$(launchctl print "system/$l" 2>/dev/null | awk -F' = ' '/^\t*state = /{print $2; exit}')
  printf "  %-38s %s\n" "${l#dev.private-ai-stack.}" "${s:-NOT LOADED}"
done
echo
echo "ports:"
for p in 8002 11435 9419 8093 3001; do
  printf "  127.0.0.1:%-6s " "$p"; nc -z -G 2 127.0.0.1 "$p" >/dev/null 2>&1 && echo up || echo DOWN
done
echo
echo "public:"
for u in https://infer.offbyone.ai/v1/models https://captcha.jfay.dev/health https://voice.jfay.dev/api/health \
         https://chat.faymous.work/health https://observe.faymous.work/api/health; do
  printf "  %-26s " "$(echo "$u" | cut -d/ -f3)"
  curl -s -m 20 -A boot-check/1.0 -o /dev/null -w "%{http_code}\n" "$u" \
    -H "Authorization: Bearer $($HOME/.local/bin/infer-key reveal voice-demo 2>/dev/null)"
done
echo
echo "can it actually answer?"
K=$($HOME/.local/bin/infer-key reveal voice-demo 2>/dev/null)
curl -s -m 90 http://127.0.0.1:11435/v1/chat/completions -H "Authorization: Bearer $K" \
  -H "content-type: application/json" \
  -d '{"model":"qwen3.6-35b-a3b","max_tokens":12,"chat_template_kwargs":{"enable_thinking":false},"messages":[{"role":"user","content":"Say hello in one short sentence."}]}' \
  | python3 -c "import sys,json;d=json.load(sys.stdin);print('  ', d['choices'][0]['message']['content'][:80])" 2>/dev/null \
  || echo "   no answer — check ~/Library/Logs/omlx-daemon.log"
