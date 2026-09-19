"""
=============================================================
  CAMPUS DIGITAL TWIN v2 — SIMULATOR COORDINATOR (threading)
=============================================================

Architecture
------------
    main()  (coordinator thread, daemon)
      ├─ FacilityThread("science-block")     ── independent timeline
      ├─ FacilityThread("central-library")   ── independent timeline
      ├─ FacilityThread("main-parking")      ── independent timeline
      ├─ FacilityThread("dormitory-a")       ── independent timeline
      ├─ FacilityThread("student-union")     ── independent timeline
      ├─ FacilityThread("recreation-center") ── independent timeline
      └─ anomaly loop: every ~45 s, maybe inject a cascading event

Facility threads never talk to each other; the coordinator is the
only writer of anomaly pressure. Every occupancy mutation flows
through CampusState.update_facility() → SQLite + WebSocket fan-out.

Run standalone (backend embedded by default):
    CAMPUS_TWIN_EMBED_SIM=0 uvicorn backend.app:app --port 8000
    python -m backend.simulator.threads
"""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Dict

from backend import db as database
from backend.facilities import FACILITY_DEFS
from backend.state import campus_state
from backend.simulator import anomalies as anomaly_engine
from backend.simulator.facility_thread import FacilityThread

logger = logging.getLogger("campus_twin.sim")

ANOMALY_CHECK_SECONDS = 45      # coordinator cadence for anomaly rolls
STAGGER_SECONDS = 0.45          # between thread starts (desynchronized timelines; 14 buildings ≈ 6 s)

# Live facility-thread registry — set by main() so the REST layer can
# fire anomalies on demand (POST /api/anomaly/trigger).
_active_threads: Dict[str, FacilityThread] | None = None


def main() -> None:
    """
    Coordinator entry point. Blocks forever (daemon threads).
    Safe to call via asyncio.to_thread (embedded) or as __main__.
    """
    database.init_db()
    for fdef in FACILITY_DEFS:
        campus_state.register_facility(dict(fdef))

    anomaly_engine.bind_state_getter(lambda: campus_state)

    # ── Spawn one independent timeline thread per building ──
    threads: Dict[str, FacilityThread] = {}
    start_hours = [7.0, 8.25, 9.5, 10.75, 12.0, 13.5]   # staggered sim clocks
    for i, fdef in enumerate(FACILITY_DEFS):
        t = FacilityThread(fdef, campus_state, start_hour=start_hours[i % len(start_hours)])
        threads[fdef["id"]] = t
        t.start()
        time.sleep(STAGGER_SECONDS)

    global _active_threads
    _active_threads = threads
    logger.info("🎛️  Coordinator online: %d facility threads running", len(threads))

    # Pre-seed the predictive model: run 3 immediate ticks so
    # /api/predictions has a moving average on first request.
    for t in threads.values():
        t._tick()

    # ── Coordinator loop: anomaly injection + liveness ───────
    last_fire: Dict[str, float] = {}
    rng = random.Random()

    try:
        while True:
            time.sleep(ANOMALY_CHECK_SECONDS)
            now = time.monotonic()

            for spec in anomaly_engine.ANOMALIES:
                if now - last_fire.get(spec["id"], 0) < spec["cooldown_s"]:
                    continue
                if rng.random() < spec["probability"]:
                    try:
                        title, narrative = spec["apply"]({"threads": threads})
                    except Exception:            # noqa: BLE001
                        logger.exception("Anomaly '%s' failed", spec["id"])
                        continue

                    last_fire[spec["id"]] = now
                    logger.info("🌪️  ANOMALY: %s", title)

                    # Broadcast the narrative so the dashboard can
                    # banner it (the alert fan-out already happened
                    # inside spec.apply via state.raise_alert).
                    from backend.hub import hub
                    hub.broadcast_threadsafe({
                        "type": "anomaly",
                        "title": title,
                        "narrative": narrative,
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S") + "Z",
                    })
    except KeyboardInterrupt:
        logger.info("🛑 Coordinator stopping facility threads…")
    finally:
        for t in threads.values():
            t.stop()


def trigger_anomaly(anomaly_id: str):
    """
    Fire an anomaly NOW (demo control).

    anomaly_id: a catalog id, or "random" to roll the dice.
    Returns (title, narrative), None if the id is unknown, or the
    string "no-simulator" if the coordinator is not running.
    """
    if _active_threads is None:
        return "no-simulator"

    catalog = {a["id"]: a for a in anomaly_engine.ANOMALIES}
    if anomaly_id == "random":
        spec = anomaly_engine.ANOMALIES[random.randrange(len(anomaly_engine.ANOMALIES))]
    else:
        spec = catalog.get(anomaly_id)
        if spec is None:
            return None

    try:
        title, narrative = spec["apply"]({"threads": _active_threads})
    except Exception:                    # noqa: BLE001
        logger.exception("Manual anomaly '%s' failed", spec["id"])
        return None

    logger.info("⚡  ANOMALY TRIGGERED (manual): %s", title)
    from backend.hub import hub
    hub.broadcast_threadsafe({
        "type": "anomaly", "title": title, "narrative": narrative,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S") + "Z",
    })
    return title, narrative


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  [%(levelname)-8s]  %(name)s  %(message)s")
    main()
