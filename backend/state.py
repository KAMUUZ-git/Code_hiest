"""
=============================================================
  CAMPUS DIGITAL TWIN v2 — STATE MANAGER (thread-safe twin)
=============================================================

Single source of truth for the live campus state.

- `update_facility()` is the ONLY mutation path. REST ingestion and
  the embedded simulator both funnel through it, so alerts and
  broadcasts can never be bypassed.
- History: SQLite is the durable record; a bounded deque per
  facility feeds the Chart.js graph without touching the DB on
  every WebSocket fan-out.
- Boot restore: `restore_from_db()` re-seeds state from the newest
  telemetry rows so a backend restart is invisible on the map.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.db import OccupancyRecord, history_for_facility, insert_records, latest_record_per_facility
from backend.hub import hub

logger = logging.getLogger("campus_twin.state")

# Bounded per-facility history for the live chart (fast path).
HISTORY_LIMIT = 240


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_status(pct: float) -> str:
    """Traffic-light tier for an occupancy percentage."""
    if pct >= 85.0:
        return "RED"
    if pct >= 60.0:
        return "AMBER"
    return "GREEN"


class CampusState:
    """
    Thread-safe campus twin.

    Locking model: one RLock guards `facilities` and the history
    deques. It is held only for dict/deque mutations and snapshot
    reads — never across DB I/O or network calls.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.facilities: Dict[str, Dict[str, Any]] = {}
        self._history: Dict[str, deque] = {}
        self.started_at = _utc_now_iso()

    # ── Registration / restore ───────────────────────────────

    def register_facility(self, facility: Dict[str, Any]) -> None:
        """Insert or replace a facility definition (id, name, type, coords, capacity…)."""
        with self._lock:
            self.facilities[facility["id"]] = facility
            self._history.setdefault(facility["id"], deque(maxlen=HISTORY_LIMIT))

    def restore_from_db(self) -> int:
        """
        Seed/refresh live state from the newest SQLite row per facility.
        Returns the number of facilities restored. Facilities without
        history keep their registered defaults.
        """
        latest = latest_record_per_facility()
        restored = 0
        with self._lock:
            for fid, rec in latest.items():
                f = self.facilities.get(fid)
                if not f:
                    continue
                f["occupancy"] = rec.occupancy
                f["occupancy_pct"] = round(rec.occupancy_pct, 2)
                f["status"] = compute_status(rec.occupancy_pct)
                restored += 1
            logger.info("♻️  Restored %d facility state(s) from SQLite", restored)
        return restored

    def preload_history(self, window_minutes: int = 60) -> None:
        """Warm the in-memory chart buffers from SQLite (last hour)."""
        with self._lock:
            ids = list(self.facilities.keys())
        for fid in ids:
            rows = history_for_facility(fid, window_minutes=window_minutes, limit=HISTORY_LIMIT)
            with self._lock:
                dq = self._history.setdefault(fid, deque(maxlen=HISTORY_LIMIT))
                for r in rows:
                    dq.append({
                        "ts": r.ts.isoformat() if r.ts else _utc_now_iso(),
                        "occupancy": r.occupancy,
                        "occupancy_pct": round(r.occupancy_pct, 2),
                    })

    # ── Mutation (the single write path) ──────────────────────

    def update_facility(
        self,
        facility_id: str,
        occupancy: int,
        *,
        source: str = "api",
        note: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Apply a telemetry tick and return the broadcast event dict,
        or None if the facility is unknown / value rejected.

        Persists to SQLite and appends to the chart buffer. Raises
        ValueError if occupancy exceeds capacity (callers translate
        that into HTTP 422 or a simulator warning).
        """
        with self._lock:
            f = self.facilities.get(facility_id)
            if not f:
                return None
            capacity = int(f["capacity"])
            if occupancy > capacity:
                raise ValueError(
                    f"occupancy {occupancy} exceeds capacity {capacity} for '{facility_id}'"
                )

            occupancy = max(0, int(occupancy))
            pct = round(occupancy / capacity * 100.0, 2) if capacity else 0.0
            prev_status = f.get("status")
            f["occupancy"] = occupancy
            f["occupancy_pct"] = pct
            f["status"] = compute_status(pct)
            f["last_updated"] = _utc_now_iso()
            if note:
                f["activity"] = note

            # ── Chart buffer append (bounded) ─────────────────
            self._history.setdefault(facility_id, deque(maxlen=HISTORY_LIMIT)).append({
                "ts": f["last_updated"],
                "occupancy": occupancy,
                "occupancy_pct": pct,
            })

        # ── Durable record (outside the state lock) ───────────
        try:
            insert_records([OccupancyRecord(
                facility_id=facility_id,
                occupancy=occupancy,
                occupancy_pct=pct,
                capacity=capacity,
                ts=datetime.now(timezone.utc),
            )])
        except Exception:                      # noqa: BLE001 — persistence must never kill telemetry
            logger.exception("Failed to persist telemetry for %s", facility_id)

        event = self.facility_event(facility_id)
        if event:
            event["previous_status"] = prev_status
            event["source"] = source
            # Single broadcast point: EVERY telemetry mutation (REST
            # ingestion or simulator thread) fans out to /ws here.
            hub.broadcast_threadsafe({
                "type": "telemetry", "facility": event, "ts": event["last_updated"],
            })
        return event

    def raise_alert(self, facility_id: str, alert: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Append an alert to a facility and return its updated event."""
        with self._lock:
            f = self.facilities.get(facility_id)
            if not f:
                return None
            entry = {
                "type": alert.get("type", "INFO"),
                "severity": alert.get("severity", "info"),
                "message": alert.get("message", ""),
                "ts": _utc_now_iso(),
            }
            f.setdefault("alerts", []).append(entry)
            # Bound unbounded growth: keep the newest 20 per facility.
            del f["alerts"][:-20]
            f["last_updated"] = _utc_now_iso()
        event = self.facility_event(facility_id)
        if event:
            hub.broadcast_threadsafe({
                "type": "alert", "facility_id": facility_id,
                "alert": entry, "ts": entry["ts"],
            })
        return event

    def clear_alerts(self, facility_id: str) -> int:
        """Dismiss all alerts for a facility; returns how many were removed."""
        with self._lock:
            f = self.facilities.get(facility_id)
            if not f:
                return 0
            n = len(f.get("alerts", []))
            f["alerts"] = []
            return n

    # ── Reads / snapshots ─────────────────────────────────────

    def facility_event(self, facility_id: str) -> Optional[Dict[str, Any]]:
        """Broadcast-shaped snapshot of one facility."""
        with self._lock:
            f = self.facilities.get(facility_id)
            if not f:
                return None
            return {
                "id": f["id"],
                "name": f["name"],
                "type": f.get("type", "building"),
                "category": f.get("category", "academic"),
                "coords": f.get("coords"),
                "capacity": f["capacity"],
                "hours": f.get("hours", "—"),
                "zone_m": f.get("zone_m", 70),
                "occupancy": f["occupancy"],
                "occupancy_pct": f.get("occupancy_pct", 0.0),
                "status": f.get("status", "GREEN"),
                "activity": f.get("activity", "Operational"),
                "alerts": list(f.get("alerts", [])),
                "last_updated": f.get("last_updated", _utc_now_iso()),
            }

    def snapshot(self) -> List[Dict[str, Any]]:
        """Full-campus broadcast payload (list of facility events)."""
        with self._lock:
            ids = list(self.facilities.keys())
        events = [self.facility_event(fid) for fid in ids]
        return [e for e in events if e]

    def history(
        self,
        facility_id: str,
        window_minutes: int = 60,
        limit: int = 120,
    ) -> List[Dict[str, Any]]:
        """
        Chart data for one facility: in-memory buffer first; if the
        backend just booted (buffer cold) fall back to SQLite so the
        graph renders with real history immediately.
        """
        with self._lock:
            dq = self._history.get(facility_id)
            points = list(dq) if dq else []

        if len(points) < 2:
            rows = history_for_facility(facility_id, window_minutes=window_minutes, limit=limit)
            points = [
                {"ts": r.ts.isoformat(), "occupancy": r.occupancy, "occupancy_pct": round(r.occupancy_pct, 2)}
                for r in rows
            ]

        cutoff = datetime.now(timezone.utc).timestamp() - window_minutes * 60
        recent = [p for p in points if _parse_iso(p["ts"]).timestamp() >= cutoff]
        return recent[-limit:]

    def summary(self) -> Dict[str, Any]:
        """Campus-wide aggregation for the executive panel."""
        events = self.snapshot()
        if not events:
            return {
                "total_facilities": 0,
                "campus_average_occupancy": 0.0,
                "busiest_facility": None,
                "quietest_facility": None,
                "total_active_alerts": 0,
                "facilities_by_status": {"GREEN": 0, "AMBER": 0, "RED": 0},
                "generated_at": _utc_now_iso(),
            }

        by_pct = sorted(events, key=lambda e: e["occupancy_pct"])
        status_counts: Dict[str, int] = {"GREEN": 0, "AMBER": 0, "RED": 0}
        for e in events:
            status_counts[e["status"]] = status_counts.get(e["status"], 0) + 1

        return {
            "total_facilities": len(events),
            "campus_average_occupancy": round(sum(e["occupancy_pct"] for e in events) / len(events), 2),
            "busiest_facility": {
                "id": by_pct[-1]["id"], "name": by_pct[-1]["name"],
                "occupancy_pct": by_pct[-1]["occupancy_pct"], "status": by_pct[-1]["status"],
            },
            "quietest_facility": {
                "id": by_pct[0]["id"], "name": by_pct[0]["name"],
                "occupancy_pct": by_pct[0]["occupancy_pct"], "status": by_pct[0]["status"],
            },
            "total_active_alerts": sum(len(e["alerts"]) for e in events),
            "facilities_by_status": status_counts,
            "generated_at": _utc_now_iso(),
        }

    def reset_to_baseline(self, fraction: float = 0.05) -> List[Dict[str, Any]]:
        """
        Demo reset: drop every facility to `fraction` of capacity, clear
        alerts, and persist the reset rows so a restart keeps the reset.
        Returns the list of broadcast events for the reset.
        """
        events: List[Dict[str, Any]] = []
        with self._lock:
            ids = list(self.facilities.keys())
        for fid in ids:
            with self._lock:
                f = self.facilities[fid]
                occ = max(1, int(f["capacity"] * fraction))
                f["occupancy"] = occ
                f["occupancy_pct"] = round(occ / f["capacity"] * 100.0, 2)
                f["status"] = "GREEN"
                f["alerts"] = []
                f["activity"] = "Operational — baseline"
                f["last_updated"] = _utc_now_iso()
            try:
                insert_records([OccupancyRecord(
                    facility_id=fid,
                    occupancy=f["occupancy"],
                    occupancy_pct=f["occupancy_pct"],
                    capacity=f["capacity"],
                    ts=datetime.now(timezone.utc),
                )])
            except Exception:                  # noqa: BLE001
                logger.exception("Reset persist failed for %s", fid)
            event = self.facility_event(fid)
            if event:
                events.append(event)
        logger.info("🔄 Campus reset to %.0f%% baseline (%d facilities)", fraction * 100, len(ids))
        return events


def _parse_iso(ts: str) -> datetime:
    """ISO-8601 → aware datetime (tolerates the +00:00 / Z forms)."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


# ── Process-wide singleton (import-safe) ─────────────────────
campus_state = CampusState()
