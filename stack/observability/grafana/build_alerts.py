"""Generate provisioning/alerting/rules.yml (JSON is valid YAML). Edit here, run `python3 build_alerts.py`.

Annotation templating gotcha: `$values.A` is an object, not a number. Piping it into `humanizePercentage`
or `printf` fails at notification time and Grafana logs "Error in expanding template" — the alert still
fires, but the body a person reads is broken. Always use `$values.A.Value`.
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).parent / "provisioning/alerting/rules.yml"


def rule(uid, title, expr, op, thr, for_, severity, summary, desc=""):
    return {"uid": uid, "title": title, "condition": "C", "for": for_, "labels": {"severity": severity},
            "annotations": {"summary": summary, "description": desc},
            "data": [
                {"refId": "A", "relativeTimeRange": {"from": 600, "to": 0}, "datasourceUid": "prom",
                 "model": {"refId": "A", "expr": expr, "instant": True, "intervalMs": 15000, "maxDataPoints": 100}},
                {"refId": "C", "datasourceUid": "__expr__",
                 "model": {"refId": "C", "type": "threshold", "expression": "A",
                           "conditions": [{"evaluator": {"type": op, "params": [thr]}, "operator": {"type": "and"},
                                           "query": {"params": ["A"]}, "reducer": {"type": "last"}, "type": "query"}]}}],
            "noDataState": "OK", "execErrState": "Error", "isPaused": False}


RULES = [
    rule("pas-service-down", "Service down", 'min by (service) (probe_success{job="probes", alert!="false"})', "lt", 1, "2m", "critical",
         "{{ $labels.service }} is not answering its health endpoint",
         "Probed every 15 s by blackbox. Targets live in observability/prometheus/internal-targets.json; an entry "
         "with alert:\"false\" is watched on the dashboard but never alerts."),
    rule("pas-public-down", "Public hostname unreachable", 'min by (service) (probe_success{job="public"})', "lt", 1, "5m", "critical",
         "{{ $labels.service }} cannot be reached from the internet",
         "The service itself may be fine: this is the tunnel, DNS or the proxy in front of it. A Cloudflare Access "
         "login page counts as reachable."),
    rule("pas-llm-errors", "Model requests failing", "sum(increase(litellm_proxy_failed_requests_metric_total[10m])) or vector(0)", "gt", 3, "0s", "warning",
         "{{ $values.A.Value | printf \"%.0f\" }} failed model requests in the last 10 minutes",
         "Look at the Errors panel and the LiteLLM logs; usually the backend is down or a model was renamed."),
    rule("pas-chat-slow", "Chat p95 latency over 2 minutes",
         'histogram_quantile(0.95, sum by (le) (rate(litellm_request_total_latency_metric_bucket{requested_model="local/chat"}[15m])))', "gt", 120, "10m", "warning",
         "local/chat p95 is {{ $values.A.Value | printf \"%.0f\" }} s",
         "Sustained slowness: a batch job is sharing the GPU, or the model is swapping."),
    rule("pas-rag-ingest-errors", "Document ingest errors", 'sum(increase(rag_ingested_files_total{result="error"}[10m])) or vector(0)', "gt", 0, "0s", "warning",
         "{{ $values.A.Value | printf \"%.0f\" }} files failed to index in the last 10 minutes",
         "See data/audit.jsonl ingest_error events for the filename and reason."),
    rule("pas-voice-warm", "Voice demo speech path stale",
         "max by (stage) (voice_warm_success_age_seconds)", "gt", 900, "5m", "critical",
         "The demo's {{ $labels.stage }} stage has not answered for {{ $values.A.Value | printf \"%.0f\" }} s",
         "The demo warms speech, transcription and answering every 4 minutes, so 15 minutes without one "
         "means that stage is failing for visitors too. oMLX has an open bug where /v1/audio/speech starts "
         "returning 500 after hours of uptime and only restarting oMLX fixes it — check the speech stage "
         "first, then `docker logs deployer-voice-demo`."),
    rule("pas-restarts", "Container restarting", "max by (service) (clamp_min(increase(container_restarts_total[1h]), 0))", "gt", 2, "0s", "warning",
         "{{ $labels.service }} restarted {{ $values.A.Value | printf \"%.0f\" }} times in the last hour",
         "docker compose logs <service>"),
    rule("pas-disk", "Disk almost full on the Mac", "max(host_disk_used_ratio)", "gt", 0.9, "10m", "critical",
         "The Mac's disk is {{ $values.A.Value | humanizePercentage }} full",
         "Everything on this machine stops when the boot volume fills: models, Docker, backups. `docker system prune` "
         "and stack/backups are the usual places to reclaim space."),
    # Swap *in use* is the wrong signal: macOS leaves pages in the swapfile long after the pressure is gone
    # (measured draining at 0.4 GB every two hours), so a one-off squeeze would page someone for days about a
    # machine that is fine. What matters is swapping happening now, and what macOS itself says about pressure.
    rule("pas-swap", "The Mac is swapping", "delta(host_swap_used_bytes[30m])", "gt", 1e9, "10m", "warning",
         "Swap grew by {{ $values.A.Value | humanize1024 }}B in half an hour",
         "Something is over-committed. Check which models are resident (/v1/models/status): a model with no "
         "ttl_seconds stays loaded forever once used, which is what caused the 52 GB of swap on 2026-09-28."),
    rule("pas-pressure", "macOS reports memory pressure", "host_memory_pressure_level", "gte", 2, "10m", "warning",
         "Memory pressure level {{ $values.A.Value | printf \"%.0f\" }} (0 normal, 1 warning, 2 critical)",
         "The machine is compressing or paging to keep up. Unload a model or shrink the Docker VM."),
    rule("pas-host-metrics-down", "Host metrics exporter down", "up{job=\"host\"}", "lt", 1, "10m", "warning",
         "No disk or memory readings from the Mac",
         "launchctl list | grep host-metrics; the agent is dev.private-ai-stack.host-metrics."),
    rule("pas-memory", "Container memory above 85% of the Docker VM", "sum(container_memory_bytes) / max(container_memory_limit_bytes)", "gt", 0.85, "5m", "warning",
         "Containers use {{ $values.A.Value | humanizePercentage }} of the Docker memory limit",
         "On macOS this is the colima / Docker Desktop VM, not the Mac. Raise the VM memory or trim services."),
]

RETIRED = [
    "gp-run-unfinished",   # replaced by gp-run-incomplete, which reads the summary's mtime
]

OUT.write_text(json.dumps({"apiVersion": 1,
                           "deleteRules": [{"orgId": 1, "uid": uid} for uid in RETIRED],
                           "groups": [
    {"orgId": 1, "name": "private-ai-stack", "folder": "Private AI stack", "interval": "1m", "rules": RULES}]}, indent=1))
print(f"wrote {OUT.name}: {len(RULES)} stack rules, {len(RETIRED)} retired")

# ---- local overlay ----
# Alerts for anything else on this machine live outside this repo: an alerts_*_local.py in
# stack/local/ (gitignored) writes its own rules-*.yml next to this one. Grafana provisions every
# file in the alerting directory, so nothing private is committed to be paged about.
if __name__ == "__main__":
    import runpy

    for _extra in sorted((Path(__file__).resolve().parents[2] / "local").glob("alerts_*_local.py")):
        print("running local overlay:", _extra.name)
        runpy.run_path(str(_extra), run_name="__main__")