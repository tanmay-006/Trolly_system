import argparse
import importlib
import statistics
import sys
import threading
import time
from collections import deque

try:
    GPIO = importlib.import_module("RPi.GPIO")
except Exception as exc:  # pragma: no cover - hardware import guard
    print(f"[ERROR] Could not import RPi.GPIO: {exc}")
    print("Run this script on Raspberry Pi with RPi.GPIO installed.")
    raise SystemExit(1)

try:
    _legacy_hx711 = importlib.import_module("hx711v0_5_1")
    HX711 = getattr(_legacy_hx711, "HX711")
    _HX711_VARIANT = "legacy"
except Exception:
    try:
        _modern_hx711 = importlib.import_module("hx711")
        HX711 = getattr(_modern_hx711, "HX711")
        _HX711_VARIANT = "modern"
    except Exception as exc:  # pragma: no cover - hardware import guard
        print(f"[ERROR] Could not import HX711 library: {exc}")
        print("Install one of: hx711 (preferred) or hx711v0_5_1")
        raise SystemExit(1)


def _safe_call(target, timeout_seconds, *args):
    result_box = {"value": None, "error": None}

    def _runner():
        try:
            result_box["value"] = target(*args)
        except Exception as err:
            result_box["error"] = err

    worker = threading.Thread(target=_runner, daemon=True)
    worker.start()
    worker.join(max(0.05, timeout_seconds))
    if worker.is_alive():
        raise TimeoutError(f"HX711 call timed out after {timeout_seconds:.2f}s")
    if result_box["error"] is not None:
        raise result_box["error"]
    return result_box["value"]


def _as_float(value):
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        data = [float(v) for v in value if v is not None]
        if not data:
            return None
        return sum(data) / len(data)
    return float(value)


class ScaleReader:
    def __init__(self, dout_pin, sck_pin, reference_unit, sample_count, timeout_seconds):
        self._hx = None
        self._variant = _HX711_VARIANT
        self._reference_unit = float(reference_unit)
        self._sample_count = max(1, int(sample_count))
        self._timeout_seconds = max(0.05, float(timeout_seconds))
        self._zero_offset = 0.0
        self._dout_pin = dout_pin
        self._sck_pin = sck_pin

        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        self._hx = HX711(dout_pin, sck_pin)

        if self._variant == "legacy":
            self._hx.setReadingFormat("MSB", "MSB")
            self._hx.setReferenceUnit(self._reference_unit, "A")
            self._hx.reset()
        else:
            if hasattr(HX711, "GPIO") and hasattr(HX711.GPIO, "setwarnings"):
                HX711.GPIO.setwarnings(False)
            _safe_call(self._hx.reset, self._timeout_seconds)

    def zero(self):
        if self._variant == "legacy":
            self._hx.autosetOffset("A")
            self._zero_offset = 0.0
            return 0.0

        raw = self.read_raw()
        if raw is None:
            return None
        self._zero_offset = float(raw)
        return self._zero_offset

    def read_raw(self):
        if self._variant == "legacy":
            value = self._hx.getWeight("A")
            return _as_float(value)

        value = _safe_call(self._hx.get_raw_data, self._timeout_seconds, self._sample_count)
        return _as_float(value)

    def read_grams(self):
        if self._variant == "legacy":
            return self.read_raw()

        raw = self.read_raw()
        if raw is None:
            return None
        return (float(raw) - float(self._zero_offset)) / float(self._reference_unit)

    def close(self):
        try:
            if self._variant == "modern" and hasattr(self._hx, "power_down"):
                _safe_call(self._hx.power_down, self._timeout_seconds)
        except Exception:
            pass
        try:
            if self._variant == "modern" and hasattr(HX711, "GPIO") and hasattr(HX711.GPIO, "cleanup"):
                HX711.GPIO.cleanup([self._dout_pin, self._sck_pin])
            else:
                GPIO.cleanup([self._dout_pin, self._sck_pin])
        except Exception:
            pass


def parse_args():
    parser = argparse.ArgumentParser(
        description="Simple weighing-machine style display for HX711 (channel A)."
    )
    parser.add_argument("--dout", type=int, default=5, help="HX711 DOUT BCM pin (default: 5)")
    parser.add_argument("--pd-sck", type=int, default=6, help="HX711 PD_SCK BCM pin (default: 6)")
    parser.add_argument(
        "--reference-unit",
        type=float,
        default=114.0,
        help="Calibration factor. Use your calibrated value for accurate grams.",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=10,
        help="Number of recent readings used to smooth displayed weight.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=1.0,
        help="Timeout per HX711 read call.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.1,
        help="Seconds between displayed updates.",
    )
    parser.add_argument(
        "--zero-threshold",
        type=float,
        default=0.5,
        help="Display 0.0g when absolute weight is below this threshold.",
    )
    return parser.parse_args()


def format_weight(grams):
    if abs(grams) >= 1000.0:
        return f"{grams / 1000.0:8.3f} kg"
    return f"{grams:8.1f} g"


def main():
    args = parse_args()

    if args.samples < 1:
        print("[ERROR] --samples must be >= 1")
        return 1

    reader = ScaleReader(
        dout_pin=args.dout,
        sck_pin=args.pd_sck,
        reference_unit=args.reference_unit,
        sample_count=args.samples,
        timeout_seconds=args.timeout_seconds,
    )

    print("[INFO] Remove all weight from the platform, then press Enter to tare.")
    input()

    print("[INFO] Taring scale...")
    zero_val = reader.zero()
    if zero_val is None:
        print("[ERROR] Failed to capture zero offset. Check wiring/power and retry.")
        reader.close()
        return 1

    print(f"[INFO] Calibration factor set to {args.reference_unit}.")
    if _HX711_VARIANT == "modern":
        print(f"[INFO] Zero offset captured at {zero_val:.2f} raw counts.")
    print(f"[INFO] Using HX711 library variant: {_HX711_VARIANT}.")
    print("[INFO] Ready. Place item on scale. Press Ctrl+C to exit.")

    recent = deque(maxlen=args.samples)

    try:
        while True:
            try:
                weight = reader.read_grams()
            except Exception as exc:
                print(f"\n[WARN] Read failed: {exc}")
                time.sleep(args.interval)
                continue

            if weight is None:
                continue

            recent.append(weight)

            stable_grams = statistics.median(recent)
            if abs(stable_grams) < args.zero_threshold:
                stable_grams = 0.0

            print(f"\rWeight: {format_weight(stable_grams)}", end="", flush=True)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n[INFO] Exiting scale display.")
    finally:
        reader.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
