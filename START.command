#!/bin/bash
# ============================================================
#  🏫 CAMPUS DIGITAL TWIN — ONE-CLICK LAUNCHER (macOS)
#  Double-click this file. That's it. That's the whole thing.
# ============================================================

cd "$(dirname "$0")"

echo ""
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║   🏫  CAMPUS DIGITAL TWIN — starting up…      ║"
echo "  ╚══════════════════════════════════════════════╝"
echo ""

PY="python3"

# ── 1. Find Python 3 (install via Xcode CLT prompt if missing) ──
if ! command -v $PY >/dev/null 2>&1; then
  echo "🐍  Python 3 not found. A window may pop up — click 'Install'."
  touch /tmp/.com.apple.dt.CommandLineTools.installondemand.in-progress
  $PY 2>/dev/null
  if ! command -v $PY >/dev/null 2>&1; then
    echo "❌  Could not find Python 3 after installation. Run me again!"
    read -n 1 -s -r -p "Press any key to close…"
    exit 1
  fi
fi
echo "✅  Python found: $($PY --version 2>&1)"

# ── 2. Create the project's private Python sandbox (once) ──
if [ ! -x ".venv/bin/python" ]; then
  echo "📦  First run: setting things up (2–3 minutes, one time only)…"
  $PY -m venv .venv
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -r requirements.txt
  echo "✅  Setup complete! Next time this is instant."
else
  echo "✅  Everything already set up."
fi

# ── 3. Free port 8000 if a ghost of a previous run lingers ──
if lsof -nP -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "🔁  Stopping the old copy of the server…"
  kill $(lsof -t -nP -iTCP:8000 -sTCP:LISTEN) 2>/dev/null
  sleep 2
fi

# ── 4. Start the platform + open the dashboard ──
echo "🚀  Starting the engine…"
nohup .venv/bin/uvicorn backend.app:app --host 127.0.0.1 --port 8000 > /tmp/campus_twin.log 2>&1 &

echo ""
echo "⏳  Waking up all 6 buildings (10 seconds)…"
for i in 10 9 8 7 6 5 4 3 2 1; do
  printf "   %d…\r" "$i"; sleep 1
done

if lsof -nP -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "✅  Engine is running!  Opening your dashboard…"
  open "http://127.0.0.1:8000/"
  echo ""
  echo "  ╔════════════════════════════════════════════════════╗"
  echo "  ║  🎉  HAVE FUN!                                     ║"
  echo "  ║  • Click any building to see its live chart        ║"
  echo "  ║  • Press ⚡ Cause Chaos to trigger an event        ║"
  echo "  ║  • Keep this window OPEN while you play            ║"
  echo "  ║  • To STOP: press Ctrl+C here (or close window)    ║"
  echo "  ╚════════════════════════════════════════════════════╝"
else
  echo "❌  The engine didn't start. Log:"
  tail -20 /tmp/campus_twin.log
fi

echo ""
read -n 1 -s -r -p "Press any key to STOP the server and close…"
kill $(lsof -t -nP -iTCP:8000 -sTCP:LISTEN) 2>/dev/null
echo "👋  Stopped. Bye!"
