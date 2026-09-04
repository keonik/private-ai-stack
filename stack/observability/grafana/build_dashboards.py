"""Generate the provisioned dashboards (dashboards/*.json). Edit here, run `python3 build_dashboards.py`.
Kept as a builder because hand-editing Grafana JSON is how dashboards rot."""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).parent / "dashboards"
PROM, LOKI, SPEND = {"type": "prometheus", "uid": "prom"}, {"type": "loki", "uid": "loki"}, {"type": "grafana-postgresql-datasource", "uid": "spend"}
_id = 0


def nid() -> int:
    global _id
    _id += 1
    return _id


def grid(x, y, w, h):
    return {"x": x, "y": y, "w": w, "h": h}


def row(title, y):
    return {"id": nid(), "type": "row", "title": title, "collapsed": False, "gridPos": grid(0, y, 24, 1), "panels": []}


def ts(title, targets, pos, unit=None, ds=PROM, legend="{{__name__}}", stack=False, desc=None, min0=True):
    p = {"id": nid(), "type": "timeseries", "title": title, "datasource": ds, "gridPos": grid(*pos),
         "fieldConfig": {"defaults": {"unit": unit or "short", "custom": {"lineWidth": 2, "fillOpacity": 12 if not stack else 40,
                                                                          "stacking": {"mode": "normal" if stack else "none"}, "showPoints": "never"}}, "overrides": []},
         "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi", "sort": "desc"}},
         "targets": []}
    if min0:
        p["fieldConfig"]["defaults"]["min"] = 0
    if desc:
        p["description"] = desc
    for i, t in enumerate(targets):
        if isinstance(t, str):
            t = {"expr": t}
        tgt = {"refId": chr(65 + i), "datasource": ds, **t}
        if ds is PROM:
            tgt.setdefault("legendFormat", legend)
        p["targets"].append(tgt)
    return p


def stat(title, expr, pos, unit=None, ds=PROM, mappings=None, thresholds=None, legend=None, desc=None, decimals=None, reduce="lastNotNull", sql=False):
    p = {"id": nid(), "type": "stat", "title": title, "datasource": ds, "gridPos": grid(*pos),
         "fieldConfig": {"defaults": {"unit": unit or "short", "mappings": mappings or [],
                                      "thresholds": thresholds or {"mode": "absolute", "steps": [{"color": "green", "value": None}]}}, "overrides": []},
         "options": {"reduceOptions": {"calcs": [reduce], "fields": "", "values": False}, "colorMode": "background", "graphMode": "none",
                     "textMode": "value_and_name" if legend else "value", "justifyMode": "center"},
         "targets": [{"refId": "A", "datasource": ds, "rawSql" if sql else "expr": expr, "format": "table" if sql else "time_series"} | ({"legendFormat": legend} if legend else {})]}
    if desc:
        p["description"] = desc
    if decimals is not None:
        p["fieldConfig"]["defaults"]["decimals"] = decimals
    return p


UPDOWN = [{"type": "value", "options": {"0": {"text": "DOWN", "color": "red"}, "1": {"text": "UP", "color": "green"}}}]
RED_GREEN = {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "green", "value": 1}]}


def logs(title, expr, pos):
    return {"id": nid(), "type": "logs", "title": title, "datasource": LOKI, "gridPos": grid(*pos),
            "options": {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending", "dedupStrategy": "none", "enableLogDetails": True},
            "targets": [{"refId": "A", "datasource": LOKI, "expr": expr}]}


def table_sql(title, sql, pos, desc=None):
    p = {"id": nid(), "type": "table", "title": title, "datasource": SPEND, "gridPos": grid(*pos),
         "options": {"cellHeight": "sm", "showHeader": True}, "fieldConfig": {"defaults": {}, "overrides": []},
         "targets": [{"refId": "A", "datasource": SPEND, "rawSql": sql, "format": "table"}]}
    if desc:
        p["description"] = desc
    return p


def dashboard(uid, title, panels, tags, refresh="30s", frm="now-6h"):
    return {"uid": uid, "title": title, "tags": tags, "timezone": "browser", "schemaVersion": 39, "version": 1, "editable": True,
            "refresh": refresh, "time": {"from": frm, "to": "now"}, "graphTooltip": 1, "panels": panels,
            "templating": {"list": []}, "annotations": {"list": []}}


# ------------------------------------------------------------------ overview
P = []
P.append(row("Is it up", 0))
for i, (svc, label) in enumerate([("litellm", "LiteLLM (router)"), ("open-webui", "Open WebUI (chat)"), ("rag-ingest", "rag-ingest (documents)"), ("host.docker.internal", "oMLX (models, on the host)")]):
    P.append(stat(label, f'min(probe_success{{service="{svc}"}})', (i * 5, 1, 5, 3), mappings=UPDOWN, thresholds=RED_GREEN))
P.append(stat("Restarts (24h)", "sum(delta(container_restarts_total[24h])) or vector(0)", (20, 1, 4, 3),
              thresholds={"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "orange", "value": 1}, {"color": "red", "value": 5}]}, decimals=0))

P.append(row("Model traffic (LiteLLM /metrics)", 4))
P.append(ts("Requests per minute, by model", ['sum by (requested_model) (rate(litellm_proxy_total_requests_metric_total{route=~"/v1/.*|/chat/.*|/embeddings"}[5m])) * 60'], (0, 5, 8, 8), "reqpm", legend="{{requested_model}}", stack=True))
P.append(ts("Latency p50 / p95, chat models", ['histogram_quantile(0.5, sum by (le, requested_model) (rate(litellm_request_total_latency_metric_bucket{requested_model!~".*embed.*"}[10m])))',
                                                {"expr": 'histogram_quantile(0.95, sum by (le, requested_model) (rate(litellm_request_total_latency_metric_bucket{requested_model!~".*embed.*"}[10m])))', "legendFormat": "p95 {{requested_model}}"}],
            (8, 5, 8, 8), "s", legend="p50 {{requested_model}}", desc="End-to-end request time through the router (includes queueing and generation)."))
P.append(ts("Tokens per minute", [{"expr": "sum(rate(litellm_input_tokens_metric_total[5m])) * 60", "legendFormat": "input"},
                                  {"expr": "sum(rate(litellm_output_tokens_metric_total[5m])) * 60", "legendFormat": "output"}], (16, 5, 8, 8), "short", stack=True))
P.append(ts("Failed requests per minute", ['sum by (requested_model, exception_class) (rate(litellm_proxy_failed_requests_metric_total[5m])) * 60 or vector(0)'], (0, 13, 8, 6), "reqpm", legend="{{requested_model}} {{exception_class}}"))
P.append(ts("In flight", ["litellm_in_flight_requests"], (8, 13, 8, 6), "short", legend="requests"))
P.append(ts("Seconds per output token (deployment)", ['sum by (model) (rate(litellm_deployment_latency_per_output_token_sum[10m])) / sum by (model) (rate(litellm_deployment_latency_per_output_token_count[10m]))'], (16, 13, 8, 6), "s", legend="{{model}}", desc="Generation speed per backend model. Rising = the box is busy or thermally throttled."))

P.append(row("History and spend (LiteLLM spend log in Postgres)", 19))
P.append(ts("Requests per hour, by model group", [{"rawSql": 'SELECT $__timeGroupAlias("startTime", 1h), model_group AS metric, count(*) AS value FROM "LiteLLM_SpendLogs" WHERE $__timeFilter("startTime") GROUP BY 1, 2 ORDER BY 1', "format": "time_series"}], (0, 20, 8, 8), "short", ds=SPEND, stack=True))
P.append(ts("Time to first token p50, by model group", [{"rawSql": 'SELECT $__timeGroupAlias("startTime", 1h), model_group AS metric, percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM ("completionStartTime" - "startTime"))) AS value FROM "LiteLLM_SpendLogs" WHERE $__timeFilter("startTime") AND "completionStartTime" IS NOT NULL AND call_type LIKE \'%completion%\' GROUP BY 1, 2 ORDER BY 1', "format": "time_series"}], (8, 20, 8, 8), "s", ds=SPEND, desc="How long a user waits before the first word appears. The number people feel."))
P.append(ts("Spend per day, by model group", [{"rawSql": 'SELECT $__timeGroupAlias("startTime", 1d), model_group AS metric, sum(spend) AS value FROM "LiteLLM_SpendLogs" WHERE $__timeFilter("startTime") GROUP BY 1, 2 ORDER BY 1', "format": "time_series"}], (16, 20, 8, 8), "currencyUSD", ds=SPEND, stack=True, desc="Local models cost 0 unless you set prices in litellm/config.yaml; cloud fallbacks show real cost here."))
P.append(table_sql("Who used what (selected range)", 'SELECT coalesce(nullif(metadata->>\'user_api_key_alias\', \'\'), left(api_key, 12)) AS key, model_group AS model, count(*) AS requests, sum(total_tokens) AS tokens, round(sum(spend)::numeric, 4) AS spend, round(avg(extract(epoch FROM ("endTime" - "startTime")))::numeric, 1) AS avg_s FROM "LiteLLM_SpendLogs" WHERE $__timeFilter("startTime") GROUP BY 1, 2 ORDER BY tokens DESC LIMIT 25', (0, 28, 24, 8)))

P.append(row("Containers (docker-stats exporter)", 36))
P.append(ts("CPU %, by service", ["container_cpu_percent"], (0, 37, 8, 7), "percent", legend="{{service}}"))
P.append(ts("Memory, by service", ["container_memory_bytes"], (8, 37, 8, 7), "bytes", legend="{{service}}", stack=True))
P.append(ts("Network in / out", [{"expr": "sum by (service) (rate(container_network_receive_bytes_total[5m]))", "legendFormat": "rx {{service}}"},
                                 {"expr": "- sum by (service) (rate(container_network_transmit_bytes_total[5m]))", "legendFormat": "tx {{service}}"}], (16, 37, 8, 7), "Bps", min0=False))

P.append(row("Logs (Loki)", 44))
P.append(logs("Errors and warnings across the stack", '{service=~"litellm|open-webui|rag-ingest|postgres"} |~ "(?i)(error|warn|traceback|exception)" != "GET /metrics"', (0, 45, 24, 10)))
(OUT / "overview.json").write_text(json.dumps(dashboard("pas-overview", "Private AI stack — overview", P, ["private-ai-stack"]), indent=1))

# ------------------------------------------------------------------ rag
_id = 100
R = []
R.append(row("Index", 0))
R.append(stat("Chunks", "rag_chunks", (0, 1, 4, 3), decimals=0))
R.append(stat("Documents", "rag_documents", (4, 1, 4, 3), decimals=0))
R.append(stat("With extracted fields", "rag_documents_with_fields", (8, 1, 4, 3), decimals=0))
R.append(stat("Ingest errors (24h)", 'sum(increase(rag_ingested_files_total{result="error"}[24h])) or vector(0)', (12, 1, 4, 3), decimals=0,
              thresholds={"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}))
R.append(stat("Search p95 (1h)", 'histogram_quantile(0.95, sum by (le) (rate(rag_request_seconds_bucket{endpoint="/search"}[1h])))', (16, 1, 4, 3), "s", decimals=2))
R.append(stat("Query p95 (1h)", 'histogram_quantile(0.95, sum by (le) (rate(rag_request_seconds_bucket{endpoint="/query"}[1h])))', (20, 1, 4, 3), "s", decimals=1,
              desc="/query includes the answer generation by the chat model."))
R.append(row("Traffic", 4))
R.append(ts("Requests per minute, by endpoint", ["sum by (endpoint) (rate(rag_requests_total[5m])) * 60"], (0, 5, 8, 8), "reqpm", legend="{{endpoint}}", stack=True))
R.append(ts("Latency p95, by endpoint", ['histogram_quantile(0.95, sum by (le, endpoint) (rate(rag_request_seconds_bucket[10m])))'], (8, 5, 8, 8), "s", legend="{{endpoint}}"))
R.append(ts("Non-200 responses per minute", ['sum by (endpoint, status) (rate(rag_requests_total{status!="200"}[5m])) * 60 or vector(0)'], (16, 5, 8, 8), "reqpm", legend="{{endpoint}} {{status}}"))
R.append(ts("Embedding call latency p50 / p95", [{"expr": "histogram_quantile(0.5, sum by (le) (rate(rag_embed_seconds_bucket[10m])))", "legendFormat": "p50"},
                                                  {"expr": "histogram_quantile(0.95, sum by (le) (rate(rag_embed_seconds_bucket[10m])))", "legendFormat": "p95"}], (0, 13, 8, 7), "s",
             desc="One call per search plus one per batch of chunks at ingest. Slow here = oMLX busy."))
R.append(ts("Hits returned per search (avg)", ["rate(rag_search_hits_sum[10m]) / rate(rag_search_hits_count[10m])"], (8, 13, 8, 7), "short", legend="avg hits", desc="Falls to 0 when a filter or ACL excludes everything."))
R.append(ts("Files indexed / skipped / removed per hour", ["sum by (result) (increase(rag_ingested_files_total[1h]))"], (16, 13, 8, 7), "short", legend="{{result}}", stack=True))
R.append(row("Logs", 20))
R.append(logs("rag-ingest", '{service="rag-ingest"} != "GET /metrics" != "GET /health"', (0, 21, 24, 10)))
(OUT / "rag.json").write_text(json.dumps(dashboard("pas-rag", "Private AI stack — RAG service", R, ["private-ai-stack"]), indent=1))
print("wrote", [p.name for p in OUT.glob("*.json")])
