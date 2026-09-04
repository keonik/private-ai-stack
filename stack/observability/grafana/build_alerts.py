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
    rule("pas-service-down", "Service down", "min by (service) (probe_success)", "lt", 1, "2m", "critical",
         "{{ $labels.service }} is not answering its health endpoint",
         "Probed every 15 s by blackbox. oMLX is probed on the host at :8002."),
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
    rule("pas-memory", "Container memory above 85% of the Docker VM", "sum(container_memory_bytes) / max(container_memory_limit_bytes)", "gt", 0.85, "5m", "warning",
         "Containers use {{ $values.A.Value | humanizePercentage }} of the Docker memory limit",
         "On macOS this is the colima / Docker Desktop VM, not the Mac. Raise the VM memory or trim services."),
]

OUT.write_text(json.dumps({"apiVersion": 1, "groups": [
    {"orgId": 1, "name": "private-ai-stack", "folder": "Private AI stack", "interval": "1m", "rules": RULES}]}, indent=1))
print(f"wrote {OUT.name}: {len(RULES)} rules")
