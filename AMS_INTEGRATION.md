# AMS-DIG-PROC / AMIS8140-01 integration — progress handoff

**Date:** 2026-10-12 · **Repo:** `jmnich/FTSApplication`, branch `main`
**Status:** **implemented** — AMS driver, dual-DAQ controller, GUI integration are in place.
**Tested:** driver against physical hardware (exact sample counts), controller logic with synthetic data unit tests, app GUI smoke test. **Not yet tested:** full end-to-end measurement with Zaber + reference DAQ (equipment available 2026-10-13).
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
6. **Application integration implemented and partially tested (same day):**
   - `ams_driver.py` — full `DAQDriver` implementation; verified on hardware: connect, 2 s capture at 13 671.875 Hz returned exactly 28 672 samples in 2.41 s, reference is `None`, trigger path returns a clear error.
   - `background_controller.py` — dual-DAQ support: second (`DAQRefDriver`) driver, parallel capture (helper thread), time-base alignment (unit-tested: correlation 1.000000, error exactly at the theoretical interpolation bound), failure propagation.
   - `main.py` — new DAQ type `AMIS8140`, `Reference source` selection (Internal/MFLI/ADP2230), scrollable Conn tab, AMS port/temperature controls, reference status label, per-DAQ sampling-rate combos, trigger tab disabled for AMS/dual mode, live detector temperature display (2 s poll), safer MFLI ID parsing (missing `;` no longer crashes).
   - `settings_manager.py` — new defaults (`referenceDaqType`, `referenceSelectedFrequencyIndex`, `amsPort`, `amsBaudrate`, `amsDetectorTemperature`, `amsWaitForTemperature`, `amsSelectedFrequencyIndex`).
   - `detectorTool.py` — **detector level tool** window (button "Detector\ntool" in the top button column, enabled only when AMIS8140 is selected): set/read the detector temperature setpoint, live temperature + `temperature_ok` indicator (yes/no with consecutive-ok streak), live output level monitoring (mean/pk-pk/noise readouts + level-over-time plot) at a selectable effective rate; the main capture buttons are locked while monitoring; end-to-end tested against hardware.
   - `daq_base.py` — capability flags `ProvidesReferenceChannel`, `SupportsTriggerMode`.
   - App smoke test: GUI launches and runs with the new code.

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

## 4.1 Known issue: reported detector temperature rotates between three values (2026-10-12)

While the app is idle, `MessageStatus.detector_temperature` (1 message/s, all frames CRC-valid) jumps between three clusters:

| cluster | values observed |
|---|---|
| ~250.6 K | 250.591 … 250.651 |
| ~267.8 K | 267.748 … 267.812 |
| ~300.0 K | 299.960 (constant) |

This is physically implausible (no detector can swing 50 K per second); the values appear to round-robin and `temperature_ok` flips randomly regardless of the value. All other status fields (sampling_state, processing_state, data_overflow_counter, messages_received_counter) stay constant. The TEC setpoint read back via `MESSAGE_CONFIGURE_DETECTOR_TEMPERATURE` is **0** (the board's own temperature controller is disabled; the AMIS module's integrated controller may be doing the actual stabilization).

Evidence from a 90 s raw status capture; firmware **v1.1.0**, UUID `34002c001351333130343532`.

Impact / handling:
- The app displays the raw reported value faithfully (it may flicker between the clusters).
- "Wait until stable" was hardened to require **5 consecutive `temperature_ok` statuses** (`temperatureOkStreak` in `ams_driver.py`); the GUI shows "(stable)" only after a streak of 3.
- Recommended: ask VIGO support whether `detector_temperature` is valid on firmware v1.1.0 for the AMIS8140-01; send them the firmware version, UUID and the cluster table above. Cross-check the actual detector temperature mechanically if possible.

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
Justification: constant start offset only shifts x-origin (phase — magnitude spectra unaffected); ppm-level clock drift over a 5 s scan is negligible; margins cover the rest. **Implemented** as `BackgroundController.alignInterferogramsToReferenceTimeBase` (unit-tested against synthetic signals on the two real rate tables). Still worth an explicit confirmation with the user during the first live dual-DAQ test; the algorithm lives in one place, so changing it is cheap.

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

## 6. Files changed

| File | State |
|---|---|
| `ams_driver.py` | **new**: `AMSSerialDriver` — full `DAQDriver` implementation (posix shim, +1 block, gap/overflow checks, temperature) |
| `background_controller.py` | **modified**: dual-DAQ (`DAQRefDriver`, `SetReferenceDAQReadyFlagMethod`), parallel capture, time-base alignment, `postConnectSetup` hook |
| `main.py` | **modified**: `AMIS8140` DAQ type, Reference source selection, scrollable Conn tab, AMS controls, ref status, per-DAQ rate combos, trigger gating, live temperature poll, MFLI ID parse hardening |
| `daq_base.py` | **modified**: `ProvidesReferenceChannel`, `SupportsTriggerMode` flags |
| `settings_manager.py` | **modified**: reference + AMS defaults |
| `tools/ams_bench.py` | **new**: bench validation script (§7) |
| `requirements.txt` | **modified**: `ams-dig-proc>=0.0.9` |
| `AMS_INTEGRATION.md` | **new**: this document |
| `app_settings.txt` | runtime state, intentionally not committed |

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

## 8. Tomorrow's test procedure (Zaber + reference available)

1. Pull this repo, set up the venv (`python3.14 -m venv .venv`, `pip install -r requirements.txt`), connect Zaber + reference DAQ + AMS board.
2. Start the app; in the **Conn** tab:
   - `DAQ type` = **AMIS8140** (AMS port should already autodetect, e.g. `/dev/ttyUSB0`)
   - `Reference source` = **MFLI** (or ADP2230); enter the MFLI `devID;IP`
   - `Detector temp. [K]` = e.g. `250` if you want the app to set the setpoint (0 = don't touch)
   - click **Connect all** — status should show `READY` for General / DAQ system / Reference DAQ / Delay line
3. **Inspect ref. signal** button without capturing to verify the MFLI reference channel (checks DAQ plumbing).
4. Single capture with a short scan; verify:
   - interferogram/spectrum plots appear; no "alignment failed" messages
   - the x-axis scale is correct (compare with an MFLI-only capture of the same scan; same spectral features/scale, small phase differences are expected)
5. Then multiple captures with averaging, then Save results / Save .h5 (raw export gets the aligned ref+detector pair, unchanged format).
6. If the reference is a different device than planned earlier (`referenceSelectedFrequencyIndex` default 12 → 14 600 Hz), adjust in `Reference sampling freq.`.

Remaining/optional work after testing:
- Confirm the alignment approach with the user; consider cropping the Zaber ramp (crop setting) if the LWIR band edge shows artifacts.
- Optional: adjustment-tool support for AMS; fake-serial unit tests; docs/README updates.

## 9. Related app issues found during the earlier full analysis (out of scope, for awareness)

- Capture buttons not disabled during a running measurement → concurrent measurement threads possible (high impact).
- `data_export_tool.exportAllDataMultipleMeasurements` crashes (`IndexError`) when "Save results" is clicked before any measurement.
- MFLI device ID parse crash when the ID box has no `;` separator.
- `messagebox` called from worker thread in `background_controller.measurementsWork`.
- Zaber serial has no read timeout → can hang.
- `iconbitmap(default=...)` is Windows-only but still called in `absorbanceTool.py`.

## 10. Open questions

- Time-base alignment: implemented (§5.2); confirm during the first dual-DAQ measurement.
- Detector temperature: the app sets the setpoint only when the field is non-zero (runtime only, no NVM write) — confirm the desired operating value (the bench unit stabilizes at 250.61 K).
- Should higher effective rates (ratio ≤ 256) be exposed as "experimental" for short bursts? Bench says not reliable at 1 Mbit/s.
- UART baud raise via `MessageConfigInterfaces.uartBaud` is possible but untested — only if higher rates are needed later.
- Trigger mode: unavailable for AMS (single channel, external trigger only); dual-DAQ mode also disables the existing MFLI trigger path. Revisit if a shared trigger source is added.
