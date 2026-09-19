# 🏫 Campus Digital Twin — START HERE

Hi! This page tells you **everything** you need. It's really just one button. 😄

---

## 🚀 How to turn it on (the ONLY step)

1. Open the folder **freebuff** in Finder.
2. **Double-click** the file called **`START.command`**.
3. A black window appears (that's the engine talking — it's normal!). After ~10 seconds your browser opens by itself and — **boom** — your campus is alive. 🎉

> ⚠️ **First time only:** it quietly installs its parts for 2–3 minutes. That's a one-time thing — after that it starts in seconds.

> 🛑 **To stop:** go back to the black window and press any key. Done.

> 🍎 **If your Mac says "cannot be opened":** right-click `START.command` → **Open** → **Open**. (Macs are just shy the first time.)

---

## 🕹️ What can I do in the dashboard?

| Try this | What happens |
|---|---|
| **Watch the map** | 6 glowing campus zones pulse like little radars |
| **Click any building** (map dot or card) | Its page opens with a **live line graph** of people over time |
| **Press ⚡ Cause Chaos** | You trigger a thunderstorm, fire drill, HVAC failure & more — watch buildings change color! |
| **Press Reset Demo** | Everything calms back down |
| **Watch "60-min Forecast"** | The computer **predicts the future** — which building will get too full soon |

**Bonus round (for explorers):** add `/docs` to the web address → `http://127.0.0.1:8000/docs` — an auto-generated playground for the whole API.

---

## 🔌 Cool things it can do (for tech folks)

- **WebSocket streaming** — the dashboard never asks "any update?" — the server just *tells* it. That's why it feels instant.
- **SQLite database** — every measurement is saved in `data/campus.db`. Close everything, reopen, and the campus remembers where it left off.
- **Prediction engine** — a moving-average + slope model that says which building hits 100% within 60 minutes.
- **6 simulator threads** — each building lives its own life (labs are busy at lunch, the gym is packed after work).
- **Cascading anomalies** — one storm and *multiple* buildings react at once. That's the "digital twin" part.

---

## 🧯 Something looks wrong?

| Symptom | Fix |
|---|---|
| Browser says "can't connect" | The black window was closed. Double-click `START.command` again. |
| Badge says "WS Connecting…" forever | Wait ~10s. If still stuck, close the black window and relaunch. |
| Port already in use / old ghost server | The launcher kills old copies automatically — or restart your Mac's Terminal once. |
| Want a completely fresh world? | Delete the file `data/campus.db`, then relaunch. |

---

*Built with FastAPI + WebSockets + SQLite + Leaflet + Chart.js. Made with 💙 — now go cause some chaos.* ⚡
