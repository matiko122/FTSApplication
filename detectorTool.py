import time
from threading import *

import customtkinter as ctk
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from tkinter import messagebox


class DetectorTool:
    """Small tool window for the AMIS8140-01 detector (AMS-DIG-PROC board).

    Allows setting the detector temperature setpoint, reading it back and
    watching the detector output level live (continuous acquisition with
    mean / peak-peak / noise readouts and a level-over-time plot).
    """

    def __init__(self, root, grabDetectorDriver, setMonitoringActive,
                 initialTemperatureSetting, samplingRateStrings):

        self.root = root
        self.grabDetectorDriver = grabDetectorDriver
        self.setMonitoringActive = setMonitoringActive
        self.samplingRateStrings = samplingRateStrings

        self.monitoring = False
        self.monitoringStopFlag = False
        self.closed = False

        self.levelHistory = []
        self.timeHistory = []
        self.bufferCount = 0
        self.startTime = 0.0
        self.lastPlotRefresh = 0.0

        self.plotLineColor = "gold"
        self.backgroundGray = "#242424"

        ctk.set_appearance_mode("dark")
        self.window = ctk.CTkToplevel()
        self.window.protocol("WM_DELETE_WINDOW", self.onClosing)
        self.window.geometry("980x560")
        self.window.minsize(width=820, height=480)
        self.window.title("Detector level tool - AMIS8140-01 / AMS-DIG-PROC")
        self.window.resizable(True, True)

        self.window.columnconfigure(0, weight=0, minsize=330)
        self.window.columnconfigure(1, weight=1)
        self.window.rowconfigure(0, weight=1)

        # ----------------------------------------------------------------
        # controls column
        # ----------------------------------------------------------------
        self.controlsFrame = ctk.CTkFrame(master=self.window, fg_color="dimgrey", corner_radius=10)
        self.controlsFrame.grid(row=0, column=0, sticky="NSEW", padx=(8, 4), pady=8)
        self.controlsFrame.columnconfigure(0, weight=1)

        self.titleLabel = ctk.CTkLabel(master=self.controlsFrame, text="Detector level tool",
                                       font=ctk.CTkFont(size=16, weight="bold"))
        self.titleLabel.grid(row=0, column=0, sticky="W", padx=10, pady=(10, 0))

        self.subtitleLabel = ctk.CTkLabel(master=self.controlsFrame, text="AMIS8140-01 via AMS-DIG-PROC",
                                          font=ctk.CTkFont(size=11))
        self.subtitleLabel.grid(row=1, column=0, sticky="W", padx=10, pady=(0, 8))

        # --- detector temperature ---------------------------------------
        self.temperatureFrame = ctk.CTkFrame(master=self.controlsFrame, fg_color="gray18", corner_radius=8)
        self.temperatureFrame.grid(row=2, column=0, sticky="EW", padx=8, pady=4)
        self.temperatureFrame.columnconfigure(1, weight=1)

        self.temperatureHeader = ctk.CTkLabel(master=self.temperatureFrame, text="Detector temperature",
                                              font=ctk.CTkFont(size=12, weight="bold"))
        self.temperatureHeader.grid(row=0, column=0, columnspan=3, sticky="W", padx=8, pady=(6, 2))

        self.temperatureBox = ctk.CTkEntry(master=self.temperatureFrame, width=90, height=28)
        self.temperatureBox.insert(0, initialTemperatureSetting)
        self.temperatureBox.grid(row=1, column=0, sticky="W", padx=(8, 4), pady=4)

        self.setTemperatureButton = ctk.CTkButton(master=self.temperatureFrame, text="Set [K]",
                                                  width=80, height=28, corner_radius=8,
                                                  command=self.onSetTemperature)
        self.setTemperatureButton.grid(row=1, column=1, sticky="W", padx=4, pady=4)

        self.readSetpointButton = ctk.CTkButton(master=self.temperatureFrame, text="Read setpoint",
                                                width=110, height=28, corner_radius=8,
                                                command=self.onReadSetpoint)
        self.readSetpointButton.grid(row=2, column=0, columnspan=2, sticky="W", padx=8, pady=4)

        self.setpointLabel = ctk.CTkLabel(master=self.temperatureFrame, text="Setpoint: --",
                                          font=ctk.CTkFont(size=12))
        self.setpointLabel.grid(row=3, column=0, columnspan=3, sticky="W", padx=8, pady=(0, 2))

        self.reportedTemperatureLabel = ctk.CTkLabel(master=self.temperatureFrame, text="Reported: --",
                                                     font=ctk.CTkFont(size=12))
        self.reportedTemperatureLabel.grid(row=4, column=0, columnspan=3, sticky="W", padx=8, pady=(0, 8))

        # --- output level monitoring ------------------------------------
        self.monitorFrame = ctk.CTkFrame(master=self.controlsFrame, fg_color="gray18", corner_radius=8)
        self.monitorFrame.grid(row=3, column=0, sticky="EW", padx=8, pady=4)
        self.monitorFrame.columnconfigure(0, weight=1)

        self.monitorHeader = ctk.CTkLabel(master=self.monitorFrame, text="Output level",
                                          font=ctk.CTkFont(size=12, weight="bold"))
        self.monitorHeader.grid(row=0, column=0, sticky="W", padx=8, pady=(6, 2))

        self.rateLabel = ctk.CTkLabel(master=self.monitorFrame, text="Effective sample rate",
                                      font=ctk.CTkFont(size=12))
        self.rateLabel.grid(row=1, column=0, sticky="W", padx=8, pady=(4, 0))

        self.rateCombo = ctk.CTkComboBox(master=self.monitorFrame, values=self.samplingRateStrings,
                                         state="readonly", width=150, height=28)
        self.rateCombo.grid(row=2, column=0, sticky="W", padx=8, pady=(0, 4))
        if len(self.samplingRateStrings) > 0:
            self.rateCombo.set(self.samplingRateStrings[0])

        self.startButton = ctk.CTkButton(master=self.monitorFrame, text="Start", width=90, height=36,
                                         corner_radius=8, fg_color="darkgreen",
                                         command=self.onStartMonitoring)
        self.startButton.grid(row=3, column=0, sticky="W", padx=8, pady=4)

        self.stopButton = ctk.CTkButton(master=self.monitorFrame, text="Stop", width=90, height=36,
                                        corner_radius=8, fg_color="darkred", state="disabled",
                                        command=self.onStopMonitoring)
        self.stopButton.grid(row=3, column=0, sticky="E", padx=8, pady=4)

        self.levelLabel = ctk.CTkLabel(master=self.monitorFrame, text="Level: -- V",
                                       font=ctk.CTkFont(size=22, weight="bold"))
        self.levelLabel.grid(row=4, column=0, sticky="W", padx=8, pady=(8, 0))

        self.peakToPeakLabel = ctk.CTkLabel(master=self.monitorFrame, text="Peak-peak: -- V",
                                            font=ctk.CTkFont(size=12))
        self.peakToPeakLabel.grid(row=5, column=0, sticky="W", padx=8, pady=(2, 0))

        self.noiseLabel = ctk.CTkLabel(master=self.monitorFrame, text="Noise (std): -- V",
                                       font=ctk.CTkFont(size=12))
        self.noiseLabel.grid(row=6, column=0, sticky="W", padx=8, pady=(0, 8))

        self.statusLabel = ctk.CTkLabel(master=self.controlsFrame, text="", font=ctk.CTkFont(size=11))
        self.statusLabel.grid(row=4, column=0, sticky="W", padx=10, pady=(4, 8))

        # ----------------------------------------------------------------
        # plot column
        # ----------------------------------------------------------------
        self.plotFrame = ctk.CTkFrame(master=self.window, fg_color="darkblue")
        self.plotFrame.grid(row=0, column=1, sticky="NSEW", padx=(4, 8), pady=8)

        plt.style.use('dark_background')
        self.fig, self.ax = plt.subplots()
        self.fig.suptitle("Detector output level")
        self.ax.set_xlabel('Time [s]')
        self.ax.set_ylabel('Voltage [V]')
        self.fig.set_facecolor(self.backgroundGray)
        self.fig.set_size_inches(100, 100)
        self.fig.subplots_adjust(left=0.12, right=0.99, bottom=0.12, top=0.93, wspace=0, hspace=0)
        self.fig.set_tight_layout(True)
        self.canvasPlot = FigureCanvasTkAgg(self.fig, master=self.plotFrame)
        self.canvasPlot.get_tk_widget().pack(side='right', fill='both', expand=True)
        self.canvasPlot.draw()
        plt.close()

        self.window.attributes('-topmost', 1)
        self.window.update()

        # show the currently configured setpoint if the detector is connected
        t = Thread(target=self._readSetpointThread, daemon=True)
        t.start()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _getDriver(self):
        driver = self.grabDetectorDriver()
        if (driver is None or not getattr(driver, "isConnected", False)
                or not hasattr(driver, "beginLevelMonitoring")):
            return None
        return driver

    # ------------------------------------------------------------------
    # temperature controls
    # ------------------------------------------------------------------

    def onSetTemperature(self):
        driver = self._getDriver()
        if driver is None:
            messagebox.showwarning("Detector tool",
                                   "The AMS detector is not connected.\n"
                                   "Use 'Connect all' in the main window first.",
                                   parent=self.window)
            return

        try:
            temperatureInK = int(float(self.temperatureBox.get()))
        except Exception:
            messagebox.showwarning("Detector tool",
                                   "Enter the temperature as a number in Kelvin.",
                                   parent=self.window)
            return

        if temperatureInK == 0:
            confirmed = messagebox.askyesno("Detector tool",
                                            "A temperature of 0 K disables the board's\n"
                                            "temperature controller. Continue?", parent=self.window)
            if not confirmed:
                return

        if driver.setDetectorTemperatureNow(temperatureInK):
            self.statusLabel.configure(text=f"Requested detector temperature: {temperatureInK} K")
        else:
            messagebox.showerror("Detector tool", "Setting the detector temperature failed.",
                                 parent=self.window)

    def onReadSetpoint(self):
        t = Thread(target=self._readSetpointThread, daemon=True)
        t.start()

    def _readSetpointThread(self):
        driver = self._getDriver()
        if driver is None:
            self._postToWindow(lambda: self.setpointLabel.configure(text="Setpoint: not connected"))
            return

        setpoint = driver.readDetectorTemperatureSetpoint()
        if setpoint is None:
            text = "Setpoint: read failed"
        elif setpoint == 0:
            text = "Setpoint: 0 K (controller disabled)"
        else:
            text = f"Setpoint: {setpoint} K"

        self._postToWindow(lambda: self.setpointLabel.configure(text=text))

    # ------------------------------------------------------------------
    # output level monitoring
    # ------------------------------------------------------------------

    def onStartMonitoring(self):
        driver = self._getDriver()
        if driver is None:
            messagebox.showwarning("Detector tool",
                                   "The AMS detector is not connected.\n"
                                   "Use 'Connect all' in the main window first.",
                                   parent=self.window)
            return

        if getattr(driver, "measurementInProgress", False) and not getattr(driver, "monitoringModeActive", False):
            messagebox.showwarning("Detector tool",
                                   "A measurement is currently running.\n"
                                   "Stop it before starting the level monitor.",
                                   parent=self.window)
            return

        try:
            rateIndex = self.samplingRateStrings.index(self.rateCombo.get())
        except ValueError:
            rateIndex = 0

        status = driver.beginLevelMonitoring(rateIndex)
        if status is not None:
            messagebox.showerror("Detector tool", status, parent=self.window)
            return

        self.monitoring = True
        self.monitoringStopFlag = False
        self.levelHistory = []
        self.timeHistory = []
        self.bufferCount = 0
        self.startTime = time.monotonic()
        self.lastPlotRefresh = 0.0

        self.setMonitoringActive(True)
        self.startButton.configure(state="disabled")
        self.stopButton.configure(state="normal")
        self.statusLabel.configure(text="Monitoring...")

        t = Thread(target=self.monitoringThread, daemon=True)
        t.start()

    def onStopMonitoring(self):
        self.monitoringStopFlag = True

    def monitoringThread(self):
        while not self.monitoringStopFlag and not self.closed:
            driver = self._getDriver()
            if driver is None:
                break

            block = driver.readLevelBlock(timeout=0.5)
            if block is None:
                continue

            self.levelHistory.append(block["level"])
            self.timeHistory.append(time.monotonic() - self.startTime)
            self.bufferCount += 1

            # keep the history bounded
            if len(self.levelHistory) > 3000:
                self.levelHistory = self.levelHistory[-1500:]
                self.timeHistory = self.timeHistory[-1500:]

            level = block["level"]
            peakToPeak = block["pk_pk"]
            noise = block["std"]
            self._postToWindow(lambda l=level, p=peakToPeak, s=noise: self._updateReadouts(l, p, s))

        driver = self._getDriver()
        if driver is not None:
            driver.endLevelMonitoring()

        self._postToWindow(self._onMonitoringStopped)

    def _updateReadouts(self, level, peakToPeak, noise):
        if self.closed:
            return

        self.levelLabel.configure(text=f"Level: {level:+.4f} V")
        self.peakToPeakLabel.configure(text=f"Peak-peak: {peakToPeak:.4f} V")
        self.noiseLabel.configure(text=f"Noise (std): {noise:.5f} V")

        driver = self.grabDetectorDriver()
        if driver is not None and getattr(driver, "detectorTemperatureK", None) is not None:
            self.reportedTemperatureLabel.configure(
                text=f"Reported: {driver.detectorTemperatureK:.2f} K")

        now = time.monotonic()
        if now - self.lastPlotRefresh > 0.25:
            self.lastPlotRefresh = now
            self._redrawPlot()

    def _redrawPlot(self):
        if self.closed or len(self.levelHistory) == 0:
            return

        self.ax.clear()
        self.ax.grid(color="dimgrey", linestyle='-', linewidth=1, alpha=0.6)
        self.ax.plot(self.timeHistory, self.levelHistory, color=self.plotLineColor)
        self.ax.set_xlabel('Time [s]')
        self.ax.set_ylabel('Voltage [V]')
        self.canvasPlot.draw()

    def _onMonitoringStopped(self):
        self.monitoring = False
        self.setMonitoringActive(False)

        if self.closed:
            return

        self.startButton.configure(state="normal")
        self.stopButton.configure(state="disabled")
        self.statusLabel.configure(text="Monitoring stopped")
        self._redrawPlot()

    def _postToWindow(self, callback):
        if self.closed:
            return
        try:
            self.window.after(0, callback)
        except Exception:
            pass

    # ------------------------------------------------------------------

    def onClosing(self):
        self.closed = True
        self.monitoringStopFlag = True

        driver = self.grabDetectorDriver()
        if driver is not None and getattr(driver, "monitoringModeActive", False):
            driver.endLevelMonitoring()
        self.setMonitoringActive(False)

        try:
            self.window.destroy()
        except Exception:
            pass
