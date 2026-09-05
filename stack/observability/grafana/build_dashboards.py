"""Generate the provisioned dashboards (dashboards/*.json). Edit here, run `python3 build_dashboards.py`.
Kept as a builder because hand-editing Grafana JSON is how dashboards rot."""
from __future__ import annotations

import json
from pathlib import Path

# One directory per provisioned Grafana folder — see provisioning/dashboards/dashboards.yml.
OUT = Path(__file__).parent / "dashboards/stack"
OUT_GAMEPLAN = Path(__file__).parent / "dashboards/gameplan"
OUT.mkdir(parents=True, exist_ok=True)
OUT_GAMEPLAN.mkdir(parents=True, exist_ok=True)
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
         "targets": [{"refId": "A", "datasource": ds, "rawSql" if sql else "expr": expr, "format": "table" if sql else "time_series"}
                     | ({} if sql else {"instant": True}) | ({"legendFormat": legend} if legend else {})]}
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
# Layout uses a running y cursor so panels can be added or reordered without renumbering everything.
P = []
_y = [0]


def at(w, h, x=0):
    return (x, _y[0], w, h)


def down(h):
    _y[0] += h


P.append(row("Is it up", _y[0])); down(1)
P.append(stat("Services", 'min by (service) (probe_success{job="probes", alert!="false"})', at(20, 4), mappings=UPDOWN,
              thresholds=RED_GREEN, legend="{{service}}",
              desc="One tile per entry in observability/prometheus/internal-targets.json. Add a service there and it "
                   "appears here; set alert:\"false\" on an entry to watch it without waking anyone."))
P.append(stat("Restarts (24h)", "clamp_min(sum(increase(container_restarts_total[24h])), 0) or vector(0)", at(4, 4, 20),
              thresholds={"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "orange", "value": 1}, {"color": "red", "value": 5}]},
              decimals=0)); down(4)

P.append(row("Reachable from the internet", _y[0])); down(1)
P.append(stat("Public endpoints", 'min by (service) (probe_success{job="public"})', at(24, 3), mappings=UPDOWN,
              thresholds=RED_GREEN, legend="{{service}}",
              desc="Each hostname in observability/prometheus/public-targets.json, fetched over the internet every 15 s. "
                   "DOWN here with the service UP above means the tunnel is broken, not the app. A Cloudflare Access "
                   "login page still counts as UP.")); down(3)

P.append(row("The Mac", _y[0])); down(1)
PCT = {"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "orange", "value": 80}, {"color": "red", "value": 90}]}
P.append(stat("Disk used", "max(host_disk_used_ratio) * 100", at(4, 4, 0), "percent", thresholds=PCT, decimals=0,
              desc="The Mac's boot volume. Everything stops when this fills, so it is the first tile to look at."))
P.append(stat("Disk free", "min(host_disk_free_bytes)", at(4, 4, 4), "bytes", decimals=0))
P.append(stat("Memory used", "host_memory_used_bytes / host_memory_total_bytes * 100", at(4, 4, 8), "percent", thresholds=PCT, decimals=0,
              desc="App + wired + compressed, the way Activity Monitor counts it. File cache is excluded, so this stays "
                   "meaningful; a large model held in memory shows up here."))
P.append(stat("Swap used", "host_swap_used_bytes", at(4, 4, 12), "bytes", decimals=1,
              thresholds={"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "orange", "value": 2e9}, {"color": "red", "value": 8e9}]},
              desc="Swapping on a machine serving models is the clearest sign it is over-committed."))
P.append(stat("Load per core", "host_load1 / host_cpus", at(4, 4, 16), "short", decimals=2,
              thresholds={"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "orange", "value": 1}, {"color": "red", "value": 2}]}))
P.append(stat("Docker images on disk", "docker_disk_images_bytes", at(4, 4, 20), "bytes", decimals=1,
              desc="Inside the Docker VM. `docker system prune` reclaims it.")); down(4)
P.append(ts("Disk free on the Mac", ["host_disk_free_bytes"], at(8, 7, 0), "bytes", legend="{{mount}}"))
P.append(ts("Memory", [{"expr": "host_memory_used_bytes", "legendFormat": "used"},
                       {"expr": "host_memory_cached_bytes", "legendFormat": "cache"},
                       {"expr": "host_memory_free_bytes", "legendFormat": "free"}], at(8, 7, 8), "bytes", stack=True))
P.append(ts("Load average", [{"expr": "host_load1", "legendFormat": "1m"}, {"expr": "host_load5", "legendFormat": "5m"},
                             {"expr": "host_load15", "legendFormat": "15m"}], at(8, 7, 16), "short")); down(7)

P.append(row("Model traffic (LiteLLM /metrics)", _y[0])); down(1)
P.append(ts("Requests per minute, by model", ['sum by (requested_model) (rate(litellm_proxy_total_requests_metric_total{route=~"/v1/.*|/chat/.*|/embeddings"}[5m])) * 60'], at(8, 8, 0), "reqpm", legend="{{requested_model}}", stack=True))
P.append(ts("Latency p50 / p95, chat models", ['histogram_quantile(0.5, sum by (le, requested_model) (rate(litellm_request_total_latency_metric_bucket{requested_model!~".*embed.*"}[10m])))',
                                                {"expr": 'histogram_quantile(0.95, sum by (le, requested_model) (rate(litellm_request_total_latency_metric_bucket{requested_model!~".*embed.*"}[10m])))', "legendFormat": "p95 {{requested_model}}"}],
            at(8, 8, 8), "s", legend="p50 {{requested_model}}", desc="End-to-end request time through the router (includes queueing and generation)."))
P.append(ts("Tokens per minute", [{"expr": "sum(rate(litellm_input_tokens_metric_total[5m])) * 60", "legendFormat": "input"},
                                  {"expr": "sum(rate(litellm_output_tokens_metric_total[5m])) * 60", "legendFormat": "output"}], at(8, 8, 16), "short", stack=True)); down(8)
P.append(ts("Failed requests per minute", ['sum by (requested_model, exception_class) (rate(litellm_proxy_failed_requests_metric_total[5m])) * 60 or vector(0)'], at(8, 6, 0), "reqpm", legend="{{requested_model}} {{exception_class}}"))
P.append(ts("In flight", ["litellm_in_flight_requests"], at(8, 6, 8), "short", legend="requests"))
P.append(ts("Seconds per output token (deployment)", ['sum by (model) (rate(litellm_deployment_latency_per_output_token_sum[10m])) / sum by (model) (rate(litellm_deployment_latency_per_output_token_count[10m]))'], at(8, 6, 16), "s", legend="{{model}}", desc="Generation speed per backend model. Rising = the box is busy or thermally throttled.")); down(6)

P.append(row("History and spend (LiteLLM spend log in Postgres)", _y[0])); down(1)
P.append(ts("Requests per hour, by model group", [{"rawSql": 'SELECT $__timeGroupAlias("startTime", 1h), model_group AS metric, count(*) AS value FROM "LiteLLM_SpendLogs" WHERE $__timeFilter("startTime") GROUP BY 1, 2 ORDER BY 1', "format": "time_series"}], at(8, 8, 0), "short", ds=SPEND, stack=True))
P.append(ts("Time to first token p50, by model group", [{"rawSql": 'SELECT $__timeGroupAlias("startTime", 1h), model_group AS metric, percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM ("completionStartTime" - "startTime"))) AS value FROM "LiteLLM_SpendLogs" WHERE $__timeFilter("startTime") AND "completionStartTime" IS NOT NULL AND call_type LIKE \'%completion%\' GROUP BY 1, 2 ORDER BY 1', "format": "time_series"}], at(8, 8, 8), "s", ds=SPEND, desc="How long a user waits before the first word appears. The number people feel."))
P.append(ts("Spend per day, by model group", [{"rawSql": 'SELECT $__timeGroupAlias("startTime", 1d), model_group AS metric, sum(spend) AS value FROM "LiteLLM_SpendLogs" WHERE $__timeFilter("startTime") GROUP BY 1, 2 ORDER BY 1', "format": "time_series"}], at(8, 8, 16), "currencyUSD", ds=SPEND, stack=True, desc="Local models cost 0 unless you set prices in litellm/config.yaml; cloud fallbacks show real cost here.")); down(8)
P.append(table_sql("Who used what (selected range)", 'SELECT coalesce(nullif(metadata->>\'user_api_key_alias\', \'\'), left(api_key, 12)) AS key, model_group AS model, count(*) AS requests, sum(total_tokens) AS tokens, round(sum(spend)::numeric, 4) AS spend, round(avg(extract(epoch FROM ("endTime" - "startTime")))::numeric, 1) AS avg_s FROM "LiteLLM_SpendLogs" WHERE $__timeFilter("startTime") GROUP BY 1, 2 ORDER BY tokens DESC LIMIT 25', at(24, 8, 0))); down(8)

P.append(row("Containers (every project on this machine)", _y[0])); down(1)
P.append(ts("CPU %, by container", ["container_cpu_percent"], at(8, 7, 0), "percent", legend="{{project}}/{{service}}"))
P.append(ts("Memory, by container", ["container_memory_bytes"], at(8, 7, 8), "bytes", legend="{{project}}/{{service}}", stack=True))
P.append(ts("Network in / out", [{"expr": "sum by (service) (rate(container_network_receive_bytes_total[5m]))", "legendFormat": "rx {{service}}"},
                                 {"expr": "- sum by (service) (rate(container_network_transmit_bytes_total[5m]))", "legendFormat": "tx {{service}}"}], at(8, 7, 16), "Bps", min0=False)); down(7)

P.append(row("Logs (Loki)", _y[0])); down(1)
P.append(logs("Errors and warnings across every container", '{container=~".+"} |~ "(?i)(error|warn|traceback|exception)" != "GET /metrics"', at(24, 10))); down(10)
P.sort(key=lambda d: (d["gridPos"]["y"], d["gridPos"]["x"]))
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
# ------------------------------------------------------------------ gameplan
# Gameplan Network (github.com/offbyone-ai/gameplan-network) shares this Mac: a worker service under
# launchd, and four cron pipelines that start, work and exit. Metrics come from two places — the
# worker's own /metrics, and the pipeline exporter that reads the state files the crons leave behind.
_id = 300
G = []
_y = [0]

OK_FAIL = [{"type": "value", "options": {"0": {"text": "FAILED", "color": "red"},
                                         "0.5": {"text": "PARTIAL", "color": "orange"},
                                         "1": {"text": "OK", "color": "green"}}}]
GREEN_RED = {"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}
AGE = {"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "orange", "value": 6 * 3600}, {"color": "red", "value": 12 * 3600}]}

G.append(row("Last download run", _y[0])); down(1)
G.append(stat("Since last run", "time() - gameplan_pipeline_last_run_completed_timestamp_seconds", at(4, 4, 0), "s",
              thresholds=AGE, decimals=0,
              desc="The hourly pipeline runs 04:00–23:00, so a healthy overnight gap reaches five hours. "
                   "Orange starts after six."))
G.append(stat("Reports downloaded", 'gameplan_pipeline_last_run_reports{outcome="downloaded"}', at(4, 4, 4), decimals=0,
              desc="Zero is normal and common: a report already in the database is skipped, not re-fetched. "
                   "Read this next to 'Report fetches failed', which is the number that means trouble."))
G.append(stat("Report fetches failed", 'gameplan_pipeline_last_run_reports{outcome="failed"}', at(4, 4, 8), decimals=0,
              thresholds=GREEN_RED,
              desc="The portal answering with something that is not a PDF lands here. A sustained non-zero value "
                   "is the anti-bot arms race, not a network blip."))
G.append(stat("Counties failed", 'gameplan_pipeline_last_run_counties{outcome="failed"}', at(4, 4, 12), decimals=0,
              thresholds={"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "orange", "value": 1}, {"color": "red", "value": 10}]}))
G.append(stat("Run duration", "gameplan_pipeline_last_run_duration_seconds", at(4, 4, 16), "s", decimals=0))
G.append(stat("Run outcome", "gameplan_pipeline_last_run_ok", at(4, 4, 20), mappings=OK_FAIL, legend="{{job}}",
              thresholds={"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "orange", "value": 0.5}, {"color": "green", "value": 1}]},
              desc="Recomputed from the run's own county counts, the same way the pipeline derives what it reports "
                   "to the server — not read back out of a log line. The label says which entrypoint wrote it: the "
                   "hourly pipeline and the nightly reconcile both produce these.")); down(4)

G.append(ts("Reports per run, by outcome", ["gameplan_pipeline_last_run_reports"], at(12, 7, 0), "short", legend="{{outcome}}",
            desc="Stepped, not rated: each point is the last completed run, so the line is flat between runs."))
G.append(ts("Counties per run, by outcome", ["gameplan_pipeline_last_run_counties"], at(12, 7, 12), "short", legend="{{outcome}}")); down(7)

G.append(row("Portal pushback", _y[0])); down(1)
G.append(stat("Breaker cooldown left", "gameplan_portal_breaker_cooldown_remaining_seconds", at(4, 4, 0), "s", decimals=0,
              thresholds={"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "orange", "value": 1}, {"color": "red", "value": 3600}]},
              desc="Non-zero means the pipeline is deliberately standing down because the portal flagged it."))
G.append(stat("Request spacing", "gameplan_portal_breaker_spacing_seconds", at(4, 4, 4), "s", decimals=1,
              desc="Adapted gap between portal requests. It ratchets up under pushback and decays when things are quiet."))
G.append(stat("Escalation", "gameplan_portal_breaker_fruitless_trips", at(4, 4, 8), decimals=0,
              thresholds={"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "orange", "value": 2}, {"color": "red", "value": 4}]},
              desc="Consecutive breaker trips that yielded nothing."))
G.append(stat("CAPTCHAs last run", "gameplan_pipeline_last_run_captcha_attempts", at(4, 4, 12), decimals=0,
              desc="The leading indicator: CAPTCHA volume climbs before downloads start failing."))
G.append(stat("Egress", "gameplan_portal_breaker_egress_info", at(8, 4, 16), legend="{{egress}}",
              desc="Which network the breaker state was learned on. The crash portal wants the VPN off and "
                   "person-search wants it on, so this is worth a glance when downloads misbehave.")); down(4)
G.append(ts("Spacing and cooldown", [{"expr": "gameplan_portal_breaker_spacing_seconds", "legendFormat": "spacing"},
                                     {"expr": "gameplan_portal_breaker_cooldown_remaining_seconds", "legendFormat": "cooldown left"}], at(12, 7, 0), "s"))
G.append(ts("CAPTCHA attempts per run", ["gameplan_pipeline_last_run_captcha_attempts"], at(12, 7, 12), "short", legend="attempts")); down(7)

G.append(row("Worker service", _y[0])); down(1)
G.append(stat("Worker", 'up{job="gameplan-worker"}', at(3, 4, 0), mappings=UPDOWN, thresholds=RED_GREEN,
              desc="Scraped directly at :4000/metrics with the worker API key. DOWN here with the process alive "
                   "usually means WORKER_API_KEY in stack/.env no longer matches the worker's."))
G.append(stat("ocrmypdf", 'gameplan_worker_dependency_up{dependency="ocrmypdf"}', at(3, 4, 3), mappings=UPDOWN, thresholds=RED_GREEN))
G.append(stat("Vision model", "gameplan_worker_vision_model_served", at(3, 4, 6), mappings=UPDOWN, thresholds=RED_GREEN,
              desc="Whether the endpoint actually serves the configured VISION_MODEL. When this goes DOWN nothing "
                   "errors until a scraper meets a CAPTCHA, and then downloads stall with no obvious cause."))
G.append(stat("Browsers busy", 'gameplan_worker_pool_active{pool="browser"}', at(3, 4, 9), decimals=0))
G.append(stat("Browsers queued", 'gameplan_worker_pool_queued{pool="browser"}', at(3, 4, 12), decimals=0,
              thresholds={"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "orange", "value": 1}, {"color": "red", "value": 5}]}))
G.append(stat("OCR busy", 'gameplan_worker_pool_active{pool="ocr"}', at(3, 4, 15), decimals=0))
G.append(stat("OCR queued", 'gameplan_worker_pool_queued{pool="ocr"}', at(3, 4, 18), decimals=0,
              thresholds={"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "orange", "value": 1}, {"color": "red", "value": 5}]}))
G.append(stat("Worker uptime", "gameplan_worker_uptime_seconds", at(3, 4, 21), "s", decimals=0)); down(4)

G.append(ts("Requests per minute, by route", ["sum by (route) (rate(gameplan_worker_http_requests_total[5m])) * 60"], at(8, 7, 0), "reqpm", legend="{{route}}", stack=True))
G.append(ts("Request latency p95, by route", ["histogram_quantile(0.95, sum by (le, route) (rate(gameplan_worker_http_request_duration_seconds_bucket[10m])))"], at(8, 7, 8), "s", legend="{{route}}"))
G.append(ts("Non-2xx responses per minute", ['sum by (route, status) (rate(gameplan_worker_http_requests_total{status!~"2.."}[5m])) * 60 or vector(0)'], at(8, 7, 16), "reqpm", legend="{{route}} {{status}}")); down(7)
G.append(ts("Person searches per hour, by outcome", ["sum by (status) (increase(gameplan_worker_person_search_total[1h]))"], at(8, 7, 0), "short", legend="{{status}}", stack=True,
            desc="'rate-limited' and 'error' climbing together means the free scrapers are being blocked — and every "
                 "search that fails here falls back to paid Enformion on the server."))
G.append(ts("Person search p95 duration", ["histogram_quantile(0.95, sum by (le) (rate(gameplan_worker_person_search_duration_seconds_bucket[30m])))"], at(8, 7, 8), "s", legend="p95",
            desc="Includes time queued on the browser semaphore."))
G.append(ts("OCR runs per hour, by outcome", ["sum by (outcome) (increase(gameplan_worker_ocr_runs_total[1h]))"], at(8, 7, 16), "short", legend="{{outcome}}", stack=True)); down(7)

G.append(row("Geometry-parser shadow", _y[0])); down(1)
G.append(stat("Reports compared", 'gameplan_shadow_reports{outcome="compared"}', at(4, 4, 0), decimals=0))
G.append(stat("Differing", 'gameplan_shadow_reports{outcome="differs"}', at(4, 4, 4), decimals=0,
              thresholds={"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "orange", "value": 1}]}))
G.append(stat("Errors", 'gameplan_shadow_reports{outcome="errors"}', at(4, 4, 8), decimals=0, thresholds=GREEN_RED))
G.append(stat("Worst field agreement", "min(gameplan_shadow_field_match_ratio)", at(4, 4, 12), "percentunit", decimals=2,
              thresholds={"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "orange", "value": 0.98}, {"color": "green", "value": 0.999}]},
              desc="An aggregate parity number hides the one field that regressed, so this tracks the worst field."))
G.append(stat("Pages compared", "gameplan_shadow_pages", at(4, 4, 16), decimals=0))
G.append(stat("Ledger age", "time() - gameplan_shadow_updated_timestamp_seconds", at(4, 4, 20), "s", decimals=0, thresholds=AGE)); down(4)
G.append(ts("Fields that do not fully agree", ["gameplan_shadow_field_match_ratio < 1"], at(24, 7, 0), "percentunit", legend="{{field}}",
            desc="Only fields below 100% are drawn — everything else agreeing is the normal case and would bury them.")); down(7)

G.append(row("Cron freshness and logs", _y[0])); down(1)
G.append(stat("Since each cron last wrote", "time() - gameplan_cron_log_updated_timestamp_seconds", at(12, 4, 0), "s",
              legend="{{job}}", thresholds={"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "orange", "value": 26 * 3600}, {"color": "red", "value": 48 * 3600}]},
              decimals=0, desc="daily runs hourly 04:00–23:00, shadow at :20 past those hours, reconcile at 00:10, "
                               "integrations at :30. Reconcile and integrations are expected to look a day old."))
G.append(stat("Since a run last completed", "time() - gameplan_pipeline_last_run_summary_written_timestamp_seconds", at(6, 4, 12), "s",
              decimals=0, thresholds={"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "orange", "value": 5400}, {"color": "red", "value": 6 * 3600}]},
              desc="A run that is killed mid-flight writes no summary, so this keeps climbing \u2014 which is how a dead "
                   "run shows up. Orange past 90 min; the hourly pipeline\u2019s own runs are far shorter than that."))
G.append(stat("Exporter sources readable", "gameplan_pipeline_source_up", at(6, 4, 18), mappings=UPDOWN, thresholds=RED_GREEN,
              legend="{{source}}", desc="A source the exporter cannot read reports DOWN here rather than silently "
                                        "exporting nothing, which would look like a healthy quiet system.")); down(4)
G.append(logs("Pipeline errors and warnings", '{job="gameplan"} |~ "(?i)(error|warn|fail|not a valid pdf)"', at(24, 10))); down(10)
G.append(logs("Hourly download pipeline", '{job="gameplan", pipeline="daily"}', at(24, 10))); down(10)

G.sort(key=lambda d: (d["gridPos"]["y"], d["gridPos"]["x"]))
(OUT_GAMEPLAN / "gameplan.json").write_text(json.dumps(dashboard("gp-overview", "Gameplan — pipeline & worker", G, ["gameplan"]), indent=1))

print("wrote", sorted(str(p.relative_to(Path(__file__).parent)) for p in Path(__file__).parent.glob("dashboards/*/*.json")))
