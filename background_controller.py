import time
from threading import *
from datetime import datetime

import numpy

from zaber_driver import ZaberDriver
from data_processor import DataProcessor
import logging
import numpy as np
import math

from tkinter import messagebox

class BackgroundController:

    def __init__(self, daqDrv, zaberDrv):
        print("Background controller created")
        self.stopRequestFlag = False

        self.DataAnalyzer = DataProcessor()
        self.orderedMeasurementsCount = 0
        self.mfliFrequencyIndex = 0
        self.mfliSamplesCount = 0
        self.scanStartPosition = 0
        self.scanLength = 0
        self.scanSpeed = 0
        self.triggerModeEnabled = False
        self.triggerLevel = None
        self.triggerHysteresis = None
        self.triggerReference = None
        self.selectedApodizationWindowType = None

        self.DAQDriver       = daqDrv
        self.ZaberDriver    = zaberDrv

        # external methods used for communication between the background controller and the GUI
        self.SetStatusMessageMethod         = None
        self.SetGeneralReadyFlagMethod      = None
        self.SetDAQReadyFlagMethod          = None
        self.SetDelayLineReadyFlagMethod    = None
        self.SetReferenceDAQReadyFlagMethod = None
        self.UploadNewDataMethod            = None
        self.SendResultsToPlot              = None
        self.NotifyAllMeasurementsDone      = None

        self.ZaberPort                      = None
        self.daqDeviceName                  = None
        self.daqIP                          = None
        self.daqType                        = None

        # optional separate DAQ providing the laser reference interferogram
        self.DAQRefDriver                   = None
        self.referenceDaqType               = "Internal"
        self.referenceDaqDeviceName         = None
        self.referenceDaqIP                 = None
        self.referenceSamplingFrequencyIndex = 0
        self.referenceSamplingFrequencyValue = None
        self.mfliSamplingFrequencyValue     = None
        self.dualDaqMode                    = False

        self.rawInterferograms = []
        self.rawReferenceSignals = []
        self.interferogramTimestamps = []
        self.processedInterferogramsX = []
        self.processedInterferogramsY = []
        self.spectraX = []
        self.spectraY = []
        self.averageSpectrumX = None
        self.averageSpectrumY = None

    def setZaberPort(self, port):
        self.ZaberPort = port

    def requestStop(self):
        self.stopRequestFlag = True

    def setDAQDeviceName(self, name):
        self.daqDeviceName = name

    def setDAQIP(self, ip):
        self.daqIP = ip

    def setReferenceDAQDriver(self, driver, daqType):
        self.DAQRefDriver = driver
        self.referenceDaqType = daqType

    def setReferenceDAQDeviceName(self, name):
        self.referenceDaqDeviceName = name

    def setReferenceDAQIP(self, ip):
        self.referenceDaqIP = ip

    def initializationWork(self):

        if self.daqType == "Simulated":
            self.SetStatusMessageMethod("Connecting to simulated DAQ...")

            if self.DAQDriver.tryConnect(self.daqDeviceName, serverHost=self.daqIP):
                self.SetDAQReadyFlagMethod(True)
            else:
                self.SetDAQReadyFlagMethod(False)

            self.SetDelayLineReadyFlagMethod(True)

            if self.DAQDriver.isConnected:
                self.SetStatusMessageMethod("Conneted to hardware")
                self.SetGeneralReadyFlagMethod(True)
            else:
                self.SetStatusMessageMethod("One or more hardware components\nfailed to connect")
                self.SetGeneralReadyFlagMethod(False)

            return

        if self.ZaberPort is None or self.daqDeviceName is None:
            self.SetStatusMessageMethod("Select ports and IDs\nfor hardware modules")
            return

        self.SetStatusMessageMethod("Connecting to hardware...")

        if self.DAQDriver.tryConnect(self.daqDeviceName, serverHost=self.daqIP):
            self.SetDAQReadyFlagMethod(True)
        else:
            self.SetDAQReadyFlagMethod(False)

        if self.DAQRefDriver is not None:
            self.SetStatusMessageMethod("Connecting to reference DAQ...")
            if self.DAQRefDriver.tryConnect(self.referenceDaqDeviceName, serverHost=self.referenceDaqIP):
                self.SetReferenceDAQReadyFlagMethod(True)
            else:
                self.SetReferenceDAQReadyFlagMethod(False)

        if self.ZaberDriver.tryConnect(self.ZaberPort):
            self.SetStatusMessageMethod("Homing...")
            self.ZaberDriver.home()
            self.ZaberDriver.waitUntilIdle()
            self.SetDelayLineReadyFlagMethod(True)
        else:
            self.SetDelayLineReadyFlagMethod(False)

        # optional driver specific setup (e.g. AMS detector temperature)
        if hasattr(self.DAQDriver, "postConnectSetup"):
            try:
                self.DAQDriver.postConnectSetup(self.SetStatusMessageMethod)
            except Exception as e:
                logging.info(f"Application controller: post connect setup failed: {e}")

        referenceConnected = (self.DAQRefDriver is None or self.DAQRefDriver.isConnected)

        if self.ZaberDriver.isConnected and self.DAQDriver.isConnected and referenceConnected:
            self.SetStatusMessageMethod("Conneted to hardware")
            self.SetGeneralReadyFlagMethod(True)
        else:
            self.SetStatusMessageMethod("One or more hardware components\nfailed to connect")
            self.SetGeneralReadyFlagMethod(False)


    def performInitialization(self):
        logging.info(f"Application controller: hardware initialization starting")
        t = Thread(target=self.initializationWork, daemon=True)
        t.start()


    def performMeasurements(self, measurementsCount, samplingFrequency, scanStart, scanLength, scanSpeed,
                            trigModeEnabled, trigLevel, trigHysteresis, trigReference, apodizationWindow,
                            referenceSamplingFrequency=None):
        logging.info(f"Application controller: measurement starting")

        # reset and configure the backgroung controller
        self.rawInterferograms.clear()
        self.rawReferenceSignals.clear()
        self.interferogramTimestamps.clear()
        self.processedInterferogramsX.clear()
        self.processedInterferogramsY.clear()
        self.spectraX.clear()
        self.spectraY.clear()
        self.averageSpectrumX = None
        self.averageSpectrumY = None

        self.orderedMeasurementsCount   = measurementsCount
        self.mfliFrequencyIndex         = samplingFrequency
        self.referenceSamplingFrequencyIndex = (referenceSamplingFrequency
                                                if referenceSamplingFrequency is not None
                                                else samplingFrequency)
        self.scanStartPosition          = scanStart
        self.scanLength                 = scanLength
        self.scanSpeed                  = scanSpeed
        self.selectedApodizationWindowType = apodizationWindow

        # trigger mode is not available in dual-DAQ mode and for DAQs without
        # hardware triggering support (e.g. the AMS-DIG-PROC)
        if self.DAQRefDriver is not None or not getattr(self.DAQDriver, "SupportsTriggerMode", True):
            trigModeEnabled = False

        self.triggerModeEnabled         = trigModeEnabled
        self.triggerLevel               = trigLevel
        self.triggerHysteresis          = trigHysteresis
        self.triggerReference           = trigReference

        self.stopRequestFlag            = False

        # start the actual measurement thread
        t = Thread(target=self.measurementsWork, daemon=True)
        t.start()


    def measurementsWork(self):
        self.SetStatusMessageMethod("Preparing...")

        if self.stopRequestFlag:
            self.stopRequestFlag = False
            self.SetStatusMessageMethod("Measurement stopped")
            return

        status = self.performAcqusition()

        if status == "ok":
            self.allMeasurementsDone()  # all ordered measurements are completed, terminate gracefully
        elif status == "stop":
            self.SetStatusMessageMethod("Measurement stopped")
        else:
            self.SetStatusMessageMethod("Measurement failed")
            messagebox.showerror('Measurement failed', status)


    def performAcqusition(self):

        if self.daqType == "Simulated":
            return self._performSimulatedAcquisition()

        self.ZaberDriver.waitUntilIdle()

        scanDuration = self.scanLength / (self.scanSpeed * 1000)

        mfliSamplingFrequency = self.DAQDriver.SamplingRates[self.mfliFrequencyIndex]
        self.mfliSamplingFrequencyValue = mfliSamplingFrequency
        self.mfliSamplesCount = scanDuration * mfliSamplingFrequency

        # configure DAQ
        self.DAQDriver.configureForMeasurement(samplingFreqIndex=self.mfliFrequencyIndex,
                                                sampleLength=self.mfliSamplesCount,
                                                triggerEnabled=self.triggerModeEnabled,
                                                triggerLevel=self.triggerLevel,
                                                triggerReference=self.triggerReference,
                                                triggerHysteresis=self.triggerHysteresis)

        # configure the optional separate reference DAQ
        self.dualDaqMode = self.DAQRefDriver is not None
        if self.dualDaqMode:
            self.referenceSamplingFrequencyValue = \
                self.DAQRefDriver.SamplingRates[self.referenceSamplingFrequencyIndex]
            self.referenceSamplesCount = scanDuration * self.referenceSamplingFrequencyValue

            self.DAQRefDriver.configureForMeasurement(
                samplingFreqIndex=self.referenceSamplingFrequencyIndex,
                sampleLength=self.referenceSamplesCount,
                triggerEnabled=False,
                triggerLevel=self.triggerLevel,
                triggerReference=self.triggerReference,
                triggerHysteresis=self.triggerHysteresis)

        time.sleep(1)

        failedAcquisitionsCount = 0
        failMessages = []

        # acquire all data
        i = 0
        while i < self.orderedMeasurementsCount:
            i += 1
        # for i in range(0, self.orderedMeasurementsCount):
            if failedAcquisitionsCount >= math.ceil(self.orderedMeasurementsCount * 0.2):

                if self.orderedMeasurementsCount == 1:
                    errstatus = "Single measurement failed"
                else:
                    errstatus = "More than 20% of the ordered measurements failed"

                return errstatus

            self.ZaberDriver.waitUntilIdle()
            time.sleep(0.25)  # wait to let the mirror settle

            if self.stopRequestFlag:
                self.stopRequestFlag = False
                self.SetStatusMessageMethod("Measurement stopped")
                return "stop"

            # calculate start and stop positions for the delay line
            # note: direction of scan from zaber motor to the other end
            preferred_margin = self.scanSpeed * 1000 * 0.5
            startPosition = self.scanStartPosition + preferred_margin
            endPosition = self.scanStartPosition - self.scanLength - preferred_margin

            # clamp the values
            if endPosition < 0:
                endPosition = 0

            if startPosition > self.ZaberDriver.DelayLineNominalLength:
                startPosition = self.ZaberDriver.DelayLineNominalLength

            # send the delay line to the starting position
            self.ZaberDriver.setPosition(position=startPosition, speed=ZaberDriver.MaxSpeed)

            # if trigger mode is enabled arm the trigger and wait for a moment until it takes effect
            if self.triggerModeEnabled:
                self.DAQDriver.armTrigger()
                # time.sleep(1.0)

            # wait until the mirror is in position
            self.ZaberDriver.waitUntilIdle()
            time.sleep(1.0)  # wait to let the mirror settle

            self.SetStatusMessageMethod("Acquisition...")
            # acquire data (note: zaber uses us/s, interface uses mm/s)
            # prepare scanning trajectory with a 0.1 sec marging if possible

            self.ZaberDriver.setPosition(endPosition, speed=self.scanSpeed * 1000)

            # if self.scanStartPosition-self.scanLength > preferred_margin:
            #     self.ZaberDriver.setPosition(position=self.scanStartPosition - self.scanLength - preferred_margin,
            #                                  speed=self.scanSpeed * 1000)
            # else:
            #     self.ZaberDriver.setPosition(position=self.scanStartPosition - self.scanLength,
            #                                  speed=self.scanSpeed * 1000)

            if self.dualDaqMode:
                measStatus, referenceStatus = self._acquireDualDaq()
            elif self.triggerModeEnabled:
                measStatus = self.DAQDriver.measureDataWithPrearmedTrigger()
                referenceStatus = "ok"
            else:
                measStatus = self.DAQDriver.measureDataStandaloneMethod()
                referenceStatus = "ok"

            self.ZaberDriver.waitUntilIdle()

            scanCompletionTimestamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")

            # try to complete the measurement even if acquisition fails a few times
            if measStatus != "ok" or referenceStatus != "ok":
                failedAcquisitionsCount += 1
                i -= 1
                failureMessage = measStatus if measStatus != "ok" else referenceStatus
                print("Measurement cycle skipped due to error: " + str(failureMessage))
                continue

            # send the delay line to the starting position while the calculations are running
            self.ZaberDriver.setPosition(position=startPosition, speed=ZaberDriver.MaxSpeed)

            self.SetStatusMessageMethod("Calculations...")

            if self.dualDaqMode:
                rawReferenceSignal, rawInterferogramSignal = self.alignInterferogramsToReferenceTimeBase(
                    rawReference=self.DAQRefDriver.lastReferenceData,
                    referenceSampleRate=self.referenceSamplingFrequencyValue,
                    rawInterferogram=self.DAQDriver.lastInterferogramData,
                    interferogramSampleRate=self.mfliSamplingFrequencyValue)

                if rawReferenceSignal is None:
                    self.SetStatusMessageMethod("Interferogram alignment failed")
                    failedAcquisitionsCount += 1
                    i -= 1
                    print("Interferogram alignment failed")
                    continue
            else:
                rawReferenceSignal = np.copy(self.DAQDriver.lastReferenceData)
                rawInterferogramSignal = np.copy(self.DAQDriver.lastInterferogramData)

            try:
                results = self.DataAnalyzer.analyzeDataHilbertInterpolation(rawReferenceSignal=rawReferenceSignal,
                                                        rawInterferogram=rawInterferogramSignal,
                                                        apodizationWindowType=self.selectedApodizationWindowType)
            except:
                self.SetStatusMessageMethod("Data acquisition or analysis failed")
                failedAcquisitionsCount += 1
                i -= 1
                print("Data acquisition or analysis failed due to exception")
                continue

            self.rawInterferograms.append(np.copy(rawInterferogramSignal))
            self.rawReferenceSignals.append(np.copy(rawReferenceSignal))
            self.interferogramTimestamps.append(scanCompletionTimestamp)
            self.spectraX.append(np.copy(results["spectrumX"]))
            self.spectraY.append(np.copy(results["spectrumY"]))
            self.processedInterferogramsX.append(np.copy(results["interferogramX"]))
            self.processedInterferogramsY.append(np.copy(results["interferogramY"]))

            # equalize lengths of all spectra before averaging
            minimalSpectrumLength = len(self.spectraX[0])

            for s in self.spectraX:
                if len(s) < minimalSpectrumLength:
                    minimalSpectrumLength = len(s)

            for z in range(0, len(self.spectraX)):
                if len(self.spectraX[z]) > minimalSpectrumLength:
                    self.spectraX[z] = self.spectraX[z][:minimalSpectrumLength - 1]
                    self.spectraY[z] = self.spectraY[z][:minimalSpectrumLength - 1]

            # calculate an average spectrum
            sumArr = numpy.zeros(len(self.spectraY[0]))
            for s in self.spectraY:
                sumArr += s

            sumArr /= len(self.spectraY)

            self.averageSpectrumX = self.spectraX[0]
            self.averageSpectrumY = sumArr

            self.SendResultsToPlot(results["interferogramX"], results["rawInterferogramY"],
                                   results["spectrumX"], results["spectrumY"],
                                   self.averageSpectrumX, self.averageSpectrumY, i,
                                   results["apodizationWindow"])

        return "ok"

    def _acquireDualDaq(self):
        """Capture the detector and reference channels in parallel.

        The two DAQs are blocking devices on separate interfaces, so the
        reference capture runs in a helper thread while the detector capture
        runs in this one. Both cover the same mirror movement.
        """
        referenceResult = {}

        def referenceCapture():
            referenceResult["status"] = self.DAQRefDriver.measureDataStandaloneMethod()

        referenceThread = Thread(target=referenceCapture, daemon=True)
        referenceThread.start()

        detectorStatus = self.DAQDriver.measureDataStandaloneMethod()

        referenceThread.join()

        return detectorStatus, referenceResult.get("status", "reference acquisition failed")

    @staticmethod
    def alignInterferogramsToReferenceTimeBase(rawReference, referenceSampleRate,
                                               rawInterferogram, interferogramSampleRate):
        """Resample the detector interferogram from its own time base onto the
        time base of the reference signal.

        Two different DAQs cannot share a sample rate, but both record the
        same mirror movement. After this resampling the unchanged Hilbert
        reference analysis (DataProcessor.analyzeDataHilbertInterpolation)
        can be used as with a single DAQ. The overlap window is cropped so
        that no edge clamping occurs.
        """
        if (rawReference is None or rawInterferogram is None or
                referenceSampleRate is None or interferogramSampleRate is None):
            return None, None

        rawReference = np.asarray(rawReference, dtype=np.float64)
        rawInterferogram = np.asarray(rawInterferogram, dtype=np.float64)

        if len(rawReference) == 0 or len(rawInterferogram) == 0:
            return None, None

        if referenceSampleRate == interferogramSampleRate and len(rawReference) == len(rawInterferogram):
            return rawReference, rawInterferogram

        referenceTimes = np.arange(len(rawReference)) / referenceSampleRate
        interferogramTimes = np.arange(len(rawInterferogram)) / interferogramSampleRate

        overlapStart = max(referenceTimes[0], interferogramTimes[0])
        overlapEnd = min(referenceTimes[-1], interferogramTimes[-1])

        if overlapEnd <= overlapStart:
            return None, None

        overlapMask = (referenceTimes >= overlapStart) & (referenceTimes <= overlapEnd)
        alignedInterferogram = np.interp(referenceTimes[overlapMask], interferogramTimes, rawInterferogram)

        return rawReference[overlapMask], alignedInterferogram

    def _performSimulatedAcquisition(self):
        mfliSamplingFrequency = self.DAQDriver.SamplingRates[self.mfliFrequencyIndex]
        self.mfliSamplesCount = (self.scanLength / (self.scanSpeed * 1000)) * mfliSamplingFrequency

        # configure DAQ
        self.DAQDriver.configureForMeasurement(samplingFreqIndex=self.mfliFrequencyIndex,
                                                sampleLength=self.mfliSamplesCount,
                                                triggerEnabled=self.triggerModeEnabled,
                                                triggerLevel=self.triggerLevel,
                                                triggerReference=self.triggerReference,
                                                triggerHysteresis=self.triggerHysteresis)

        time.sleep(1)

        failedAcquisitionsCount = 0

        i = 0
        while i < self.orderedMeasurementsCount:
            i += 1

            if failedAcquisitionsCount >= math.ceil(self.orderedMeasurementsCount * 0.2):
                if self.orderedMeasurementsCount == 1:
                    errstatus = "Single measurement failed"
                else:
                    errstatus = "More than 20% of the ordered measurements failed"
                return errstatus

            if self.stopRequestFlag:
                self.stopRequestFlag = False
                self.SetStatusMessageMethod("Measurement stopped")
                return "stop"

            self.SetStatusMessageMethod("Acquisition...")

            if self.triggerModeEnabled:
                self.DAQDriver.armTrigger()
                measStatus = self.DAQDriver.measureDataWithPrearmedTrigger()
            else:
                measStatus = self.DAQDriver.measureDataStandaloneMethod()

            scanCompletionTimestamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")

            if measStatus != "ok":
                failedAcquisitionsCount += 1
                i -= 1
                print("Measurement cycle skipped due to error: " + measStatus)
                continue

            self.SetStatusMessageMethod("Calculations...")

            try:
                results = self.DataAnalyzer.analyzeDataHilbertInterpolation(
                    rawReferenceSignal=self.DAQDriver.lastReferenceData,
                    rawInterferogram=self.DAQDriver.lastInterferogramData,
                    apodizationWindowType=self.selectedApodizationWindowType)
            except:
                self.SetStatusMessageMethod("Data acquisition or analysis failed")
                failedAcquisitionsCount += 1
                i -= 1
                print("Data acquisition or analysis failed due to exception")
                continue

            self.rawInterferograms.append(np.copy(self.DAQDriver.lastInterferogramData))
            self.rawReferenceSignals.append(np.copy(self.DAQDriver.lastReferenceData))
            self.interferogramTimestamps.append(scanCompletionTimestamp)
            self.spectraX.append(np.copy(results["spectrumX"]))
            self.spectraY.append(np.copy(results["spectrumY"]))
            self.processedInterferogramsX.append(np.copy(results["interferogramX"]))
            self.processedInterferogramsY.append(np.copy(results["interferogramY"]))

            # equalize lengths of all spectra before averaging
            minimalSpectrumLength = len(self.spectraX[0])

            for s in self.spectraX:
                if len(s) < minimalSpectrumLength:
                    minimalSpectrumLength = len(s)

            for z in range(0, len(self.spectraX)):
                if len(self.spectraX[z]) > minimalSpectrumLength:
                    self.spectraX[z] = self.spectraX[z][:minimalSpectrumLength - 1]
                    self.spectraY[z] = self.spectraY[z][:minimalSpectrumLength - 1]

            # calculate an average spectrum
            sumArr = numpy.zeros(len(self.spectraY[0]))
            for s in self.spectraY:
                sumArr += s

            sumArr /= len(self.spectraY)

            self.averageSpectrumX = self.spectraX[0]
            self.averageSpectrumY = sumArr

            self.SendResultsToPlot(results["interferogramX"], results["rawInterferogramY"],
                                   results["spectrumX"], results["spectrumY"],
                                   self.averageSpectrumX, self.averageSpectrumY, i,
                                   results["apodizationWindow"])

        return "ok"


    def allMeasurementsDone(self):
        self.NotifyAllMeasurementsDone()
        # self.SetStatusMessageMethod("Done")
