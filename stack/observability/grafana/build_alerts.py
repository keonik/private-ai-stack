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
    rule("pas-restarts", "Container restarting", "max by (service) (clamp_min(increase(container_restarts_total[1h]), 0))", "gt", 2, "0s", "warning",
         "{{ $labels.service }} restarted {{ $values.A.Value | printf \"%.0f\" }} times in the last hour",
         "docker compose logs <service>"),
    rule("pas-disk", "Disk almost full on the Mac", "max(host_disk_used_ratio)", "gt", 0.9, "10m", "critical",
         "The Mac's disk is {{ $values.A.Value | humanizePercentage }} full",
         "Everything on this machine stops when the boot volume fills: models, Docker, backups. `docker system prune` "
         "and stack/backups are the usual places to reclaim space."),
    rule("pas-swap", "The Mac is swapping", "host_swap_used_bytes", "gt", 8e9, "15m", "warning",
         "Swap in use is {{ $values.A.Value | humanize1024 }}B",
         "A machine serving models should not swap. Something is over-committed: check the Memory panel and which "
         "models are loaded."),
    rule("pas-host-metrics-down", "Host metrics exporter down", "up{job=\"host\"}", "lt", 1, "10m", "warning",
         "No disk or memory readings from the Mac",
         "launchctl list | grep host-metrics; the agent is dev.private-ai-stack.host-metrics."),
    rule("pas-memory", "Container memory above 85% of the Docker VM", "sum(container_memory_bytes) / max(container_memory_limit_bytes)", "gt", 0.85, "5m", "warning",
         "Containers use {{ $values.A.Value | humanizePercentage }} of the Docker memory limit",
         "On macOS this is the colima / Docker Desktop VM, not the Mac. Raise the VM memory or trim services."),
]

# Gameplan Network shares this Mac. Its rules live in their own group so they can be silenced
# without touching the stack's, and route through the same contact point.
GAMEPLAN_RULES = [
    rule("gp-pipeline-stale", "Crash-report pipeline has not completed a run",
         "time() - gameplan_pipeline_last_run_completed_timestamp_seconds", "gt", 6 * 3600, "10m", "critical",
         "No crash-report download run has finished in {{ $values.A.Value | humanizeDuration }}",
         "The hourly pipeline runs 04:00-23:00, so a healthy overnight gap already reaches five hours - hence the "
         "six-hour threshold rather than something tighter. Check automation/daily.log on glazed and whether cron ran."),
    rule("gp-run-failed", "A download run reported failure",
         "min(gameplan_pipeline_last_run_ok)", "lt", 1, "0s", "warning",
         "The last download run finished {{ if eq $values.A.Value 0.0 }}failed{{ else }}partial{{ end }}",
         "Recomputed from the run's own county counts: 0.5 means some counties failed but others worked, 0 means "
         "none succeeded. Check which counties on the dashboard before assuming the portal is the cause."),
    rule("gp-download-failures", "Crash-report fetches are failing",
         'gameplan_pipeline_last_run_reports{outcome="failed"} / clamp_min(gameplan_pipeline_last_run_reports{outcome="found"}, 1)',
         "gt", 0.5, "0s", "critical",
         "{{ $values.A.Value | humanizePercentage }} of the reports the portal listed failed to download",
         "The portal answering with something that is not a PDF is the anti-bot arms race, not a network blip. "
         "See the crash-portal-download-campaign playbook in the gameplan repo before changing anything."),
    rule("gp-counties-failing", "Counties failing in the download run",
         'gameplan_pipeline_last_run_counties{outcome="failed"}', "gt", 10, "0s", "warning",
         "{{ $values.A.Value | printf \"%.0f\" }} counties failed in the last run",
         "A handful failing is routine. Ten or more at once is the portal, not the counties."),
    rule("gp-portal-escalating", "Portal breaker is escalating",
         "gameplan_portal_breaker_fruitless_trips", "gt", 3, "30m", "warning",
         "The portal circuit breaker has tripped {{ $values.A.Value | printf \"%.0f\" }} times without getting anywhere",
         "Request spacing is ratcheting up and the pipeline is standing down. Check the egress tile: the crash portal "
         "wants the VPN off, and a run on the wrong network looks exactly like this."),
    rule("gp-worker-down", "Gameplan worker not scrapeable", 'up{job="gameplan-worker"}', "lt", 1, "5m", "critical",
         "The Gameplan worker is not answering /metrics",
         "launchctl list | grep gameplan-worker, and ~/Library/Logs/gameplan-worker.log. If the process is alive, "
         "WORKER_API_KEY in stack/.env may no longer match the worker's - the scrape sends it as a header."),
    rule("gp-worker-dependency", "A worker dependency is unreachable",
         "min by (dependency) (gameplan_worker_dependency_up)", "lt", 1, "10m", "warning",
         "The worker cannot reach {{ $labels.dependency }}",
         "ocrmypdf missing breaks OCR for scanned reports. The vision endpoint being down breaks CAPTCHA solving, "
         "which fails silently: nothing errors until a scraper meets a CAPTCHA and downloads stall."),
    rule("gp-worker-queue", "Work is queueing on the worker",
         "max by (pool) (gameplan_worker_pool_queued)", "gt", 5, "15m", "warning",
         "{{ $values.A.Value | printf \"%.0f\" }} calls waiting on the {{ $labels.pool }} pool",
         "Sustained queueing means demand is above MAX_BROWSER_CONCURRENCY / MAX_OCR_CONCURRENCY, or a job is wedged "
         "holding a slot."),
    rule("gp-shadow-regression", "A field the geometry parser used to read has regressed",
         "min(gameplan_shadow_field_match_ratio)", "lt", 0.98, "1h", "warning",
         "Worst field agreement is {{ $values.A.Value | humanizePercentage }}",
         "The shadow compares the production parser against the geometry parser on real PDFs. One field dropping is "
         "the signal an aggregate parity number would hide. See automation/shadow-out."),
    rule("gp-run-incomplete", "A pipeline run never completed",
         "time() - gameplan_pipeline_last_run_summary_written_timestamp_seconds", "gt", 5400, "10m", "warning",
         "No download run has written a summary in {{ $values.A.Value | humanizeDuration }}",
         "A run killed by the 3h watchdog, or one that died mid-flight, writes no summary at all — so this climbing "
         "past an hourly cadence is how a dead run shows up. Fires well before gp-pipeline-stale, which is set wide "
         "enough to sit through the 23:00-04:00 overnight gap."),
    rule("gp-reconcile-silent", "The nightly reconcile has not run",
         'time() - gameplan_cron_log_updated_timestamp_seconds{job="reconcile"}', "gt", 26 * 3600, "10m", "warning",
         "Nothing has been written to reconcile.log in {{ $values.A.Value | humanizeDuration }}",
         "Reconcile runs at 00:10 daily. It also waits on the VPN reconcile lock, so a stuck vpnctl can hold it off."),
    rule("gp-exporter-source", "The pipeline exporter cannot read a source",
         "min by (source) (gameplan_pipeline_source_up)", "lt", 1, "30m", "warning",
         "The exporter cannot read {{ $labels.source }}",
         "Every Gameplan pipeline panel is downstream of these files. A source that stopped being readable looks "
         "exactly like a quiet healthy system, which is why it alerts."),
]

OUT.write_text(json.dumps({"apiVersion": 1, "groups": [
    {"orgId": 1, "name": "private-ai-stack", "folder": "Private AI stack", "interval": "1m", "rules": RULES},
    {"orgId": 1, "name": "gameplan", "folder": "Gameplan", "interval": "1m", "rules": GAMEPLAN_RULES}]}, indent=1))
print(f"wrote {OUT.name}: {len(RULES)} stack rules, {len(GAMEPLAN_RULES)} gameplan rules")
