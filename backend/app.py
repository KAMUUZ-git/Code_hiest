"""
=============================================================
  CAMPUS DIGITAL TWIN v2 — FASTAPI APPLICATION
=============================================================

Surface
-------
REST       : /api/status, /api/status/{id}, /api/update, /api/alerts,
             /api/reset, /api/summary, /api/history/{id},
             /api/predictions, /api/health
WebSocket  : /ws — push telemetry, alerts, predictions, anomalies
Embedded   : simulator.threads main() starts on lifespan startup;
             set CAMPUS_TWIN_EMBED_SIM=0 to run it standalone.

Run:
    uvicorn backend.app:app --port 8000
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, ValidationError

from backend import db as database
from backend.analytics import HORIZON_MINUTES, WINDOW_MINUTES, campus_forecast
from backend.facilities import FACILITY_DEFS
from backend.hub import hub
from backend.state import campus_state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(levelname)-8s]  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("campus_twin.app")

EMBED_SIMULATOR = os.environ.get("CAMPUS_TWIN_EMBED_SIM", "1") == "1"
PREDICTION_PUSH_SECONDS = 20.0   # periodic /ws push cadence for the predictions HUD

# Project root — the backend serves the dashboard itself, so running
# the whole platform is ONE command: uvicorn backend.app:app
BASE_DIR = Path(__file__).resolve().parent.parent


# ─────────────────────────────────────────────────────────────
#  FACILITY REGISTRY — single shared source (backend/facilities.py)
#  14 real IIT Delhi facilities; see facilities.py for the layout.
# ─────────────────────────────────────────────────────────────


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────
#  LIFESPAN (startup / shutdown)
# ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────
    database.init_db()
    for fdef in FACILITY_DEFS:
        campus_state.register_facility(dict(fdef))
    restored = campus_state.restore_from_db()
    campus_state.preload_history(window_minutes=60)
    hub.bind_loop(asyncio.get_running_loop())

    logger.info("=" * 62)
    logger.info("🏫  Campus Digital Twin v2 — ONLINE")
    logger.info("🗺️  Facilities: %d registered | %d restored from SQLite", len(FACILITY_DEFS), restored)
    logger.info("📈  Analytics: moving-average forecast, %d min window → %d min horizon",
                WINDOW_MINUTES, HORIZON_MINUTES)
    logger.info("📖  REST       →  http://127.0.0.1:8000/docs")
    logger.info("⚡  WebSocket  →  ws://127.0.0.1:8000/ws")
    logger.info("💚  Health     →  http://127.0.0.1:8000/api/health")
    logger.info("=" * 62)

    sim_task = None
    if EMBED_SIMULATOR:
        from backend.simulator.threads import main as sim_main
        sim_task = asyncio.create_task(asyncio.to_thread(sim_main))
        logger.info("🛰️  Embedded simulator started (%d building threads)", len(FACILITY_DEFS))

    # Periodic predictive push: the 60-min forecast HUD refreshes over
    # the same WebSocket stream — no polling loop on the frontend.
    async def push_predictions() -> None:
        while True:
            await asyncio.sleep(PREDICTION_PUSH_SECONDS)
            try:
                await hub.broadcast_json({"type": "predictions", **campus_forecast()})
            except Exception:          # noqa: BLE001
                logger.exception("Prediction push failed")

    pred_task = asyncio.create_task(push_predictions())

    yield

    # ── Shutdown ─────────────────────────────────────────────
    pred_task.cancel()
    if sim_task:
        sim_task.cancel()
        logger.info("🛰️  Simulator stopped")
    logger.info("🔴  Campus Digital Twin shutting down. Goodbye!")


# ─────────────────────────────────────────────────────────────
#  APP + MIDDLEWARE
# ─────────────────────────────────────────────────────────────

app = FastAPI(
    title="🏫 Campus Digital Twin API",
    description=(
        "Production-grade campus twin: WebSocket telemetry streaming, "
        "SQLite historical persistence, and moving-average predictive "
        "analytics with a 60-minute capacity-breach forecast."
    ),
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],           # demo profile; tighten before public deploy
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request, call_next):
    logger.info("➡️  %s %s", request.method, request.url.path)
    response = await call_next(request)
    logger.info("⬅️  %s %s → HTTP %s", request.method, request.url.path, response.status_code)
    return response


# ─────────────────────────────────────────────────────────────
#  PYDANTIC SCHEMAS
# ─────────────────────────────────────────────────────────────

class FacilityUpdate(BaseModel):
    id: str = Field(..., examples=["science-block"])
    current_occupancy: int = Field(..., ge=0, examples=[120])


class AlertCreate(BaseModel):
    facility_id: str = Field(..., examples=["science-block"])
    alert_type: str = Field(default="INFO", examples=["WARNING"])
    message: str = Field(..., min_length=3, examples=["HVAC maintenance required"])


class AnomalyTrigger(BaseModel):
    anomaly_id: str = Field(
        default="random",
        examples=["storm-surge"],
        description="Anomaly id from GET /api/anomalies, or 'random'.",
    )


# ─────────────────────────────────────────────────────────────
#  WEBSOCKET ENDPOINT /ws
# ─────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Real-time telemetry stream.

    Server → client messages (JSON, one per message):
      {type: "snapshot",    facilities: [...], ts}
      {type: "telemetry",   facility: {...}}
      {type: "alert",       facility_id, alert}
      {type: "anomaly",     title, narrative, facilities: [...], ts}
      {type: "predictions", breach_queue, facilities_at_risk, ts}

    Client → server (optional control channel):
      {action: "subscribe", facility_id}
      {action: "ping"}
    """
    await hub.connect(websocket)
    try:
        # Greet every new dashboard with a full snapshot so the map
        # paints instantly without waiting for the next tick.
        await websocket.send_json({
            "type": "snapshot",
            "facilities": campus_state.snapshot(),
            "ts": _utc_now(),
        })

        # Control channel: pings + targeted resends.
        while True:
            inbound = await websocket.receive_text()
            try:
                msg = json.loads(inbound)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "detail": "invalid json"})
                continue

            action = msg.get("action")
            if action == "ping":
                await websocket.send_json({"type": "pong", "ts": _utc_now()})
            elif action == "subscribe":
                fid = str(msg.get("facility_id", ""))
                event = campus_state.facility_event(fid)
                if event:
                    await websocket.send_json({"type": "telemetry", "facility": event})
                else:
                    await websocket.send_json({
                        "type": "snapshot", "facilities": campus_state.snapshot(), "ts": _utc_now(),
                    })
            else:
                await websocket.send_json({"type": "error", "detail": f"unknown action '{action}'"})
    except WebSocketDisconnect:
        hub.disconnect(websocket)
    except Exception as exc:                      # noqa: BLE001
        logger.warning("WS session error: %s", exc)
        hub.disconnect(websocket)


# ─────────────────────────────────────────────────────────────
#  REST ENDPOINTS
# ─────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
def dashboard():
    """Serve the Command Center — the backend hosts the whole platform."""
    return FileResponse(BASE_DIR / "index.html")


@app.get("/app.js", include_in_schema=False)
def app_js():
    return FileResponse(BASE_DIR / "app.js")


@app.get("/api/about", tags=["Health"], include_in_schema=False)
def about():
    return {
        "message": "Campus Digital Twin API v2 live",
        "websocket": "ws://127.0.0.1:8000/ws",
        "docs": "/docs",
        "health": "/api/health",
        "predictions": "/api/predictions",
    }


@app.get("/api/health", tags=["Health"])
def health():
    return {"status": "online", "timestamp": _utc_now(), "version": "2.0.0",
            "ws_clients": hub.count()}


@app.get("/api/status", tags=["Campus State"])
def get_all_status():
    return {"count": len(campus_state.facilities), "facilities": campus_state.snapshot()}


@app.get("/api/status/{facility_id}", tags=["Campus State"])
def get_facility(facility_id: str):
    event = campus_state.facility_event(facility_id)
    if not event:
        raise HTTPException(404, detail=f"Facility '{facility_id}' not found.")
    return event


@app.post("/api/update", tags=["Data Engine"])
def update_facility(payload: FacilityUpdate):
    """Telemetry ingestion — persists to SQLite and fans out over /ws
    (broadcast happens inside CampusState.update_facility, the single
    write path shared with the simulator)."""
    try:
        event = campus_state.update_facility(payload.id, payload.current_occupancy, source="api")
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc))
    if event is None:
        raise HTTPException(404, detail=f"Facility '{payload.id}' not found.")
    return {"message": f"Facility '{payload.id}' updated.", "updated_facility": event}


@app.post("/api/alerts", tags=["Alerts"], status_code=201)
def create_alert(payload: AlertCreate):
    event = campus_state.raise_alert(payload.facility_id, {
        "type": payload.alert_type, "severity": payload.alert_type.lower(), "message": payload.message,
    })
    if event is None:
        raise HTTPException(404, detail=f"Facility '{payload.facility_id}' not found.")
    return {"message": f"Alert added to '{payload.facility_id}'.", "alert": event["alerts"][-1]}


@app.delete("/api/alerts/{facility_id}", tags=["Alerts"])
def clear_alerts(facility_id: str):
    if campus_state.facility_event(facility_id) is None:
        raise HTTPException(404, detail=f"Facility '{facility_id}' not found.")
    n = campus_state.clear_alerts(facility_id)
    hub.broadcast_threadsafe({
        "type": "telemetry", "facility": campus_state.facility_event(facility_id), "ts": _utc_now(),
    })
    return {"message": f"All alerts cleared for '{facility_id}'.", "alerts_cleared": n}


@app.post("/api/reset", tags=["Demo Control"])
def reset_campus():
    events = campus_state.reset_to_baseline()
    hub.broadcast_threadsafe({"type": "reset", "facilities": events, "ts": _utc_now()})
    return {"message": "Campus reset to baseline.", "facilities_reset": len(events)}


@app.get("/api/anomalies", tags=["Demo Control"])
def list_anomalies():
    """Catalog of triggerable environmental anomalies."""
    from backend.simulator import anomalies as anomaly_engine
    return {
        "anomalies": [
            {"id": a["id"], "title": a["title"]} for a in anomaly_engine.ANOMALIES
        ]
    }


@app.post("/api/anomaly/trigger", tags=["Demo Control"])
def trigger_anomaly_endpoint(payload: AnomalyTrigger):
    """
    ⚡ Fire an environmental anomaly on demand (demo/showmanship control).

    Pressure cascades across the affected buildings over the next few
    simulator ticks, alerts are raised, and the dashboard banner appears
    via the WebSocket stream — all through the normal write path.
    """
    from backend.simulator import threads as sim

    result = sim.trigger_anomaly(payload.anomaly_id)
    if result == "no-simulator":
        raise HTTPException(
            503,
            detail="Simulator is not running (backend started with "
                   "CAMPUS_TWIN_EMBED_SIM=0). Restart with the simulator enabled.",
        )
    if result is None:
        raise HTTPException(
            404,
            detail=f"Unknown anomaly '{payload.anomaly_id}'. "
                   "See GET /api/anomalies for valid ids.",
        )
    title, narrative = result
    return {"message": f"Anomaly triggered: {title}", "title": title, "narrative": narrative}


@app.get("/api/summary", tags=["Demo Control"])
def executive_summary():
    return campus_state.summary()


@app.get("/api/history/{facility_id}", tags=["Analytics"])
def get_history(
    facility_id: str,
    minutes: int = Query(60, ge=1, le=1440, description="Trailing window in minutes"),
    limit: int = Query(240, ge=1, le=1000, description="Max points returned"),
):
    """Historical occupancy (SQLite) — feeds the Chart.js sidebar graph."""
    if campus_state.facility_event(facility_id) is None:
        raise HTTPException(404, detail=f"Facility '{facility_id}' not found.")
    points = campus_state.history(facility_id, window_minutes=minutes, limit=limit)
    return {"facility_id": facility_id, "window_minutes": minutes,
            "points": points, "generated_at": _utc_now()}


@app.get("/api/predictions", tags=["Analytics"])
def predictions():
    """60-minute moving-average capacity-breach forecast (the spec endpoint)."""
    return campus_forecast()


# ─────────────────────────────────────────────────────────────
#  EXCEPTION HANDLERS
# ─────────────────────────────────────────────────────────────

@app.exception_handler(ValidationError)
async def validation_error_handler(request, exc: ValidationError):
    logger.warning("⚠️  Validation error on %s", request.url.path)
    return JSONResponse(status_code=422, content={"error": "Payload validation failed",
                                                  "problems": exc.errors()})


@app.exception_handler(Exception)
async def global_exception_handler(request, exc: Exception):
    logger.exception("💥  Unhandled exception on %s", request.url.path)
    return JSONResponse(status_code=500, content={"error": "Internal server error",
                                                  "detail": str(exc)})
