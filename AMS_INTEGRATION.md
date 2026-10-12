# AMS-DIG-PROC / AMIS8140-01 integration — progress handoff

**Date:** 2026-10-12 · **Repo:** `jmnich/FTSApplication`, branch `main`
**Status:** planning + hardware bench validation **COMPLETE**; the app driver (`ams_driver.py`) is **not yet implemented**.
**Purpose of this document:** allow another agent/developer to continue on a different computer without re-deriving anything.

---

## 1. Task

Add a new DAQ type to the FTS app: **AMIS8140-01** (VIGO Photonics InAsSb LWIR detection module) digitized by an **AMS-DIG-PROC** board (with AMS-DIG-USB adapter). The board has its own Python library: <https://gitlab.com/vigophotonics/ams-dig-proc> (PyPI: `ams-dig-proc`).

**User decision on architecture (already made, do not re-litigate):**
- The AMS card is a **single-channel detector** DAQ.
- The **reference laser interferogram comes from a separate interface** (MFLI or ADP2230), selectable by the user in the GUI.
- **Do not change how the app constructs interferograms or processes data** (keep the existing Hilbert-reference method, FFT pipeline, averaging, exports).
- One mathematically necessary exception is flagged in §5.2 (time-base alignment) — confirm it with the user before implementing.

## 2. What was already done

1. Full app analysis (architecture, bugs, risks) — see §9 for the short list relevant to the DAQ work.
2. `requirements.txt` created earlier (Python ≥ 3.12, tested 3.14.7). Includes the `setuptools==81.0.0` pin required by `norton_beer` (it imports the removed `pkg_resources`).
3. `ams-dig-proc>=0.0.9` added to `requirements.txt`.
4. `tools/ams_bench.py` written and **validated against real hardware**. It is the reference for all protocol facts below.
5. Hardware bench session (2026-10-12): connection, telemetry, temperature, data streaming all confirmed. Zaber + reference interface were not available; sensor is physically blinded (flat signal is expected).

## 3. Hardware / environment facts

| Item | Value |
|---|---|
| Board | AMS-DIG-PROC + AMS-DIG-USB adapter (FTDI 0403:6015), port `/dev/ttyUSB0` on the bench machine |
| UART | 1 000 000 baud, 8N1, COBS framing + CRC32 (CRC is checked by the library) |
| ADC | 16-bit @ 7 MS/s, output samples **32-bit (4 B)** after the processing pipeline |
| Detector | AMIS8140-01, TEC-stabilized; bench unit holds **250.61 K**, `temp_ok=True` |
| Permissions | Arch/CachyOS: tty nodes are `root:uucp`; on the bench machine logind granted a per-user ACL. On another machine the user may need `sudo usermod -aG uucp $USER` + relogin (Debian/Ubuntu: group `dialout`) |
| Library | `ams-dig-proc 0.0.9` (PyPI). Git `main` (0.1.0) adds only firmware/UUID/bootloader helpers — acquisition API is identical. Both verified importable on Python 3.14. |

## 4. Bench-verified protocol facts (CRITICAL — these drive the driver design)

1. **POSIX bug in the vendor library:** `ReaderThread._open_port()` calls `serial.Serial.set_buffer_size()`, which exists **only in pyserial's Windows backend** (`serialwin32.py`). On Linux/macOS this raises `AttributeError` and the port never opens. Required shim (already in `tools/ams_bench.py`):
   ```python
   import serial
   if not hasattr(serial.Serial, "set_buffer_size"):
       serial.Serial.set_buffer_size = lambda self, rx_size=None, tx_size=None: None
   ```
2. **`samples_count` in `workmode_freerunning(samples_count)` = ADC (input) samples at the physical rate.** Verified: 14 155 776 samples took exactly ≈2.0 s at 7 MS/s.
3. **The board delivers one output buffer LESS than requested** (input blocks `N` → output buffers `N-1`; `N=1` → 0 buffers). The driver must request **+1 block** and then trim. Verified multiple times.
4. **Output `sample_size = 4 B`** (32-bit post-processing data).
5. **Rate table at 1 Mbit/s** (physical 7 MS/s ÷ oversampling ratio, ratio must be a power of 2):

   | effective rate | ratio | data rate @4 B | verdict |
   |---|---|---|---|
   | 27 343.75 Hz | 256 | 109 kB/s | **NOT usable** (2 buffers lost in a 3 s capture) |
   | **13 671.875 Hz** | **512** | **54.7 kB/s** | **clean — use as fastest default** |
   | 6 835.9375 Hz | 1024 | 27.3 kB/s | clean |
   | 3 417.97 Hz | 2048 | 13.7 kB/s | (not yet measured, expected clean) |
   | 1 708.98 Hz | 4096 | 6.8 kB/s | (expected clean) |

   Proposed driver constant: `SamplingRates = (13671.875, 6835.9375, 3417.96875, 1708.984375, 854.4921875)` (descending, matching the app's MFLI-style list).
6. **Integrity check:** at ratio 256, `data_overflow_counter` stayed 0 while buffers were lost — the **1-byte `MessageOutputData.counter` gap check is the reliable integrity signal**, not the overflow counter. Driver must fail a scan on counter gaps.
7. **Start latency** (command → first data message): 0.17 s @ ratio 256, **0.25 s @ 512**, 0.41 s @ 1024. Comfortably inside the app's 0.5 s scan margins.
8. **Temperature:** `set_detector_temperature(K)` is a runtime setting; setpoint apparently persisted across a board reboot on the bench unit (250.61 K before and after). `reset()` temporarily shows a bogus high reading (299.96 K) that settles back. Do not `save_config()` without the user's intent (writes NVM).
9. **Simulation workmode** smoke test: data messages arrive, but a test sine encoded as uint16 raw samples did not round-trip (came back flat full-scale). Not needed for the app; revisit only if useful.
10. No CRC/frame errors observed (`incorrect=0` on all clean runs).

**Bench summary (final run, `--duration 3`):**
```
ratio  rate[Sa/s]  size[B]   recv/exp   gaps  overflow   latency[s]
  256    27343.75        4  79872/83968    2        0       0.166   <- saturated link
  512    13671.88        4  43008/43008    0        0       0.253   <- best clean
 1024     6835.94        4  22528/22528    0        0       0.407
```

## 5. Agreed integration design

### 5.1 Architecture — dual DAQ

```
BackgroundController
 ├─ DAQDriver      (detector, e.g. AMSSerialDriver)        ── captures y(t_det)
 └─ DAQRefDriver   (reference: MFLI or ADP2230, optional)  ── captures ref(t_ref)
      per scan: move mirror → start detector capture in a helper thread,
                run reference capture in the controller thread, join,
                align time bases, then call the UNCHANGED
                DataProcessor.analyzeDataHilbertInterpolation(ref, y_aligned, window)
```

- New capability flag in `daq_base.DAQDriver`: `ProvidesReferenceChannel = True` (AMS sets `False`).
- `Internal` reference mode = today's behavior (one driver provides both channels; MFLI/ADP2230/Simulated).
- `AMIS8140` main DAQ forces an external reference choice.
- Trigger mode disabled in dual mode for v1 (no shared hardware trigger).

### 5.2 The one processing addition — time-base alignment (needs user confirmation)

The DAQs cannot share a sample rate (MFLI list: 14 600 Hz… ; AMS: 13 671.875 Hz — no intersection). Without alignment the x-axis would be off by ~7 %. Minimal fix proposed (≈10 lines in `background_controller.py`, `data_processor.py` untouched):

```python
t_ref = np.arange(n_ref) / fs_ref
t_det = np.arange(n_det) / fs_det
overlap  = (t_ref >= t_det[0]) & (t_ref <= t_det[-1])   # crops start jitter + tail
y_aligned = np.interp(t_ref[overlap], t_det, y_det)
ref_crop  = ref[overlap]
```
Justification: constant start offset only shifts x-origin (phase — magnitude spectra unaffected); ppm-level clock drift over a 5 s scan is negligible; margins cover the rest. **Flag this to the user before implementing** — they asked not to change processing and may prefer this handled differently.

### 5.3 GUI / settings changes (planned)

- Main combo `daqTypeCombo`: add `AMIS8140` (existing: `MFLI` / `ADP2230` / `Simulated`).
- New combo **"Reference source"**: `Internal (same DAQ)` / `MFLI` / `ADP2230`; `Internal` disabled when main = AMIS8140.
- New "Ref. DAQ" readiness status label; `_updateCaptureButtons` requires all selected DAQs ready + delay line.
- AMS controls in the Conn tab: serial port combo + refresh (reuse Zaber pattern), detector temperature entry, "wait for stabilization" switch, live temperature label.
- Per-DAQ sampling-rate combos; settings keys: existing `mfliSelectedFrequencyIndex` (reference/legacy types) + new `amsSelectedFrequencyIndex`.
- New settings defaults in `settings_manager.py`: `referenceDaqType:"Internal"`, `amsPort:"/dev/ttyUSB0"`, `amsBaudrate:"1000000"`, `amsDetectorTemperature:"250"` (bench unit value — confirm), `amsWaitForTemperature:"False"`, `amsSelectedFrequencyIndex`.
- Lazy-import the AMS driver in `main.py` (`try/except ImportError`) so the app still starts without `ams-dig-proc` installed.

### 5.4 Driver skeleton (`ams_driver.py`, to implement)

```python
class AMSSerialDriver(DAQDriver):
    PhysicalSampleRate   = 7_000_000
    OutputBufferSamples  = 2048          # output samples per message
    SamplingRates        = (13671.875, 6835.9375, 3417.96875, 1708.984375, 854.4921875)
    ProvidesReferenceChannel = False
    SupportsTriggerMode      = False
    SampleCountQuantum       = 2048

    # tryConnect(port, serverHost=None): POSIX shim; workmode_stop; drain status; temp set/wait (optional)
    # configureForMeasurement(samplingFreqIndex, sampleLength, ...):
    #     ratio = 7e6 / rate; out_blocks = ceil(sampleLength / 2048)
    #     in_samples = (out_blocks + 1) * 2048 * ratio
    #     workmode_stop → processing_disable → processing_oversampling(0, ratio, 2048) → set_samplerate(7e6)
    # measureDataStandaloneMethod():
    #     flush_input → workmode_freerunning(in_samples)
    #     read [MessageOutputData, MessageStatus] until out_samples collected
    #       or board stops (sampling_state==0) or deadline (in_samples/7e6 + margin)
    #     verify: buffer-counter gaps == 0, overflow delta == 0 → else return error string
    #     lastInterferogramData = concatenated voltages (trimmed); lastReferenceData = None
    #     workmode_stop() in finally
    # disconnect(): workmode_stop; reader_thread.stop(); reader_thread.ser.close()
```

## 6. Files changed so far

| File | State |
|---|---|
| `requirements.txt` | **modified (uncommitted)**: `ams-dig-proc>=0.0.9` added below `zhinst` |
| `tools/ams_bench.py` | **new**: bench validation script (see §7) |
| `AMS_INTEGRATION.md` | **new**: this document |
| `app_settings.txt` | modified by local test runs (runtime state — intentionally not committed) |

`tools/ams_bench.py` already contains: autodetect port, access check, POSIX shim, status/temperature/simulation tests, freerun rate tests with the +1-block compensation, counter-gap & overflow reporting, `--reset-board`, `--outdir` CSV dumps.

## 7. Running the bench on a new machine

```bash
cd <repo>
python3.14 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# quick sanity:
python tools/ams_bench.py --status-only

# full bench:
python tools/ams_bench.py --duration 3 --outdir /tmp/ams_bench
```

Expected health: status message within 2 s; `T≈250.6 K ok=True`; ratio 512/1024 complete (`recv==exp`, `gaps=0`); ratio 256 may show gaps (expected, do not use).

## 8. Next steps (ordered)

1. **Confirm with the user:** (a) time-base alignment approach (§5.2), (b) detector operating temperature for the settings default, (c) DAQ type display name (`AMIS8140`).
2. Implement `ams_driver.py` (§5.4) + `ProvidesReferenceChannel` flag in `daq_base.py`.
3. `background_controller.py`: second driver, parallel capture, alignment helper, temperature hook.
4. `main.py`: combos, status labels, AMS controls, per-DAQ rate combos, lazy import.
5. `settings_manager.py`: new defaults.
6. Bench end-to-end with Zaber + reference (Zaber not yet available during first bench; sensor was blinded).
   - Cross-validate spectra: same scan captured with MFLI-only vs AMS-detector + MFLI-reference.
7. Optional later: adjustment-tool support, unit tests with a fake serial replaying CRC-valid frames, commit/push.

## 9. Related app issues found during the earlier full analysis (out of scope, for awareness)

- Capture buttons not disabled during a running measurement → concurrent measurement threads possible (high impact).
- `data_export_tool.exportAllDataMultipleMeasurements` crashes (`IndexError`) when "Save results" is clicked before any measurement.
- MFLI device ID parse crash when the ID box has no `;` separator.
- `messagebox` called from worker thread in `background_controller.measurementsWork`.
- Zaber serial has no read timeout → can hang.
- `iconbitmap(default=...)` is Windows-only but still called in `absorbanceTool.py`.

## 10. Open questions

- Does the user want detector temperature written to NVM (`save_config`) or runtime-only? (current recommendation: runtime-only)
- Should higher effective rates (ratio ≤ 256) be exposed as "experimental" for short bursts? (bench says not reliable)
- UART baud raise via `MessageConfigInterfaces.uartBaud` is possible but untested — only if higher rates are needed later.
