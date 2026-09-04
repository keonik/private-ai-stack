"""Turn Grafana's alert webhook into a notification a person can read on a phone.

Grafana posts a JSON envelope; ntfy would show that envelope verbatim. This relay unwraps it and
forwards one short notification per alert: a title like "Service down - rag-ingest", the summary as the
body, severity as priority and tags, and a link back to the alert in Grafana.

Set NTFY_URL to an ntfy topic (https://ntfy.sh/<topic>) or to a Slack/Discord webhook; the payload
shape is chosen from the URL. Unset, alerts are logged here and go no further.
Standard library only. Listens on :9418, POST /alert.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

URL = os.environ.get("NTFY_URL", "").strip()
EMOJI = {"firing": "rotating_light", "resolved": "white_check_mark"}
PRIORITY = {"critical": "5", "warning": "4", "info": "3"}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def send(title: str, body: str, severity: str, status: str, link: str) -> None:
    if not URL:
        log(f"[no NTFY_URL] {title}: {body}")
        return
    if "hooks.slack.com" in URL:
        data = json.dumps({"text": f"*{title}*\n{body}" + (f"\n{link}" if link else "")}).encode()
        headers = {"Content-Type": "application/json"}
    elif "discord.com" in URL or "discordapp.com" in URL:
        data = json.dumps({"content": f"**{title}**\n{body}" + (f"\n{link}" if link else "")}).encode()
        headers = {"Content-Type": "application/json"}
    else:  # ntfy
        data = body.encode()
        headers = {"Title": title, "Tags": EMOJI.get(status, "bell"),
                   "Priority": PRIORITY.get(severity, "3") if status == "firing" else "2"}
        if link:
            headers["Click"] = link
    try:
        urllib.request.urlopen(urllib.request.Request(URL, data=data, headers=headers), timeout=15).read()
        log(f"sent: {title}")
    except urllib.error.HTTPError as e:
        log(f"notify failed {e.code}: {e.read()[:200]!r}")
    except Exception as e:
        log(f"notify failed: {e}")


def handle(payload: dict) -> int:
    alerts = payload.get("alerts") or [payload]
    for a in alerts:
        labels = a.get("labels") or {}
        ann = a.get("annotations") or {}
        status = a.get("status") or payload.get("status") or "firing"
        name = labels.get("alertname", "Alert")
        where = labels.get("service") or labels.get("container") or ""
        title = f"{name} - {where}" if where else name
        if status == "resolved":
            title = "Resolved: " + title
        body = ann.get("summary") or ann.get("description") or a.get("valueString") or "(no summary)"
        if ann.get("description") and ann.get("summary"):
            body += "\n" + ann["description"]
        send(title, body, labels.get("severity", "info"), status, a.get("generatorURL") or payload.get("externalURL") or "")
    return len(alerts)


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
        try:
            n = handle(json.loads(raw or b"{}"))
            self.send_response(200)
        except Exception as e:
            log(f"bad payload: {e}")
            n = 0
            self.send_response(400)
        self.end_headers()
        self.wfile.write(f"{n}\n".encode())

    def do_GET(self):  # health
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok\n")

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    log(f"alert-relay up; forwarding to {'(unset - logging only)' if not URL else URL.split('://')[0] + '://' + URL.split('/')[2] + '/...'}")
    HTTPServer(("0.0.0.0", 9418), H).serve_forever()
