"""
Campus Digital Twin v2 — Autonomous Data Simulator.

Each of the 6 campus facilities runs on its own independent timeline
thread (Python `threading`), producing organic, desynchronized
telemetry. A coordinator thread injects environmental anomalies that
cascade across multiple buildings simultaneously.
"""
