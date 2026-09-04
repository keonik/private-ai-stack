"""Disk, memory and load for the Mac itself, in Prometheus format on :9419.

A container cannot see macOS — it sees the Linux VM Docker runs in — so this runs on the host under
launchd (see launchd/dev.private-ai-stack.host-metrics.plist). Standard library only, and written for
the system python at /usr/bin/python3 so it needs no virtualenv.

Metrics: host_disk_free_bytes / host_disk_total_bytes / host_disk_used_ratio{mount}, host_memory_*,
host_swap_used_bytes, host_load1/5/15, host_cpus, host_uptime_seconds, host_metrics_up.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(os.environ.get("PORT", "9419"))
MOUNTS = [m for m in os.environ.get("MOUNTS", "/").split(",") if m]  # add mounts comma-separated


def sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return ""


def disk():
    out = []
    for m in MOUNTS:
        try:
            s = os.statvfs(m)
        except OSError:
            continue
        total = s.f_blocks * s.f_frsize
        free = s.f_bavail * s.f_frsize
        if not total:
            continue
        out.append('host_disk_total_bytes{mount="%s"} %d' % (m, total))
        out.append('host_disk_free_bytes{mount="%s"} %d' % (m, free))
        out.append('host_disk_used_ratio{mount="%s"} %.4f' % (m, 1 - free / total))
    return out


def memory():
    out = []
    total = sh("sysctl -n hw.memsize").strip()
    if total.isdigit():
        out.append("host_memory_total_bytes %s" % total)
    vm = sh("vm_stat")
    m = re.search(r"page size of (\d+) bytes", vm)
    page = int(m.group(1)) if m else 4096
    pages = dict(re.findall(r"^(.+?):\s+(\d+)\.$", vm, re.M))

    def p(name):
        return int(pages.get(name, 0)) * page

    # macOS keeps almost all RAM occupied by file cache, so "total minus free" is useless for alerting.
    # These follow Activity Monitor: Memory Used = app + wired + compressed, with cache counted separately.
    free = p("Pages free") + p("Pages speculative")
    app = max(p("Anonymous pages") - p("Pages purgeable"), 0)
    wired, comp = p("Pages wired down"), p("Pages occupied by compressor")
    out.append("host_memory_app_bytes %d" % app)
    out.append("host_memory_wired_bytes %d" % wired)
    out.append("host_memory_compressed_bytes %d" % comp)
    out.append("host_memory_used_bytes %d" % (app + wired + comp))
    out.append("host_memory_cached_bytes %d" % (p("File-backed pages") + p("Pages purgeable")))
    out.append("host_memory_free_bytes %d" % free)
    pressure = sh("sysctl -n kern.memorystatus_vm_pressure_level").strip()
    if pressure.isdigit():  # 1 normal, 2 warning, 4 critical
        out.append("host_memory_pressure_level %s" % pressure)
    sw = re.search(r"used = ([\d.]+)([MG])", sh("sysctl -n vm.swapusage"))
    if sw:
        out.append("host_swap_used_bytes %d" % int(float(sw.group(1)) * (1 << (30 if sw.group(2) == "G" else 20))))
    return out


def cpu():
    out = ["host_cpus %d" % (os.cpu_count() or 0)]
    try:
        l1, l5, l15 = os.getloadavg()
        out += ["host_load1 %.2f" % l1, "host_load5 %.2f" % l5, "host_load15 %.2f" % l15]
    except OSError:
        pass
    b = re.search(r"sec = (\d+)", sh("sysctl -n kern.boottime"))
    if b:
        out.append("host_uptime_seconds %d" % (time.time() - int(b.group(1))))
    return out


def collect():
    t0 = time.time()
    lines = disk() + memory() + cpu()
    lines.append("host_metrics_scrape_seconds %.3f" % (time.time() - t0))
    lines.append("host_metrics_up 1")
    return "\n".join(lines) + "\n"


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            body = collect().encode()
        except Exception as e:
            body = ("host_metrics_up 0\n# %s\n" % e).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", PORT), H).serve_forever()
