"""Generate the provisioned dashboards (dashboards/*.json). Edit here, run `python3 build_dashboards.py`.
Kept as a builder because hand-editing Grafana JSON is how dashboards rot."""
from __future__ import annotations

import json
from pathlib import Path

# One directory per provisioned Grafana folder — see provisioning/dashboards/dashboards.yml.
OUT = Path(__file__).parent / "dashboards/stack"
OUT.mkdir(parents=True, exist_ok=True)
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

# ------------------------------------------------------------------ voice demo
# Scraped from the voice demo's /metrics: how real conversations feel, not how the tests say they should.
_id = 200
V = []
Q = lambda q, m, by="": f'histogram_quantile({q}, sum by (le{", " + by if by else ""}) (rate({m}_bucket[$__rate_interval])))'
V.append(row("How it feels", 0))
V.append(stat("First sound p50 (24h)", 'histogram_quantile(0.5, sum by (le) (increase(voice_first_sound_seconds_bucket[24h])))', (0, 1, 4, 4), "s", decimals=2,
              desc="End of your speech to the first thing you hear — a filler or the answer."))
V.append(stat("Answer heard p50 (24h)", 'histogram_quantile(0.5, sum by (le) (increase(voice_answer_heard_seconds_bucket[24h])))', (4, 1, 4, 4), "s", decimals=2,
              desc="End of your speech to the first word of the actual answer."))
V.append(stat("Answer heard p95 (24h)", 'histogram_quantile(0.95, sum by (le) (increase(voice_answer_heard_seconds_bucket[24h])))', (8, 1, 4, 4), "s", decimals=2,
              thresholds={"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "orange", "value": 3}, {"color": "red", "value": 5}]}))
V.append(stat("Turns (24h)", 'sum(increase(voice_turns_total{outcome=~"done|interrupted"}[24h])) or vector(0)', (12, 1, 4, 4), decimals=0))
V.append(stat("Talked over it (24h)", 'sum(increase(voice_turns_total{outcome="interrupted"}[24h])) or vector(0)', (16, 1, 4, 4), decimals=0,
              desc="Replies stopped because someone talked over them."))
V.append(stat("Rate-limited (24h)", 'sum(increase(voice_rate_limited_total[24h])) or vector(0)', (20, 1, 4, 4), decimals=0,
              thresholds={"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "orange", "value": 1}]}))
V.append(row("Latency", 5))
V.append(ts("Answer heard p50, by speech engine", [Q(0.5, "voice_answer_heard_seconds", "engine")], (0, 6, 12, 8), "s", legend="{{engine}}"))
V.append(ts("Answer heard p50, by end-of-turn mode", [Q(0.5, "voice_answer_heard_seconds", "turn")], (12, 6, 12, 8), "s", legend="{{turn}}",
            desc="smart = Smart Turn model after a 0.3 s pause; pause = a fixed silence. Both include that pause."))
V.append(ts("First sound p50, fillers on vs off", [Q(0.5, "voice_first_sound_seconds", "fillers")], (0, 14, 12, 8), "s", legend="fillers {{fillers}}"))
V.append(ts("Pipeline stages p50", [Q(0.5, "voice_stage_seconds", "stage")], (12, 14, 12, 8), "s", legend="{{stage}}",
            desc="From the end-of-turn decision: transcript back (heard) and the first sentence written (first_words)."))
V.append(row("Turn-taking", 22))
V.append(ts("How turns ended", ["sum by (outcome) (increase(voice_turns_total[$__rate_interval]))"], (0, 23, 8, 8), "short", legend="{{outcome}}", stack=True,
            desc="reopened = you carried on before the answer started; ignored = a 'yeah' over the reply; continued = spoke again before any answer played."))
V.append(ts("Smart Turn verdicts", ["sum by (verdict) (increase(voice_smart_turn_total[$__rate_interval]))"], (8, 23, 8, 8), "short", legend="{{verdict}}", stack=True))
V.append(ts("Sounds over the reply", ["sum by (verdict) (increase(voice_backchannel_total[$__rate_interval]))"], (16, 23, 8, 8), "short", legend="{{verdict}}", stack=True,
            desc="backchannel = 'yeah', 'mm-hmm' and friends, ignored; interrupt = real words, stopped the reply; ambient = no words at all."))
V.append({"id": nid(), "type": "heatmap", "title": "Smart Turn probability (how sure it was)", "datasource": PROM, "gridPos": grid(0, 31, 12, 8),
          "options": {"calculate": False, "yAxis": {"unit": "short"}, "color": {"scheme": "Oranges", "mode": "scheme"}},
          "targets": [{"refId": "A", "datasource": PROM, "expr": "sum by (le) (increase(voice_smart_turn_probability_bucket[$__rate_interval]))", "format": "heatmap", "legendFormat": "{{le}}"}],
          "description": "Most mass near 0 or 1 means the model is confident; a pile in the middle means pauses it cannot call."})
V.append(ts("Smart Turn inference p50 / p95", [{"expr": Q(0.5, "voice_smart_turn_inference_seconds"), "legendFormat": "p50"},
                                               {"expr": Q(0.95, "voice_smart_turn_inference_seconds"), "legendFormat": "p95"}], (12, 31, 12, 8), "s"))
V.sort(key=lambda d: (d["gridPos"]["y"], d["gridPos"]["x"]))
(OUT / "voice.json").write_text(json.dumps(dashboard("pas-voice", "Private AI stack — voice demo", V, ["private-ai-stack", "voice"], frm="now-24h"), indent=1))

# ------------------------------------------------------------------ local overlay
# Anything else on this machine that you want dashboards for lives outside this repo: drop a
# build_*_local.py in stack/local/ (gitignored) and it runs here with the same helpers, writing to
# its own folder under dashboards/. Nothing private has to be committed to be monitored.
if __name__ == "__main__":  # not when an overlay imports these helpers
    import runpy

    for _extra in sorted((Path(__file__).resolve().parents[2] / "local").glob("build_*_local.py")):
        print("running local overlay:", _extra.name)
        runpy.run_path(str(_extra), run_name="__main__")
