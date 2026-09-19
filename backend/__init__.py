"""
Campus Digital Twin v2 — Backend Engine package.

Modules
-------
db        : SQLModel / SQLite persistence layer (historical occupancy)
state     : Thread-safe in-memory campus state, mirrored to SQLite
hub       : WebSocket connection hub (fan-out broadcast)
app       : FastAPI application (REST + WS + predictive analytics)
simulator : Autonomous multi-threaded data simulator (6 buildings)
"""
