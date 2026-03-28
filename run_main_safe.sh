#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

# Stop any previously running runtime instance.
if pgrep -af "python.*main.py" >/tmp/smart_trolley_running.txt; then
  while IFS= read -r line; do
    pid="$(printf "%s" "$line" | awk '{print $1}')"
    if [[ -n "${pid}" ]]; then
      kill "${pid}" 2>/dev/null || true
      sleep 0.2
      kill -9 "${pid}" 2>/dev/null || true
    fi
  done < /tmp/smart_trolley_running.txt
fi

rm -f /tmp/smart_trolley_main.lock

exec /home/trolley/Trolly_system/.venv/bin/python /home/trolley/Trolly_system/main.py
