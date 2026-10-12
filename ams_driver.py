import logging
import time

import numpy as np

import serial

from daq_base import DAQDriver

# ---------------------------------------------------------------------------
# pyserial compatibility patch
#
# The VIGO library calls Serial.set_buffer_size(), which only exists in
# pyserial's Windows backend (serialwin32). On Linux/macOS this raises
# AttributeError while opening the port, so patch it to a no-op.
# ---------------------------------------------------------------------------
if not hasattr(serial.Serial, "set_buffer_size"):
    serial.Serial.set_buffer_size = lambda self, rx_size=None, tx_size=None: None

try:
    from ams_module import AMSSerialModule
    from ams_messages import MessageOutputData, MessageStatus, MessageIds, MessageConfigDetectorTemperature

    AMS_LIBRARY_AVAILABLE = True
except ImportError:
    AMSSerialModule = None
    MessageOutputData = None
    MessageStatus = None
    MessageIds = None
    MessageConfigDetectorTemperature = None
    AMS_LIBRARY_AVAILABLE = False


class AMSSerialDriver(DAQDriver):
    """DAQ driver for the AMIS8140-01 detector digitized by an AMS-DIG-PROC
    board (VIGO Photonics).

    The board has a single ADC channel, so it provides the detector signal
    only - the laser reference interferogram must be acquired with a separate
    reference DAQ (MFLI or ADP2230).

    Protocol details verified on hardware (2026-10-12):
      - samples_count passed to workmode_freerunning() counts ADC (input)
        samples at the physical sample rate (7 MS/s).
      - the board returns one output buffer LESS than the number of requested
        input blocks, therefore one extra block is always requested.
      - output samples are 32 bit (4 bytes).
      - at 1 Mbit/s the fastest fully clean stream is 13671.875 Sa/s
        (oversampling ratio 512, 54.7 kB/s); ratio 256 loses buffers.
      - lost buffers are detected via gaps of the per-message buffer counter
        (the data_overflow_counter stays at zero in that case).
    """

    PhysicalSampleRate = 7.0E6          # [Sa/s]
    ProcessingBufferSamples = 2048      # samples in one output message

    # effective output rate = PhysicalSampleRate / oversampling ratio
    # (ratio is a power of two, see module docstring)
    SamplingRates = (13671.875, 6835.9375, 3417.96875, 1708.984375, 854.4921875)

    # capability flags used by the application layer
    ProvidesReferenceChannel = False
    SupportsTriggerMode = False

    # On firmware v1.1.0 the status field detector_temperature carries three
    # different readings in turn; only one of them follows the setpoint (bench
    # 2026-10-12: ~250.6 K and ~268 K stay put, the third locks to the
    # setpoint). The board's temperature_ok flag never asserted while locked.
    # The window must stay narrower than the gap to the other two readings.
    SetpointTrackingWindowK = 2.0
    SetpointLockToleranceK = 0.5
    SetpointLockSamplesRequired = 5
    SetpointTrackingStaleAfter = 15.0   # [s], readings rotate every ~3 s

    PortOpenSettleTime = 1.0            # [s]

    def __init__(self):
        super().__init__()

        self.module = None
        self.port = None

        self.oversamplingRatio = None
        self.inputSamplesCount = 0

        self.measurementInProgress = False
        self.monitoringModeActive = False
        self.detectorTemperatureK = None
        self.detectorTemperatureOk = None
        self.detectorTemperatureSetpoint = None
        self.setpointReadPending = False
        self.trackingSetpoint = None
        self.trackedTemperatureK = None
        self.trackedTemperatureTime = None
        self.trackedLockStreak = 0
        self.dataOverflowCounter = 0
        self.messagesReceivedCounter = 0

        # configured from the application before performInitialization()
        self.configuredDetectorTemperature = 0.0
        self.waitForTemperatureStabilization = False
        self.temperatureWaitTimeout = 600.0

    # ------------------------------------------------------------------
    # configuration setters used by the GUI
    # ------------------------------------------------------------------

    def setDetectorTemperature(self, temperatureInK):
        self.configuredDetectorTemperature = float(temperatureInK)

    def setWaitForTemperature(self, waitEnabled):
        self.waitForTemperatureStabilization = bool(waitEnabled)

    # ------------------------------------------------------------------
    # connection handling
    # ------------------------------------------------------------------

    def tryConnect(self, port, serverHost=None):
        if not AMS_LIBRARY_AVAILABLE:
            logging.error("AMS driver: ams-dig-proc library is not installed")
            self.isConnected = False
            return False

        self.port = port
        logging.info(f"AMS driver trying to connect to: {port}")

        try:
            self.module = AMSSerialModule(port=port, baudrate=1_000_000)
        except Exception as e:
            print(f"AMS-DIG-PROC connection failed: {e}")
            logging.info(f"AMS driver: connection failed with message: {e}")
            self.module = None
            self.isConnected = False
            return False

        logging.info("AMS driver: connected")
        self.isConnected = True

        # opening the port resets the board's runtime configuration (setpoint
        # back to the NVM value) and commands sent in the first ~0.5 s are lost
        time.sleep(self.PortOpenSettleTime)

        # make sure the board does not stream stale data from an earlier session
        try:
            self.module.workmode_stop()
            time.sleep(0.1)
            self.refreshStatus()
        except Exception as e:
            logging.info(f"AMS driver: initial cleanup failed: {e}")

        # the setpoint is needed to pick the controlled reading out of the
        # status messages
        self.readDetectorTemperatureSetpoint()

        return True

    def postConnectSetup(self, statusCallback=None):
        """Optional hook called by the background controller after a
        successful connection. Applies the detector temperature settings."""
        if self.configuredDetectorTemperature > 0:
            logging.info(f"AMS driver: setting detector temperature to "
                         f"{self.configuredDetectorTemperature} K")
            try:
                self.module.set_detector_temperature(int(self.configuredDetectorTemperature))
                self._setKnownSetpoint(int(self.configuredDetectorTemperature))
            except Exception as e:
                logging.info(f"AMS driver: setting detector temperature failed: {e}")

        if self.configuredDetectorTemperature > 0 and self.waitForTemperatureStabilization:
            return self.waitForTemperatureStabilizationNow(statusCallback)

        return True

    def waitForTemperatureStabilizationNow(self, statusCallback=None):
        logging.info("AMS driver: waiting for detector temperature stabilization")
        if statusCallback is not None:
            statusCallback("Stabilizing detector temperature...")

        deadline = time.monotonic() + self.temperatureWaitTimeout
        self.module.flush_input()

        # the board's temperature_ok flag is not usable on firmware v1.1.0, so
        # stability is judged from the reading that follows the setpoint
        while time.monotonic() < deadline:
            msg = self.module.read_one_message(
                msgClasses=[MessageStatus, MessageConfigDetectorTemperature], timeout=0.5)
            if msg is None:
                continue

            self._handleNonDataMessage(msg)

            if statusCallback is not None and self.trackedTemperatureK is not None:
                statusCallback(f"Detector temperature: {self.trackedTemperatureK:.2f} K")

            if self.isSetpointLocked():
                logging.info("AMS driver: detector temperature stabilized")
                return True

        logging.info("AMS driver: temperature stabilization wait timed out")
        return False

    def refreshStatus(self):
        """Non-blocking drain of pending status messages; updates cached
        temperature and counter values. Never mixes with a running capture."""
        if self.module is None:
            return None

        lastMessage = None
        while True:
            msg = self.module.read_one_message(
                msgClasses=[MessageStatus, MessageConfigDetectorTemperature],
                block=False, timeout=0.1)
            if msg is None:
                break
            lastMessage = msg
            self._handleNonDataMessage(msg)

        return lastMessage

    def _handleNonDataMessage(self, message):
        """Central dispatch for non-data messages (status / config responses).

        Every reader in this driver passes such messages here, so no reader
        discards information another one is waiting for (e.g. the temperature
        setpoint response or the periodic status required for the live
        temperature display)."""
        if MessageStatus is not None and isinstance(message, MessageStatus):
            self._updateCachedStatus(message)
        elif MessageConfigDetectorTemperature is not None and \
                isinstance(message, MessageConfigDetectorTemperature):
            self._setKnownSetpoint(int(message.temperature))
            self.setpointReadPending = False

    def _setKnownSetpoint(self, setpointK):
        if setpointK != self.trackingSetpoint:
            self.trackedTemperatureK = None
            self.trackedTemperatureTime = None
            self.trackedLockStreak = 0
        self.trackingSetpoint = setpointK
        self.detectorTemperatureSetpoint = setpointK

    def _updateTrackedTemperature(self, temperatureK):
        setpoint = self.trackingSetpoint
        if not setpoint or abs(temperatureK - setpoint) > self.SetpointTrackingWindowK:
            return

        now = time.monotonic()
        if self.trackedTemperatureTime is None or \
                now - self.trackedTemperatureTime > self.SetpointTrackingStaleAfter:
            self.trackedLockStreak = 0

        if abs(temperatureK - setpoint) <= self.SetpointLockToleranceK:
            self.trackedLockStreak += 1
        else:
            self.trackedLockStreak = 0

        self.trackedTemperatureK = temperatureK
        self.trackedTemperatureTime = now

    def isTrackedTemperatureFresh(self):
        return self.trackedTemperatureTime is not None and \
            time.monotonic() - self.trackedTemperatureTime <= self.SetpointTrackingStaleAfter

    def isSetpointLocked(self):
        return self.isTrackedTemperatureFresh() and \
            self.trackedLockStreak >= self.SetpointLockSamplesRequired

    def _updateCachedStatus(self, statusMessage):
        self.detectorTemperatureK = statusMessage.detector_temperature / 1000.0
        self.detectorTemperatureOk = bool(statusMessage.temperature_ok)
        self._updateTrackedTemperature(self.detectorTemperatureK)

        self.dataOverflowCounter = int(statusMessage.data_overflow_counter)
        self.messagesReceivedCounter = int(statusMessage.messages_received_counter)

    def disconnect(self):
        try:
            if self.module is not None:
                self.module.workmode_stop()
                self.module.reader_thread.stop()
                self.module.reader_thread.ser.close()
        except Exception as e:
            logging.info(f"AMS driver: disconnect cleanup failed: {e}")
        finally:
            self.module = None
            self.isConnected = False
            logging.info("AMS driver: disconnected")

    # ------------------------------------------------------------------
    # detector level tool support (temperature setpoint + live monitoring)
    # ------------------------------------------------------------------

    def setDetectorTemperatureNow(self, temperatureInK):
        """Applies a detector temperature setpoint immediately (no NVM write)."""
        if self.module is None:
            return False
        try:
            self.module.set_detector_temperature(int(temperatureInK))
            self._setKnownSetpoint(int(temperatureInK))
            logging.info(f"AMS driver: detector temperature set to {temperatureInK} K")
            return True
        except Exception as e:
            logging.info(f"AMS driver: setting detector temperature failed: {e}")
            return False

    def requestDetectorTemperatureSetpointRead(self):
        """Requests the detector temperature setpoint from the board.

        The response is picked up by whichever message reader is currently
        active (status poll, level monitor or measurement loop) and stored in
        self.detectorTemperatureSetpoint; setpointReadPending is cleared."""
        if self.module is None or MessageIds is None:
            return False

        try:
            self.detectorTemperatureSetpoint = None
            self.setpointReadPending = True
            self.module.read_config(MessageIds.MESSAGE_CONFIGURE_DETECTOR_TEMPERATURE)
            return True
        except Exception as e:
            self.setpointReadPending = False
            logging.info(f"AMS driver: setpoint read request failed: {e}")
            return False

    def readDetectorTemperatureSetpoint(self, timeout=2.0):
        """Reads the detector temperature setpoint currently stored on the
        board [K] (0 = controller disabled). Returns None if not readable.

        Safe to use while the level monitor or a measurement is running: the
        response is consumed and stored by the active reader loop."""
        if self.module is None or MessageIds is None:
            return None

        if not self.requestDetectorTemperatureSetpointRead():
            return None

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and self.setpointReadPending:
            if not self.monitoringModeActive and not self.measurementInProgress:
                # no other reader is active: pick the response up here
                msg = self.module.read_one_message(
                    msgClasses=[MessageStatus, MessageConfigDetectorTemperature], timeout=0.2)
                if msg is not None:
                    self._handleNonDataMessage(msg)
            else:
                time.sleep(0.05)

        if self.setpointReadPending:
            logging.info("AMS driver: setpoint read timed out")
            return None

        return self.detectorTemperatureSetpoint

    def beginLevelMonitoring(self, samplingFreqIndex=0):
        """Starts a continuous freerunning capture for the detector level tool.
        Returns None on success or an error string."""
        if self.module is None:
            return "AMS-DIG-PROC not connected"

        index = int(samplingFreqIndex)
        if not 0 <= index < len(self.SamplingRates):
            index = 0

        effectiveRate = self.SamplingRates[index]
        ratio = int(round(self.PhysicalSampleRate / effectiveRate))

        try:
            self.module.workmode_stop()
            self.module.processing_disable()
            self.module.processing_oversampling(0, ratio, self.ProcessingBufferSamples)
            self.module.set_samplerate(int(self.PhysicalSampleRate))
            self.module.flush_input()
            self.module.workmode_freerunning(0)     # continuous until stopped
        except Exception as e:
            self.monitoringModeActive = False
            self.measurementInProgress = False
            return f"monitoring start failed: {e}"

        self.monitoringModeActive = True
        # re-use the measurement flag so the GUI status poll keeps its hands
        # off the serial port while monitoring
        self.measurementInProgress = True
        self.currentMeasurementFrequency = effectiveRate

        logging.info(f"AMS driver: level monitoring started at {effectiveRate} Hz")
        return None

    def readLevelBlock(self, timeout=0.5):
        """Reads the next data buffer for level monitoring and returns its
        statistics: level (mean), pk_pk, std and sample count. None on timeout."""
        if self.module is None or not self.monitoringModeActive:
            return None

        msg = self.module.read_one_message(
            msgClasses=[MessageOutputData, MessageStatus, MessageConfigDetectorTemperature],
            timeout=timeout)
        if msg is None:
            return None

        if not isinstance(msg, MessageOutputData):
            # status / setpoint response: keep the caches warm, no data to report
            self._handleNonDataMessage(msg)
            return None

        data = np.asarray(msg.voltages, dtype=np.float64)
        if data.size == 0:
            return None

        return {
            "level": float(np.mean(data)),
            "pk_pk": float(np.max(data) - np.min(data)),
            "std": float(np.std(data)),
            "samples": int(data.size),
        }

    def endLevelMonitoring(self):
        self.monitoringModeActive = False
        self.measurementInProgress = False
        if self.module is not None:
            try:
                self.module.workmode_stop()
            except Exception:
                pass
        logging.info("AMS driver: level monitoring stopped")

    # ------------------------------------------------------------------
    # DAQDriver interface
    # ------------------------------------------------------------------

    def configureForMeasurement(self, samplingFreqIndex, sampleLength, triggerEnabled, triggerLevel,
                                triggerReference, triggerHysteresis):
        if self.module is None:
            return

        if triggerEnabled:
            logging.info("AMS driver: trigger mode is not supported, "
                         "continuing with untriggered acquisition")

        index = int(samplingFreqIndex)
        if not 0 <= index < len(self.SamplingRates):
            index = 0

        effectiveRate = self.SamplingRates[index]
        self.oversamplingRatio = int(round(self.PhysicalSampleRate / effectiveRate))
        self.currentMeasurementFrequency = effectiveRate

        # the board delivers one output buffer less than requested, so one
        # extra input block is requested and trimmed after the capture
        outputBlocks = int(np.ceil(float(sampleLength) / self.ProcessingBufferSamples))
        self.currentMeasurementPointsCount = outputBlocks * self.ProcessingBufferSamples
        self.inputSamplesCount = (outputBlocks + 1) * self.ProcessingBufferSamples * self.oversamplingRatio

        logging.info(f"AMS driver: configuration for measurement. "
                     f"effective rate: {effectiveRate} Hz, oversampling ratio: {self.oversamplingRatio}, "
                     f"output samples: {self.currentMeasurementPointsCount}, "
                     f"input samples: {self.inputSamplesCount}")

        self.module.workmode_stop()
        self.module.processing_disable()
        self.module.processing_oversampling(0, self.oversamplingRatio, self.ProcessingBufferSamples)
        self.module.set_samplerate(int(self.PhysicalSampleRate))
        self.module.flush_input()

    def armTrigger(self):
        logging.info("AMS driver: trigger mode is not supported")

    def measureDataWithPrearmedTrigger(self):
        return "AMS-DIG-PROC does not support triggered acquisition"

    def measureDataStandaloneMethod(self):
        if self.module is None:
            return "AMS-DIG-PROC not connected"

        expectedSamples = int(self.currentMeasurementPointsCount)
        if expectedSamples <= 0 or self.inputSamplesCount <= 0:
            return "invalid measurement configuration"

        overallStatus = "ok"
        self.measurementInProgress = True

        try:
            self.module.flush_input()
            self.refreshStatus()
            overflowBefore = self.dataOverflowCounter

            expectedDuration = self.inputSamplesCount / self.PhysicalSampleRate
            deadline = time.monotonic() + expectedDuration + 10.0

            self.module.workmode_freerunning(int(self.inputSamplesCount))

            chunks = []
            bufferCounters = []
            receivedSamples = 0
            bufferCounterGaps = 0
            previousCounter = None
            overflowSeen = overflowBefore
            boardIdleSince = None

            while receivedSamples < expectedSamples and time.monotonic() < deadline:
                msg = self.module.read_one_message(
                    msgClasses=[MessageOutputData, MessageStatus, MessageConfigDetectorTemperature],
                    timeout=0.2)

                if msg is None:
                    # stop early once the board has finished sampling and all
                    # data has been drained from the serial port
                    if boardIdleSince is not None and time.monotonic() - boardIdleSince > 2.0:
                        break
                    continue

                if isinstance(msg, MessageOutputData):
                    boardIdleSince = None
                    counter = int(msg.counter)
                    if previousCounter is not None and (counter - previousCounter) % 256 != 1:
                        bufferCounterGaps += 1
                    previousCounter = counter
                    bufferCounters.append(counter)
                    chunks.append(np.asarray(msg.voltages, dtype=np.float64))
                    receivedSamples += len(chunks[-1])
                else:
                    self._handleNonDataMessage(msg)
                    if isinstance(msg, MessageStatus):
                        overflowSeen = max(overflowSeen, msg.data_overflow_counter)
                        if int(msg.sampling_state) == 0:
                            if boardIdleSince is None:
                                boardIdleSince = time.monotonic()
                        else:
                            boardIdleSince = None

            overflowDelta = overflowSeen - overflowBefore

        finally:
            self.measurementInProgress = False
            try:
                self.module.workmode_stop()
            except Exception:
                pass

        if receivedSamples == 0:
            overallStatus = "acquisition timeout"

        if overallStatus == "ok" and (bufferCounterGaps > 0 or overflowDelta > 0):
            overallStatus = (f"data loss detected (buffer counter gaps: {bufferCounterGaps}, "
                             f"overflow counter increase: {overflowDelta})")

        if overallStatus == "ok" and receivedSamples < expectedSamples:
            overallStatus = "incomplete acquisition"

        if overallStatus != "ok":
            self.lastInterferogramData = None
            self.lastReferenceData = None
            logging.info(f"AMS driver: acquisition failed: {overallStatus}")
            return overallStatus

        data = np.concatenate(chunks)
        self.lastInterferogramData = data[:expectedSamples]
        self.lastReferenceData = None

        logging.info(f"AMS driver: acquired {len(self.lastInterferogramData)} samples "
                     f"({len(chunks)} buffers)")

        return "ok"
