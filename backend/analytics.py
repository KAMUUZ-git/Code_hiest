"""
=============================================================
  CAMPUS DIGITAL TWIN v2 — PREDICTIVE ANALYTICS
=============================================================

Forecast model: trailing moving average + linear slope.

For each facility we take the trailing telemetry window (SQLite,
default 15 min), compute:

    slope_pct_per_min = (last − first) / window_span

and project forward HORIZON_MINUTES. The moving average smooths
sensor noise before the slope is taken, which keeps one noisy tick
from fabricating a false "breach in 12 minutes" headline. Facilities
whose projected occupancy crosses 100% within the horizon are
returned sorted by minutes-to-breach — that ordering IS the ops
queue for the command center.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from backend.db import history_for_facility
from backend.state import campus_state

logger = logging.getLogger("campus_twin.analytics")

WINDOW_MINUTES = 15       # trailing window feeding the model
HORIZON_MINUTES = 60      # forecast horizon (spec: next 60 minutes)
BREACH_THRESHOLD = 100.0  # occupancy_pct that counts as "at capacity"
MOVING_AVG_POINTS = 5     # points in the simple moving average
MIN_POINTS_FOR_FORECAST = 4
# A slope taken over a shorter real-time span is pure noise (warm-up
# ticks land seconds apart). Require this much history before trusting
# the projection; below it we report insufficient_data.
MIN_SPAN_MINUTES = 2.0
# Physical display cap for the projected value — a digital twin can
# ramp fast, but 999% is a rendering artifact, not a forecast.
MAX_PROJECTED_PCT = 150.0


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _moving_average(values: List[float], window: int = MOVING_AVG_POINTS) -> List[float]:
    """Simple trailing moving average (window clamped to series length)."""
    if not values:
        return []
    w = min(window, len(values))
    out: List[float] = []
    for i in range(len(values)):
        lo = max(0, i - w + 1)
        chunk = values[lo:i + 1]
        out.append(sum(chunk) / len(chunk))
    return out


def forecast_facility(facility_id: str) -> Dict[str, Any]:
    """
    Forecast one facility. Always returns a well-formed dict —
    facilities with insufficient history get `forecast: "insufficient_data"`
    so the frontend can grey them out instead of guessing.
    """
    f = campus_state.facility_event(facility_id)
    if not f:
        return {"facility_id": facility_id, "forecast": "unknown_facility"}

    points = campus_state.history(facility_id, window_minutes=WINDOW_MINUTES, limit=200)
    pcts = [p["occupancy_pct"] for p in points]

    base = {
        "facility_id": facility_id,
        "name": f["name"],
        "capacity": f["capacity"],
        "current_occupancy": f["occupancy"],
        "current_pct": f["occupancy_pct"],
        "status": f["status"],
        "horizon_minutes": HORIZON_MINUTES,
        "window_minutes": WINDOW_MINUTES,
    }

    if len(pcts) < MIN_POINTS_FOR_FORECAST:
        return {**base, "forecast": "insufficient_data",
                "samples": len(pcts),
                "will_breach": False}

    # ── Model ────────────────────────────────────────────────────
    smoothed = _moving_average(pcts)
    t0 = _parse_iso(points[0]["ts"])
    t1 = _parse_iso(points[-1]["ts"])
    span_min = (t1 - t0).total_seconds() / 60.0

    if span_min < MIN_SPAN_MINUTES:
        return {**base, "forecast": "insufficient_data",
                "samples": len(pcts),
                "will_breach": False}

    slope_per_min = (smoothed[-1] - smoothed[0]) / span_min

    projected = _clamp(
        smoothed[-1] + slope_per_min * HORIZON_MINUTES, 0.0, MAX_PROJECTED_PCT
    )

    minutes_to_breach: Any = None
    if slope_per_min > 0:
        # Solve smoothed_last + slope*t = BREACH_THRESHOLD for t.
        raw = (BREACH_THRESHOLD - smoothed[-1]) / slope_per_min
        # Only a breach WITHIN the horizon is an actionable forecast.
        if 0 < raw <= HORIZON_MINUTES:
            minutes_to_breach = round(raw)

    will_breach = minutes_to_breach is not None

    return {
        **base,
        "forecast": "ok",
        "samples": len(pcts),
        "moving_avg_now": round(smoothed[-1], 2),
        "slope_pct_per_min": round(slope_per_min, 4),
        "trend": ("rising" if slope_per_min > 0.01 else
                  "falling" if slope_per_min < -0.01 else "stable"),
        "projected_pct_60m": round(min(max(projected, 0.0), 999.0), 2),
        "minutes_to_capacity": minutes_to_breach,
        "will_breach": will_breach,
    }


def campus_forecast() -> Dict[str, Any]:
    """
    Campus-wide 60-minute outlook: every facility forecast, sorted so
    the soonest breach is first, plus a breach queue for the HUD.
    """
    with campus_state._lock:
        ids = list(campus_state.facilities.keys())

    forecasts = [forecast_facility(fid) for fid in ids]

    breach_queue = sorted(
        (f for f in forecasts if f.get("will_breach")),
        key=lambda f: f.get("minutes_to_capacity") if f.get("minutes_to_capacity") is not None else 10_000,
    )

    return {
        "model": {
            "type": "moving_average_linear_projection",
            "window_minutes": WINDOW_MINUTES,
            "horizon_minutes": HORIZON_MINUTES,
            "smoothing_points": MOVING_AVG_POINTS,
        },
        # False while facilities are still inside the warm-up guard —
        # the frontend renders "warming up" instead of a false "all clear".
        "model_ready": any(f.get("forecast") == "ok" for f in forecasts),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "facilities_at_risk": len(breach_queue),
        "breach_queue": [
            {
                "facility_id": f["facility_id"],
                "name": f["name"],
                "current_pct": f["current_pct"],
                "projected_pct_60m": f.get("projected_pct_60m"),
                "minutes_to_capacity": f.get("minutes_to_capacity"),
                "trend": f.get("trend"),
            }
            for f in breach_queue
        ],
        "forecasts": forecasts,
    }
