#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

PYTHON="/home/trolley/Trolly_system/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
  echo "Virtual environment not found at $PYTHON" >&2
  exit 1
fi

cleanup() {
  local pids
  pids="${POS_APP_PID:-} ${MAIN_PID:-}"
  for pid in $pids; do
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  wait "${POS_APP_PID:-}" 2>/dev/null || true
  wait "${MAIN_PID:-}" 2>/dev/null || true
}

trap cleanup EXIT INT TERM

echo "Starting Smart Trolley services..."

"$PYTHON" /home/trolley/Trolly_system/pos_app.py &
POS_APP_PID=$!

"$PYTHON" /home/trolley/Trolly_system/main.py &
MAIN_PID=$!

echo "pos_app.py PID: $POS_APP_PID"
echo "main.py PID: $MAIN_PID"
echo "Press Ctrl+C to stop both services."

wait -n "$POS_APP_PID" "$MAIN_PID"
STATUS=$?

cleanup
exit "$STATUS"
