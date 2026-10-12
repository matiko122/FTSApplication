import os
import matplotlib
matplotlib.use("TkAgg")
import customtkinter as ctk
import numpy as np
import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import sys
from mfli_driver import MFLIDriver
from digilent_driver import DigilentDriver
from simulated_daq import SimulatedDAQ
from ams_driver import AMSSerialDriver, AMS_LIBRARY_AVAILABLE
from zaber_driver import ZaberDriver
import time
import si_prefix as si
from background_controller import BackgroundController
import serial.tools.list_ports
import settings_manager as SM
import logging
from tkinter.filedialog import asksaveasfilename
from tkinter import filedialog
from tkinter import messagebox
from datetime import datetime
import data_export_tool as DataExportTool
import absorbanceTool as AbsorbanceTool
import adjustmentTool as AdjustmentTool
import data_processor as DataProcessor
class FTSApp:

    def __init__(self):
        self.appVersion = "1.2_260914"

        logging.basicConfig(filename='ftsapp.log', format='%(asctime)s %(message)s', level=logging.INFO)
        logging.info('========= Application started =========')

        self.absorbanceToolWindow = None
        self.adjustmentToolWindow = None

        # optional separate DAQ for the reference interferogram
        self.DAQRefDriver = None
        self.referenceDaqReady = False
        self.mainSamplingRates = list(MFLIDriver.MFLISamplingRates)
        self.mainSamplingRateStrings = []
        self.referenceSamplingRates = list(MFLIDriver.MFLISamplingRates)
        self.referenceSamplingRateStrings = []

        if SM.isSettingsFileAvailable():
            self.appSettings = SM.readSettingsFromFile()
            SM.validateAndFixSettings(self.appSettings)
        else:
            self.appSettings = SM.getDefaultSettings()

        self.settingsUsedForCurrentMeasurement = self.appSettings.copy()

        # constants
        self.backgroundGray = "#242424"
        self.plotLineColor = "gold" # dodgerblue
        self.currentSpectrumX = []
        self.currentSpectrumY = []
        self.currentInterferogramX = []
        self.currentInterferogramY = []
        self.currentAverageSpectrumX = []
        self.currentAverageSpectrumY = []
        self.currentApodizationWindow = []

        self.currentlyAvailableCOMPorts = []
        self.daqReady = False
        self.delayLineReady = False

        # construct GUI
        ctk.set_appearance_mode("dark")
        self.root = ctk.CTk()
        self.root.geometry("1200x800")
        self.root.minsize(800, 800)
        self.root.title("FTS App" + "   " + "v." + self.appVersion)
        # self.root.iconbitmap(default='icon.ico')
        self.root.resizable(True, True)
        # self.root.state('zoomed')
        self.root.withdraw()

        self.screen_geometry = self.root.winfo_geometry()
        scr_width = int(self.screen_geometry.split('x')[0])

        if scr_width > 1920:
            menu_col_width = 350
        else:
            menu_col_width = 250

        self.root.columnconfigure(0, weight=1, minsize=menu_col_width)
        self.root.columnconfigure(1, weight=3)

        self.root.rowconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        self.root.protocol("WM_DELETE_WINDOW", self.onClosing)

        # build GUI elements
        self.frameTopPlot = ctk.CTkFrame(master=self.root, fg_color="darkblue")

        self.frameTopPlot.grid(row=0, column=1, padx=(5, 5), pady=0)#, sticky="N")

        self.frameBottomPlot = ctk.CTkFrame(master=self.root, fg_color="darkblue")
        # self.frame.place(relx=0.33, rely=0.025)
        self.frameBottomPlot.grid(row=1, column=1, padx=(5, 5), pady=0)#, sticky="N")

        # buttons
        # create a frame to hold all the controls related to measurements
        # ==============================================================================================================
        self.frameButtonsTop = ctk.CTkFrame(master=self.root,
                                            height=60,
                                            width=120,
                                            fg_color="dimgrey",
                                            corner_radius=10)
        self.frameButtonsTop.grid(row=0, column=0, sticky="NSEW", padx=5, pady=5)

        self.frameButtonsTop.columnconfigure(0, weight=1)
        self.frameButtonsTop.columnconfigure(1, weight=1)

        self.frameButtonsTop.rowconfigure(0, weight=1)
        self.frameButtonsTop.rowconfigure(1, weight=1)
        self.frameButtonsTop.rowconfigure(2, weight=1, minsize=40)
        self.frameButtonsTop.rowconfigure(3, weight=1)
        self.frameButtonsTop.rowconfigure(4, weight=1)
        self.frameButtonsTop.rowconfigure(5, weight=1)

        self.buttonSingle = ctk.CTkButton(master=self.frameButtonsTop,
                                          text="Single\ncapture",
                                          width=120,
                                          height=75,
                                          corner_radius=10,
                                          command=self.onCmdSingleCapture,
                                          state="disabled")
        self.buttonSingle.grid(row=0, column=0, sticky="N", padx=5, pady=5)

        self.multipleMeasStartButton = ctk.CTkButton(master=self.frameButtonsTop,
                                            text="Capture\nmultiple",
                                            width=120,
                                            height=75,
                                            corner_radius=10,
                                            command=self.onCmdMultipleCapture,
                                            state="disabled")
        self.multipleMeasStartButton.grid(row=1, column=0, sticky="N", padx=5, pady=5)

        self.multipleMeasStopButton = ctk.CTkButton(master=self.frameButtonsTop,
                                            text="Stop",
                                            width=120,
                                            height=75,
                                            corner_radius=10,
                                            fg_color="darkred",
                                            command=self.onCmdStopMeasurement)
        self.multipleMeasStopButton.grid(row=1, column=1, sticky="N", padx=5, pady=5)

        self.multipleMeasLabel = ctk.CTkLabel(master=self.frameButtonsTop,
                                                    text="Number of\naverages",
                                                    font=ctk.CTkFont(size=12))
        self.multipleMeasLabel.grid(row=2, column=0, sticky="N", padx=5, pady=5)

        self.multipleMeasBox = ctk.CTkEntry(master=self.frameButtonsTop,
                                        width=120, height=30)
        self.multipleMeasBox.insert(0, self.appSettings["averagingCount"])
        self.multipleMeasBox.grid(row=2, column=1, sticky="N", padx=5, pady=5)
        self.multipleMeasBox.bind("<FocusOut>", self.onCmdUpdateAveragingCount)
        self.multipleMeasBox.bind("<Return>", self.onCmdUpdateAveragingCount)

        self.buttonInterferogram = ctk.CTkButton(master=self.frameButtonsTop,
                                                 text="Inspect\ninterferogram",
                                                 width=120,
                                                 height=75,
                                                 corner_radius=10,
                                                 fg_color="darkgreen",
                                                 command=self.onCmdOpenInterferogramPlot)
        self.buttonInterferogram.grid(row=3, column=0, sticky="N", padx=5, pady=5)

        self.buttonSpectrum = ctk.CTkButton(master=self.frameButtonsTop,
                                            text="Inspect\nspectrum",
                                            width=120,
                                            height=75,
                                            corner_radius=10,
                                            fg_color="darkgreen",
                                            command=self.onCmdOpenSpectrumPlot)
        self.buttonSpectrum.grid(row=3, column=1, sticky="N", padx=5, pady=5)

        self.referenceSignalButton = ctk.CTkButton(master=self.frameButtonsTop,
                                            text="Inspect\nref. signal",
                                            width=120,
                                            height=75,
                                            corner_radius=10,
                                            fg_color="darkgreen",
                                            command=self.onCmdReferencePlot)
        self.referenceSignalButton.grid(row=4, column=0, sticky="N", padx=5, pady=5)

        self.adjustmentToolButton = ctk.CTkButton(master=self.frameButtonsTop,
                                            text="Adjustment\ntool",
                                            width=120,
                                            height=75,
                                            corner_radius=10,
                                            command=self.onCmdOpenAdjustmentTool)
        self.adjustmentToolButton.grid(row=4, column=1, sticky="N", padx=5, pady=5)

        self.absorbanceToolButton = ctk.CTkButton(master=self.frameButtonsTop,
                                            text="Absorbance\ntool",
                                            width=120,
                                            height=75,
                                            corner_radius=10,
                                            command=self.onCmdOpenAbsorbanceTool)
        self.absorbanceToolButton.grid(row=5, column=0, sticky="N", padx=5, pady=5)

        self.buttonArchive = ctk.CTkButton(master=self.frameButtonsTop,
                                           text="Archive\nviewer",
                                           width=120,
                                           height=75,
                                           corner_radius=10,
                                           command=self.onCmdUnusedButton)
        self.buttonArchive.grid(row=5, column=1, sticky="N", padx=5, pady=5)
        self.buttonArchive.configure(state="disabled")

        # create a frame to hold all the controls related to settings and configuration
        # ==============================================================================================================
        self.frameButtonsBottom = ctk.CTkFrame(master=self.root,
                                               height=60,
                                               width=5000,
                                               fg_color="dimgrey",
                                               corner_radius=10)
        self.frameButtonsBottom.grid(row=1, column=0, sticky="NSEW", padx=5, pady=5)

        self.frameButtonsBottom.columnconfigure(0, weight=1)
        self.frameButtonsBottom.columnconfigure(1, weight=1)

        # self.frameButtonsBottom.rowconfigure(0, weight=1)
        # self.frameButtonsBottom.rowconfigure(1, weight=1)
        # self.frameButtonsBottom.rowconfigure(2, weight=1)
        # self.frameButtonsBottom.rowconfigure(3, weight=1)
        # self.frameButtonsBottom.rowconfigure(4, weight=1)

        self.configLabel = ctk.CTkLabel(master=self.frameButtonsBottom,
                                        text="Settings",
                                        font=ctk.CTkFont(size=16, weight="bold"))
        self.configLabel.grid(row=0, column=0, columnspan=2, sticky="NSEW", padx=5, pady=5)

        # create settings tabs
        self.settingsTabs = ctk.CTkTabview(master=self.frameButtonsBottom)
        self.settingsTabs.grid(row=4, column=0, columnspan=2, sticky="NSEW", padx=2, pady=2)
        self.settingsTabs.add("Scan")
        self.settingsTabs.add("Trg")
        self.settingsTabs.add("Proc")
        self.settingsTabs.add("Conn")
        self.settingsTabs.add("Plot")
        self.settingsTabs.add("Sv")

        # configure settings 'SCAN' tab
        # ==============================================================================================================
        self.settingsTabs.tab("Scan").columnconfigure(0, weight=1)
        self.settingsTabs.tab("Scan").columnconfigure(1, weight=1)

        self.samplingFreqLabelHeader = ctk.CTkLabel(master=self.settingsTabs.tab("Scan"),
                                                    text="Sampling\nfreqency",
                                                    font=ctk.CTkFont(size=12))
        self.samplingFreqLabelHeader.grid(row=0, column=0, sticky="E", padx=5, pady=5)

        self.MFLIFreqneuenciesAsStrings = []

        for f in MFLIDriver.MFLISamplingRates:
            self.MFLIFreqneuenciesAsStrings.append(si.si_format(f, precision=2) + "Hz")

        self.samplingFreqCombo = ctk.CTkComboBox(master=self.settingsTabs.tab("Scan"),
                                                 values=self.MFLIFreqneuenciesAsStrings,
                                                 state="readonly",
                                                 width=120)
        self.samplingFreqCombo.grid(row=0, column=1, sticky="E", padx=5, pady=5)
        self.samplingFreqCombo.set(self.MFLIFreqneuenciesAsStrings[int(self.appSettings["mfliSelectedFrequencyIndex"])])

        self.startingPosLabel = ctk.CTkLabel(master=self.settingsTabs.tab("Scan"),
                                                    text="Start\nposition [\u03BCm]",
                                                    font=ctk.CTkFont(size=12))
        self.startingPosLabel.grid(row=1, column=0, sticky="E", padx=5, pady=5)

        self.startingPosBox = ctk.CTkEntry(master=self.settingsTabs.tab("Scan"),
                                        width=120, height=30)
        self.startingPosBox.insert(0, self.appSettings["delayLineConfiguredScanStart"])
        self.startingPosBox.grid(row=1, column=1, sticky="E", padx=5, pady=5)
        self.startingPosBox.bind("<FocusOut>", self.onCmdUpdateStartingPositionFromBox)
        self.startingPosBox.bind("<Return>", self.onCmdUpdateStartingPositionFromBox)

        self.scanLengthLabel = ctk.CTkLabel(master=self.settingsTabs.tab("Scan"),
                                                    text="Scan\nlength [\u03BCm]",
                                                    font=ctk.CTkFont(size=12))
        self.scanLengthLabel.grid(row=2, column=0, sticky="E", padx=5, pady=5)

        self.scanLengthBox = ctk.CTkEntry(master=self.settingsTabs.tab("Scan"),
                                        width=120, height=30)
        # self.scanLengthBox.configure(wrap='none')
        self.scanLengthBox.insert(0, self.appSettings["delayLineConfiguredScanLength"])
        self.scanLengthBox.grid(row=2, column=1, sticky="E", padx=5, pady=5)
        self.scanLengthBox.bind("<FocusOut>", self.onCmdUpdateScanLengthFromBox)
        self.scanLengthBox.bind("<Return>", self.onCmdUpdateScanLengthFromBox)

        self.scanLengthSlider = ctk.CTkSlider(master = self.settingsTabs.tab("Scan"),
                                              width = 250,
                                              height = 20,
                                              from_ = int(self.appSettings["delayLineMinimalScanLength"]),
                                              to = ZaberDriver.DelayLineNominalLength -
                                                 (ZaberDriver.DelayLineNominalLength -
                                                  int(self.appSettings["delayLineConfiguredScanStart"])),
                                              number_of_steps=(ZaberDriver.DelayLineNominalLength -
                                                             (ZaberDriver.DelayLineNominalLength -
                                                             int(self.appSettings["delayLineConfiguredScanStart"])) -
                                                             int(self.appSettings["delayLineMinimalScanLength"])) / 100,
                                              command=self.onCmdUpdateScanLengthFromSlider)
        self.scanLengthSlider.grid(row=3, column=0, columnspan=2, sticky="N", padx=5, pady=5)
        self.scanLengthSlider.set(float(self.appSettings["delayLineConfiguredScanLength"]))

        self.scanSpeedLabel = ctk.CTkLabel(master=self.settingsTabs.tab("Scan"),
                                                    text="Scan speed\n[mm/s]",
                                                    font=ctk.CTkFont(size=12))
        self.scanSpeedLabel.grid(row=4, column=0, sticky="E", padx=5, pady=5)

        self.scanSpeedValueLabel = ctk.CTkLabel(master=self.settingsTabs.tab("Scan"),
                                                    text=self.appSettings["delayLineConfiguredScanSpeed"],
                                                    font=ctk.CTkFont(size=12))
        self.scanSpeedValueLabel.grid(row=4, column=1, sticky="W", padx=5, pady=5)

        self.scanSpeedSlider = ctk.CTkSlider(master = self.settingsTabs.tab("Scan"),
                                              width = 250,
                                              height = 20,
                                              from_ = 0,
                                              to = int(self.appSettings["delayLineSpeedSliderTicks"]),
                                              number_of_steps = int(self.appSettings["delayLineSpeedSliderTicks"]),
                                              command=self.onCmdScanSpeedUpdateFromSlider)
        self.scanSpeedSlider.grid(row=5, column=0, columnspan=2, sticky="N", padx=5, pady=5)



        sliderRange = float(self.appSettings["delayLineMaximumSpeed"]) - float(self.appSettings["delayLineMinimumSpeed"])

        sliderSetting = (((float(self.appSettings["delayLineConfiguredScanSpeed"]) -
                            float(self.appSettings["delayLineMinimumSpeed"])) / sliderRange) *
                            int(self.appSettings["delayLineSpeedSliderTicks"]))

        self.scanSpeedSlider.set(sliderSetting)

        # configure settings 'PROC' tab
        # ==============================================================================================================
        # data processing stuff

        self.settingsTabs.tab("Proc").columnconfigure(0, weight=1)
        self.settingsTabs.tab("Proc").columnconfigure(1, weight=1)

        self.settingsTabs.tab("Proc").rowconfigure(0, weight=1)
        self.settingsTabs.tab("Proc").rowconfigure(1, weight=1)
        self.settingsTabs.tab("Proc").rowconfigure(2, weight=1)
        self.settingsTabs.tab("Proc").rowconfigure(3, weight=1)
        self.settingsTabs.tab("Proc").rowconfigure(4, weight=1)

        self.apodizationComboLabel = ctk.CTkLabel(master=self.settingsTabs.tab("Proc"),
                                                    text="Apodization\nwindow",
                                                    font=ctk.CTkFont(size=12))
        self.apodizationComboLabel.grid(row=0, column=0, sticky="E", padx=5, pady=5)

        self.apodizationTypeCombo = ctk.CTkComboBox(master=self.settingsTabs.tab("Proc"),
                                                 values= DataProcessor.getApodizationWindowsTypesList(),
                                                 state="readonly",
                                                 width=130)
        self.apodizationTypeCombo.grid(row=0, column=1, sticky="E", padx=5, pady=5)
        # self.apodizationTypeCombo.set(DataProcessor.getApodizationWindowsTypesList()[0])
        self.apodizationTypeCombo.set(self.appSettings["apodizationWindow"])

        # configure settings 'TRIG' tab
        # ==============================================================================================================
        self.settingsTabs.tab("Trg").columnconfigure(0, weight=1)
        self.settingsTabs.tab("Trg").columnconfigure(1, weight=1)

        self.settingsTabs.tab("Trg").rowconfigure(0, weight=1)
        self.settingsTabs.tab("Trg").rowconfigure(1, weight=1)
        self.settingsTabs.tab("Trg").rowconfigure(2, weight=1)
        self.settingsTabs.tab("Trg").rowconfigure(3, weight=1)

        self.triggerEnableSwitch = ctk.CTkSwitch(master=self.settingsTabs.tab("Trg"),
                                                    text="Triggered acquisition mode",
                                                    command=self.onCmdTriggerSwitchModified,
                                                    onvalue="True", offvalue="False")
        self.triggerEnableSwitch.grid(row=0, column=0, columnspan=2, padx=5, pady=5, sticky="W")

        if self.appSettings["triggerModeEnabled"] == "True":
            self.triggerEnableSwitch.select()
        else:
            self.triggerEnableSwitch.deselect()

        self.triggerLevelLabel = ctk.CTkLabel(master=self.settingsTabs.tab("Trg"),
                                                    text="Trigger\nlevel [mV]",
                                                    font=ctk.CTkFont(size=12))
        self.triggerLevelLabel.grid(row=1, column=0, sticky="E", padx=5, pady=5)

        self.triggerLevelBox = ctk.CTkEntry(master=self.settingsTabs.tab("Trg"),
                                        width=120, height=30)
        self.triggerLevelBox.insert(0, self.appSettings["triggerLevel"])
        self.triggerLevelBox.grid(row=1, column=1, sticky="E", padx=5, pady=5)
        self.triggerLevelBox.bind("<FocusOut>", self.onCmdRefreshTriggerSettings)
        self.triggerLevelBox.bind("<Return>", self.onCmdRefreshTriggerSettings)

        self.triggerHysteresisLabel = ctk.CTkLabel(master=self.settingsTabs.tab("Trg"),
                                                    text="Trigger\nhysteresis [mV]",
                                                    font=ctk.CTkFont(size=12))
        self.triggerHysteresisLabel.grid(row=2, column=0, sticky="E", padx=5, pady=5)

        self.triggerHysteresisBox = ctk.CTkEntry(master=self.settingsTabs.tab("Trg"),
                                        width=120, height=30)
        self.triggerHysteresisBox.insert(0, self.appSettings["triggerHysteresis"])
        self.triggerHysteresisBox.grid(row=2, column=1, sticky="E", padx=5, pady=5)
        self.triggerHysteresisBox.bind("<FocusOut>", self.onCmdRefreshTriggerSettings)
        self.triggerHysteresisBox.bind("<Return>", self.onCmdRefreshTriggerSettings)

        self.triggerReferenceLabel = ctk.CTkLabel(master=self.settingsTabs.tab("Trg"),
                                                    text="Trigger\nreference [%]",
                                                    font=ctk.CTkFont(size=12))
        self.triggerReferenceLabel.grid(row=3, column=0, sticky="E", padx=5, pady=5)

        self.triggerReferenceBox = ctk.CTkEntry(master=self.settingsTabs.tab("Trg"),
                                        width=120, height=30)
        self.triggerReferenceBox.insert(0, self.appSettings["triggerReference"])
        self.triggerReferenceBox.grid(row=3, column=1, sticky="E", padx=5, pady=5)
        self.triggerReferenceBox.bind("<FocusOut>", self.onCmdRefreshTriggerSettings)
        self.triggerReferenceBox.bind("<Return>", self.onCmdRefreshTriggerSettings)

        # configure settings 'HARDWARE' tab
        # ==============================================================================================================
        self.settingsTabs.tab("Conn").columnconfigure(0, weight=1)
        self.settingsTabs.tab("Conn").rowconfigure(0, weight=1)

        # the connection tab is scrollable because it may hold the controls of
        # two DAQ systems at once (detector + optional reference DAQ)
        self.connFrame = ctk.CTkScrollableFrame(master=self.settingsTabs.tab("Conn"),
                                                fg_color="transparent")
        self.connFrame.grid(row=0, column=0, sticky="NSEW")
        self.connFrame.columnconfigure(0, weight=1)
        self.connFrame.columnconfigure(1, weight=1)

        self.daqTypeLabel = ctk.CTkLabel(master=self.connFrame,
                                         text="DAQ type",
                                         font=ctk.CTkFont(size=12))
        self.daqTypeLabel.grid(row=0, column=0, sticky="E", padx=5, pady=5)

        self.daqTypeCombo = ctk.CTkComboBox(master=self.connFrame,
                                             values=["MFLI", "ADP2230", "AMIS8140", "Simulated"],
                                             state="readonly",
                                             width=140,
                                             command=self.onCmdDAQTypeChanged)
        self.daqTypeCombo.grid(row=0, column=1, sticky="W", padx=5, pady=5)
        self.daqTypeCombo.set(self.appSettings.get("daqType", "MFLI"))

        self.referenceSourceLabel = ctk.CTkLabel(master=self.connFrame,
                                                 text="Reference\nsource",
                                                 font=ctk.CTkFont(size=12))
        self.referenceSourceLabel.grid(row=1, column=0, sticky="E", padx=5, pady=5)

        self.referenceSourceCombo = ctk.CTkComboBox(master=self.connFrame,
                                                    values=["Internal"],
                                                    state="readonly",
                                                    width=140,
                                                    command=self.onCmdReferenceSourceChanged)
        self.referenceSourceCombo.grid(row=1, column=1, sticky="W", padx=5, pady=5)
        self.referenceSourceCombo.set(self.appSettings.get("referenceDaqType", "Internal"))

        self.referenceFreqLabel = ctk.CTkLabel(master=self.connFrame,
                                               text="Reference\nsampling freq.",
                                               font=ctk.CTkFont(size=12))

        self.referenceFreqCombo = ctk.CTkComboBox(master=self.connFrame,
                                                  values=[],
                                                  state="readonly",
                                                  width=140)

        self.hardwareStatusLabelHeader = ctk.CTkLabel(master=self.connFrame,
                                                      text="General\nstatus",
                                                      font=ctk.CTkFont(size=12))
        self.hardwareStatusLabelHeader.grid(row=3, column=0, sticky="E", padx=5, pady=5)

        self.hardwareStatusLabel = ctk.CTkLabel(master=self.connFrame,
                                                text="NOT\nREADY",
                                                text_color="red",
                                                font=ctk.CTkFont(size=14, weight="bold"))
        self.hardwareStatusLabel.grid(row=3, column=1, sticky="W", padx=5, pady=5)

        self.mfliStatusLabelHeader = ctk.CTkLabel(master=self.connFrame,
                                                  text="DAQ\nsystem",
                                                  font=ctk.CTkFont(size=12))
        self.mfliStatusLabelHeader.grid(row=4, column=0, sticky="E", padx=5, pady=5)

        self.mfliStatusLabel = ctk.CTkLabel(master=self.connFrame,
                                            text="NOT\nREADY",
                                            text_color="red",
                                            font=ctk.CTkFont(size=14, weight="bold"))
        self.mfliStatusLabel.grid(row=4, column=1, sticky="W", padx=5, pady=5)

        self.referenceStatusLabelHeader = ctk.CTkLabel(master=self.connFrame,
                                                       text="Reference\nDAQ",
                                                       font=ctk.CTkFont(size=12))
        self.referenceStatusLabelHeader.grid(row=5, column=0, sticky="E", padx=5, pady=5)

        self.referenceStatusLabel = ctk.CTkLabel(master=self.connFrame,
                                                 text="N/A",
                                                 text_color="gray",
                                                 font=ctk.CTkFont(size=14, weight="bold"))
        self.referenceStatusLabel.grid(row=5, column=1, sticky="W", padx=5, pady=5)

        self.zaberStatusLabelHeader = ctk.CTkLabel(master=self.connFrame,
                                                   text="Delay\nline",
                                                   font=ctk.CTkFont(size=12))
        self.zaberStatusLabelHeader.grid(row=6, column=0, sticky="E", padx=5, pady=5)

        self.zaberStatusLabel = ctk.CTkLabel(master=self.connFrame,
                                             text="NOT\nREADY",
                                             text_color="red",
                                             font=ctk.CTkFont(size=14, weight="bold"))
        self.zaberStatusLabel.grid(row=6, column=1, sticky="W", padx=5, pady=5)

        self.zaberCOMLabel = ctk.CTkLabel(master=self.connFrame,
                                          text="Zaber port",
                                          font=ctk.CTkFont(size=12))
        self.zaberCOMLabel.grid(row=7, column=0, sticky="W", padx=5, pady=5)

        self.zaberPortCombo = ctk.CTkComboBox(master=self.connFrame,
                                              values=[],
                                              state="readonly",
                                              width=140)
        self.zaberPortCombo.grid(row=7, column=1, sticky="E", padx=5, pady=5)
        self.onCmdRefreshCOMPorts()

        # set the last used Zaber COM port if it is currently available
        if self.currentlyAvailableCOMPorts.__contains__(self.appSettings["delayLineCOMPort"]):
            self.zaberPortCombo.set(self.appSettings["delayLineCOMPort"])

        self.zaberPortRefreshButton = ctk.CTkButton(master=self.connFrame,
                                            text="",
                                            width=20,
                                            height=20,
                                            corner_radius=4,
                                            command=self.onCmdRefreshCOMPorts)
        self.zaberPortRefreshButton.grid(row=7, column=0, sticky="E", padx=5, pady=5)

        # DAQ-specific controls (main DAQ on the first line, optional
        # reference DAQ on the second line) — shown/hidden per selection
        self.mfliIDLabel = ctk.CTkLabel(master=self.connFrame,
                                        text="MFLI ID",
                                        font=ctk.CTkFont(size=12))

        self.mfliIDBox = ctk.CTkTextbox(master=self.connFrame,
                                        width=140, height=30)
        self.mfliIDBox.configure(wrap='none')
        self.mfliIDBox.insert("0.0", self.appSettings["mfliDeviceID"])

        self.digilentRangeLabel = ctk.CTkLabel(master=self.connFrame,
                                               text="Range [V]",
                                               font=ctk.CTkFont(size=12))

        self.digilentRangeCombo = ctk.CTkComboBox(master=self.connFrame,
                                                   values=["1", "2.5", "5", "10"],
                                                   state="readonly",
                                                   width=140)
        self.digilentRangeCombo.set(self.appSettings.get("digilentRange", "5"))

        self.digilentInfoLabel = ctk.CTkLabel(master=self.connFrame,
                                              text="Auto-detected via USB",
                                              font=ctk.CTkFont(size=11))

        self.simulatedNoiseLabel = ctk.CTkLabel(master=self.connFrame,
                                                text="Noise level",
                                                font=ctk.CTkFont(size=12))

        self.simulatedNoiseSlider = ctk.CTkSlider(master=self.connFrame,
                                                   width=140,
                                                   height=20,
                                                   from_=0,
                                                   to=1.0,
                                                   number_of_steps=100,
                                                   command=self.onCmdSimulatedNoiseChanged)

        self.simulatedNoiseValueLabel = ctk.CTkLabel(master=self.connFrame,
                                                      text="0.10",
                                                      font=ctk.CTkFont(size=12))

        # AMS-DIG-PROC specific controls
        self.amsPortLabel = ctk.CTkLabel(master=self.connFrame,
                                         text="AMS port",
                                         font=ctk.CTkFont(size=12))

        self.amsPortCombo = ctk.CTkComboBox(master=self.connFrame,
                                            values=[],
                                            state="readonly",
                                            width=140)

        self.amsPortRefreshButton = ctk.CTkButton(master=self.connFrame,
                                                  text="",
                                                  width=20,
                                                  height=20,
                                                  corner_radius=4,
                                                  command=self.onCmdRefreshAMSPorts)

        self.amsTemperatureLabel = ctk.CTkLabel(master=self.connFrame,
                                                text="Detector temp. [K]\n(0 = don't change)",
                                                font=ctk.CTkFont(size=12))

        self.amsTemperatureBox = ctk.CTkEntry(master=self.connFrame,
                                              width=140, height=30)
        self.amsTemperatureBox.insert(0, self.appSettings.get("amsDetectorTemperature", "0"))

        self.amsWaitTemperatureSwitch = ctk.CTkSwitch(master=self.connFrame,
                                                      text="Wait until stabilized",
                                                      font=ctk.CTkFont(size=12),
                                                      onvalue="True", offvalue="False")

        if self.appSettings.get("amsWaitForTemperature", "False") == "True":
            self.amsWaitTemperatureSwitch.select()
        else:
            self.amsWaitTemperatureSwitch.deselect()

        self.amsTemperatureValueLabel = ctk.CTkLabel(master=self.connFrame,
                                                     text="Detector: --",
                                                     font=ctk.CTkFont(size=12))

        # show the correct controls for the current DAQ selections
        self._refreshReferenceSourceCombo()
        self._refreshMainSamplingCombo()
        self._refreshReferenceSamplingCombo()
        self._showDAQControls()
        self._updateDaqControlStates()

        self.buttonHardware = ctk.CTkButton(master=self.connFrame,
                                            text="Connect\nall",
                                            width=120,
                                            height=80,
                                            corner_radius=10,
                                            command=self.onCmdConnectHardware)
        self.buttonHardware.grid(row=13, column=0, sticky="N", padx=5, pady=5)

        self.buttonHomeMirror = ctk.CTkButton(master=self.connFrame,
                                              text="Home\nmirror",
                                              width=120,
                                              height=80,
                                              corner_radius=10,
                                              command=self.onCmdUnusedButton)
        self.buttonHomeMirror.grid(row=13, column=1, sticky="N", padx=5, pady=5)

        # configure settings 'EXPORT' tab
        # ==============================================================================================================
        self.settingsTabs.tab("Sv").columnconfigure(0, weight=1)
        self.settingsTabs.tab("Sv").columnconfigure(1, weight=1)
        self.settingsTabs.tab("Sv").columnconfigure(2, weight=1)

        self.buttonSaveNormal = ctk.CTkButton(master=self.settingsTabs.tab("Sv"),
                                            text="Save\nresults",
                                            width=120,
                                            height=80,
                                            corner_radius=10,
                                            command=self.onCmdSaveFull)
        self.buttonSaveNormal.grid(row=0, column=0, sticky="N", padx=5, pady=5)

        self.buttonSaveCSV = ctk.CTkButton(master=self.settingsTabs.tab("Sv"),
                                            text="Save\nspec.\nas .csv",
                                            width=120,
                                            height=80,
                                            corner_radius=10,
                                            command=self.onCmdSaveCSVOnly)
        self.buttonSaveCSV.grid(row=0, column=1, sticky="N", padx=5, pady=5)

        self.buttonSaveH5 = ctk.CTkButton(master=self.settingsTabs.tab("Sv"),
                                            text="Save\n.h5",
                                            width=120,
                                            height=80,
                                            corner_radius=10,
                                            command=self.onCmdSaveH5)
        self.buttonSaveH5.grid(row=0, column=2, sticky="N", padx=5, pady=5)

        self.exportRawDataSwitch = ctk.CTkSwitch(master=self.settingsTabs.tab("Sv"),
                                                    text="Export raw data", command=self.onExportSwitchModified,
                                                    onvalue="True", offvalue="False")
        self.exportRawDataSwitch.grid(row=1, column=0, columnspan=3, padx=5, pady=5, sticky="W")

        if self.appSettings["saveRawData"] == "True":
            self.exportRawDataSwitch.select()
        else:
            self.exportRawDataSwitch.deselect()

        self.exportToMatSwitch = ctk.CTkSwitch(master=self.settingsTabs.tab("Sv"),
                                                    text="Export to .MAT", command=self.onExportSwitchModified,
                                                    onvalue="True", offvalue="False")
        self.exportToMatSwitch.grid(row=2, column=0, columnspan=3, padx=5, pady=5, sticky="W")

        if self.appSettings["saveDataToMAT"] == "True":
            self.exportToMatSwitch.select()
        else:
            self.exportToMatSwitch.deselect()

        self.commentsLabel = ctk.CTkLabel(master=self.settingsTabs.tab("Sv"),
                                                    text="Comments:",
                                                    font=ctk.CTkFont(size=12))
        self.commentsLabel.grid(row=3, column=0, columnspan=3, sticky="W", padx=5, pady=(10,0))

        self.commentsTextBox = ctk.CTkTextbox(master=self.settingsTabs.tab("Sv"),
                                              text_color='ghost white',
                                              bg_color='gray18',
                                              corner_radius=10)

        self.commentsTextBox.grid(row=4, column=0, columnspan=3, padx=5, pady=5, sticky='W')

        # configure settings 'PLOTS' tab
        # ==============================================================================================================
        self.settingsTabs.tab("Plot").columnconfigure(0, weight=1)
        self.settingsTabs.tab("Plot").columnconfigure(1, weight=1)

        self.settingsTabs.tab("Plot").columnconfigure(0, weight=1)
        self.settingsTabs.tab("Plot").columnconfigure(1, weight=1)

        self.spectrumXMinLabel = ctk.CTkLabel(master=self.settingsTabs.tab("Plot"),
                                                    text="Spectrum plot\nX MIN [\u03BCm]",
                                                    font=ctk.CTkFont(size=12))
        self.spectrumXMinLabel.grid(row=0, column=0, sticky="E", padx=5, pady=5)

        self.spectrumXMinBox = ctk.CTkEntry(master=self.settingsTabs.tab("Plot"),
                                        width=120, height=30)
        # self.scanLengthBox.configure(wrap='none')
        self.spectrumXMinBox.insert(0, self.appSettings["plotSpectrumXRangeMin"])
        self.spectrumXMinBox.grid(row=0, column=1, sticky="E", padx=5, pady=5)
        self.spectrumXMinBox.bind("<FocusOut>", self.onCmdUpdateSpectrumPlotRanges)
        self.spectrumXMinBox.bind("<Return>", self.onCmdUpdateSpectrumPlotRanges)

        self.spectrumXMaxLabel = ctk.CTkLabel(master=self.settingsTabs.tab("Plot"),
                                                    text="Spectrum plot\nX MAX [\u03BCm]",
                                                    font=ctk.CTkFont(size=12))
        self.spectrumXMaxLabel.grid(row=1, column=0, sticky="E", padx=5, pady=5)

        self.spectrumXMaxBox = ctk.CTkEntry(master=self.settingsTabs.tab("Plot"),
                                        width=120, height=30)
        # self.scanLengthBox.configure(wrap='none')
        self.spectrumXMaxBox.insert(0, self.appSettings["plotSpectrumXRangeMax"])
        self.spectrumXMaxBox.grid(row=1, column=1, sticky="E", padx=5, pady=5)
        self.spectrumXMaxBox.bind("<FocusOut>", self.onCmdUpdateSpectrumPlotRanges)
        self.spectrumXMaxBox.bind("<Return>", self.onCmdUpdateSpectrumPlotRanges)

        self.spectrumYMinLabel = ctk.CTkLabel(master=self.settingsTabs.tab("Plot"),
                                                    text="Spectrum plot\nY MIN [dBm]",
                                                    font=ctk.CTkFont(size=12))
        self.spectrumYMinLabel.grid(row=2, column=0, sticky="E", padx=5, pady=5)

        self.spectrumYMinBox = ctk.CTkEntry(master=self.settingsTabs.tab("Plot"),
                                        width=120, height=30)
        # self.scanLengthBox.configure(wrap='none')
        self.spectrumYMinBox.insert(0, self.appSettings["plotSpectrumYRangeMin"])
        self.spectrumYMinBox.grid(row=2, column=1, sticky="E", padx=5, pady=5)
        self.spectrumYMinBox.bind("<FocusOut>", self.onCmdUpdateSpectrumPlotRanges)
        self.spectrumYMinBox.bind("<Return>", self.onCmdUpdateSpectrumPlotRanges)

        self.spectrumYMaxLabel = ctk.CTkLabel(master=self.settingsTabs.tab("Plot"),
                                                    text="Spectrum plot\nY MAX [dBm]",
                                                    font=ctk.CTkFont(size=12))
        self.spectrumYMaxLabel.grid(row=3, column=0, sticky="E", padx=5, pady=5)

        self.spectrumYMaxBox = ctk.CTkEntry(master=self.settingsTabs.tab("Plot"),
                                        width=120, height=30)
        # self.scanLengthBox.configure(wrap='none')
        self.spectrumYMaxBox.insert(0, self.appSettings["plotSpectrumYRangeMax"])
        self.spectrumYMaxBox.grid(row=3, column=1, sticky="E", padx=5, pady=5)
        self.spectrumYMaxBox.bind("<FocusOut>", self.onCmdUpdateSpectrumPlotRanges)
        self.spectrumYMaxBox.bind("<Return>", self.onCmdUpdateSpectrumPlotRanges)

        self.xUnitsLabel = ctk.CTkLabel(master=self.settingsTabs.tab("Plot"),
                                                    text="Select X axis\nunit",
                                                    font=ctk.CTkFont(size=12))
        self.xUnitsLabel.grid(row=4, column=0, sticky="E", padx=5, pady=5)


        self.currentXUnit = "cm-1"

        self.xUnitRadioUMVar = ctk.IntVar(value=0)
        self.xUnitRadioUM = ctk.CTkRadioButton(master=self.settingsTabs.tab("Plot"),
                                                text="\u03BCm",
                                                command=self.onCmdUnitRadioUM,
                                                value=1,
                                                variable=self.xUnitRadioUMVar)
        self.xUnitRadioUM.grid(row=4, column=1, sticky="W", padx=5, pady=5)

        if self.currentXUnit == "um":
            self.xUnitRadioUM.select()
        else:
            self.xUnitRadioUM.deselect()

        self.xUnitRadioFreqVar = ctk.IntVar(value=0)
        self.xUnitRadioFreq = ctk.CTkRadioButton(master=self.settingsTabs.tab("Plot"),
                                                text="THz",
                                                command=self.onCmdUnitRadioTHz,
                                                value=1,
                                                variable=self.xUnitRadioFreqVar)
        self.xUnitRadioFreq.grid(row=5, column=1, sticky="W", padx=5, pady=5)

        if self.currentXUnit == "thz":
            self.xUnitRadioFreq.select()
        else:
            self.xUnitRadioFreq.deselect()

        self.xUnitRadioCMVar = ctk.IntVar(value=0)
        self.xUnitRadioCM = ctk.CTkRadioButton(master=self.settingsTabs.tab("Plot"),
                                                text="cm-1",
                                                command=self.onCmdUnitRadioCM,
                                                value=1,
                                                variable=self.xUnitRadioCMVar)
        self.xUnitRadioCM.grid(row=6, column=1, sticky="W", padx=5, pady=5)

        if self.currentXUnit == "cm-1":
            self.xUnitRadioCM.select()
        else:
            self.xUnitRadioCM.deselect()

        # Create plots
        # ==============================================================================================================
        plt.style.use('dark_background')
        self.figTop, self.axTop = plt.subplots()
        self.figTop.suptitle("Interferogram")
        self.axTop.set_xlabel('Mirror position [\u03BCm]')
        self.axTop.set_ylabel('Detector voltage [V]')
        self.figTop.set_facecolor(self.backgroundGray)
        self.figTop.set_size_inches(100, 100)
        self.figTop.subplots_adjust(left=0.1, right=0.99, bottom=0.1, top=0.97, wspace=0, hspace=0)
        self.figTop.set_tight_layout(True)
        self.canvasTopPlot = FigureCanvasTkAgg(self.figTop, master=self.frameTopPlot)
        self.canvasTopPlot.get_tk_widget().pack(side='right', fill='both', expand=True)
        self.canvasTopPlot.draw()

        self.figBot, self.axBot = plt.subplots()
        self.figBot.suptitle("Spectrum")
        self.axBot.set_xlabel('Wavelength [\u03BCm]')
        self.axBot.set_ylabel('[dBm]')
        self.figBot.set_facecolor(self.backgroundGray)
        self.figBot.set_size_inches(100, 100)
        self.figBot.subplots_adjust(left=0.1, right=0.99, bottom=0.1, top=0.97, wspace=0, hspace=0)
        self.figBot.set_tight_layout(True)
        self.canvasBotPlot = FigureCanvasTkAgg(self.figBot, master=self.frameBottomPlot)
        self.canvasBotPlot.get_tk_widget().pack(side='right', fill='both', expand=True)
        self.canvasBotPlot.draw()

        self.updatePlot()
        plt.close()
        plt.close()

        self.axBot.set_xlim(float(self.appSettings["plotSpectrumXRangeMin"]),
                            float(self.appSettings["plotSpectrumXRangeMax"]))
        self.axBot.set_ylim(float(self.appSettings["plotSpectrumYRangeMin"]),
                            float(self.appSettings["plotSpectrumYRangeMax"]))
        # self.axBot.set_yscale("log")

        self.settingsTabs.set("Conn")

        # create a status bar
        self.statusLabel = ctk.CTkLabel(master=self.root,
                                        text="",
                                        font=ctk.CTkFont(size=12))
        self.statusLabel.grid(row=0, column=1, columnspan=2, sticky="NE", padx=15, pady=5)

        # create drivers
        self.DAQDriver = None
        self.ZaberDrv = ZaberDriver()

        # set up the background application controller
        self.ApplicationController = BackgroundController(self.DAQDriver, self.ZaberDrv)
        self.ApplicationController.SetStatusMessageMethod = self.updateStatusMessage
        self.ApplicationController.SetGeneralReadyFlagMethod = self.setGeneralReadyFlag
        self.ApplicationController.SetDAQReadyFlagMethod = self.setDAQReadyFlag
        self.ApplicationController.SetDelayLineReadyFlagMethod = self.setDelayLineReadyFlag
        self.ApplicationController.SetReferenceDAQReadyFlagMethod = self.setReferenceDAQReadyFlag
        self.ApplicationController.SendResultsToPlot = self.receiveMeasurementResults
        self.ApplicationController.NotifyAllMeasurementsDone = self.receiveNotificationAllMeasurementsDone

        # set the Zaber port from the combo
        self.ApplicationController.ZaberPort = self.zaberPortCombo.get().replace(' ', '').replace(
            '\t', '').replace('\n', '').replace('\r', '')

        # self.ApplicationController.performInitialization()
        # run the app
        self.root.update()
        self.root.deiconify()   # show the window after it's loaded
        self.root.after(2000, self._pollAMSStatus)
        self.root.mainloop()

    def updateStatusMessage(self, message):
        self.root.after(0, lambda: self.statusLabel.configure(text=message))
        logging.info(f"Status bar message set to: {message}")

    def getApplicationSettings(self):
        return self.appSettings

    def setApplicationSettings(self, settings):
        self.appSettings = settings

    def setGeneralReadyFlag(self, isReady):
        def _():
            if isReady:
                self.hardwareStatusLabel.configure(text="READY", text_color="lightgreen")
                logging.info(f"General status: ready")
            else:
                self.hardwareStatusLabel.configure(text="NOT\nREADY", text_color="red")
                logging.info(f"General status: not ready")
        self.root.after(0, _)

    def setDAQReadyFlag(self, isReady):
        def _():
            self.daqReady = isReady
            if isReady:
                self.mfliStatusLabel.configure(text="READY", text_color="lightgreen")
                if self.daqTypeCombo.get() == "MFLI":
                    self.appSettings["mfliDeviceID"] = self.mfliIDBox.get("0.0", "end").replace(' ', '').replace('\t', '').replace('\n', '').replace('\r', '')
                logging.info(f"DAQ status: ready")
            else:
                self.mfliStatusLabel.configure(text="NOT\nREADY", text_color="red")
                logging.info(f"DAQ status: not ready")
            self._updateDaqControlStates()
            self._updateCaptureButtons()
        self.root.after(0, _)

    def setReferenceDAQReadyFlag(self, isReady):
        def _():
            self.referenceDaqReady = isReady
            if self._hasExternalReference():
                if isReady:
                    self.referenceStatusLabel.configure(text="READY", text_color="lightgreen")
                    logging.info(f"Reference DAQ status: ready")
                else:
                    self.referenceStatusLabel.configure(text="NOT\nREADY", text_color="red")
                    logging.info(f"Reference DAQ status: not ready")
            else:
                self.referenceStatusLabel.configure(text="N/A", text_color="gray")
            self._updateDaqControlStates()
            self._updateCaptureButtons()
        self.root.after(0, _)

    def _hasExternalReference(self):
        return self.referenceSourceCombo.get() != "Internal"

    def _updateDaqControlStates(self):
        mainType = self.daqTypeCombo.get()
        self._setScanControlsEnabled(mainType != "Simulated")
        self._setTriggerControlsEnabled(mainType in ("MFLI", "ADP2230") and not self._hasExternalReference())
        self._setReferenceFreqVisible()

    def _setReferenceFreqVisible(self):
        if self._hasExternalReference():
            self.referenceFreqLabel.grid(row=2, column=0, sticky="E", padx=5, pady=5)
            self.referenceFreqCombo.grid(row=2, column=1, sticky="W", padx=5, pady=5)
        else:
            self.referenceFreqLabel.grid_forget()
            self.referenceFreqCombo.grid_forget()

    def _updateCaptureButtons(self):
        isSimulated = self.daqTypeCombo.get() == "Simulated"
        ready = self.daqReady
        if not isSimulated:
            ready = ready and self.delayLineReady
        if self._hasExternalReference():
            ready = ready and self.referenceDaqReady
        state = "normal" if ready else "disabled"
        self.buttonSingle.configure(state=state)
        self.multipleMeasStartButton.configure(state=state)

    def _setScanControlsEnabled(self, enabled):
        state = "normal" if enabled else "disabled"
        for widget in [self.samplingFreqCombo, self.startingPosBox, self.scanLengthBox,
                       self.scanLengthSlider, self.scanSpeedSlider]:
            try:
                widget.configure(state=state)
            except Exception:
                pass

    def _setTriggerControlsEnabled(self, enabled):
        state = "normal" if enabled else "disabled"
        for widget in [self.triggerEnableSwitch, self.triggerLevelBox,
                       self.triggerHysteresisBox, self.triggerReferenceBox]:
            try:
                widget.configure(state=state)
            except Exception:
                pass

    def setDelayLineReadyFlag(self, isReady):
        def _():
            self.delayLineReady = isReady
            if isReady:
                self.zaberStatusLabel.configure(text="READY", text_color="lightgreen")
                self.appSettings["delayLineCOMPort"] = self.zaberPortCombo.get()
                logging.info(f"Delay line status: ready")
            else:
                self.zaberStatusLabel.configure(text="NOT\nREADY", text_color="red")
                logging.info(f"Delay line status: not ready")
            self._updateCaptureButtons()
        self.root.after(0, _)

    def receiveMeasurementResults(self, interfX, interfY, spectrumX, spectrumY, averageSpectrumX, averageSpectrumY,
        completedMeasurements, apodizationWindow):

        self.currentInterferogramX = interfX
        self.currentInterferogramY = interfY
        self.currentSpectrumX = spectrumX
        self.currentSpectrumY = spectrumY
        self.currentAverageSpectrumX = averageSpectrumX
        self.currentAverageSpectrumY = averageSpectrumY
        self.currentApodizationWindow = apodizationWindow

        self.root.after(0, self._updateUIAfterMeasurement, completedMeasurements)

    def _updateUIAfterMeasurement(self, completedMeasurements):
        self.multipleMeasBox.delete(0, "end")

        if completedMeasurements == self.ApplicationController.orderedMeasurementsCount:
            self.multipleMeasBox.insert(0,f"{self.ApplicationController.orderedMeasurementsCount}")
        else:
            self.multipleMeasBox.insert(0,
                                        f"{completedMeasurements}/{self.ApplicationController.orderedMeasurementsCount}")
        self.updatePlot()

    def receiveNotificationAllMeasurementsDone(self):
        logging.info("All ordered measurements done")
        self.updateStatusMessage("Done")

    def onCmdTriggerSwitchModified(self):
        self.onCmdRefreshTriggerSettings(None)

    def onCmdRefreshTriggerSettings(self, other):
        # trigger switch
        self.appSettings["triggerModeEnabled"] = self.triggerEnableSwitch.get()

        # handle trigger reference setting
        try:
            newReferenceSetting = float(self.triggerReferenceBox.get())

            if newReferenceSetting > 99:
                newReferenceSetting = 99
            elif newReferenceSetting < 1:
                newReferenceSetting = 1

            self.appSettings["triggerReference"] = str(newReferenceSetting)

            self.triggerReferenceBox.delete(0, "end")
            self.triggerReferenceBox.insert(0, str(newReferenceSetting))
        except:
            newReferenceSetting = float(self.appSettings["triggerReference"])
            self.triggerReferenceBox.delete(0, "end")
            self.triggerReferenceBox.insert(0, str(newReferenceSetting))

        # handle trigger level setting
        try:
            newLevelSetting = float(self.triggerLevelBox.get())
            self.appSettings["triggerLevel"] = str(newLevelSetting)
        except:
            newLevelSetting = float(self.appSettings["triggerLevel"])
            self.triggerLevelBox.delete(0, "end")
            self.triggerLevelBox.insert(0, str(newLevelSetting))

        # handle trigger hysteresis setting
        try:
            newHysteresisSetting = float(self.triggerHysteresisBox.get())
            self.appSettings["triggerHysteresis"] = str(newHysteresisSetting)
        except:
            newHysteresisSetting = float(self.appSettings["triggerHysteresis"])
            self.triggerHysteresisBox.delete(0, "end")
            self.triggerHysteresisBox.insert(0, str(newHysteresisSetting))

        self.updatePlot() # update plots to redraw the trigger cursor

    def onCmdRefreshCOMPorts(self):
        ports = serial.tools.list_ports.comports()
        self.currentlyAvailableCOMPorts.clear()
        for port, desc, hwid in sorted(ports):
            self.currentlyAvailableCOMPorts.append("{}".format(port))

        if len(self.currentlyAvailableCOMPorts) == 0:
            self.currentlyAvailableCOMPorts.append("NONE")

        self.zaberPortCombo.configure(values=self.currentlyAvailableCOMPorts)
        self.zaberPortCombo.set(self.currentlyAvailableCOMPorts[0])

        logging.info(f"COM ports refresh. Found: {self.currentlyAvailableCOMPorts}")

    def onCmdRefreshAMSPorts(self):
        ports = serial.tools.list_ports.comports()

        # prefer USB serial adapters, the AMS-DIG-USB appears as ttyUSB*/ACM*
        usbPorts = []
        allPorts = []
        for port, desc, hwid in sorted(ports):
            portName = "{}".format(port)
            allPorts.append(portName)
            if "usb" in portName.lower() or "acm" in portName.lower():
                usbPorts.append(portName)

        availablePorts = usbPorts if len(usbPorts) > 0 else allPorts
        if len(availablePorts) == 0:
            availablePorts = ["NONE"]

        self.amsPortCombo.configure(values=availablePorts)

        savedPort = self.appSettings.get("amsPort", "")
        if savedPort in availablePorts:
            self.amsPortCombo.set(savedPort)
        else:
            self.amsPortCombo.set(availablePorts[0])

        logging.info(f"AMS ports refresh. Found: {availablePorts}")

    def onCmdSingleCapture(self):
        self._updateSimulatedNoiseLevel()
        self._updateDigilentRange()

        self._storeSamplingFrequencySettings()

        logging.info(f"Single capture started")

        self.appSettings["apodizationWindow"] = self.apodizationTypeCombo.get()

        self.settingsUsedForCurrentMeasurement = self.appSettings.copy()
        self.settingsUsedForCurrentMeasurement["averagingCount"] = 1

        if self.appSettings["triggerModeEnabled"] == "True":
            trgMode = True
        else:
            trgMode = False

        self.ApplicationController.performMeasurements(measurementsCount=1,
                                                       samplingFrequency=self._samplingIndexFromCombo(
                                                           self.samplingFreqCombo, self.mainSamplingRateStrings),
                                                       referenceSamplingFrequency=self._getReferenceSamplingFrequencyIndex(),
                                                       scanStart=int(self.appSettings["delayLineConfiguredScanStart"]),
                                                       scanLength=int(self.appSettings["delayLineConfiguredScanLength"]),
                                                       scanSpeed=float(self.appSettings["delayLineConfiguredScanSpeed"]),
                                                       trigModeEnabled=trgMode,
                                                       trigLevel=float(self.appSettings["triggerLevel"]),
                                                       trigHysteresis=float(self.appSettings["triggerHysteresis"]),
                                                       trigReference=float(self.appSettings["triggerReference"]),
                                                       apodizationWindow = self.apodizationTypeCombo.get())

    def onCmdMultipleCapture(self):

        self._updateSimulatedNoiseLevel()
        self._updateDigilentRange()

        try:
            measCount = int(self.appSettings["averagingCount"])
        except:
            logging.info(f"Multiple captures failed due to incorrect data in the measurement count setting")
            self.updateStatusMessage("Incorrect data in the\nmeasurement count setting")
            return

        self._storeSamplingFrequencySettings()

        self.appSettings["apodizationWindow"] = self.apodizationTypeCombo.get()
        self.settingsUsedForCurrentMeasurement["apodizationWindow"] = self.apodizationTypeCombo.get()

        logging.info(f"Multiple captures with averaging started. Count = {measCount}")

        # trigger mode
        if self.appSettings["triggerModeEnabled"] == "True":
            trgMode = True
        else:
            trgMode = False

        # actual mesurement order
        self.ApplicationController.performMeasurements(measurementsCount=measCount,
                                                       samplingFrequency=self._samplingIndexFromCombo(
                                                           self.samplingFreqCombo, self.mainSamplingRateStrings),
                                                       referenceSamplingFrequency=self._getReferenceSamplingFrequencyIndex(),
                                                       scanStart=int(self.appSettings["delayLineConfiguredScanStart"]),
                                                       scanLength=int(self.appSettings["delayLineConfiguredScanLength"]),
                                                       scanSpeed=float(self.appSettings["delayLineConfiguredScanSpeed"]),
                                                       trigModeEnabled=trgMode,
                                                       trigLevel=float(self.appSettings["triggerLevel"]),
                                                       trigHysteresis=float(self.appSettings["triggerHysteresis"]),
                                                       trigReference=float(self.appSettings["triggerReference"]),
                                                       apodizationWindow = self.apodizationTypeCombo.get())
    def onCmdStopMeasurement(self):
        self.ApplicationController.requestStop()

    def onCmdUpdateScanLengthFromSlider(self, other):
        sliderSetting = self.scanLengthSlider.get()
        self.scanLengthBox.delete(0, "end")
        self.scanLengthBox.insert(0, str(int(sliderSetting)))
        self.appSettings["delayLineConfiguredScanLength"] = str(sliderSetting)

    def onCmdUpdateScanLengthFromBox(self, other):
        minSetting = int(self.appSettings["delayLineMinimalScanLength"])
        maxSetting =  (ZaberDriver.DelayLineNominalLength -
                       (ZaberDriver.DelayLineNominalLength - int(self.appSettings["delayLineConfiguredScanStart"])))

        try:
            newSetting = int(self.scanLengthBox.get())
        except:
            newSetting = (maxSetting - minSetting) / 2

        if newSetting > maxSetting:
            newSetting = maxSetting
        elif newSetting < minSetting:
            newSetting = minSetting

        self.scanLengthBox.delete(0, "end")
        self.scanLengthBox.insert(0, str(newSetting))
        self.scanLengthSlider.set(newSetting)
        self.appSettings["delayLineConfiguredScanLength"] = str(newSetting)

    def onExportSwitchModified(self):
        self.appSettings["saveDataToMAT"] = self.exportToMatSwitch.get()
        self.appSettings["saveRawData"] = self.exportRawDataSwitch.get()

        print(self.appSettings["saveDataToMAT"])
        print(self.appSettings["saveRawData"])

    def onCmdUpdateStartingPositionFromBox(self, other):
        minSetting = 2000
        maxSetting = ZaberDriver.DelayLineNominalLength

        try:
            newSetting = int(self.startingPosBox.get())
        except:
            newSetting = 149000

        if newSetting > maxSetting:
            newSetting = maxSetting
        elif newSetting < minSetting:
            newSetting = minSetting

        self.startingPosBox.delete(0, "end")
        self.startingPosBox.insert(0, str(newSetting))

        self.appSettings["delayLineConfiguredScanStart"] = str(newSetting)
        configuredStartingPosition = int(self.appSettings["delayLineConfiguredScanStart"])
        minimalScanLength = int(self.appSettings["delayLineMinimalScanLength"])

        logging.info(f"Scan starting position set to: {configuredStartingPosition}")

        # make sure scan length settings are valid and slider is configured correctly
        self.scanLengthSlider.configure(to=ZaberDriver.DelayLineNominalLength -
                                           (ZaberDriver.DelayLineNominalLength -
                                           configuredStartingPosition))
        self.scanLengthSlider.configure(number_of_steps=(ZaberDriver.DelayLineNominalLength -
                                                    (ZaberDriver.DelayLineNominalLength -
                                                    configuredStartingPosition) -
                                                    minimalScanLength) / 100)
        # use this command to make sure settings are ok
        self.onCmdUpdateScanLengthFromBox(None)

    def onCmdScanSpeedUpdateFromSlider(self, other):
        sliderSetting = self.scanSpeedSlider.get()

        proportional = sliderSetting / int(self.appSettings["delayLineSpeedSliderTicks"])
        sliderRange = (float(self.appSettings["delayLineMaximumSpeed"]) -
                       float(self.appSettings["delayLineMinimumSpeed"]))

        configuredScanSpeed = proportional * sliderRange + float(self.appSettings["delayLineMinimumSpeed"])

        self.scanSpeedValueLabel.configure(text="{:.1f}".format(configuredScanSpeed))
        self.appSettings["delayLineConfiguredScanSpeed"] = "{:.1f}".format(configuredScanSpeed)

    def onCmdUpdateAveragingCount(self, other):

        try:
            newSetting = int(self.multipleMeasBox.get().replace(' ', '').replace('\t', '').replace('\n', '').replace(
            '\r', ''))

            if 1 < newSetting <= 10000:
                self.appSettings["averagingCount"] = str(newSetting)
                self.multipleMeasBox.delete(0, "end")
                self.multipleMeasBox.insert(0, self.appSettings["averagingCount"])
            else:
                self.multipleMeasBox.delete(0, "end")
                self.multipleMeasBox.insert(0, self.appSettings["averagingCount"])

        except:
            self.multipleMeasBox.delete(0, "end")
            self.multipleMeasBox.insert(0, self.appSettings["averagingCount"])

    def onCmdUnusedButton(self):
        print("Unused button click")

    def onCmdUnitRadioCM(self):
        if self.xUnitRadioCMVar.get() != 0:
            self.currentXUnit = "cm-1"

        self.xUnitRadioUM.deselect()
        self.xUnitRadioFreq.deselect()

        self.updatePlot()

    def onCmdUnitRadioUM(self):
        if self.xUnitRadioUMVar.get() != 0:
            self.currentXUnit = "um"

        self.xUnitRadioCM.deselect()
        self.xUnitRadioFreq.deselect()

        self.updatePlot()

    def convertUMtoCM(self, dataInUM):
        return (1.0 / dataInUM) * 10000.0

    def convertUMtoTHz(self, dataInUM):
        return 299.792458 / dataInUM

    def onCmdUnitRadioTHz(self):
        if self.xUnitRadioFreqVar.get() != 0:
            self.currentXUnit = "thz"

        self.xUnitRadioUM.deselect()
        self.xUnitRadioCM.deselect()

        self.updatePlot()

    def onCmdOpenAbsorbanceTool(self):
        self.absorbanceToolWindow = AbsorbanceTool.AbsorbanceTool(self.root, self.appSettings, self.plotLineColor)

        self.absorbanceToolWindow.grabSampleSpectrumDataAverage = self.giveSpectrumForAbsorbanceAverage
        self.absorbanceToolWindow.grabReferenceSpectrumDataAverage = self.giveSpectrumForAbsorbanceAverage
        self.absorbanceToolWindow.grabSampleSpectrumDataLast = self.giveSpectrumForAbsorbanceLast
        self.absorbanceToolWindow.grabReferenceSpectrumDataLast = self.giveSpectrumForAbsorbanceLast
        self.absorbanceToolWindow.grabApplicationSettings = self.getApplicationSettings
        self.absorbanceToolWindow.setApplicationSettings = self.setApplicationSettings

    def onCmdOpenAdjustmentTool(self):
        if self.daqTypeCombo.get() not in ("MFLI", "ADP2230"):
            messagebox.showwarning("Adjustment tool",
                                   "The adjustment tool requires the MFLI or ADP2230 DAQ type.\n"
                                   "Select one as the main DAQ and connect.")
            return
        SM.saveSettingsToFile(self.appSettings)     # try to maintain settings coherence
        self.adjustmentToolWindow = AdjustmentTool.AdjustmentTool(self.root, self.ZaberDrv, self.DAQDriver)

    def onCmdUpdateSpectrumPlotRanges(self, other):
        self.appSettings["plotSpectrumXRangeMin"] = self.spectrumXMinBox.get()
        self.appSettings["plotSpectrumXRangeMax"] = self.spectrumXMaxBox.get()
        self.appSettings["plotSpectrumYRangeMin"] = self.spectrumYMinBox.get()
        self.appSettings["plotSpectrumYRangeMax"] = self.spectrumYMaxBox.get()

        ymin = float(self.appSettings["plotSpectrumYRangeMin"])
        ymax = float(self.appSettings["plotSpectrumYRangeMax"])
        xmin = float(self.appSettings["plotSpectrumXRangeMin"])
        xmax = float(self.appSettings["plotSpectrumXRangeMax"])

        # self.axBot.set_xlim(xmin, xmax)
        # self.axBot.set_ylim(ymin, ymax)

        logging.info(f"Spectrum plot ranges updated to: X({xmin},{xmax}) Y({ymin},{ymax})")

        # self.canvasBotPlot.draw()
        # self.root.update()
        self.updatePlot()

    def _parseMFLIIdentifier(self):
        # the MFLI ID box holds "devXXXX;hostname-or-IP"; the host part is
        # optional and defaults to the local data server
        strippedMFLIID = self.mfliIDBox.get("0.0", "end").replace(' ', '').replace('\t', '').replace('\n', '').replace(
            '\r', '')
        self.appSettings["mfliDeviceID"] = strippedMFLIID

        identifierParts = strippedMFLIID.split(';')
        deviceID = identifierParts[0]
        serverHost = identifierParts[1] if len(identifierParts) > 1 else "localhost"
        return deviceID, serverHost

    def onCmdConnectHardware(self):
        daqType = self.daqTypeCombo.get()
        self.appSettings["daqType"] = daqType

        referenceSource = self.referenceSourceCombo.get()
        if daqType == "Simulated":
            referenceSource = "Internal"
            self.referenceSourceCombo.set(referenceSource)
        if daqType == "AMIS8140" and referenceSource == "Internal":
            messagebox.showerror("AMS-DIG-PROC", "The AMS board provides the detector signal only.\n"
                                                 "Select MFLI or ADP2230 as the reference source.")
            return
        self.appSettings["referenceDaqType"] = referenceSource

        # disconnect the previous drivers if they exist
        if self.DAQDriver is not None and hasattr(self.DAQDriver, 'disconnect'):
            self.DAQDriver.disconnect()
        if self.DAQRefDriver is not None and hasattr(self.DAQRefDriver, 'disconnect'):
            self.DAQRefDriver.disconnect()
        self.DAQRefDriver = None

        strippedZaberPort = self.zaberPortCombo.get().replace(' ', '').replace('\t', '').replace('\n', '').replace(
            '\r', '')

        try:
            # create the detector DAQ driver based on the selected type
            if daqType == "MFLI":
                detectedDeviceID, detectedServerHost = self._parseMFLIIdentifier()

                self.DAQDriver = MFLIDriver(detectedDeviceID)
                self.ApplicationController.DAQDriver = self.DAQDriver
                self.ApplicationController.setDAQDeviceName(detectedDeviceID)
                self.ApplicationController.setDAQIP(detectedServerHost)

                logging.info(f"Attempting to connect to hardware. Zaber port: {strippedZaberPort} and MFLI devID: {detectedDeviceID}")

            elif daqType == "ADP2230":
                self.appSettings["digilentRange"] = self.digilentRangeCombo.get()

                self.DAQDriver = DigilentDriver()
                self.DAQDriver.setChannelRange(float(self.digilentRangeCombo.get()))
                self.ApplicationController.DAQDriver = self.DAQDriver
                self.ApplicationController.setDAQDeviceName("ADP2230")
                self.ApplicationController.setDAQIP("")

                logging.info(f"Attempting to connect to hardware. Zaber port: {strippedZaberPort} and ADP2230")

            elif daqType == "AMIS8140":
                if not AMS_LIBRARY_AVAILABLE:
                    messagebox.showerror("AMS-DIG-PROC", "The ams-dig-proc library is not installed.\n"
                                                         "Install the requirements and try again.")
                    return

                amsPort = self.amsPortCombo.get().replace(' ', '').replace('\t', '').replace('\n', '').replace('\r', '')
                if len(amsPort) == 0 or amsPort == "NONE":
                    messagebox.showerror("AMS-DIG-PROC", "No serial port selected for the AMS board.")
                    return

                self.appSettings["amsPort"] = amsPort
                self.appSettings["amsDetectorTemperature"] = self.amsTemperatureBox.get()
                self.appSettings["amsWaitForTemperature"] = self.amsWaitTemperatureSwitch.get()

                try:
                    configuredTemperature = float(self.amsTemperatureBox.get())
                except Exception:
                    configuredTemperature = 0.0

                self.DAQDriver = AMSSerialDriver()
                self.DAQDriver.setDetectorTemperature(configuredTemperature)
                self.DAQDriver.setWaitForTemperature(self.amsWaitTemperatureSwitch.get() == "True")
                self.ApplicationController.DAQDriver = self.DAQDriver
                self.ApplicationController.setDAQDeviceName(amsPort)
                self.ApplicationController.setDAQIP("")

                logging.info(f"Attempting to connect to hardware. Zaber port: {strippedZaberPort} and AMS port: {amsPort}")

            elif daqType == "Simulated":
                self.appSettings["simulatedNoiseLevel"] = str(self.simulatedNoiseSlider.get())

                self.DAQDriver = SimulatedDAQ()
                self.DAQDriver.setNoiseLevel(self.simulatedNoiseSlider.get())
                self.ApplicationController.DAQDriver = self.DAQDriver
                self.ApplicationController.setDAQDeviceName("Simulated")
                self.ApplicationController.setDAQIP("")

                logging.info(f"Attempting to connect to hardware. Zaber port: {strippedZaberPort} and Simulated DAQ")

            # optionally create a separate reference DAQ driver
            if referenceSource == "MFLI":
                detectedDeviceID, detectedServerHost = self._parseMFLIIdentifier()

                self.DAQRefDriver = MFLIDriver(detectedDeviceID)
                self.ApplicationController.setReferenceDAQDeviceName(detectedDeviceID)
                self.ApplicationController.setReferenceDAQIP(detectedServerHost)

                logging.info(f"Reference DAQ: MFLI devID: {detectedDeviceID}")

            elif referenceSource == "ADP2230":
                self.appSettings["digilentRange"] = self.digilentRangeCombo.get()

                self.DAQRefDriver = DigilentDriver()
                self.DAQRefDriver.setChannelRange(float(self.digilentRangeCombo.get()))
                self.ApplicationController.setReferenceDAQDeviceName("ADP2230")
                self.ApplicationController.setReferenceDAQIP("")

                logging.info(f"Reference DAQ: ADP2230")

        except Exception as e:
            logging.info(f"Hardware connection setup failed: {e}")
            messagebox.showerror("Hardware connection", f"Hardware connection setup failed:\n{e}")
            return

        self.ApplicationController.setReferenceDAQDriver(self.DAQRefDriver, referenceSource)
        self.ApplicationController.daqType = daqType
        self.ApplicationController.setZaberPort(strippedZaberPort)
        self.ApplicationController.performInitialization()

    def _refreshReferenceSourceCombo(self):
        mainType = self.daqTypeCombo.get()

        # the reference cannot be captured by the same interface instance as
        # the detector signal; "Internal" means one DAQ provides both channels
        if mainType == "MFLI":
            allowedValues = ["Internal", "ADP2230"]
        elif mainType == "ADP2230":
            allowedValues = ["Internal", "MFLI"]
        elif mainType == "AMIS8140":
            allowedValues = ["MFLI", "ADP2230"]
        else:
            allowedValues = ["Internal"]

        self.referenceSourceCombo.configure(values=allowedValues)

        currentValue = self.referenceSourceCombo.get()
        if currentValue not in allowedValues:
            currentValue = allowedValues[0]
            self.referenceSourceCombo.set(currentValue)

        self.appSettings["referenceDaqType"] = currentValue

    def _refreshMainSamplingCombo(self):
        mainType = self.daqTypeCombo.get()

        if mainType == "AMIS8140":
            rates = list(AMSSerialDriver.SamplingRates)
            settingsKey = "amsSelectedFrequencyIndex"
        else:
            rates = list(MFLIDriver.MFLISamplingRates)
            settingsKey = "mfliSelectedFrequencyIndex"

        self.mainSamplingRates = rates
        self.mainSamplingRateStrings = [si.si_format(f, precision=2) + "Hz" for f in rates]

        self.samplingFreqCombo.configure(values=self.mainSamplingRateStrings)

        index = int(self.appSettings.get(settingsKey, "0"))
        if not 0 <= index < len(self.mainSamplingRateStrings):
            index = 0
        self.samplingFreqCombo.set(self.mainSamplingRateStrings[index])

    def _refreshReferenceSamplingCombo(self):
        # the supported reference DAQs (MFLI, ADP2230) share the same rate table
        rates = list(MFLIDriver.MFLISamplingRates)
        self.referenceSamplingRates = rates
        self.referenceSamplingRateStrings = [si.si_format(f, precision=2) + "Hz" for f in rates]

        self.referenceFreqCombo.configure(values=self.referenceSamplingRateStrings)

        index = int(self.appSettings.get("referenceSelectedFrequencyIndex", "12"))
        if not 0 <= index < len(self.referenceSamplingRateStrings):
            index = 0
        self.referenceFreqCombo.set(self.referenceSamplingRateStrings[index])

        self._setReferenceFreqVisible()

    @staticmethod
    def _samplingIndexFromCombo(combo, rateStrings):
        try:
            return rateStrings.index(combo.get())
        except ValueError:
            return 0

    def _storeSamplingFrequencySettings(self):
        mainType = self.daqTypeCombo.get()
        mainIndex = self._samplingIndexFromCombo(self.samplingFreqCombo, self.mainSamplingRateStrings)

        if mainType == "AMIS8140":
            self.appSettings["amsSelectedFrequencyIndex"] = str(mainIndex)
        else:
            self.appSettings["mfliSelectedFrequencyIndex"] = str(mainIndex)

        if self._hasExternalReference():
            referenceIndex = self._samplingIndexFromCombo(self.referenceFreqCombo, self.referenceSamplingRateStrings)
            self.appSettings["referenceSelectedFrequencyIndex"] = str(referenceIndex)

    def _getReferenceSamplingFrequencyIndex(self):
        if self._hasExternalReference():
            return self._samplingIndexFromCombo(self.referenceFreqCombo, self.referenceSamplingRateStrings)
        return None

    def _showDAQControls(self):
        mainType = self.daqTypeCombo.get()
        referenceType = self.referenceSourceCombo.get()

        # hide all DAQ-specific controls first
        for widget in [self.mfliIDLabel, self.mfliIDBox,
                       self.digilentRangeLabel, self.digilentRangeCombo, self.digilentInfoLabel,
                       self.simulatedNoiseLabel, self.simulatedNoiseSlider,
                       self.amsPortLabel, self.amsPortCombo, self.amsPortRefreshButton,
                       self.amsTemperatureLabel, self.amsTemperatureBox,
                       self.amsWaitTemperatureSwitch, self.amsTemperatureValueLabel]:
            widget.grid_forget()

        # main DAQ specific controls (first line)
        if mainType == "MFLI":
            self.mfliIDLabel.grid(row=8, column=0, sticky="W", padx=5, pady=5)
            self.mfliIDBox.grid(row=8, column=1, sticky="E", padx=5, pady=5)

        elif mainType == "ADP2230":
            self.digilentRangeLabel.grid(row=8, column=0, sticky="W", padx=5, pady=5)
            self.digilentRangeCombo.grid(row=8, column=1, sticky="E", padx=5, pady=5)

        elif mainType == "Simulated":
            self.simulatedNoiseLabel.grid(row=8, column=0, sticky="W", padx=5, pady=5)
            self.simulatedNoiseSlider.grid(row=8, column=1, sticky="EW", padx=5, pady=5)
            self.simulatedNoiseSlider.set(float(self.appSettings.get("simulatedNoiseLevel", "0.1")))
            self._updateNoiseLabel()

        elif mainType == "AMIS8140":
            self.onCmdRefreshAMSPorts()
            self.amsPortLabel.grid(row=8, column=0, sticky="W", padx=5, pady=5)
            self.amsPortCombo.grid(row=8, column=1, sticky="E", padx=5, pady=5)
            self.amsPortRefreshButton.grid(row=8, column=2, sticky="W", padx=2, pady=5)

            self.amsTemperatureLabel.grid(row=10, column=0, sticky="W", padx=5, pady=5)
            self.amsTemperatureBox.grid(row=10, column=1, sticky="E", padx=5, pady=5)
            self.amsWaitTemperatureSwitch.grid(row=11, column=0, columnspan=2, sticky="W", padx=5, pady=2)
            self.amsTemperatureValueLabel.grid(row=12, column=0, columnspan=2, sticky="W", padx=5, pady=2)

        # reference DAQ specific controls (second line) — only needed when the
        # main DAQ does not already provide them
        if referenceType == "MFLI" and mainType != "MFLI":
            self.mfliIDLabel.grid(row=9, column=0, sticky="W", padx=5, pady=5)
            self.mfliIDBox.grid(row=9, column=1, sticky="E", padx=5, pady=5)

        elif referenceType == "ADP2230" and mainType != "ADP2230":
            self.digilentRangeLabel.grid(row=9, column=0, sticky="W", padx=5, pady=5)
            self.digilentRangeCombo.grid(row=9, column=1, sticky="E", padx=5, pady=5)

    def onCmdDAQTypeChanged(self, selectedType):
        self.appSettings["daqType"] = selectedType
        self._refreshReferenceSourceCombo()
        self._refreshMainSamplingCombo()
        self._refreshReferenceSamplingCombo()
        self._showDAQControls()
        self._updateDaqControlStates()
        self._updateCaptureButtons()

    def onCmdReferenceSourceChanged(self, selectedSource):
        self.appSettings["referenceDaqType"] = selectedSource
        self._refreshReferenceSamplingCombo()
        self._showDAQControls()
        self._updateDaqControlStates()
        self._updateCaptureButtons()

    def onCmdSimulatedNoiseChanged(self, value):
        self.appSettings["simulatedNoiseLevel"] = str(value)
        self._updateNoiseLabel()

    def _updateNoiseLabel(self):
        self.simulatedNoiseLabel.configure(text=f"Noise: {self.simulatedNoiseSlider.get():.2f}")

    def _updateSimulatedNoiseLevel(self):
        if self.DAQDriver is not None and isinstance(self.DAQDriver, SimulatedDAQ):
            self.DAQDriver.setNoiseLevel(self.simulatedNoiseSlider.get())

    def _updateDigilentRange(self):
        if self.DAQDriver is not None and isinstance(self.DAQDriver, DigilentDriver):
            self.DAQDriver.setChannelRange(float(self.digilentRangeCombo.get()))

    def _pollAMSStatus(self):
        # periodically refresh the AMS detector temperature display; this is
        # skipped while a measurement is running so that no data messages are
        # consumed by the status reader
        try:
            driver = self.DAQDriver
            if (driver is not None and isinstance(driver, AMSSerialDriver) and
                    driver.isConnected and not driver.measurementInProgress):
                driver.refreshStatus()
                if driver.detectorTemperatureK is not None:
                    temperatureText = f"Detector: {driver.detectorTemperatureK:.2f} K"
                    if driver.detectorTemperatureOk:
                        temperatureText = temperatureText + " (stable)"
                    self.amsTemperatureValueLabel.configure(text=temperatureText)
        except Exception:
            pass

        self.root.after(2000, self._pollAMSStatus)

    def onCmdOpenSpectrumPlot(self):
        logging.info(f"External spectrum plot open")

        mpl.rcParams.update(mpl.rcParamsDefault)
        plt.figure()
        plt.locator_params(nbins=15)
        plt.rc('xtick', labelsize=18)
        plt.rc('ytick', labelsize=18)
        plt.title("Spectrum", fontsize=20)

        # display X axis with units selected by the user
        if self.currentXUnit == "um":

            plt.xlim(float(self.appSettings["plotSpectrumXRangeMin"]),
                                float(self.appSettings["plotSpectrumXRangeMax"]))
            plt.xlabel('Wavelength [\u03BCm]', fontsize=20)
            plt.plot(self.currentAverageSpectrumX, self.currentAverageSpectrumY, color="dodgerblue")
            # plt.xscale("linear")

        elif self.currentXUnit == "thz":
            plt.xlim(self.convertUMtoTHz(float(self.appSettings["plotSpectrumXRangeMin"])),
                                self.convertUMtoTHz(float(self.appSettings["plotSpectrumXRangeMax"])))
            plt.xlabel('Frequency [THz]', fontsize=20)
            plt.semilogx(self.convertUMtoTHz(self.currentAverageSpectrumX),
                         self.currentAverageSpectrumY, color="dodgerblue")
            # plt.xscale("log")

        else:
            plt.xlim(self.convertUMtoCM(float(self.appSettings["plotSpectrumXRangeMin"])),
                                self.convertUMtoCM(float(self.appSettings["plotSpectrumXRangeMax"])))
            plt.xlabel('Wavenumber [cm-1]', fontsize=20)
            plt.plot(self.convertUMtoCM(self.currentAverageSpectrumX),
                     self.currentAverageSpectrumY, color="dodgerblue")
            # plt.xscale("linear")

        plt.ylim((float(self.appSettings["plotSpectrumYRangeMin"]), float(self.appSettings["plotSpectrumYRangeMax"])))
        # plt.xlabel("Wavelength [\u03BCm]", fontsize=20)
        plt.ylabel("Intensity [dBm]", fontsize=20)
        # plt.plot(self.currentAverageSpectrumX, self.currentAverageSpectrumY, color="dodgerblue")
        # plt.xlim((float(self.appSettings["plotSpectrumXRangeMin"]), float(self.appSettings["plotSpectrumXRangeMax"])))
        plt.grid(alpha=0.3)
        plt.ion()
        plt.pause(1.0)
        plt.show()
        plt.pause(1.0)
        plt.ioff()

    def onCmdOpenInterferogramPlot(self):
        logging.info(f"External interferogram plot open")

        mpl.rcParams.update(mpl.rcParamsDefault)
        plt.figure()
        plt.locator_params(nbins=15)
        plt.rc('xtick', labelsize=18)
        plt.rc('ytick', labelsize=18)
        plt.title("Interferogram", fontsize=20)
        plt.xlabel("Position [\u03BCm]", fontsize=20)
        plt.ylabel("Detector voltage [V]", fontsize=20)
        plt.plot(self.currentInterferogramX, self.currentInterferogramY)
        plt.xlim((min(self.currentInterferogramX), max(self.currentInterferogramX)))
        plt.grid(alpha=0.3)
        plt.ion()
        plt.pause(1.0)
        plt.show()
        plt.pause(1.0)
        plt.ioff()

    def onCmdReferencePlot(self):
        logging.info(f"Reference signal plot open")

        if self._hasExternalReference():
            referenceDriver = self.DAQRefDriver
        else:
            referenceDriver = self.DAQDriver

        if referenceDriver is None or getattr(referenceDriver, "lastReferenceData", None) is None:
            messagebox.showwarning("Reference signal", "No reference signal data available yet")
            return

        mpl.rcParams.update(mpl.rcParamsDefault)
        plt.figure()
        plt.locator_params(nbins=15)
        plt.rc('xtick', labelsize=18)
        plt.rc('ytick', labelsize=18)
        plt.title("Raw data with reference signal", fontsize=20)
        plt.xlabel("Sample num", fontsize=20)
        plt.ylabel("Detector voltage [V]", fontsize=20)
        plt.plot(referenceDriver.lastReferenceData, alpha=0.75)
        if self.DAQDriver is not None and getattr(self.DAQDriver, "lastInterferogramData", None) is not None:
            plt.plot(self.DAQDriver.lastInterferogramData)
        plt.grid(alpha=0.3)
        plt.ion()
        plt.pause(1.0)
        plt.show()
        plt.pause(1.0)
        plt.ioff()

    def onCmdSaveCSVOnly(self):
        DataExportTool.exportSpectrumAsCSV(self.currentSpectrumX, self.currentSpectrumY)

    def onCmdSaveFull(self):

        self.settingsUsedForCurrentMeasurement["saveDataToMAT"] = self.appSettings["saveDataToMAT"]
        self.settingsUsedForCurrentMeasurement["saveRawData"] = self.appSettings["saveRawData"]

        DataExportTool.exportAllDataMultipleMeasurements(
            averageSpectrumX            = self.currentAverageSpectrumX,
            averageSpectrumY            = self.currentAverageSpectrumY,
            rawSpectraX                 = self.ApplicationController.spectraX,
            rawSpectraY                 = self.ApplicationController.spectraY,
            correctedInterferogramsX    = self.ApplicationController.processedInterferogramsX,
            correctedInterferogramsY    = self.ApplicationController.processedInterferogramsY,
            interferogramsRaw           = self.ApplicationController.rawInterferograms,
            referenceSignalsRaw         = self.ApplicationController.rawReferenceSignals,
            settings                    = self.settingsUsedForCurrentMeasurement,
            comments                    = self.commentsTextBox.get("0.0", "end")
        )

    def onCmdSaveH5(self):
        exportSettings = self.settingsUsedForCurrentMeasurement.copy()
        exportSettings["appVersion"] = self.appVersion

        DataExportTool.exportRawInterferogramsToH5(
            interferogramsRaw       = self.ApplicationController.rawInterferograms,
            referenceSignalsRaw     = self.ApplicationController.rawReferenceSignals,
            interferogramTimestamps = self.ApplicationController.interferogramTimestamps,
            settings                = exportSettings,
            comments                = self.commentsTextBox.get("0.0", "end")
        )

    def onClosing(self):
        if self.DAQDriver is not None and hasattr(self.DAQDriver, 'disconnect'):
            self.DAQDriver.disconnect()
        if self.DAQRefDriver is not None and hasattr(self.DAQRefDriver, 'disconnect'):
            self.DAQRefDriver.disconnect()
        SM.saveSettingsToFile(self.appSettings)
        # make sure the application closes properly when the main window is destroyed
        logging.info('========= Application closed =========\n\n\n')
        sys.exit()

    def updatePlot(self):
        self.loadDataToPlots(self.currentInterferogramX, self.currentInterferogramY,
                             self.currentSpectrumX, self.currentSpectrumY,
                             self.currentAverageSpectrumX, self.currentAverageSpectrumY, 0,
                             self.currentApodizationWindow)

    def giveSpectrumForAbsorbanceLast(self):
        return ('Wavelength [\u03BCm]', 'Intensity [dBm]',
                self.currentSpectrumX.copy(), self.currentSpectrumY.copy())

    def giveSpectrumForAbsorbanceAverage(self):
        return ('Wavelength [\u03BCm]', 'Intensity [dBm]',
                self.currentAverageSpectrumX.copy(), self.currentAverageSpectrumY.copy())
    def loadDataToPlots(self, interferogramX, interferogramY, spectrumX, spectrumY, averageSpectrumX, averageSpectrumY,
                        completedMeasurements, apodizationWindow):

        if completedMeasurements != 0:
            self.multipleMeasBox.delete(0, "end")
            self.multipleMeasBox.insert(0,
                                    f"{completedMeasurements}/{self.ApplicationController.orderedMeasurementsCount}")

        plt.style.use('dark_background')
        self.axBot.clear()
        self.axTop.clear()

        # plot spectrum
        self.axBot.grid(color="dimgrey", linestyle='-', linewidth=1, alpha=0.6)

        self.axBot.set_ylim(float(self.appSettings["plotSpectrumYRangeMin"]),
                            float(self.appSettings["plotSpectrumYRangeMax"]))

        self.axBot.set_ylabel('[dBm]')

        # display X axis with units selected by the user
        spectrumXAxisToPlotLast = None
        spectrumXAxisToPlotAverage = None

        if self.currentXUnit == "um":
            self.axBot.set_xlim(float(self.appSettings["plotSpectrumXRangeMin"]),
                                float(self.appSettings["plotSpectrumXRangeMax"]))
            self.axBot.set_xlabel('Wavelength [\u03BCm]')
            self.axBot.set_xscale("linear")

            start, end = self.axBot.get_xlim()
            self.axBot.xaxis.set_ticks(np.linspace(start, end, 10))

            spectrumXAxisToPlotLast = spectrumX
            spectrumXAxisToPlotAverage = averageSpectrumX

        elif self.currentXUnit == "thz":
            self.axBot.set_xlim(self.convertUMtoTHz(float(self.appSettings["plotSpectrumXRangeMin"])),
                                self.convertUMtoTHz(float(self.appSettings["plotSpectrumXRangeMax"])))
            self.axBot.set_xlabel('Frequency [THz]')
            self.axBot.set_xscale("log")

            start, end = self.axBot.get_xlim()
            print(f"{start}, {end}")
            print(np.geomspace(end, start, 10))
            self.axBot.xaxis.set_ticks(np.geomspace(end, start, 10))
            self.axBot.xaxis.set_major_formatter(mpl.ticker.ScalarFormatter())

            if spectrumX is not None and len(spectrumX) > 0:
                spectrumXAxisToPlotLast = self.convertUMtoTHz(spectrumX)
                spectrumXAxisToPlotAverage = self.convertUMtoTHz(averageSpectrumX)

        else:
            self.axBot.set_xlim(self.convertUMtoCM(float(self.appSettings["plotSpectrumXRangeMin"])),
                                self.convertUMtoCM(float(self.appSettings["plotSpectrumXRangeMax"])))

            self.axBot.set_xlabel('Wavenumber [cm-1]')
            self.axBot.set_xscale("linear")

            start, end = self.axBot.get_xlim()
            self.axBot.xaxis.set_ticks(np.linspace(start, end, 10))

            if spectrumX is not None and len(spectrumX) > 0:
                spectrumXAxisToPlotLast = self.convertUMtoCM(spectrumX)
                spectrumXAxisToPlotAverage = self.convertUMtoCM(averageSpectrumX)

        if ((spectrumXAxisToPlotLast is not None) and (spectrumY is not None) and
                (len(spectrumXAxisToPlotLast) == len(spectrumY)) and (len(spectrumXAxisToPlotLast) > 0)):

            self.axBot.plot(spectrumXAxisToPlotLast, spectrumY, color="grey", alpha=0.7)

        if ((spectrumXAxisToPlotAverage is not None) and (averageSpectrumY is not None) and
                (len(spectrumXAxisToPlotAverage) == len(averageSpectrumY)) and (len(spectrumXAxisToPlotAverage) > 0)):

            self.axBot.plot(spectrumXAxisToPlotAverage, averageSpectrumY, color=self.plotLineColor)

        # plot interferogram
        self.axTop.grid(color="dimgrey", linestyle='-', linewidth=1, alpha=0.6)

        interferogramValidForDisplay = False
        if (interferogramX is not None and interferogramY is not None and
                len(interferogramX) == len(interferogramY) and len(interferogramX) > 0):

            interferogramValidForDisplay = True

        # draw trigger and hysteresis cursors
        if self.appSettings["triggerModeEnabled"] == 'True':
            # make sure the markers will be displayed correctly even if there is no interferogram loaded
            if interferogramValidForDisplay:
                triggerMarkerX = [np.min(interferogramX), np.max(interferogramX)]
                hysteresisMarkerX = [np.min(interferogramX), np.max(interferogramX)]
            else:
                triggerMarkerX = [0.0, 1.0]
                hysteresisMarkerX = [0.0, 1.0]
                self.axTop.set_xlim(0.0, 1.0)
                self.axTop.set_ylim(- float(self.appSettings["triggerLevel"]) / 1000.0 - 0.1,
                                    float(self.appSettings["triggerLevel"]) / 1000.0 + 0.1)

            triggerMarkerY = [float(self.appSettings["triggerLevel"]) / 1000.0,
                       float(self.appSettings["triggerLevel"]) / 1000.0]

            self.axTop.plot(triggerMarkerX, triggerMarkerY, color="red", alpha=0.75,
                            label="Trigger", linestyle="dashed")

            # hysteresisMarkerYVal = ((float(self.appSettings["triggerLevel"]) / 1000.0) -
            #                         (float(self.appSettings["triggerHysteresis"]) / 1000.0))

            hysteresisMarkerYVal = (float(self.appSettings["triggerHysteresis"]) / 1000.0)

            hysteresisMarkerY = [hysteresisMarkerYVal, hysteresisMarkerYVal]

            self.axTop.plot(hysteresisMarkerX, hysteresisMarkerY, color="lime", alpha=0.75,
                            label="Hysteresis", linestyle="dashed")

            self.axTop.set_xlabel('Mirror position [\u03BCm]')
            self.axTop.set_ylabel('Detector voltage [V]')

        # plot the interferogram itself
        if interferogramValidForDisplay:
            self.axTop.plot(interferogramX, apodizationWindow * np.max(interferogramY), color="grey", alpha=0.7)
            self.axTop.plot(interferogramX, interferogramY, color=self.plotLineColor, label="Signal")
            self.axTop.set_xlim(np.min(interferogramX), np.max(interferogramX))

        if self.appSettings["triggerModeEnabled"] == 'True':
            self.axTop.legend()

        self.canvasTopPlot.draw()
        self.canvasBotPlot.draw()
        self.root.update()


# run the app
if __name__ == "__main__":
    CTK_Window = FTSApp()
