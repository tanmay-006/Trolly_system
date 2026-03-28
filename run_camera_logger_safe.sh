#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

# Stop any previous camera logger instance.
if pgrep -af "python.*camera_product_logger.py" >/tmp/camera_logger_running.txt; then
  while IFS= read -r line; do
    pid="$(printf "%s" "$line" | awk '{print $1}')"
    if [[ -n "${pid}" ]]; then
      kill "${pid}" 2>/dev/null || true
      sleep 0.3
      kill -9 "${pid}" 2>/dev/null || true
    fi
  done < /tmp/camera_logger_running.txt
fi

rm -f /tmp/camera_product_logger.lock

# Conservative SPI speed is often more stable after repeated restarts.
export TFT_BUS_SPEED_HZ="${TFT_BUS_SPEED_HZ:-4000000}"
export CAMERA_LOGGER_AUTO_TAKEOVER="${CAMERA_LOGGER_AUTO_TAKEOVER:-1}"

exec /home/trolley/Trolly_system/.venv/bin/python /home/trolley/Trolly_system/camera_product_logger.py
