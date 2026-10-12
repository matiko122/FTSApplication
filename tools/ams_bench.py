#!/usr/bin/env python3
"""
AMS-DIG-PROC bench validation script (Phase 0 of the AMIS8140-01 integration).

Runs against the physical board, no FTS app changes involved. It validates the
assumptions the driver implementation depends on:

  1. serial connection at 1 Mbit/s, status messages, optional firmware read
     (firmware/UUID reads exist only in the git version of ams-dig-proc, not
     in PyPI 0.0.9 -- they are used when available)
  2. optional detector temperature set + stabilization wait
  3. simulation workmode smoke test (known samples through the full stack)
  4. freerunning captures at candidate oversampling ratios, reporting:
       - actual output sample size (16-bit vs 32-bit -> matters for UART budget)
       - samples_count semantics (requested vs received)
       - effective sample rate, wall time, first-data latency
       - buffer `counter` continuity (dropped frames) and data_overflow_counter

Usage (inside the project venv, with the board connected):

    python tools/ams_bench.py                      # autodetect port, run everything
    python tools/ams_bench.py --port /dev/ttyUSB0
    python tools/ams_bench.py --temp 263           # also set detector temperature
    python tools/ams_bench.py --status-only
    python tools/ams_bench.py --no-sim --no-rate-test
    python tools/ams_bench.py --ratios 256,512,1024 --duration 2 --outdir /tmp/ams_bench
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
import time

import numpy as np
import serial

try:
    from ams_module import AMSSerialModule
    from ams_messages import MessageOutputData, MessageStatus, SampleSize
except ImportError as exc:  # pragma: no cover
    print(f"ERROR: ams-dig-proc not importable ({exc}).")
    print("Install it with:  pip install -r requirements.txt")
    sys.exit(2)

PHYSICAL_RATE = 7_000_000          # ADC rate [Sa/s]
OUTPUT_BUFFER_SAMPLES = 2048       # post-processing buffer size (library docs)
CANDIDATE_RATIOS = (256, 512, 1024)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def apply_posix_compat() -> None:
    """Patch pyserial for POSIX.

    The AMS library calls Serial.set_buffer_size(), which only exists in
    pyserial's Windows backend (serialwin32). On Linux/macOS this raises
    AttributeError while opening the port. Patch it to a no-op; the kernel
    RX buffer then stays at the OS default.
    """
    if not hasattr(serial.Serial, "set_buffer_size"):
        def _set_buffer_size_noop(self, rx_size=None, tx_size=None):  # noqa: ANN001
            return None

        serial.Serial.set_buffer_size = _set_buffer_size_noop
        print("note: applied POSIX compat patch for pyserial set_buffer_size()")


def autodetect_port() -> str | None:
    candidates = sorted(glob.glob("/dev/ttyUSB*")) + sorted(glob.glob("/dev/ttyACM*"))
    if candidates:
        print(f"Autodetected serial candidates: {', '.join(candidates)}")
    return candidates[0] if candidates else None


def check_port_access(port: str) -> None:
    if not os.path.exists(port):
        print(f"ERROR: {port} does not exist. Is the board connected?")
        sys.exit(2)
    if not os.access(port, os.R_OK | os.W_OK):
        print(f"ERROR: no read/write access to {port}.")
        print("Fix with one of:")
        print(f"    sudo usermod -aG uucp $USER   # then log out/in (or 'newgrp uucp' in this shell)")
        print(f"    sudo chmod a+rw {port}        # quick, temporary (lost on replug)")
        sys.exit(2)


def status_str(st: MessageStatus) -> str:
    return (f"T={st.detector_temperature / 1000:.2f} K ok={bool(st.temperature_ok)} "
            f"sampling={st.sampling_state} processing={st.processing_state} "
            f"overflow={st.data_overflow_counter} rx_count={st.messages_received_counter} "
            f"reset={st.reset_flag} cfg_changed={st.configuration_changed}")


def drain_status(module: AMSSerialModule, duration: float) -> MessageStatus | None:
    """Read MessageStatus messages for `duration` seconds, return the last one."""
    deadline = time.monotonic() + duration
    last = None
    while time.monotonic() < deadline:
        msg = module.read_one_message(msgClasses=MessageStatus, timeout=0.2)
        if msg is not None:
            last = msg
    return last


def cleanup(module: AMSSerialModule | None) -> None:
    if module is None:
        return
    try:
        module.workmode_stop()
    except Exception:
        pass
    try:
        module.reader_thread.stop()
        module.reader_thread.ser.close()
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# tests
# --------------------------------------------------------------------------- #

def test_connection(module: AMSSerialModule) -> None:
    print("\n== 1. connection / status ==")
    module.flush_input()
    st = drain_status(module, 2.0)
    if st is None:
        print("  no status message received (board awake? firmware running?)")
    else:
        print("  " + status_str(st))

    if hasattr(module, "read_firmware_version"):
        module.flush_input()
        module.read_firmware_version()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            msg = module.read_one_message(timeout=0.2)
            if msg is not None and "Firmware" in type(msg).__name__:
                print(f"  firmware: {msg}")
                break
        else:
            print("  no firmware version message received")
    else:
        print("  firmware read not available in this ams-dig-proc version "
              "(git version provides it)")


def test_temperature(module: AMSSerialModule, kelvin: int, timeout_s: float) -> None:
    print(f"\n== 2. detector temperature -> {kelvin} K ==")
    module.workmode_stop()
    module.set_detector_temperature(kelvin)
    print(f"  requested {kelvin} K, waiting up to {timeout_s:.0f} s ...")
    t0 = time.monotonic()
    last_print = 0.0
    module.flush_input()
    while time.monotonic() - t0 < timeout_s:
        msg = module.read_one_message(msgClasses=MessageStatus, timeout=0.5)
        now = time.monotonic()
        if msg is not None:
            if now - last_print > 2.0 or msg.temperature_ok:
                print(f"  T = {msg.detector_temperature / 1000:.2f} K "
                      f"(ok={bool(msg.temperature_ok)})")
                last_print = now
            if msg.temperature_ok:
                print("  temperature stable")
                return
    print("  TIMEOUT waiting for stabilization (continuing anyway)")


def test_simulation(module: AMSSerialModule, n_samples: int = 1024) -> None:
    print("\n== 3. simulation workmode smoke test ==")
    raw = ((np.sin(np.linspace(0, 8 * np.pi, n_samples)) * 0.5 + 0.5) * 65535).astype(np.uint16)

    module.workmode_stop()
    module.processing_disable()
    module.workmode_simulation(list(map(int, raw)), SampleSize.SAMPLE_SIZE_16b, 0.0, 1)
    module.flush_input()

    received = 0
    reported_size = None
    first_values = None
    deadline = time.monotonic() + 5.0
    while received < 3 and time.monotonic() < deadline:
        msg = module.read_one_message(msgClasses=MessageOutputData, timeout=0.5)
        if msg is None:
            continue
        received += 1
        reported_size = msg.sample_size
        if first_values is None and len(msg.voltages) > 0:
            first_values = msg.voltages[:4]
    module.workmode_stop()

    if received == 0:
        print("  no simulation data received (skip? check sample size / period)")
    else:
        print(f"  received {received} data message(s), sample_size={reported_size}, "
              f"first voltages={np.round(first_values, 4) if first_values is not None else 'n/a'}")


def test_freerun_rate(module: AMSSerialModule, ratio: int, duration_s: float,
                      outdir: str | None) -> dict:
    effective_rate = PHYSICAL_RATE / ratio
    # NOTE: the board delivers one output buffer LESS than the number of
    # requested input blocks (empirically verified on hardware: 27 blocks ->
    # 26 buffers, 1 block -> 0 buffers). Request one extra input block so
    # exactly `out_blocks` output buffers come back.
    out_blocks = int(np.ceil(duration_s * effective_rate / OUTPUT_BUFFER_SAMPLES))
    out_samples = out_blocks * OUTPUT_BUFFER_SAMPLES
    in_samples = (out_blocks + 1) * OUTPUT_BUFFER_SAMPLES * ratio

    module.workmode_stop()
    module.processing_disable()
    module.processing_oversampling(0, ratio, OUTPUT_BUFFER_SAMPLES)
    module.set_samplerate(PHYSICAL_RATE)
    module.flush_input()

    baseline = drain_status(module, 0.5)
    overflow_before = baseline.data_overflow_counter if baseline else 0

    chunks: list[np.ndarray] = []
    counters: list[int] = []
    sample_size = None
    overflow_seen = overflow_before
    t_first = None

    t_cmd = time.monotonic()
    module.workmode_freerunning(in_samples)
    deadline = t_cmd + duration_s + 15.0

    total_samples = 0
    t_last = None
    board_idle_since = None
    while total_samples < out_samples and time.monotonic() < deadline:
        msg = module.read_one_message(msgClasses=[MessageOutputData, MessageStatus], timeout=0.2)
        if msg is None:
            # stop early once the board has stopped sampling and data is drained
            if board_idle_since is not None and time.monotonic() - board_idle_since > 2.0:
                break
            continue
        if isinstance(msg, MessageOutputData):
            now = time.monotonic()
            if t_first is None:
                t_first = now
            t_last = now
            board_idle_since = None
            arr = np.asarray(msg.voltages, dtype=np.float64)
            chunks.append(arr)
            counters.append(int(msg.counter))
            sample_size = int(msg.sample_size)
            total_samples += len(arr)
        elif isinstance(msg, MessageStatus):
            overflow_seen = max(overflow_seen, msg.data_overflow_counter)
            if int(msg.sampling_state) == 0:
                if board_idle_since is None:
                    board_idle_since = time.monotonic()
            else:
                board_idle_since = None

    module.workmode_stop()

    # gap analysis on the 1-byte buffer counter (increments once per data message)
    gaps = 0
    for prev, cur in zip(counters, counters[1:]):
        if (cur - prev) % 256 != 1:
            gaps += 1

    data = np.concatenate(chunks) if chunks else np.array([])
    data_window = (t_last - t_first) if (t_first and t_last and t_last > t_first) else 0.0
    result = {
        "ratio": ratio,
        "effective_rate": effective_rate,
        "sample_size_bytes": sample_size,
        "expected_samples": out_samples,
        "received_samples": total_samples,
        "wall_s": (t_last if t_last is not None else time.monotonic()) - t_cmd,
        "data_window_s": data_window,
        "measured_rate": (total_samples / data_window) if data_window > 0 else 0.0,
        "first_data_latency_s": (t_first - t_cmd) if t_first else float("nan"),
        "counter_gaps": gaps,
        "overflow_delta": overflow_seen - overflow_before,
        "mean_V": float(data.mean()) if data.size else float("nan"),
        "pk_pk_V": float(data.max() - data.min()) if data.size else float("nan"),
    }

    print(f"  ratio={ratio:5d} rate={effective_rate:9.2f} Hz  sample_size={sample_size}B  "
          f"got {total_samples}/{out_samples} samples in {result['wall_s']:.2f}s  "
          f"measured={result['measured_rate']:.1f} Hz  latency={result['first_data_latency_s']:.3f}s  "
          f"gaps={gaps}  overflow+={result['overflow_delta']}  "
          f"mean={result['mean_V']:.3f}V pk-pk={result['pk_pk_V']:.3f}V")

    if outdir and data.size:
        os.makedirs(outdir, exist_ok=True)
        path = os.path.join(outdir, f"capture_ratio{ratio}.csv")
        np.savetxt(path, data, delimiter=",", header="voltage_V", comments="")
        print(f"  saved {path}")

    return result


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def main() -> int:
    parser = argparse.ArgumentParser(description="AMS-DIG-PROC bench validation")
    parser.add_argument("--port", default=None, help="serial port (default: autodetect)")
    parser.add_argument("--baudrate", type=int, default=1_000_000)
    parser.add_argument("--temp", type=int, default=0,
                        help="detector temperature to set [K]; 0 = do not touch")
    parser.add_argument("--temp-timeout", type=float, default=600.0,
                        help="max wait for temperature stabilization [s]")
    parser.add_argument("--status-only", action="store_true")
    parser.add_argument("--no-sim", action="store_true")
    parser.add_argument("--no-rate-test", action="store_true")
    parser.add_argument("--duration", type=float, default=2.0,
                        help="capture duration per rate test [s]")
    parser.add_argument("--reset-board", action="store_true",
                        help="reboot the board before testing "
                             "(reverts runtime config to the values stored in NVM)")
    parser.add_argument("--ratios", default=",".join(str(r) for r in CANDIDATE_RATIOS),
                        help="comma-separated oversampling ratios to test")
    parser.add_argument("--outdir", default=None,
                        help="if set, save each capture as CSV in this directory")
    args = parser.parse_args()

    port = args.port or autodetect_port()
    if not port:
        print("ERROR: no serial port given and none autodetected (/dev/ttyUSB*, /dev/ttyACM*).")
        print("Connect the AMS-DIG-USB adapter and retry, or pass --port.")
        return 2
    check_port_access(port)
    apply_posix_compat()

    print(f"AMS-DIG-PROC bench | port={port} baud={args.baudrate} "
          f"python={sys.version.split()[0]}")

    module = None
    results = []
    try:
        module = AMSSerialModule(port=port, baudrate=args.baudrate)
        print("serial port opened")

        test_connection(module)
        if args.status_only:
            return 0

        if args.reset_board:
            print("\n== board reset (reverts runtime config to NVM values) ==")
            module.reset()
            time.sleep(4.0)
            module.clear_reset_flag()
            time.sleep(0.5)
            module.flush_input()
            print("  board rebooted")

        if args.temp > 0:
            test_temperature(module, args.temp, args.temp_timeout)

        if not args.no_sim:
            test_simulation(module)

        if not args.no_rate_test:
            print("\n== 4. freerunning rate tests ==")
            ratios = [int(r) for r in args.ratios.split(",") if r.strip()]
            for ratio in ratios:
                try:
                    results.append(test_freerun_rate(module, ratio, args.duration, args.outdir))
                except Exception as exc:
                    print(f"  ratio={ratio}: FAILED with {type(exc).__name__}: {exc}")

        if results:
            print("\n== summary ==")
            print(f"{'ratio':>6} {'rate[Sa/s]':>11} {'size[B]':>8} {'recv/exp':>13} "
                  f"{'meas[Sa/s]':>11} {'latency[s]':>10} {'gaps':>5} {'overflow':>8}")
            for r in results:
                print(f"{r['ratio']:>6} {r['effective_rate']:>11.2f} {r['sample_size_bytes']:>8} "
                      f"{r['received_samples']}/{r['expected_samples']:>6} "
                      f"{r['measured_rate']:>11.1f} {r['first_data_latency_s']:>10.3f} "
                      f"{r['counter_gaps']:>5} {r['overflow_delta']:>8}")
            print("\nInterpretation notes:")
            print("  - counter gaps > 0 and/or overflow+ > 0: UART cannot keep up at this rate")
            print("  - the board delivers one output buffer less than requested; script requests +1 block")
            print("  - measured rate is between first and last message and may read a few % high")
            print("    (USB/serial batching); trust received==expected and gaps==0 instead")
            print("  - use the fastest ratio whose received==expected, gaps==0, overflow==0")

        print("\nreader frames: correct=%d incorrect=%d" % (
            getattr(module.reader_thread, "total_correct_messages_count", 0),
            getattr(module.reader_thread, "total_incorrect_messages_count", 0)))
        print("\nbench finished")
        return 0

    except KeyboardInterrupt:
        print("\ninterrupted by user")
        return 1
    except Exception as exc:
        print(f"\nFATAL: {type(exc).__name__}: {exc}")
        print("Hints:")
        print("  - 'Problem with opening port' -> port busy (another program) or permissions")
        print("  - no data at all -> wrong port, wrong baudrate, or board not powered")
        return 1
    finally:
        cleanup(module)


if __name__ == "__main__":
    sys.exit(main())
