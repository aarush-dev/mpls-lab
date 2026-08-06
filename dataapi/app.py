"""app.py -- Clean Data API (FastAPI, local-only).

The contract the AI/ML/RAG engineers consume. Bind 127.0.0.1 only -- offline
lab tool. Handlers are thin: all logic lives in sources.py / export.py.

Run:  uvicorn app:app --host 127.0.0.1 --port 8000   (from dataapi/)

Endpoints:
  GET /metrics   -- VictoriaMetrics PromQL passthrough (instant or range)
  GET /events    -- Loki log/event rows for a window
  GET /flows     -- recent nfacctd flow records
  GET /labels    -- ground-truth fault timeline
  GET /topology  -- graph JSON (nodes + links)
  GET /datasets  -- build (or return latest) joined labeled Parquet
"""
import glob
import os
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse

import sources
import export
import faults_api

app = FastAPI(title="NOC Copilot Clean Data API", version="1.0")

# Node detail loads ~510KB uncompressed (labels/events/flows) — brutal over an ssh -L
# tunnel. gzip cuts JSON ~10x. min_size skips the tiny /metrics range replies.
app.add_middleware(GZipMiddleware, minimum_size=500)

# CORS: only the Grafana browser plugin origin (localhost:3000). GET+POST is all
# the plugin needs; this allow-list IS the security boundary for /faults/* (which
# runs docker exec) -- localhost-scoped, no auth by design (brief).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.include_router(faults_api.router)


@app.get("/")
def root():
    return {
        "service": "noc-copilot-dataapi",
        "endpoints": ["/metrics", "/events", "/flows", "/labels", "/topology", "/datasets",
                      "/faults/scenarios", "/faults/inject", "/faults/active", "/faults/revert/{id}"],
        "join_key": "device",
        "schema_docs": "dataapi/schema/",
    }


@app.get("/metrics")
def metrics(
    query: str = Query(..., description="PromQL expression"),
    start: int = Query(None, description="range start, epoch s (omit for instant)"),
    end: int = Query(None, description="range end, epoch s"),
    step: int = Query(30, description="range step seconds"),
):
    """PromQL passthrough to VictoriaMetrics. Range if start given, else instant."""
    try:
        if start is not None:
            end = end or int(time.time())
            return {"result": sources.vm_query_range(query, start, end, step)}
        return {"result": sources.vm_query(query)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"VictoriaMetrics error: {e}")


@app.post("/metrics/batch")
def metrics_batch(payload: dict = Body(...)):
    """Run N range queries in one request. Node detail fires 32 PromQL queries per load;
    over an ssh -L tunnel that is 32 round-trips. VM is local, so fan them out in a thread
    pool here and return one array (input order). A failed query -> {result: []} so one dead
    metric never sinks the batch (matches the per-metric try/except the client had)."""
    queries = payload.get("queries") or []
    start = payload.get("start")
    end = payload.get("end") or int(time.time())
    step = payload.get("step", 30)

    def run(q):
        try:
            return {"result": sources.vm_query_range(q, start, end, step)}
        except Exception:  # noqa: BLE001
            return {"result": []}

    with ThreadPoolExecutor(max_workers=8) as pool:
        return {"results": list(pool.map(run, queries))}


@app.get("/events")
def events(
    start: int = Query(None, description="epoch s; default now-1h"),
    end: int = Query(None, description="epoch s; default now"),
    device: str = Query(None),
    limit: int = Query(1000),
):
    end = end or int(time.time())
    start = start or end - 3600
    try:
        return {"rows": sources.events_rows(start, end, device=device, limit=limit)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Loki error: {e}")


@app.get("/flows")
def flows(
    limit: int = Query(500),
    device: str = Query(None),
    start: int = Query(None, description="epoch s; window start (scopes the fetch itself)"),
    end: int = Query(None, description="epoch s; window end (forensic freeze passes T_snapshot)"),
):
    # start/end reach flow_rows as since/until so a named/forensic window is not
    # silently reduced to the newest log lines (ADR-0002/0015). None = unscoped (legacy).
    try:
        return {"rows": sources.flow_rows(limit=limit, device=device, since=start, until=end)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"flow source error: {e}")


@app.get("/labels")
def labels(device: str = Query(None)):
    return {"rows": sources.label_rows(device=device)}


@app.get("/topology")
def topology():
    return sources.topology_graph()


@app.get("/datasets")
def datasets(
    start: int = Query(None, description="window start epoch s; default now-1h"),
    end: int = Query(None, description="window end epoch s; default now"),
    step: int = Query(30, description="time bucket seconds"),
    build: bool = Query(False, description="force a fresh build for the window"),
):
    """Return the ML-ready joined Parquet. If build=true (or none exists) run
    export.py for the window, else return the most recent dataset file."""
    if build or start is not None:
        end = end or int(time.time())
        start = start or end - 3600
        path = export.build_dataset(start, end, step)
        return FileResponse(path, media_type="application/octet-stream",
                            filename=os.path.basename(path))
    existing = glob.glob(os.path.join(export.DATASETS_DIR, "*.parquet"))
    if not existing:
        raise HTTPException(404, "no dataset built yet; call with build=true")
    # by window END, not the lexicographic filename sort (which orders by START)
    path = max(existing, key=lambda p: int(os.path.basename(p).split("_")[2]))
    return FileResponse(path, media_type="application/octet-stream",
                        filename=os.path.basename(path))
