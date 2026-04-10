#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

PYTHON="/home/trolley/Trolly_system/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
  echo "Virtual environment not found at $PYTHON" >&2
  exit 1
fi

cleanup() {
  local pid
  pid="${MAIN_PID:-}"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
  fi
  wait "${MAIN_PID:-}" 2>/dev/null || true
}

trap cleanup EXIT INT TERM

echo "Starting Smart Trolley runtime..."

"$PYTHON" /home/trolley/Trolly_system/main.py &
MAIN_PID=$!

echo "main.py PID: $MAIN_PID"
echo "Press Ctrl+C to stop runtime."

wait "$MAIN_PID"
STATUS=$?

cleanup
exit "$STATUS"
