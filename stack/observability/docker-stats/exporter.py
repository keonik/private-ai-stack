"""Per-container CPU / memory / network / restarts for Prometheus, straight from the Docker Engine API
over the unix socket. Standard library only, so it behaves the same under colima, Docker Desktop and Linux
(cAdvisor cannot identify containers on engines that use the containerd image store).

Metrics (label service = compose service name, container = container name):
  container_running, container_cpu_percent, container_memory_bytes, container_memory_limit_bytes,
  container_network_receive_bytes_total, container_network_transmit_bytes_total, container_restarts_total,
  container_started_seconds
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

SOCK = os.environ.get("DOCKER_SOCK", "/var/run/docker.sock")
INTERVAL = float(os.environ.get("INTERVAL", "15"))
PROJECT = os.environ.get("COMPOSE_PROJECT", "")  # only this compose project when set


class UnixConn(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(SOCK)


def api(path: str):
    c = UnixConn("docker", timeout=20)
    c.request("GET", path)
    r = c.getresponse()
    body = r.read()
    c.close()
    return json.loads(body) if body else None


_lines: list[str] = []
_lock = threading.Lock()


def collect() -> list[str]:
    out: list[str] = []
    for ctr in api("/containers/json?all=true") or []:
        labels = ctr.get("Labels", {})
        if PROJECT and labels.get("com.docker.compose.project") != PROJECT:
            continue
        service = labels.get("com.docker.compose.service") or ctr["Names"][0].lstrip("/")
        name = ctr["Names"][0].lstrip("/")
        lb = f'service="{service}",container="{name}"'
        running = 1 if ctr.get("State") == "running" else 0
        out.append(f"container_running{{{lb}}} {running}")
        try:
            insp = api(f"/containers/{ctr['Id']}/json")
            out.append(f"container_restarts_total{{{lb}}} {insp.get('RestartCount', 0)}")
            started = insp.get("State", {}).get("StartedAt", "")
            if running and started:
                ts = time.mktime(time.strptime(started[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
                out.append(f"container_started_seconds{{{lb}}} {ts:.0f}")
        except Exception:
            pass
        if not running:
            continue
        try:
            s = api(f"/containers/{ctr['Id']}/stats?stream=false&one-shot=false")
            cpu = s["cpu_stats"]["cpu_usage"]["total_usage"] - s["precpu_stats"]["cpu_usage"]["total_usage"]
            sys_ = s["cpu_stats"].get("system_cpu_usage", 0) - s["precpu_stats"].get("system_cpu_usage", 0)
            ncpu = s["cpu_stats"].get("online_cpus") or len(s["cpu_stats"]["cpu_usage"].get("percpu_usage", [1]))
            pct = (cpu / sys_) * ncpu * 100.0 if sys_ > 0 else 0.0
            mem = s["memory_stats"]
            used = mem.get("usage", 0) - mem.get("stats", {}).get("inactive_file", 0)
            out.append(f"container_cpu_percent{{{lb}}} {pct:.2f}")
            out.append(f"container_memory_bytes{{{lb}}} {used}")
            out.append(f"container_memory_limit_bytes{{{lb}}} {mem.get('limit', 0)}")
            rx = sum(n.get("rx_bytes", 0) for n in (s.get("networks") or {}).values())
            tx = sum(n.get("tx_bytes", 0) for n in (s.get("networks") or {}).values())
            out.append(f"container_network_receive_bytes_total{{{lb}}} {rx}")
            out.append(f"container_network_transmit_bytes_total{{{lb}}} {tx}")
        except Exception as e:  # a container may vanish mid-loop
            out.append(f'docker_stats_errors_total{{{lb}}} 1  # {type(e).__name__}')
    return out


def loop():
    global _lines
    while True:
        t0 = time.time()
        try:
            lines = collect()
            lines.append(f"docker_stats_scrape_seconds {time.time() - t0:.2f}")
            with _lock:
                _lines = lines
        except Exception as e:
            with _lock:
                _lines = [f'docker_stats_up 0  # {e}']
        time.sleep(max(1.0, INTERVAL - (time.time() - t0)))


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        with _lock:
            body = ("\n".join(_lines) + "\ndocker_stats_up 1\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # quiet
        pass


if __name__ == "__main__":
    threading.Thread(target=loop, daemon=True).start()
    HTTPServer(("0.0.0.0", 9417), H).serve_forever()
