"""
=============================================================
  CAMPUS DIGITAL TWIN v2 — FACILITY REGISTRY (shared)
=============================================================

Single source of truth for the tracked IIT Delhi facilities.
Imported by backend.app (registration + REST) and
backend.simulator.threads (per-building timeline threads) so the
map, the API, and the simulation can never drift apart.

Coordinates are approximate on-campus positions (offset a few
tens of metres from real footprints on purpose, so sensor dots
don't sit exactly on OSM building outlines).

`archetype` selects the simulator's diurnal occupancy curve;
`category` drives the frontend's quick filters; `zone_m` sizes
the map's footprint circle.
"""

FACILITY_DEFS: list = [
    # ── Academic blocks ──────────────────────────────────────
    {
        "id": "viz-walk", "name": "Vishwakarma (LH View)", "type": "building",
        "category": "academic", "archetype": "academic",
        "coords": [28.5472, 77.1932], "capacity": 340, "zone_m": 90,
        "hours": "08:00 – 19:00", "activity": "Operational",
    },
    {
        "id": "bharti-block", "name": "Bharti Building (EE)", "type": "building",
        "category": "academic", "archetype": "academic",
        "coords": [28.5468, 77.1922], "capacity": 300, "zone_m": 80,
        "hours": "08:00 – 20:00", "activity": "Operational",
    },
    {
        "id": "sit-building", "name": "SIT Building (SBE)", "type": "building",
        "category": "academic", "archetype": "academic",
        "coords": [28.5464, 77.1942], "capacity": 200, "zone_m": 70,
        "hours": "09:00 – 18:00", "activity": "Operational",
    },
    {
        "id": "sbs-block", "name": "SBS Block (Bio Sciences)", "type": "building",
        "category": "academic", "archetype": "academic",
        "coords": [28.5476, 77.1944], "capacity": 180, "zone_m": 70,
        "hours": "09:00 – 18:00", "activity": "Operational",
    },
    # ── Labs / research ──────────────────────────────────────
    {
        "id": "lis-lab-wing", "name": "LIS Lab Wing", "type": "building",
        "category": "labs", "archetype": "labs",
        "coords": [28.5460, 77.1914], "capacity": 150, "zone_m": 65,
        "hours": "09:00 – 21:00", "activity": "Operational",
    },
    {
        "id": "c-content-lab", "name": "C-Content Lab (CIC)", "type": "building",
        "category": "labs", "archetype": "labs",
        "coords": [28.5450, 77.1912], "capacity": 120, "zone_m": 60,
        "hours": "09:00 – 21:00", "activity": "Operational",
    },
    {
        "id": "s-lab-wing", "name": "S-Lab Wing", "type": "building",
        "category": "labs", "archetype": "labs",
        "coords": [28.5456, 77.1928], "capacity": 140, "zone_m": 60,
        "hours": "09:00 – 21:00", "activity": "Operational",
    },
    # ── Central Library ──────────────────────────────────────
    {
        "id": "central-library", "name": "Central Library", "type": "building",
        "category": "academic", "archetype": "academic",
        "coords": [28.5443, 77.1930], "capacity": 650, "zone_m": 110,
        "hours": "08:00 – 23:00", "activity": "Operational",
    },
    # ── Housing ──────────────────────────────────────────────
    {
        "id": "dormitory-a", "name": "Kumaon Hostel", "type": "building",
        "category": "housing", "archetype": "housing",
        "coords": [28.5432, 77.1902], "capacity": 280, "zone_m": 85,
        "hours": "24 × 7", "activity": "Operational",
    },
    {
        "id": "dormitory-b", "name": "Karakoram Hostel", "type": "building",
        "category": "housing", "archetype": "housing",
        "coords": [28.5441, 77.1896], "capacity": 280, "zone_m": 85,
        "hours": "24 × 7", "activity": "Operational",
    },
    {
        "id": "dormitory-c", "name": "Nilgiri Hostel", "type": "building",
        "category": "housing", "archetype": "housing",
        "coords": [28.5426, 77.1910], "capacity": 280, "zone_m": 85,
        "hours": "24 × 7", "activity": "Operational",
    },
    # ── Social / recreation ──────────────────────────────────
    {
        "id": "student-union", "name": "Student Union Bldg (Bharti SCO)", "type": "building",
        "category": "social", "archetype": "social",
        "coords": [28.5449, 77.1952], "capacity": 260, "zone_m": 80,
        "hours": "07:00 – 24:00", "activity": "Operational",
    },
    {
        "id": "recreation-center", "name": "Rec Center / SAC", "type": "building",
        "category": "social", "archetype": "recreation",
        "coords": [28.5424, 77.1938], "capacity": 220, "zone_m": 95,
        "hours": "06:00 – 22:00", "activity": "Operational",
    },
    # ── Parking ──────────────────────────────────────────────
    {
        "id": "main-parking", "name": "Main Parking (Gate #2)", "type": "parking",
        "category": "parking", "archetype": "parking",
        "coords": [28.5470, 77.1902], "capacity": 200, "zone_m": 100,
        "hours": "24 × 7", "activity": "Operational",
    },
]

FACILITY_IDS: list = [f["id"] for f in FACILITY_DEFS]
