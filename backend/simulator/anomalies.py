"""
=============================================================
  CAMPUS DIGITAL TWIN v2 — ANOMALY ENGINE
  (cascading, multi-building environmental events)
=============================================================

Each anomaly is a declarative spec:

  id / title      — identity + headline for the WS `anomaly` event
  probability     — chance per coordination cycle
  cooldown_s      — minimum real seconds between recasts
  apply(ctx)      — mutates facility pressure, raises alerts,
                    returns (title, narrative) for the WS event

`apply` runs on the coordinator thread and communicates with the
facility threads ONLY through `add_pressure()` — pressure is what
makes the cascade *gradual and visible* on the map instead of an
instant teleport of occupancy values.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Tuple

logger = logging.getLogger("campus_twin.sim.anomaly")

Ctx = Dict[str, Any]

_state_getter: Callable[[], Any] = lambda: None


def bind_state_getter(getter: Callable[[], Any]) -> None:
    """Inject the CampusState singleton without a circular import."""
    global _state_getter
    _state_getter = getter


def _get_state():
    return _state_getter()


def _alert(facility_id: str, atype: str, message: str) -> None:
    """Raise an alert through the state manager (auto-broadcasts on /ws)."""
    state = _get_state()
    if state:
        state.raise_alert(facility_id, {"type": atype, "severity": atype.lower(), "message": message})


# ─────────────────────────────────────────────────────────────
#  CASCADE IMPLEMENTATIONS
# ─────────────────────────────────────────────────────────────

def _storm_surge(ctx: Ctx) -> Tuple[str, str]:
    """Rain drives everyone indoors: Union + Library spike, parking
    fills (cars stay), recreation center empties."""
    threads = ctx["threads"]
    threads["student-union"].add_pressure(+0.45)
    threads["central-library"].add_pressure(+0.30)
    threads["recreation-center"].add_pressure(-0.30)
    threads["main-parking"].add_pressure(+0.20)
    _alert("student-union", "WEATHER",
           "Storm inbound — outdoor gatherings relocating indoors. Expect surge within 15 min.")
    _alert("recreation-center", "WEATHER",
           "Outdoor pitches closed for thunderstorm; indoor sessions cancelled.")

    return "⛈️ Sudden Thunderstorm", (
        "Regional weather service flags a sudden thunderstorm cell. Outdoor activity "
        "collapses; Student Union and Central Library brace for an indoor surge, and "
        "parking demand rises as commuters shelter in place."
    )


def _pop_event(ctx: Ctx) -> Tuple[str, str]:
    """A club fair on the Union lawn pulls footfall from everywhere nearby."""
    threads = ctx["threads"]
    threads["student-union"].add_pressure(+0.50)
    threads["recreation-center"].add_pressure(+0.15)
    threads["central-library"].add_pressure(-0.15)
    for fid in ("dormitory-a", "dormitory-b", "dormitory-c"):
        threads[fid].add_pressure(-0.10)

    _alert("student-union", "EVENT",
           "Unscheduled club fair on the Union lawn — crowd density climbing fast.")

    return "🎪 Pop-up Event at Student Union", (
        "A student club fair just kicked off at the Union lawn. Footfall is being pulled "
        "from the Library and Dormitory quad toward the Union, with overflow reaching "
        "the Recreation Center plaza."
    )


def _hvac_failure(ctx: Ctx) -> Tuple[str, str]:
    """Bharti Block becomes unusable → its labs relocate to Library + Union."""
    threads = ctx["threads"]
    threads["bharti-block"].add_pressure(-0.55)
    threads["central-library"].add_pressure(+0.25)
    threads["student-union"].add_pressure(+0.20)

    _alert("bharti-block", "CRITICAL",
           "CRITICAL: HVAC failure in Bharti Building — sessions relocating.")
    _alert("central-library", "INFO",
           "Relocation notice: displaced lab sessions arriving from Bharti Building.")

    return "🌡️ HVAC Failure — Bharti Block", (
        "Cooling loop failure in the Bharti Building (EE). Lab sessions are evacuating to "
        "the Central Library and Student Union while facilities dispatches a repair crew."
    )


def _exam_crunch(ctx: Ctx) -> Tuple[str, str]:
    """Exam week: library stays packed late, parking empties earlier."""
    threads = ctx["threads"]
    threads["central-library"].add_pressure(+0.40)
    threads["bharti-block"].add_pressure(+0.20)
    threads["main-parking"].add_pressure(-0.25)
    threads["recreation-center"].add_pressure(-0.20)

    _alert("central-library", "CAPACITY",
           "Exam-week surge: silent-study floors approaching capacity. Overflow to Level 2.")

    return "📝 Exam Week Crunch", (
        "Exam-week behaviour detected: Library occupancy climbing well above the diurnal "
        "curve, parking draining early as students camp indoors, gym traffic down."
    )


def _sensor_fault(ctx: Ctx) -> Tuple[str, str]:
    """Gate sensor glitch: parking count drifts upward, then self-corrects."""
    threads = ctx["threads"]
    threads["main-parking"].add_pressure(+0.30)

    _alert("main-parking", "FAULT",
           "Entry-gate sensor fault — counts may over-report. Traffic team dispatched.")

    return "🚧 Parking Gate Sensor Fault", (
        "The Main Parking entry-gate counter is over-reporting. Ops is treating the "
        "reading as unreliable until the sensor self-corrects."
    )


def _fire_drill(ctx: Ctx) -> Tuple[str, str]:
    """The big one: every building drains toward exits + parking spikes."""
    threads = ctx["threads"]
    for fid in ("viz-walk", "bharti-block", "sit-building", "sbs-block",
                "lis-lab-wing", "c-content-lab", "s-lab-wing",
                "central-library", "dormitory-a", "dormitory-b", "dormitory-c",
                "student-union", "recreation-center"):
        threads[fid].add_pressure(-0.50)
    threads["main-parking"].add_pressure(+0.45)

    for fid in ("viz-walk", "bharti-block", "sit-building", "sbs-block",
                "lis-lab-wing", "c-content-lab", "s-lab-wing",
                "central-library", "dormitory-a", "dormitory-b", "dormitory-c",
                "student-union", "recreation-center"):
        _alert(fid, "CRITICAL", "Fire drill in progress — evacuate via nearest exit.")

    return "🚨 Campus-Wide Fire Drill", (
        "A scheduled campus-wide fire drill is underway: every occupied building is "
        "draining toward assembly points while Main Parking spikes with vehicles "
        "holding at the gates."
    )


# ─────────────────────────────────────────────────────────────
#  ANOMALY CATALOG (probability per coordination cycle)
# ─────────────────────────────────────────────────────────────

ANOMALIES: List[Dict[str, Any]] = [
    {
        "id": "storm-surge",
        "title": "⛈️ Sudden Thunderstorm",
        "probability": 0.30,
        "cooldown_s": 240,
        "apply": _storm_surge,
    },
    {
        "id": "pop-event",
        "title": "🎪 Pop-up Event at Student Union",
        "probability": 0.18,
        "cooldown_s": 300,
        "apply": _pop_event,
    },
    {
        "id": "hvac-failure",
        "title": "🌡️ HVAC Failure — Science Block",
        "probability": 0.12,
        "cooldown_s": 420,
        "apply": _hvac_failure,
    },
    {
        "id": "exam-crunch",
        "title": "📝 Exam Week Crunch",
        "probability": 0.22,
        "cooldown_s": 360,
        "apply": _exam_crunch,
    },
    {
        "id": "sensor-fault",
        "title": "🚧 Parking Gate Sensor Fault",
        "probability": 0.10,
        "cooldown_s": 480,
        "apply": _sensor_fault,
    },
    {
        "id": "fire-drill",
        "title": "🚨 Campus-Wide Fire Drill",
        "probability": 0.08,
        "cooldown_s": 600,
        "apply": _fire_drill,
    },
]
