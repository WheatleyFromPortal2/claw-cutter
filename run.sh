#!/usr/bin/env bash
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="$ROOT/claw-cutter.log"

: > "$LOG"

cleanup() {
    trap - INT TERM EXIT
    echo ""
    echo "[run.sh]   Stopping…"
    kill 0 2>/dev/null
    wait 2>/dev/null
}
trap cleanup INT TERM EXIT

# Backend — source venv, run from backend/
(
    source "$ROOT/.venv/bin/activate"
    cd "$ROOT/backend"
    exec python main.py
) 2>&1 | sed -u 's/^/[backend]  /' | tee -a "$LOG" &

# Frontend — run from frontend/
(
    cd "$ROOT/frontend"
    exec npm run dev
) 2>&1 | sed -u 's/^/[frontend] /' | tee -a "$LOG" &

echo "[run.sh]   Log → $LOG" | tee -a "$LOG"
wait
