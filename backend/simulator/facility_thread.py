"""
=============================================================
  CAMPUS DIGITAL TWIN v2 — FACILITY THREAD
  (one independent timeline per building)
=============================================================

Each FacilityThread owns:
  - a live simulated clock (starts staggered so buildings don't
    tick in lockstep — the map feels alive, not mechanical),
  - a diurnal occupancy curve tuned per facility archetype
    (labs, library, parking, housing, social, recreation),
  - an organic random walk around that curve so motion looks
    like people, not a sine wave,
  - an anomaly-pressure channel the coordinator writes into
    (e.g. rain → +0.35 pressure on indoor social spaces).

Threads publish by calling state.update_facility(), which is the
single mutation path → alerts + SQLite + WebSocket fan-out all
happen automatically.
"""

from __future__ import annotations

import logging
import math
import random
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict

logger = logging.getLogger("campus_twin.sim")

# Simulation pacing: 1 real second = 15 simulated minutes per tick.
TICK_REAL_SECONDS = 2.0
TICK_SIM_MINUTES = 30


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


class FacilityThread(threading.Thread):
    """
    Autonomous timeline thread for one campus facility.

    Parameters
    ----------
    spec : dict  — facility definition (id, name, capacity, archetype…)
    state_mgr    — CampusState singleton (mutation path)
    start_hour   — initial simulated local hour (staggered per thread)
    """

    def __init__(self, spec: Dict[str, Any], state_mgr, start_hour: float) -> None:
        super().__init__(name=f"sim-{spec['id']}", daemon=True)
        self.spec = spec
        self.facility_id: str = spec["id"]
        self.capacity: int = spec["capacity"]
        self.archetype: str = spec.get("archetype", spec.get("category", "academic"))
        self.zone_m: int = int(spec.get("zone_m", 70))
        self.state_mgr = state_mgr

        self.sim_clock = datetime(2026, 9, 21, int(start_hour), int((start_hour % 1) * 60))
        self._clock_lock = threading.Lock()

        # Anomaly pressure written by the coordinator (thread-safe via GIL
        # on float assignment, but we use a lock for clarity).
        self._pressure = 0.0
        self._pressure_lock = threading.Lock()

        self._stop = threading.Event()
        self._random = random.Random()          # per-thread RNG — no cross-thread contention

        # Random-walk momentum (fraction of capacity carried between ticks)
        self._momentum = 0.0

    # ── Anomaly pressure API (coordinator → facility) ────────

    def add_pressure(self, delta: float) -> None:
        """Coordinator nudges this facility's demand (e.g. rain flood)."""
        with self._pressure_lock:
            self._pressure += delta

    def decay_pressure(self, factor: float = 0.85) -> None:
        with self._pressure_lock:
            self._pressure *= factor

    @property
    def pressure(self) -> float:
        with self._pressure_lock:
            return self._pressure

    # ── Lifecycle ─────────────────────────────────────────────

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        logger.info("🧵  %s timeline started (archetype=%s, t0=%s)",
                    self.facility_id, self.archetype, self.sim_clock.strftime("%H:%M"))
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:                    # noqa: BLE001 — a dead sim thread must not kill the twin
                logger.exception("Simulator tick failed for %s", self.facility_id)
            self._stop.wait(TICK_REAL_SECONDS)

    # ── The tick: curve + walk + pressure → occupancy ─────────

    def _tick(self) -> None:
        with self._clock_lock:
            hour = self.sim_clock.hour + self.sim_clock.minute / 60.0
            time_label = self.sim_clock.strftime("%H:%M")
            self.sim_clock += timedelta(minutes=TICK_SIM_MINUTES)

        base = self._diurnal_base(hour)

        # Organic random walk: momentum drifts toward the curve, plus noise.
        target = base + self.pressure
        self._momentum += (target - self._momentum) * self._random.uniform(0.25, 0.5)
        self._momentum += self._random.gauss(0, 0.02)

        # Parking archetype is highly correlated with academic buildings:
        # during class hours it tracks ~1.1× building pressure. Approximate
        # that here by lifting parking demand on weekday mornings.
        if self.archetype == "parking" and 7.5 <= hour < 17:
            self._momentum += 0.05

        level = _clamp(self._momentum, 0.0, 1.0)
        occupancy = int(round(level * self.capacity))

        event = self.state_mgr.update_facility(
            self.facility_id, occupancy,
            source="simulator",
            note=self._activity_line(time_label, level),
        )
        if event is None:
            logger.warning("Facility %s not registered — thread idle", self.facility_id)
            return
        event["zone_m"] = self.zone_m

        # Pressure from an anomaly naturally decays after the event window.
        self.decay_pressure(0.75)

    # ── Diurnal archetypes (fraction of capacity by hour) ─────

    def _diurnal_base(self, hour: float) -> float:
        """Gaussian-mixture curves per archetype, hour in [0, 24)."""
        def bump(center: float, width: float, height: float) -> float:
            return height * math.exp(-((hour - center) ** 2) / (2 * width ** 2))

        if self.archetype == "labs":            # Lab wings: sharp 9–15 experiment peak
            return _clamp(bump(11, 2.0, 0.85) + bump(14.5, 1.5, 0.55) + 0.05, 0.02, 0.98)

        if self.archetype == "seminar":         # Seminar rooms: bursts between class changes
            return _clamp(bump(10, 1.0, 0.7) + bump(12, 0.8, 0.6)
                          + bump(14, 1.0, 0.7) + bump(16, 0.8, 0.5) + 0.05, 0.02, 0.98)

        if self.archetype == "hostel":          # Hostels: inverse — full overnight
            return _clamp(0.75 - 0.35 * _sigmoid((hour - 10) / 2.0)
                          + 0.45 * _sigmoid((hour - 21) / 1.5), 0.15, 0.98)

        if self.archetype == "academic":      # Academic blocks: daytime classes + evening study
            return _clamp(bump(11, 3.0, 0.55) + bump(19, 2.5, 0.9) + 0.04, 0.02, 0.98)

        if self.archetype == "parking":       # Fill at 8, drain after 17
            return _clamp(bump(9, 1.2, 0.75) + 0.85 * _sigmoid((hour - 9) / 1.2)
                          * (1 - _sigmoid((hour - 17.5) / 1.2)) + 0.08, 0.02, 0.98)

        if self.archetype == "housing":       # Dormitory: inverse — full overnight
            return _clamp(0.75 - 0.35 * _sigmoid((hour - 10) / 2.0)
                          + 0.45 * _sigmoid((hour - 21) / 1.5), 0.15, 0.98)

        if self.archetype == "social":        # Student Union: lunch + evening peaks
            return _clamp(bump(12.5, 1.5, 0.8) + bump(19, 2.0, 0.7) + 0.15, 0.05, 0.98)

        if self.archetype == "recreation":    # Gym + SAC courts: morning & post-work waves
            return _clamp(bump(7, 1.2, 0.6) + bump(18, 1.8, 0.85) + 0.08, 0.03, 0.98)

        # Cafeteria: tightly packed at meal times
        return _clamp(bump(9, 0.8, 0.5) + bump(13, 1.2, 0.95) + bump(20, 1.0, 0.7) + 0.06, 0.02, 0.98)

    def _activity_line(self, time_label: str, level: float) -> str:
        """Human-readable activity line broadcast to the dashboard."""
        pct = level * 100
        if pct >= 85:
            return f"{time_label} — Near capacity, crowd management active"
        if pct >= 60:
            return f"{time_label} — Busy, normal operations"
        if pct >= 25:
            return f"{time_label} — Steady footfall"
        return f"{time_label} — Quiet"


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))
