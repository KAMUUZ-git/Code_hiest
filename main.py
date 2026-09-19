"""
=============================================================
  CAMPUS DIGITAL TWIN — Phase 4 Backend (Final Polish & Demo Ready)
  Tech Stack: FastAPI + Uvicorn + Pydantic v2
=============================================================

  HOW TO RUN:
  -----------
  uvicorn main:app --reload --port 8000

  Swagger UI      →  http://127.0.0.1:8000/docs
  Health ping     →  http://127.0.0.1:8000/api/health
  Judge Reset     →  POST http://127.0.0.1:8000/api/reset
  Executive View  →  GET  http://127.0.0.1:8000/api/summary

  PHASE 4 CHANGES vs PHASE 3:
  ----------------------------
  + POST /api/reset   — one-shot demo reset; clears alerts + floors occupancy
  + GET  /api/summary — live campus-wide aggregation (avg occupancy, alert count)
  + Full Swagger descriptions on every endpoint (enterprise-grade docs)
  + Structured log section headers for cleaner terminal output
=============================================================
"""

# ─────────────────────────────────────────────────────────────
#  IMPORTS
# ─────────────────────────────────────────────────────────────
import logging
import sys
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError, field_validator
from typing import Optional


# ─────────────────────────────────────────────────────────────
#  1. LOGGING SETUP
# ─────────────────────────────────────────────────────────────

LOG_FORMAT  = "%(asctime)s  [%(levelname)-8s]  %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    datefmt=DATE_FORMAT,
    stream=sys.stdout,
)

logger = logging.getLogger("campus_twin")


# ─────────────────────────────────────────────────────────────
#  2. APP INITIALISATION
# ─────────────────────────────────────────────────────────────

DESCRIPTION = """
## Campus Digital Twin API — Phase 4

A **production-hardened**, demo-ready backend serving the Campus Digital Twin
hackathon project. Designed to be shown live to hackathon judges.

### Feature Overview

| Tag | Purpose |
|-----|---------|
| **Health** | Liveness probes for the frontend status badge |
| **Campus State** | Real-time facility occupancy data |
| **Data Engine** | Telemetry ingestion from the Phase 2 simulator |
| **Alerts** | Per-facility alert management |
| **Demo Control** | Judge-facing reset and executive summary endpoints |

### Quick Links
- **Reset for next judge** → `POST /api/reset`
- **Executive summary**   → `GET /api/summary`
- **Health badge**        → `GET /api/health`
"""

app = FastAPI(
    title="🏫 Campus Digital Twin API",
    description=DESCRIPTION,
    version="4.0.0",
    contact={
        "name": "Campus Digital Twin Team",
    },
    license_info={
        "name": "MIT",
    },
)


# ─────────────────────────────────────────────────────────────
#  3. LIFECYCLE EVENTS
# ─────────────────────────────────────────────────────────────

@app.on_event("startup")
async def on_startup():
    logger.info("=" * 62)
    logger.info("🏫  Campus Digital Twin API  v4.0.0  —  ONLINE")
    logger.info("📖  Swagger UI   →  http://127.0.0.1:8000/docs")
    logger.info("💚  Health       →  http://127.0.0.1:8000/api/health")
    logger.info("🔄  Judge Reset  →  POST /api/reset")
    logger.info("📊  Summary      →  GET  /api/summary")
    logger.info("=" * 62)


@app.on_event("shutdown")
async def on_shutdown():
    logger.info("🔴  Campus Digital Twin API is shutting down. Goodbye!")


# ─────────────────────────────────────────────────────────────
#  4. REQUEST LOGGING MIDDLEWARE
# ─────────────────────────────────────────────────────────────

@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Logs every inbound request and its response status code."""
    logger.info("➡️   %s  %s", request.method, request.url.path)
    response = await call_next(request)
    logger.info("⬅️   %s  %s  →  HTTP %s", request.method, request.url.path, response.status_code)
    return response


# ─────────────────────────────────────────────────────────────
#  5. GLOBAL EXCEPTION HANDLER
# ─────────────────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("💥  Unhandled exception on %s → %s", request.url.path, str(exc))
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "detail": str(exc),
            "path": str(request.url),
        },
    )


# ─────────────────────────────────────────────────────────────
#  6. PYDANTIC VALIDATION ERROR HANDLER
# ─────────────────────────────────────────────────────────────

@app.exception_handler(ValidationError)
async def validation_error_handler(request: Request, exc: ValidationError):
    """
    Converts raw Pydantic validation errors into a readable, field-level
    breakdown instead of a wall of unformatted JSON.
    """
    logger.warning(
        "⚠️   Validation error on %s — %d field(s) rejected",
        request.url.path, exc.error_count(),
    )

    invalid_fields = []
    for error in exc.errors():
        field_path = " → ".join(str(loc) for loc in error["loc"] if loc != "body")
        invalid_fields.append({
            "field":     field_path or "(root)",
            "problem":   error["msg"],
            "bad_value": str(error.get("input", "N/A")),
        })

    return JSONResponse(
        status_code=422,
        content={
            "error":          "Payload validation failed",
            "invalid_fields": invalid_fields,
            "tip":            "Open http://127.0.0.1:8000/docs for the correct schema.",
        },
    )


# ─────────────────────────────────────────────────────────────
#  7. CORS MIDDLEWARE
# ─────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────
#  8. HELPER UTILITIES
# ─────────────────────────────────────────────────────────────

# Occupancy fraction to seed each facility during a reset.
# 5% gives a visually "low but non-zero" baseline on the frontend map.
RESET_OCCUPANCY_FRACTION = 0.05


def _compute_status_color(percentage: float) -> str:
    """
    Returns a traffic-light colour string based on occupancy percentage.

      Green   — below 60 % : comfortable, normal operations
      Yellow  — 60 – 85 %  : busy, monitor closely
      Red     — above 85 % : critical / overcrowded
    """
    if percentage >= 85.0:
        return "Red"
    elif percentage >= 60.0:
        return "Yellow"
    return "Green"


def _utc_now_iso() -> str:
    """Returns the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _reset_facility(facility: dict) -> None:
    """
    In-place reset of a single facility to the baseline demo state.
    Calculates a 5% occupancy from that facility's own max_capacity
    so every building gets a proportionally correct headcount.
    """
    baseline_occupancy = max(1, int(facility["max_capacity"] * RESET_OCCUPANCY_FRACTION))
    baseline_pct       = round((baseline_occupancy / facility["max_capacity"]) * 100, 2)

    facility["current_occupancy"]    = baseline_occupancy
    facility["occupancy_percentage"] = baseline_pct
    facility["status_color"]         = _compute_status_color(baseline_pct)
    facility["active_alerts"]        = []
    facility["last_updated"]         = _utc_now_iso()


# ─────────────────────────────────────────────────────────────
#  9. IN-MEMORY DATABASE — CAMPUS STATE
# ─────────────────────────────────────────────────────────────

campus_state: dict[str, dict] = {
    "sci-block": {
        "id": "sci-block",
        "name": "Science Block",
        "type": "building",
        "coordinates": {"latitude": 28.6139, "longitude": 77.2090},
        "max_capacity": 500,
        "current_occupancy": 360,
        "occupancy_percentage": 72.0,
        "status_color": "Yellow",
        "active_alerts": [],
        "last_updated": _utc_now_iso(),
    },
    "library": {
        "id": "library",
        "name": "Library",
        "type": "building",
        "coordinates": {"latitude": 28.6145, "longitude": 77.2098},
        "max_capacity": 300,
        "current_occupancy": 135,
        "occupancy_percentage": 45.0,
        "status_color": "Green",
        "active_alerts": [],
        "last_updated": _utc_now_iso(),
    },
    "main-parking": {
        "id": "main-parking",
        "name": "Main Parking",
        "type": "parking",
        "coordinates": {"latitude": 28.6130, "longitude": 77.2075},
        "max_capacity": 200,
        "current_occupancy": 40,
        "occupancy_percentage": 20.0,
        "status_color": "Green",
        "active_alerts": [],
        "last_updated": _utc_now_iso(),
    },
}


# ─────────────────────────────────────────────────────────────
#  10. PYDANTIC SCHEMAS
# ─────────────────────────────────────────────────────────────

class FacilityUpdate(BaseModel):
    """
    Telemetry payload sent by the Data Engine to update a facility's
    live occupancy count. The server derives all other metrics automatically.
    """
    id: str = Field(
        ...,
        description="Unique facility identifier. Must match a key in `campus_state`.",
        examples=["sci-block", "library", "main-parking"],
    )
    current_occupancy: int = Field(
        ...,
        ge=0,
        description=(
            "Current live headcount or vehicle count inside the facility. "
            "Must be a non-negative integer and cannot exceed `max_capacity`."
        ),
    )

    @field_validator("current_occupancy")
    @classmethod
    def occupancy_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("current_occupancy cannot be negative.")
        return v


class AlertCreate(BaseModel):
    """
    Payload for raising a new operational alert against a specific facility.
    The alert is time-stamped by the server and appended to the facility's
    `active_alerts` list.
    """
    facility_id: str = Field(
        ...,
        description="Unique ID of the target facility.",
        examples=["sci-block"],
    )
    alert_type: str = Field(
        ...,
        description="Severity classification of the alert.",
        examples=["Info", "Warning", "Critical"],
    )
    message: str = Field(
        ...,
        min_length=3,
        description="Human-readable description of the alert condition.",
        examples=["High footfall detected", "HVAC maintenance required"],
    )


# ─────────────────────────────────────────────────────────────
#  11. API ENDPOINTS
# ─────────────────────────────────────────────────────────────

# ── 11a. Root ─────────────────────────────────────────────────

@app.get(
    "/",
    tags=["Health"],
    summary="Root liveness probe",
    description=(
        "A minimal liveness endpoint that confirms the server process is running "
        "and returns the current API version. Useful as a quick sanity check "
        "before running the full demo."
    ),
)
def root():
    return {
        "message": "Campus Digital Twin API is live",
        "version": "4.0.0",
        "docs":    "http://127.0.0.1:8000/docs",
        "health":  "/api/health",
        "reset":   "/api/reset",
        "summary": "/api/summary",
    }


# ── 11b. Health Check ─────────────────────────────────────────

@app.get(
    "/api/health",
    tags=["Health"],
    summary="Integration health check",
    description=(
        "Dedicated liveness endpoint consumed by the frontend to power the "
        "**SYSTEM ONLINE** status badge in the navigation bar. "
        "Returns `{\"status\": \"online\"}` plus the current UTC timestamp. "
        "\n\n"
        "**Frontend integration example:**\n"
        "```js\n"
        "const { status } = await fetch('/api/health').then(r => r.json());\n"
        "setBadgeColor(status === 'online' ? 'green' : 'red');\n"
        "```"
    ),
)
def health_check():
    ts = _utc_now_iso()
    logger.info("💚  Health check → ONLINE  (%s)", ts)
    return {"status": "online", "timestamp": ts}


# ── 11c. GET All Facilities ───────────────────────────────────

@app.get(
    "/api/status",
    tags=["Campus State"],
    summary="Get the full campus digital-twin state",
    description=(
        "Returns the current live state of **all** campus facilities as an "
        "ordered list. Each facility object includes coordinates, occupancy "
        "metrics, traffic-light status colour, active alerts, and a "
        "`last_updated` UTC timestamp.\n\n"
        "The 3D frontend map calls this endpoint on page-load and on a "
        "configurable polling interval to refresh pin colours and popup cards."
    ),
)
def get_all_status():
    return {
        "count":      len(campus_state),
        "facilities": list(campus_state.values()),
    }


# ── 11d. GET Single Facility ──────────────────────────────────

@app.get(
    "/api/status/{facility_id}",
    tags=["Campus State"],
    summary="Get the live state of a single facility",
    description=(
        "Returns the full data object for **one** facility identified by its "
        "unique `facility_id`. Use this for targeted refreshes when a user "
        "clicks a map pin, avoiding the overhead of fetching the full campus "
        "state.\n\n"
        "Returns **HTTP 404** with a list of valid IDs if the `facility_id` "
        "is not recognised."
    ),
)
def get_facility_status(facility_id: str):
    facility = campus_state.get(facility_id)
    if not facility:
        raise HTTPException(
            status_code=404,
            detail=f"Facility '{facility_id}' not found. "
                   f"Valid IDs: {list(campus_state.keys())}",
        )
    return facility


# ── 11e. POST Update Occupancy ────────────────────────────────

@app.post(
    "/api/update",
    tags=["Data Engine"],
    summary="Push a live occupancy update",
    description=(
        "**Core telemetry ingestion endpoint for the Data Engine simulator.**\n\n"
        "Accepts a minimal payload of `id` + `current_occupancy` and "
        "automatically derives:\n"
        "- `occupancy_percentage` = `(current_occupancy ÷ max_capacity) × 100`\n"
        "- `status_color` = `Green` / `Yellow` / `Red` (thresholds: 60% / 85%)\n"
        "- `last_updated` = current UTC timestamp\n\n"
        "**Validation rules:**\n"
        "- `current_occupancy` must be `>= 0`\n"
        "- `current_occupancy` cannot exceed the facility's `max_capacity`\n\n"
        "**Example payload:**\n"
        "```json\n"
        '{ "id": "sci-block", "current_occupancy": 450 }\n'
        "```"
    ),
)
def update_facility(payload: FacilityUpdate):
    try:
        facility = campus_state.get(payload.id)
        if not facility:
            logger.warning("❓  UPDATE rejected — unknown facility: '%s'", payload.id)
            raise HTTPException(
                status_code=404,
                detail=f"Facility '{payload.id}' not found. "
                       f"Valid IDs: {list(campus_state.keys())}",
            )

        max_cap = facility["max_capacity"]

        if payload.current_occupancy > max_cap:
            logger.warning(
                "🚨  UPDATE rejected — %s: occupancy %d exceeds max %d",
                payload.id, payload.current_occupancy, max_cap,
            )
            raise HTTPException(
                status_code=422,
                detail=(
                    f"current_occupancy ({payload.current_occupancy}) exceeds "
                    f"max_capacity ({max_cap}) for '{payload.id}'. "
                    "Verify your sensor data."
                ),
            )

        old_pct   = facility["occupancy_percentage"]
        new_pct   = round((payload.current_occupancy / max_cap) * 100, 2)
        new_color = _compute_status_color(new_pct)

        facility["current_occupancy"]    = payload.current_occupancy
        facility["occupancy_percentage"] = new_pct
        facility["status_color"]         = new_color
        facility["last_updated"]         = _utc_now_iso()

        trend = "📈" if new_pct > old_pct else ("📉" if new_pct < old_pct else "➡️")
        logger.info(
            "📡  UPDATE RECEIVED: %s is now at %.1f%%  %s  (was %.1f%%)  |  Status: %s",
            facility["name"], new_pct, trend, old_pct, new_color,
        )

        if new_color == "Red" and _compute_status_color(old_pct) != "Red":
            logger.warning(
                "🔴  THRESHOLD CROSSED: %s just went CRITICAL at %.1f%%",
                facility["name"], new_pct,
            )

        return {
            "message":          f"Facility '{payload.id}' updated successfully.",
            "updated_facility": facility,
        }

    except HTTPException:
        raise
    except ValidationError as exc:
        logger.error("⚠️   ValidationError during update: %s", str(exc))
        raise HTTPException(
            status_code=422,
            detail={
                "error":    "Payload validation failed",
                "problems": exc.errors(),
                "tip":      "See /docs → Data Engine → POST /api/update for the schema.",
            },
        )
    except Exception as exc:
        logger.error("💥  Unexpected error during update: %s", str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


# ── 11f. POST Create Alert ────────────────────────────────────

@app.post(
    "/api/alerts",
    tags=["Alerts"],
    summary="Raise a new facility alert",
    status_code=201,
    description=(
        "Appends a **timestamped** alert entry to the target facility's "
        "`active_alerts` list. Alerts are formatted server-side as:\n"
        "```\n"
        "[WARNING] HVAC maintenance required (at 2026-09-19T01:34:40+00:00)\n"
        "```\n"
        "so the frontend can render them directly in a notification panel "
        "without any further parsing.\n\n"
        "**Example payload:**\n"
        "```json\n"
        '{\n  "facility_id": "library",\n  "alert_type": "Warning",\n  '
        '"message": "HVAC maintenance required"\n}\n'
        "```"
    ),
)
def create_alert(payload: AlertCreate):
    try:
        facility = campus_state.get(payload.facility_id)
        if not facility:
            logger.warning("❓  ALERT rejected — unknown facility: '%s'", payload.facility_id)
            raise HTTPException(
                status_code=404,
                detail=f"Facility '{payload.facility_id}' not found. "
                       f"Valid IDs: {list(campus_state.keys())}",
            )

        alert_entry = (
            f"[{payload.alert_type.upper()}] {payload.message} "
            f"(at {_utc_now_iso()})"
        )
        facility["active_alerts"].append(alert_entry)

        logger.info(
            "🔔  ALERT CREATED: [%s] '%s' → %s",
            payload.alert_type.upper(), payload.message, facility["name"],
        )

        return {
            "message":             f"Alert added to '{payload.facility_id}'.",
            "alert":               alert_entry,
            "total_active_alerts": len(facility["active_alerts"]),
        }

    except HTTPException:
        raise
    except ValidationError as exc:
        logger.error("⚠️   ValidationError creating alert: %s", str(exc))
        raise HTTPException(
            status_code=422,
            detail={"error": "Payload validation failed", "problems": exc.errors()},
        )
    except Exception as exc:
        logger.error("💥  Unexpected error creating alert: %s", str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


# ── 11g. DELETE Clear Alerts ──────────────────────────────────

@app.delete(
    "/api/alerts/{facility_id}",
    tags=["Alerts"],
    summary="Clear all active alerts for a facility",
    description=(
        "Wipes the entire `active_alerts` list for the specified facility and "
        "returns the number of alerts that were dismissed.\n\n"
        "Intended for use by the frontend's **Dismiss All** button after "
        "campus operations staff have acknowledged the alerts."
    ),
)
def clear_alerts(facility_id: str):
    try:
        facility = campus_state.get(facility_id)
        if not facility:
            raise HTTPException(
                status_code=404,
                detail=f"Facility '{facility_id}' not found. "
                       f"Valid IDs: {list(campus_state.keys())}",
            )

        cleared_count = len(facility["active_alerts"])
        facility["active_alerts"] = []

        logger.info(
            "🧹  ALERTS CLEARED: %d alert(s) dismissed for %s",
            cleared_count, facility["name"],
        )

        return {
            "message":        f"All alerts cleared for '{facility_id}'.",
            "alerts_cleared": cleared_count,
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("💥  Unexpected error clearing alerts: %s", str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


# ── 11h. POST Judge Reset ─────────────────────────────────────
#
#  PHASE 4 — Demo control endpoint.
#  Resets ALL facilities to a 5% baseline in a single HTTP call.
#  No server restart needed between judge pitches.

@app.post(
    "/api/reset",
    tags=["Demo Control"],
    summary="Reset the entire campus to a clean baseline state",
    description=(
        "**⚡ Judge Reset — use this between pitches.**\n\n"
        "Resets every campus facility to a low baseline occupancy "
        f"(`{int(RESET_OCCUPANCY_FRACTION * 100)}%` of max capacity) and clears "
        "**all active alerts** across the entire campus in a single call. "
        "No server restart required.\n\n"
        "**Effect per facility:**\n"
        "- `current_occupancy` → `max_capacity × 0.05` (floored to 1)\n"
        "- `occupancy_percentage` → `≈ 5.0`\n"
        "- `status_color` → `Green`\n"
        "- `active_alerts` → `[]`\n"
        "- `last_updated` → current UTC timestamp\n\n"
        "Call this from the Swagger UI or a simple `curl` command:\n"
        "```bash\n"
        "curl -X POST http://127.0.0.1:8000/api/reset\n"
        "```"
    ),
)
def reset_campus():
    try:
        reset_summary = []

        for facility_id, facility in campus_state.items():
            alerts_cleared = len(facility["active_alerts"])
            _reset_facility(facility)          # modifies in-place

            reset_summary.append({
                "id":                  facility_id,
                "name":                facility["name"],
                "new_occupancy":       facility["current_occupancy"],
                "new_percentage":      facility["occupancy_percentage"],
                "alerts_cleared":      alerts_cleared,
            })

        logger.info("=" * 62)
        logger.info("🔄  CAMPUS RESET — all facilities returned to baseline")
        for entry in reset_summary:
            logger.info(
                "    ✅  %-20s → %d%% occupancy, %d alert(s) cleared",
                entry["name"], entry["new_percentage"], entry["alerts_cleared"],
            )
        logger.info("=" * 62)

        return {
            "message":         "Campus reset to baseline state. Ready for next demo.",
            "reset_timestamp": _utc_now_iso(),
            "facilities_reset": reset_summary,
        }

    except Exception as exc:
        logger.error("💥  Unexpected error during campus reset: %s", str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


# ── 11i. GET Executive Summary ────────────────────────────────
#
#  PHASE 4 — Live campus-wide aggregation.
#  Proves to judges that the backend is doing real-time analytics,
#  not just forwarding raw numbers from the Data Engine.

@app.get(
    "/api/summary",
    tags=["Demo Control"],
    summary="Get a live executive summary of the entire campus",
    description=(
        "Calculates and returns **campus-wide aggregated statistics** computed "
        "in real time from the current twin state. Designed to be shown on the "
        "frontend's executive dashboard panel during the pitch.\n\n"
        "**Returned metrics:**\n\n"
        "| Field | Description |\n"
        "|-------|-------------|\n"
        "| `total_facilities` | Count of tracked campus locations |\n"
        "| `campus_average_occupancy` | Blended average occupancy % across all facilities |\n"
        "| `busiest_facility` | Name + % of the currently most crowded facility |\n"
        "| `quietest_facility` | Name + % of the currently least crowded facility |\n"
        "| `total_active_alerts` | Sum of all unacknowledged alerts campus-wide |\n"
        "| `facilities_by_status` | Count of Green / Yellow / Red facilities |\n"
        "| `generated_at` | UTC timestamp of when this summary was computed |\n\n"
        "All values are derived on-the-fly — no caching, always fresh."
    ),
)
def get_executive_summary():
    try:
        facilities = list(campus_state.values())

        # ── Aggregation logic ─────────────────────────────────────────
        total         = len(facilities)
        avg_occupancy = round(
            sum(f["occupancy_percentage"] for f in facilities) / total, 2
        )
        total_alerts  = sum(len(f["active_alerts"]) for f in facilities)

        # Sort to find busiest / quietest
        sorted_by_pct  = sorted(facilities, key=lambda f: f["occupancy_percentage"])
        quietest       = sorted_by_pct[0]
        busiest        = sorted_by_pct[-1]

        # Count facilities in each traffic-light state
        color_counts: dict[str, int] = {"Green": 0, "Yellow": 0, "Red": 0}
        for f in facilities:
            color = f.get("status_color", "Green")
            color_counts[color] = color_counts.get(color, 0) + 1

        logger.info(
            "📊  SUMMARY SERVED: avg %.1f%% occupancy | %d alert(s) campus-wide",
            avg_occupancy, total_alerts,
        )

        return {
            "total_facilities":          total,
            "campus_average_occupancy":  avg_occupancy,
            "busiest_facility": {
                "name":               busiest["name"],
                "occupancy_percentage": busiest["occupancy_percentage"],
                "status_color":         busiest["status_color"],
            },
            "quietest_facility": {
                "name":               quietest["name"],
                "occupancy_percentage": quietest["occupancy_percentage"],
                "status_color":         quietest["status_color"],
            },
            "total_active_alerts":   total_alerts,
            "facilities_by_status":  color_counts,
            "generated_at":          _utc_now_iso(),
        }

    except Exception as exc:
        logger.error("💥  Unexpected error generating summary: %s", str(exc))
        raise HTTPException(status_code=500, detail=str(exc))
