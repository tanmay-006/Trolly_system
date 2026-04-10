#!/usr/bin/env python3
"""
Standalone HX711 load cell test script.

Default wiring for a 4-wire load cell into an HX711 board:
- Red   -> E+
- Black -> E-
- Green -> A-
- White -> A+

Default HX711 pins match the main trolley runtime:
- DOUT -> BCM 5
- SCK  -> BCM 6

Run this on a Raspberry Pi with RPi.GPIO and hx711 installed.
"""

from __future__ import annotations

import argparse
import importlib
import fcntl
import os
import signal
import threading
import time

try:
    GPIO = importlib.import_module("RPi.GPIO")
except Exception as exc:  # pragma: no cover - hardware import guard
    print(f"ERROR: Could not import RPi.GPIO: {exc}")
    print("Run this on a Raspberry Pi with RPi.GPIO installed.")
    raise SystemExit(1)

try:
    hx711_module = importlib.import_module("hx711")
    HX711 = getattr(hx711_module, "HX711")
except Exception as exc:  # pragma: no cover - hardware import guard
    print(f"ERROR: Could not import hx711: {exc}")
    print("Install the HX711 Python package on the Pi before running this script.")
    raise SystemExit(1)


BOARD_TO_BCM = {
    3: 2,
    5: 3,
    7: 4,
    8: 14,
    10: 15,
    11: 17,
    12: 18,
    13: 27,
    15: 22,
    16: 23,
    18: 24,
    19: 10,
    21: 9,
    22: 25,
    23: 11,
    24: 8,
    26: 7,
    27: 0,
    28: 1,
    29: 5,
    31: 6,
    32: 12,
    33: 13,
    35: 19,
    36: 16,
    37: 26,
    38: 20,
    40: 21,
}


class SingleInstanceLock:
    def __init__(self, lock_path: str = "/tmp/load_cell_test.lock"):
        self._lock_path = lock_path
        self._fd = None

    def acquire(self) -> bool:
        self._fd = open(self._lock_path, "w")
        try:
            fcntl.flock(self._fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        self._fd.write(str(os.getpid()))
        self._fd.flush()
        return True

    def release(self) -> None:
        if not self._fd:
            return
        try:
            fcntl.flock(self._fd.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            self._fd.close()
        except Exception:
            pass
        self._fd = None


class HX711Monitor:
    def __init__(self, dout_pin: int, sck_pin: int, sample_count: int, init_timeout_seconds: float, read_timeout_seconds: float):
        self._hx = None
        self._dout_pin = dout_pin
        self._sck_pin = sck_pin
        self._sample_count = max(1, sample_count)
        self._init_timeout_seconds = max(0.05, init_timeout_seconds)
        self._read_timeout_seconds = max(0.05, read_timeout_seconds)
        self._zero_offset = 0.0
        self._reference_unit = None

        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)

        try:
            if hasattr(HX711, "GPIO") and hasattr(HX711.GPIO, "setwarnings"):
                HX711.GPIO.setwarnings(False)

            self._hx = HX711(dout_pin=dout_pin, pd_sck_pin=sck_pin)
            self._safe_call("reset", self._init_timeout_seconds)
            self.zero()
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f"ERROR: HX711 init failed: {exc}")
            self._hx = None

    def _safe_call(self, method_name: str, timeout_seconds: float, *args):
        if not self._hx or not hasattr(self._hx, method_name):
            return None

        target = getattr(self._hx, method_name)
        result_box = {"value": None, "error": None}

        def _runner():
            try:
                result_box["value"] = target(*args)
            except Exception as err:
                result_box["error"] = err

        worker = threading.Thread(target=_runner, daemon=True)
        worker.start()
        worker.join(timeout_seconds)

        if worker.is_alive():
            raise TimeoutError(f"HX711 {method_name} timed out after {timeout_seconds:.2f}s")
        if result_box["error"] is not None:
            raise result_box["error"]
        return result_box["value"]

    @staticmethod
    def _normalize_numeric(value):
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            numeric_values = [float(item) for item in value if item is not None]
            if not numeric_values:
                return None
            return sum(numeric_values) / len(numeric_values)
        return float(value)

    def _read_sensor_raw(self) -> float | None:
        if not self._hx:
            return None
        if hasattr(self._hx, "get_raw_data"):
            value = self._safe_call("get_raw_data", self._read_timeout_seconds, self._sample_count)
            return self._normalize_numeric(value)
        return None

    def zero(self) -> float | None:
        raw_value = self._read_sensor_raw()
        if raw_value is None:
            return None
        self._zero_offset = float(raw_value)
        return self._zero_offset

    def tare(self) -> None:
        if not self._hx:
            return
        self._safe_call("reset", self._init_timeout_seconds)
        self.zero()

    def read_raw(self) -> float | None:
        try:
            return self._read_sensor_raw()
        except Exception as exc:
            print(f"WARN: Raw read failed: {exc}")
            return None

    def read_weight(self) -> float | None:
        if not self._hx:
            return None
        try:
            if self._reference_unit is not None:
                raw_value = self.read_raw()
                if raw_value is None:
                    return None
                return (float(raw_value) - float(self._zero_offset)) / float(self._reference_unit)
            if hasattr(self._hx, "get_weight_mean"):
                value = self._safe_call("get_weight_mean", self._read_timeout_seconds, self._sample_count)
                return self._normalize_numeric(value)
            if hasattr(self._hx, "get_weight"):
                value = self._safe_call("get_weight", self._read_timeout_seconds, self._sample_count)
                return self._normalize_numeric(value)
            return None
        except Exception as exc:
            print(f"WARN: Weight read failed: {exc}")
            return None

    def set_reference_unit(self, reference_unit: float) -> bool:
        self._reference_unit = float(reference_unit)
        if not self._hx:
            return False
        if not hasattr(self._hx, "set_reference_unit"):
            return False
        self._safe_call("set_reference_unit", self._read_timeout_seconds, reference_unit)
        return True

    def close(self) -> None:
        try:
            if self._hx and hasattr(self._hx, "power_down"):
                self._safe_call("power_down", self._read_timeout_seconds)
        except Exception:
            pass
        try:
            if hasattr(HX711, "GPIO") and hasattr(HX711.GPIO, "cleanup"):
                HX711.GPIO.cleanup([self._dout_pin, self._sck_pin])
        except Exception:
            pass
        self._hx = None


def _timestamp() -> str:
    return time.strftime("%H:%M:%S")


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor and test an HX711 load cell")
    parser.add_argument("--mode", choices=("bcm", "board"), default="bcm", help="Pin numbering mode")
    parser.add_argument("--dout-pin", type=int, default=int(os.getenv("HX711_DOUT_PIN", "5")), help="HX711 DOUT pin")
    parser.add_argument("--sck-pin", type=int, default=int(os.getenv("HX711_SCK_PIN", "6")), help="HX711 SCK pin")
    parser.add_argument("--samples", type=int, default=5, help="Number of samples to average per read")
    parser.add_argument("--poll-seconds", type=float, default=0.5, help="Delay between readings")
    parser.add_argument(
        "--init-timeout-seconds",
        type=float,
        default=float(os.getenv("HX711_INIT_TIMEOUT_SECONDS", "1.0")),
        help="Timeout for HX711 reset/tare during startup and calibration.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=float(os.getenv("HX711_READ_TIMEOUT_SECONDS", "1.0")),
        help="Timeout for each HX711 call",
    )
    parser.add_argument(
        "--reference-unit",
        type=float,
        default=None,
        help="Optional counts-per-gram calibration factor. If set, the script prints grams when supported.",
    )
    parser.add_argument(
        "--calibrate-known-grams",
        type=float,
        default=None,
        help="Calibrate from a known weight in grams, then continue monitoring.",
    )
    args = parser.parse_args()

    if args.mode == "board":
        if args.dout_pin not in BOARD_TO_BCM or args.sck_pin not in BOARD_TO_BCM:
            print("ERROR: BOARD mode only supports standard Raspberry Pi 40-pin header numbers.")
            return 1
        dout_pin = BOARD_TO_BCM[args.dout_pin]
        sck_pin = BOARD_TO_BCM[args.sck_pin]
    else:
        dout_pin = args.dout_pin
        sck_pin = args.sck_pin

    lock = SingleInstanceLock()
    if not lock.acquire():
        print("ERROR: Another load cell test instance is already running.")
        return 1

    running = True

    def _stop(_sig=None, _frame=None):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    monitor = HX711Monitor(dout_pin, sck_pin, args.samples, args.init_timeout_seconds, args.timeout_seconds)
    if not monitor._hx:
        lock.release()
        return 1

    calibrated = False
    native_calibration = False
    if args.reference_unit is not None:
        native_calibration = monitor.set_reference_unit(args.reference_unit)
        calibrated = True

    if args.calibrate_known_grams is not None:
        print()
        print("Calibration step")
        print("1. Leave the scale empty.")
        input("2. Press Enter to capture the empty-scale zero... ")
        zero_offset = monitor.zero()
        if zero_offset is None:
            print("ERROR: Could not capture the empty-scale zero.")
            monitor.close()
            lock.release()
            return 1
        print(f"Zero offset captured: {zero_offset:.2f}")
        print("3. Place the known test weight on the scale.")
        input("4. Press Enter to capture the calibration reading... ")
        calibration_raw = monitor.read_raw()
        if calibration_raw is None:
            print("ERROR: Could not read a calibration value.")
            monitor.close()
            lock.release()
            return 1
        if args.calibrate_known_grams <= 0:
            print("ERROR: Calibration weight must be greater than zero.")
            monitor.close()
            lock.release()
            return 1
        loaded_delta = abs(float(calibration_raw) - float(zero_offset))
        computed_reference_unit = loaded_delta / args.calibrate_known_grams
        if computed_reference_unit <= 0:
            print("ERROR: Computed reference unit is invalid.")
            monitor.close()
            lock.release()
            return 1
        monitor.set_reference_unit(computed_reference_unit)
        calibrated = True
        print(f"Calibration raw={calibration_raw:.2f}  known={args.calibrate_known_grams:.2f} g")
        print(f"Calibration delta={loaded_delta:.2f}  zero={zero_offset:.2f}")
        print(f"Computed reference unit: {computed_reference_unit:.6f}")
        if native_calibration:
            print("Calibration applied.")
        else:
            print("Calibration stored locally for manual conversion.")

    print("=" * 72)
    print("HX711 Load Cell Test")
    print(f"Mode: {args.mode.upper()}  DOUT: {args.dout_pin}  SCK: {args.sck_pin}")
    print(f"GPIO pins used by HX711: DOUT={dout_pin}  SCK={sck_pin}")
    print("Wiring: Red=E+  Black=E-  Green=A-  White=A+")
    print(f"Zero offset: {monitor._zero_offset:.2f}")
    if args.reference_unit is not None:
        if native_calibration:
            print(f"Calibration: enabled using reference unit {args.reference_unit}")
        else:
            print(f"Calibration: enabled using reference unit {args.reference_unit} (manual fallback)")
    else:
        print("Calibration: not set, showing raw counts only")
    print("Press Ctrl+C to stop.")
    print("=" * 72)

    try:
        while running:
            raw_value = monitor.read_raw()
            weight_value = monitor.read_weight() if calibrated else None

            if calibrated and weight_value is not None:
                raw_text = "unavailable" if raw_value is None else f"{raw_value:.2f}"
                print(f"[{_timestamp()}] raw={raw_text}  weight={weight_value:.2f} g", flush=True)
            else:
                if raw_value is None:
                    print(f"[{_timestamp()}] raw=unavailable", flush=True)
                else:
                    print(f"[{_timestamp()}] raw={raw_value:.2f}", flush=True)

            time.sleep(max(0.05, args.poll_seconds))
    finally:
        monitor.close()
        lock.release()
        print(f"[{_timestamp()}] Stopped.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())