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

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

import sources
import export

app = FastAPI(title="NOC Copilot Clean Data API", version="1.0")


@app.get("/")
def root():
    return {
        "service": "noc-copilot-dataapi",
        "endpoints": ["/metrics", "/events", "/flows", "/labels", "/topology", "/datasets"],
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
def flows(limit: int = Query(500), device: str = Query(None)):
    return {"rows": sources.flow_rows(limit=limit, device=device)}


@app.get("/labels")
def labels():
    return {"rows": sources.label_rows()}


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
    existing = sorted(glob.glob(os.path.join(export.DATASETS_DIR, "*.parquet")))
    if not existing:
        raise HTTPException(404, "no dataset built yet; call with build=true")
    path = existing[-1]
    return FileResponse(path, media_type="application/octet-stream",
                        filename=os.path.basename(path))
