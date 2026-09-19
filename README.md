# 🏫 Campus Digital Twin v2 — Production-Grade Command Center

A fully integrated, real-time campus operations platform: **WebSocket
telemetry streaming**, **SQLite historical persistence**, **moving-average
predictive analytics (60-min breach forecast)**, a **multithreaded
autonomous simulator** with cascading environmental anomalies, and a
**glassmorphism command center** (Leaflet + Chart.js).

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/KAMUUZ-git/Code_hiest)

> 🚀 **Deploying for free?** See **[DEPLOY.md](DEPLOY.md)** — Render +
> Supabase, click-by-click, zero cost.

---

## Architecture

```
┌─────────────────────────────┐        ┌──────────────────────────────┐
│  Autonomous Simulator       │        │  FastAPI Backend (port 8000) │
│  (6 facility threads +      │        │  ┌────────────────────────┐  │
│   anomaly coordinator)      │        │  │ /ws  WebSocket stream  │◄─┼── Dashboard push
│      │  every 2 s           │ update │  ├────────────────────────┤  │   (snapshot/telemetry/
│      ▼                      ├────────►  │ /api/*  REST surface   │  │    alert/anomaly/predict)
│  CampusState (thread-safe)  │  REST  │  ├────────────────────────┤  │
│      │                      │        │  │ /api/predictions  MA   │  │
│      ▼                      │        │  │  forecast (60-min)     │  │
│  SQLite (SQLModel)          │        │  └────────────────────────┘  │
│  occupancy_history table    │        └──────────────────────────────┘
└─────────────────────────────┘                 ▲
                                                │ ws://localhost:8000/ws
                                 ┌──────────────┴───────────────┐
                                 │  Frontend Command Center     │
                                 │  index.html + app.js         │
                                 │  Leaflet map · Chart.js live │
                                 │  trend · predictions HUD     │
                                 └──────────────────────────────┘
```

### Folder structure

```
.
├── index.html                     # Frontend shell (Tailwind + Leaflet + Chart.js)
├── app.js                         # Frontend logic: WS client, chart, markers, HUD
├── requirements.txt
├── render.yaml                    # Render blueprint (one-click free deploy)
├── DEPLOY.md                      # Step-by-step free cloud deployment guide
├── README.md
├── data/                          # SQLite database (auto-created)
│   └── campus.db
└── backend/
    ├── __init__.py
    ├── app.py                     # FastAPI app: REST + WS + lifespan
    ├── state.py                   # Thread-safe campus state (single write path)
    ├── db.py                      # SQLModel/SQLite persistence layer
    ├── hub.py                     # WebSocket connection hub (fan-out)
    ├── analytics.py               # Moving-average 60-min forecast engine
    ├── facilities.py              # Shared 6-zone registry
    └── simulator/
        ├── __init__.py
        ├── threads.py             # Coordinator: 6 threads + anomaly loop
        ├── facility_thread.py     # One independent timeline per building
        └── anomalies.py           # Cascading environmental anomaly catalog
```

---

## Quick start

```bash
# 1 — Install (Python 3.9–3.12)
python3 -m pip install -r requirements.txt

# 2 — Run the backend (simulator embedded by default)
uvicorn backend.app:app --port 8000

# 3 — Open the command center
#     (serve statically so app.js loads cleanly)
python3 -m http.server 5500
#     → http://localhost:5500/index.html
```

> Prefer running the simulator standalone? Set `CAMPUS_TWIN_EMBED_SIM=0`
> when launching uvicorn, then run `python -m backend.simulator.threads`
> in a second terminal. Both modes talk to the same state layer.

---

## API surface

| Method | Path | Purpose |
|--------|------|---------|
| GET  | `/ws` | **WebSocket stream** — snapshot, telemetry, alerts, anomalies, predictions |
| GET  | `/api/status` | Full campus snapshot |
| GET  | `/api/status/{id}` | One facility |
| POST | `/api/update` | Telemetry ingestion (validates capacity) |
| POST | `/api/alerts` | Raise an alert |
| DELETE | `/api/alerts/{id}` | Dismiss all alerts for a facility |
| GET  | `/api/history/{id}?minutes=60` | Historical occupancy (SQLite) |
| GET  | `/api/predictions` | 60-min moving-average breach forecast |
| GET  | `/api/summary` | Campus-wide executive aggregation |
| POST | `/api/reset` | Demo reset to 5% baseline |
| GET  | `/api/health` | Liveness + WS client count |

### WebSocket message protocol

Server → client:

```jsonc
{ "type": "snapshot",    "facilities": [ ... ], "ts": "..." }
{ "type": "telemetry",   "facility": { ... },   "ts": "..." }
{ "type": "alert",       "facility_id": "...", "alert": { ... } }
{ "type": "anomaly",     "title": "⛈️ …", "narrative": "…" }
{ "type": "predictions", "breach_queue": [ ... ] }
{ "type": "reset",       "facilities": [ ... ] }
```

Client → server (control channel):

```jsonc
{ "action": "ping" }                      // liveness
{ "action": "subscribe", "facility_id": "central-library" }
```

---

## The predictive model

`/api/predictions` implements a **moving-average + linear-slope** forecast:

1. Pull each facility's trailing 15-minute telemetry window (SQLite).
2. Smooth with a 5-point simple moving average (kills sensor noise).
3. Compute `slope_pct_per_min` across the window.
4. Project 60 minutes ahead: `smoothed_now + slope × 60`.
5. Solve for the exact **minutes-to-capacity** breach point.

Facilities are returned sorted by soonest breach — the HUD's ordering
*is* the ops queue. Facilities with <4 samples report
`forecast: "insufficient_data"` rather than guessing.

---

## Simulator design

- **One thread per building** (`threading.Thread`, daemon), each with its
  own staggered simulated clock, diurnal curve (labs / library / parking /
  housing / social / recreation), and organic random-walk momentum.
- **Anomaly coordinator** rolls every 45 s against a catalog of six
  cascading events (thunderstorm, pop-up event, HVAC failure, exam crunch,
  sensor fault, campus fire drill). Anomalies apply **pressure** to
  multiple facility threads at once — cascades ramp gradually across the
  map instead of teleporting numbers.
- All occupancy mutations funnel through
  `CampusState.update_facility()` — the single write path that
  persists to SQLite and fans out over WebSockets.

---

## Production notes (what "production-grade" means here)

- **Durable twin state** — restart the backend and it re-seeds from the
  newest SQLite rows; the map never cold-starts.
- **Backpressure-safe writes** — SQLite writes serialized behind a lock;
  chart history served from a bounded in-memory buffer with SQLite
  fallback.
- **Resilient transport** — the frontend reconnects with exponential
  backoff, re-seeds on snapshot, and degrades to REST for history.
- **Graceful failure** — a dead simulator thread logs and continues; a
  dead client socket is evicted on first failed send.
- **Security hardening for real deployment**: pin CORS origins, put
  uvicorn behind TLS, add auth on the control endpoints, and move SQLite
  → Postgres when multi-writer scale demands it.
