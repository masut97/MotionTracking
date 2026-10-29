"""Distinguish native measurement throughput from preview and stored frame rates."""
from collections import deque
import time

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QLabel, QWidget


class MeasurementRateController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window, self.reader = window, None
        self.samples = deque(maxlen=7)
        bar = window.findChild(QWidget, 'recordingBar')
        self.label = QLabel('Messdatenrate: —', bar)
        self.label.setObjectName('measurementStreamRate')
        self.label.setWordWrap(True)
        bar.layout().addWidget(self.label)
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.poll)
        self.timer.start()

    def poll(self):
        adapter = getattr(self.window.acquisition, '_adapter', None)
        reader = getattr(adapter, 'fast', None)
        if reader is not self.reader:
            self.reader = reader
            self.samples.clear()
        text = 'Messdatenrate: —'
        if reader is not None:
            try:
                header = reader.header()
                if header.state == 2:
                    now = time.monotonic()
                    sequence = int(header.write_sequence)
                    if self.samples and sequence < self.samples[-1][1]:
                        self.samples.clear()
                    self.samples.append((now, sequence))
                    if len(self.samples)>1:
                        first, count = self.samples[0]
                        rate = (sequence-count)/(now-first)
                        text = f'Messdaten: {rate:.1f} Hz (Posen + Kugeln)'
                else:
                    self.samples.clear()
            except (OSError, ValueError):
                self.samples.clear()
        stats = self.window.recorder.stats()
        if stats.state == 'Aufnahme':
            text += f'  ·  Gespeichert: {stats.frame_rate_hz:.1f} Hz / Soll {self.window.record_frequency_spin.value()} Hz'
        else:
            text += f'  ·  Aufnahmevorgabe: {self.window.record_frequency_spin.value()} Hz'
        self.label.setText(text)
