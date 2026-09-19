"""
=============================================================
  CAMPUS DIGITAL TWIN v2 — WEBSOCKET HUB (fan-out broadcast)
=============================================================

Design notes
------------
- One hub instance (process-wide singleton) tracks every connected
  dashboard socket. `broadcast()` is called from the simulator's
  ticker thread, so pushes are serialized onto the uvicorn event
  loop via `asyncio.run_coroutine_threadsafe` against the loop the
  app is running on — no polling, no missed updates, and the
  event loop never blocks on thread synchronization.
- Slow/dead clients are evicted on first failed send rather than
  stalling the whole fan-out.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Set

from fastapi import WebSocket

logger = logging.getLogger("campus_twin.hub")


class ConnectionHub:
    """Registry of connected WebSocket dashboards + thread-safe broadcast."""

    def __init__(self) -> None:
        self._clients: Set[WebSocket] = set()
        self._lock = asyncio.Lock()          # event-loop only
        self._loop: asyncio.AbstractEventLoop | None = None

    # ── Lifecycle ─────────────────────────────────────────────

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Capture the running event loop (called from lifespan startup)."""
        self._loop = loop

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._clients.add(websocket)
        logger.info("🔌 Dashboard connected (%d online)", self.count())

    def disconnect(self, websocket: WebSocket) -> None:
        # Sync-friendly: may be called from finally-blocks on the loop.
        self._clients.discard(websocket)
        logger.info("🔌 Dashboard disconnected (%d online)", self.count())

    def count(self) -> int:
        return len(self._clients)

    # ── Broadcast ─────────────────────────────────────────────

    async def broadcast_json(self, message: Dict[str, Any]) -> None:
        """Async broadcast — awaited from code already on the event loop."""
        async with self._lock:
            targets = list(self._clients)
        if not targets:
            return
        results = await asyncio.gather(
            *(self._safe_send(ws, message) for ws in targets),
            return_exceptions=True,
        )
        dead = [ws for ws, r in zip(targets, results) if isinstance(r, Exception)]
        for ws in dead:
            self.disconnect(ws)

    def broadcast_threadsafe(self, message: Dict[str, Any]) -> None:
        """
        Broadcast from a worker thread (the simulator's ticker).
        Fire-and-forget: schedules the coroutine on the bound loop.
        If no loop is bound (e.g. simulator run standalone), drop silently.
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(self.broadcast_json(message), loop)

    async def send_json(self, websocket: WebSocket, message: Dict[str, Any]) -> None:
        await websocket.send_json(message)

    @staticmethod
    async def _safe_send(ws: WebSocket, message: Dict[str, Any]) -> None:
        await ws.send_json(message)


# Process-wide singleton, imported by app + simulator.
hub = ConnectionHub()
