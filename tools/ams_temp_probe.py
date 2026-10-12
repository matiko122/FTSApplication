#!/usr/bin/env python3
"""
AMS-DIG-PROC detector temperature probe.

Records every MessageStatus frame (raw bytes + decoded fields) in two phases:

  1. baseline: board stopped, setpoint left as found
  2. after workmode_stop() + set_detector_temperature(--temp) (runtime only,
     no save_config / NVM write)

The setpoint is read back before and after the change. Results are printed as
a cluster/period summary and optionally written to CSV.

    python tools/ams_temp_probe.py --temp 263 --baseline 60 --after 120 --csv /tmp/ams_temp.csv
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ams_bench import apply_posix_compat, autodetect_port, check_port_access  # noqa: E402

from ams_module import AMSSerialModule  # noqa: E402
from ams_messages import MessageStatus, MessageConfigDetectorTemperature, MessageIds  # noqa: E402


def read_setpoint(module: AMSSerialModule, timeout: float = 3.0):
    module.read_config(MessageIds.MESSAGE_CONFIGURE_DETECTOR_TEMPERATURE)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        msg = module.read_one_message(msgClasses=MessageConfigDetectorTemperature, timeout=0.2)
        if msg is not None:
            return int(msg.temperature)
    return None


def capture(module: AMSSerialModule, phase: str, duration: float, rows: list, t0: float) -> None:
    print(f"\n== {phase}: {duration:.0f} s ==")
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        msg = module.read_one_message(msgClasses=MessageStatus, timeout=0.5)
        if msg is None:
            continue
        t = time.monotonic() - t0
        raw = bytes(msg)[:ctypes.sizeof(MessageStatus)]
        row = {
            "phase": phase,
            "t": round(t, 3),
            "temp_mK": int(msg.detector_temperature),
            "temp_ok": int(msg.temperature_ok),
            "sampling": int(msg.sampling_state),
            "processing": int(msg.processing_state),
            "rx_count": int(msg.messages_received_counter),
            "overflow": int(msg.data_overflow_counter),
            "raw_temp_hex": raw[17:21].hex(),
            "raw_hex": raw.hex(),
        }
        rows.append(row)
        print(f"  t={t:7.2f}s  T={row['temp_mK'] / 1000:8.3f} K  ok={row['temp_ok']}  "
              f"raw_T={row['raw_temp_hex']}  rx={row['rx_count']}")


def summarize(rows: list, phase: str) -> None:
    temps = [r["temp_mK"] for r in rows if r["phase"] == phase]
    if not temps:
        print(f"\n[{phase}] no status frames")
        return

    # 2 K wide buckets are enough to separate the clusters seen so far
    clusters: dict[int, list[int]] = {}
    for v in temps:
        clusters.setdefault(round(v / 2000), []).append(v)
    labels = {key: i for i, key in enumerate(sorted(clusters))}
    sequence = "".join("ABCDEFGHIJ"[labels[round(v / 2000)] % 10] for v in temps)

    print(f"\n[{phase}] {len(temps)} frames, {len(clusters)} cluster(s)")
    for key in sorted(clusters):
        vals = clusters[key]
        print(f"  {'ABCDEFGHIJ'[labels[key] % 10]}: n={len(vals):4d}  "
              f"min={min(vals) / 1000:.3f}  max={max(vals) / 1000:.3f}  "
              f"distinct={len(set(vals))}")
    print(f"  sequence: {sequence}")

    for period in range(1, 7):
        if len(sequence) > 2 * period and all(
                sequence[i] == sequence[i + period] for i in range(len(sequence) - period)):
            print(f"  strictly periodic with period {period}")
            break
    else:
        print("  not strictly periodic (period 1..6)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", default=None)
    parser.add_argument("--baudrate", type=int, default=1_000_000)
    parser.add_argument("--temp", type=int, default=263, help="setpoint for phase 2 [K]")
    parser.add_argument("--baseline", type=float, default=60.0, help="phase 1 duration [s]")
    parser.add_argument("--after", type=float, default=120.0, help="phase 2 duration [s]")
    parser.add_argument("--csv", default=None)
    args = parser.parse_args()

    port = args.port or autodetect_port()
    if not port:
        print("ERROR: no serial port found")
        return 2
    check_port_access(port)
    apply_posix_compat()

    module = AMSSerialModule(port=port, baudrate=args.baudrate)
    rows: list = []
    try:
        module.workmode_stop()
        time.sleep(0.2)
        module.flush_input()
        t0 = time.monotonic()

        print(f"setpoint before: {read_setpoint(module)} K")
        capture(module, "baseline", args.baseline, rows, t0)

        module.workmode_stop()
        time.sleep(0.2)
        module.set_detector_temperature(args.temp)
        time.sleep(0.2)
        print(f"\nset {args.temp} K, setpoint read back: {read_setpoint(module)} K")

        capture(module, f"setpoint_{args.temp}K", args.after, rows, t0)
        print(f"\nsetpoint at end: {read_setpoint(module)} K")
    finally:
        try:
            module.workmode_stop()
            module.reader_thread.stop()
            module.reader_thread.ser.close()
        except Exception as e:
            print(f"cleanup failed: {e}")

    summarize(rows, "baseline")
    summarize(rows, f"setpoint_{args.temp}K")

    if args.csv and rows:
        with open(args.csv, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nCSV written to {args.csv}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
