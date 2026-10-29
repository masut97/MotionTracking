"""General marker-profile editor and offline execution, independent of live devices."""
import json
import re
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, QUrl, Signal, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QInputDialog)


def backend(root):
    directory = str(Path(root)/'analysis_scripts')
    if directory not in sys.path:
        sys.path.insert(0, directory)
    import free_auswertung
    return free_auswertung


from .profile_store import save_profile
from .marker_selection import selection_choices, csv_marker_choices


class ProfileDialog(QDialog):
    def __init__(self, root, profile=None, parent=None, visible_choices=None):
        super().__init__(parent)
        self.analysis = backend(root)
        self.visible_choices = visible_choices
        self.choice_snapshot = selection_choices(visible_choices()) if visible_choices is not None else None
        self.setWindowTitle('Freie Markerzuordnung')
        self.resize(1000, 770)
        layout = QVBoxLayout(self)
        explanation = QLabel('Marker frei benennen und Vergleiche definieren. „Ziel relativ zu Basis“ entfernt die Bewegung der Basis; '
                             'der Referenzmarker bestimmt die Ausgabeachsen. Für eine einzelne Instanz je Geometrie die Geometrie-ID verwenden: '
                             'Tracking-IDs können bei jedem Bild wechseln. Mehrere gleichartige Marker werden nicht automatisch gleichgesetzt. '
                             'Ein physischer Austausch von Markern gleicher Geometrie ist damit nicht erkennbar.')
        explanation.setWordWrap(True); layout.addWidget(explanation)
        form = QFormLayout()
        self.name = QLineEdit('Freies Messprofil'); self.name.setMaxLength(80)
        self.reference = QComboBox()
        self.baseline = QDoubleSpinBox(); self.baseline.setRange(0, 300); self.baseline.setDecimals(2)
        self.baseline.setValue(1.); self.baseline.setSuffix(' s')
        self.baseline.setToolTip('Mittelwert ab dem ersten vollständigen Sample. 0 s = erstes vollständiges Sample.')
        form.addRow('Profilname', self.name); form.addRow('Referenzmarker / Ausgabeachsen', self.reference)
        form.addRow('Baseline vor der Bewegung', self.baseline); layout.addLayout(form)
        layout.addWidget(QLabel('1 · Marker-IDs und frei wählbare Namen'))
        self.markers = QTableWidget(0, 2); self.markers.setObjectName('freeMarkerTable')
        self.markers.setHorizontalHeaderLabels(['Geometrie / Instanz', 'Name / Bedeutung'])
        self.markers.setColumnWidth(0, 470)
        self.markers.horizontalHeader().setStretchLastSection(True)
        self.markers.setMaximumHeight(175); layout.addWidget(self.markers)
        row = QHBoxLayout()
        self._button(row, 'Marker hinzufügen', self.add_marker)
        self._button(row, 'Markierte Marker entfernen', lambda: self.remove_rows(self.markers))
        self._button(row, 'IDs aus CSV übernehmen …', self.ids_from_csv)
        if self.visible_choices is not None:
            row.addStretch(1); layout.addLayout(row)
            row = QHBoxLayout()
            self._button(row, 'Sichtbare Instanzen übernehmen', self.use_visible)
            self._button(row, 'Eindeutige Geometrien verwenden', self.use_unique_geometries)
        row.addStretch(1); layout.addLayout(row)
        layout.addWidget(QLabel('2 · Vergleiche: Ziel relativ zu Basis (beliebig viele, maximal 32)'))
        self.pairs = QTableWidget(0, 5); self.pairs.setObjectName('freeComparisonTable')
        self.pairs.setHorizontalHeaderLabels(['Vergleichsname', 'Basis', 'Ziel', 'Ausgabegrößen', 'Rotationsachse X Y Z'])
        self.pairs.setColumnWidth(0, 170); self.pairs.setColumnWidth(1, 160)
        self.pairs.setColumnWidth(2, 160); self.pairs.setColumnWidth(3, 210)
        self.pairs.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.pairs, 1)
        row = QHBoxLayout()
        self._button(row, 'Vergleich hinzufügen', self.add_pair)
        self._button(row, 'Markierte Vergleiche entfernen', lambda: self.remove_rows(self.pairs))
        self._button(row, 'Profil laden …', self.import_profile)
        self._button(row, 'Profil exportieren …', self.export_profile)
        layout.addLayout(row)
        self.error = QLabel(); self.error.setWordWrap(True); self.error.setStyleSheet('color:#ef9b9b')
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('Speichern und freien Modus auswählen')
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
        self.markers.itemChanged.connect(self.refresh_choices)
        if profile:
            self.set_profile(profile)
        elif self.visible_choices is not None:
            self.use_visible()

    def use_visible(self):
        raw = self.visible_choices()
        names = selection_choices(raw, preferred=True)
        self.choice_snapshot = selection_choices(raw)
        try:
            previous_names = self.marker_names()
        except ValueError:
            previous_names = {}
        self.markers.blockSignals(True)
        try:
            # Retain configured identities and labels, including a temporarily lost
            # reference. Missing identities are displayed disabled, never rebound.
            for marker, label in names.items():
                if marker not in previous_names and not any(
                        key.split(':')[0] == marker.split(':')[0] for key in previous_names):
                    self.add_marker(marker, label if ':' in marker else marker)
        finally:
            self.markers.blockSignals(False)
        self.refresh_choices()
        self.error.setText('Geometrie-ID = genau eine Instanz je Bild. Bei mehreren Instanzen ist eine ausdrückliche Auswahl erforderlich; keine automatische Ersatzzuordnung.')

    def use_unique_geometries(self):
        self.choice_snapshot = selection_choices(self.visible_choices())
        for row in range(self.markers.rowCount()):
            key = self.markers.item(row, 0).text().strip()
            geometry = key.split(':')[0]
            if ':' in key and geometry in self.choice_snapshot:
                self.change_selector(row, geometry)
        self.refresh_choices()
        self.error.setText('Eindeutige Geometrien umgestellt. Namen, Referenz und Vergleiche bleiben erhalten. Mehrfach vorkommende Geometrien wurden nicht ersetzt.')

    def change_selector(self, row, new_key):
        old_key = self.markers.item(row, 0).text().strip()
        new_key = str(new_key)
        if old_key == new_key:
            return
        def overlaps(key):
            return key.split(':')[0] == new_key.split(':')[0] and (key == new_key or ':' not in key or ':' not in new_key)
        if any(r != row and overlaps(self.markers.item(r, 0).text().strip())
               for r in range(self.markers.rowCount())):
            self.error.setText('Diese Zuordnung wird bereits von einem anderen Marker verwendet.')
            self.update_source_choices()
            return
        combos = [self.reference] + [self.pairs.cellWidget(r, c)
                                     for r in range(self.pairs.rowCount()) for c in (1, 2)]
        previous = [combo.currentData() for combo in combos]
        blocked = self.markers.blockSignals(True)
        self.markers.item(row, 0).setText(new_key)
        self.markers.blockSignals(blocked)
        for combo, selected in zip(combos, previous):
            self.fill_choice(combo, self.analysis.marker_selector(new_key)
                             if str(selected) == old_key else selected)
        self.update_source_choices()

    def update_source_choices(self):
        if self.choice_snapshot is None:
            return
        for row in range(self.markers.rowCount()):
            selected = self.markers.item(row, 0).text().strip()
            combo = self.markers.cellWidget(row, 0)
            if combo is None:
                combo = QComboBox()
                self.markers.setCellWidget(row, 0, combo)
                def changed(_index, source=combo):
                    if source.currentData() is None:
                        return
                    for current in range(self.markers.rowCount()):
                        if self.markers.cellWidget(current, 0) is source:
                            self.change_selector(current, source.currentData())
                            break
                combo.currentIndexChanged.connect(changed)
            combo.blockSignals(True)
            combo.clear()
            for key, label in self.choice_snapshot.items():
                combo.addItem(f'{label} ({key})', key)
            if combo.findData(selected) < 0:
                combo.addItem(f'Nicht verfügbar · {selected}', selected)
                combo.model().item(combo.count()-1).setEnabled(False)
            combo.setCurrentIndex(combo.findData(selected))
            combo.blockSignals(False)

    @staticmethod
    def _button(layout, label, action):
        button = QPushButton(label); button.clicked.connect(lambda _checked=False: action())
        layout.addWidget(button); return button

    def add_marker(self, marker=None, label=None):
        if marker is None and self.visible_choices is not None:
            self.add_visible_marker()
            return
        blocked = self.markers.blockSignals(True)
        try:
            row = self.markers.rowCount(); self.markers.insertRow(row)
            item = QTableWidgetItem(str(marker) if marker is not None else '')
            if self.visible_choices is not None:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.markers.setItem(row, 0, item)
            self.markers.setItem(row, 1, QTableWidgetItem(label or (f'Marker {marker}' if marker is not None else '')))
        finally:
            self.markers.blockSignals(blocked)
        if not blocked:
            self.refresh_choices()

    def add_visible_marker(self):
        visible = selection_choices(self.visible_choices(), preferred=True)
        try:
            existing = self.marker_names()
        except ValueError as error:
            self.error.setText(str(error)); return
        available = {key: label for key, label in visible.items() if key not in existing and not any(
            key.split(':')[0] == old.split(':')[0] and (':' not in key or ':' not in old) for old in existing)}
        if not available:
            self.error.setText('Alle aktuell sichtbaren Instanzen sind bereits in der Markerliste. Unten „Vergleich hinzufügen“ wählen.')
            return
        labels = [f'{label} ({key})' for key, label in available.items()]
        selected, accepted = QInputDialog.getItem(self, 'Sichtbaren Marker hinzufügen', 'Instanz', labels, 0, False)
        if accepted:
            key = list(available)[labels.index(selected)]
            self.choice_snapshot = selection_choices(self.visible_choices())
            self.add_marker(key, available[key])

    def remove_rows(self, table):
        for row in sorted({i.row() for i in table.selectedIndexes()}, reverse=True):
            table.removeRow(row)
        self.refresh_choices()

    def marker_names(self):
        result = {}
        for row in range(self.markers.rowCount()):
            first, second = self.markers.item(row, 0), self.markers.item(row, 1)
            if not first or not second:
                continue
            key = first.text().strip()
            if not key or not second.text().strip():
                continue
            if key in result:
                raise ValueError('Marker-IDs dürfen nicht doppelt vorkommen.')
            result[key] = second.text().strip()
        return result

    def fill_choice(self, combo, previous=None):
        if previous is None:
            previous = combo.currentData()
        try:
            names = self.marker_names()
        except ValueError as error:
            self.error.setText(str(error))
            return
        combo.blockSignals(True)
        combo.clear(); combo.addItem('Bitte wählen', None)
        for key, label in names.items():
            try:
                selector = self.analysis.marker_selector(key)
            except ValueError:
                continue
            if self.choice_snapshot is None or key in self.choice_snapshot:
                combo.addItem(f'{label} ({key})', selector)
                combo.setItemData(combo.count()-1, f'{label} ({key})', Qt.ItemDataRole.ToolTipRole)
        if previous is not None and combo.findData(previous) < 0:
            combo.addItem(f'Nicht verfügbar · {names.get(str(previous), previous)} ({previous})', previous)
            combo.model().item(combo.count()-1).setEnabled(False)
        selected = combo.findData(previous)
        combo.setCurrentIndex(max(0, selected))
        combo.blockSignals(False)

    def refresh_choices(self, *_args):
        try:
            self.marker_names()
        except ValueError as error:
            self.error.setText(str(error)); return
        self.fill_choice(self.reference)
        self.update_source_choices()
        for row in range(self.pairs.rowCount()):
            for col in (1, 2):
                combo = self.pairs.cellWidget(row, col)
                if combo is not None:
                    self.fill_choice(combo)

    def add_pair(self, value=None):
        value = value or {}
        row = self.pairs.rowCount()
        if row >= 32:
            self.error.setText('Maximal 32 Vergleiche.'); return
        self.pairs.insertRow(row)
        self.pairs.setItem(row, 0, QTableWidgetItem(value.get('name', f'Vergleich {row+1}')))
        for col, key in ((1, 'anchor'), (2, 'target')):
            combo = QComboBox(); self.fill_choice(combo, value.get(key)); self.pairs.setCellWidget(row, col, combo)
        quantities = QWidget(); box = QVBoxLayout(quantities); box.setContentsMargins(4, 4, 4, 4)
        names = ['X-Verschiebung', 'Y-Verschiebung', 'Z-Verschiebung', 'Abstand zur Baseline', 'Weg (kumulativ)', 'Rotation um gewählte Achse', 'Gesamtrotation (0–180°)']
        for key, label in zip(self.analysis.OUTPUTS, names):
            check = QCheckBox(label); check.setProperty('quantity', key)
            check.setChecked(key in value.get('outputs', list(self.analysis.OUTPUTS))); box.addWidget(check)
        self.pairs.setCellWidget(row, 3, quantities); self.pairs.setRowHeight(row, 210)
        axis_item = QTableWidgetItem(' '.join(f'{v:g}' for v in value.get('axis', [0, 0, 1])))
        axis_item.setToolTip('Drei Zahlen mit Leerzeichen: 1 0 0 = X, 0 1 0 = Y, 0 0 1 = Z. Eigene Richtungen werden normiert.')
        self.pairs.setItem(row, 4, axis_item)

    def value(self):
        if self.visible_choices is not None:
            visible = selection_choices(self.visible_choices())
            used = [self.reference.currentData()]
            for row in range(self.pairs.rowCount()):
                used.extend(self.pairs.cellWidget(row, col).currentData() for col in (1, 2))
            if any(str(key) not in visible for key in used):
                raise ValueError('Eine gewählte Instanz ist nicht mehr sichtbar. Sichtbare Instanzen übernehmen und Zuordnung prüfen.')
        pairs = []
        for row in range(self.pairs.rowCount()):
            name = self.pairs.item(row, 0).text()
            outputs = [c.property('quantity') for c in self.pairs.cellWidget(row, 3).findChildren(QCheckBox) if c.isChecked()]
            axis_text = self.pairs.item(row, 4).text().strip()
            axis = ([float(v.replace(',', '.')) for v in axis_text.split()]
                    if axis_text or 'rotation_axis' in outputs else [0,0,1])
            pairs.append(dict(name=name, anchor=self.pairs.cellWidget(row, 1).currentData(),
                              target=self.pairs.cellWidget(row, 2).currentData(), outputs=outputs, axis=axis))
        return self.analysis.validate_profile(dict(version=1, name=self.name.text(), markers=self.marker_names(),
                   reference=self.reference.currentData(), baseline_seconds=self.baseline.value(), comparisons=pairs))

    def set_profile(self, profile):
        profile = self.analysis.validate_profile(profile)
        self.markers.blockSignals(True)
        try:
            self.markers.setRowCount(0); self.pairs.setRowCount(0)
            names = profile['markers']
            if self.visible_choices is not None:
                names = dict(names)
                configured_geometries = {key.split(':')[0] for key in names}
                raw = self.visible_choices()
                self.choice_snapshot = selection_choices(raw)
                for key, label in selection_choices(raw, preferred=True).items():
                    if key.split(':')[0] not in configured_geometries:
                        names.setdefault(key, label if ':' in key else key)
            for marker, label in names.items():
                self.add_marker(marker, label)
        finally:
            self.markers.blockSignals(False)
        self.name.setText(profile['name']); self.baseline.setValue(profile['baseline_seconds'])
        self.fill_choice(self.reference, profile['reference'])
        for pair in profile['comparisons']:
            self.add_pair(pair)
        self.update_source_choices()

    def ids_from_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Marker-IDs aus Aufnahme übernehmen', '', 'CSV (*.csv)')
        if path:
            try:
                self.apply_csv_choices(csv_marker_choices(path))
            except Exception as error:
                self.error.setText(str(error))

    def apply_csv_choices(self, choices):
        self.choice_snapshot = (selection_choices(self.visible_choices())
                                if self.visible_choices is not None else choices)
        for row in range(self.markers.rowCount()):
            old = self.markers.item(row, 0).text().strip()
            geometry = old.split(':')[0]
            if ':' in old and geometry in choices and geometry in self.choice_snapshot:
                self.change_selector(row, geometry)
        known = self.marker_names()
        for marker in sorted(choices):
            self.analysis.marker_selector(marker)
            if marker not in known and not any(marker.split(':')[0] == old.split(':')[0]
                    and (':' not in marker or ':' not in old) for old in known):
                self.add_marker(marker)
        self.refresh_choices()

    def import_profile(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Profil laden', '', 'JSON (*.json)')
        if path:
            try:
                self.set_profile(self.analysis.load_profile(path)); self.error.clear()
            except Exception as error:
                self.error.setText(str(error))

    def export_profile(self):
        try:
            profile = self.value()
            path, _ = QFileDialog.getSaveFileName(self, 'Profil exportieren', 'profile.free-profile.json', 'Messprofil (*.free-profile.json)')
            if path:
                if not path.lower().endswith('.free-profile.json'):
                    path += '.free-profile.json'
                save_profile(path, profile, self.analysis.validate_profile)
        except Exception as error:
            self.error.setText(str(error))

    def accept(self):
        try:
            self.saved_profile = self.value()
        except Exception as error:
            self.error.setText(str(error)); return
        super().accept()


class AnalysisWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, root, path, profile, parent):
        super().__init__(parent); self.root, self.path, self.profile = root, path, profile

    def run(self):
        try:
            self.succeeded.emit(backend(self.root).analyze(self.path, self.profile))
        except Exception as error:
            self.failed.emit(str(error))


class FreeAnalysisController(QObject):
    def __init__(self, window, row):
        super().__init__(window); self.window = window; self.worker = None; self.editing = False
        self.profile_button = QPushButton('Freie Zuordnung …', window); self.profile_button.setObjectName('freeAnalysisSetupButton')
        self.csv_button = QPushButton('CSV frei auswerten …', window); self.csv_button.setObjectName('freeAnalysisCsvButton')
        row.addWidget(self.profile_button); row.addWidget(self.csv_button)
        self.profile_button.clicked.connect(self.edit_profile); self.csv_button.clicked.connect(self.evaluate_csv)
        self.timer = QTimer(self); self.timer.setInterval(500); self.timer.timeout.connect(self.refresh); self.timer.start()

    def busy(self):
        return self.editing or (self.worker is not None and self.worker.isRunning())

    def available(self):
        w = self.window
        process = getattr(w, '_analysis_process', None)
        return (not self.busy() and w.recorder.stats().state == 'Gestoppt'
                and w.script_panel.pending is None and not w.update_controller.busy()
                and (process is None or process.poll() is not None)
                and not (getattr(w, 'retrospective_controller', None) and w.retrospective_controller.worker
                         and w.retrospective_controller.worker.isRunning()))

    def refresh(self):
        enabled = self.available()
        self.profile_button.setEnabled(enabled); self.csv_button.setEnabled(enabled)

    def edit_profile(self):
        if not self.available():
            return
        analysis = backend(self.window.project_root)
        try:
            profile = analysis.load_profile() if analysis.PROFILE_PATH.exists() else None
        except Exception as error:
            QMessageBox.warning(self.window, 'Profil konnte nicht geladen werden', str(error)+'\nEin neues Profil kann erstellt oder importiert werden. Die bisherige Datei bleibt bis zum Speichern erhalten.')
            profile = None
        self.editing = True
        try:
            from .marker_selection import visible_marker_choices
            editor = ProfileDialog(self.window.project_root, profile, self.window,
                                   visible_choices=(lambda: visible_marker_choices(self.window))
                                   if self.window.status_store.get().connected else None)
            if editor.exec() == QDialog.DialogCode.Accepted:
                save_profile(analysis.PROFILE_PATH, editor.saved_profile, analysis.validate_profile)
                selected = self.window.project_root/'analysis_scripts/free_auswertung.py'
                self.window._populate_analysis_scripts(selected)
                self.window._analysis_preferences_changed()
        except Exception as error:
            QMessageBox.critical(self.window, 'Profilfehler', str(error))
        finally:
            self.editing = False; self.refresh()

    def evaluate_csv(self):
        if not self.available():
            return
        try:
            profile = backend(self.window.project_root).load_profile()
        except Exception as error:
            QMessageBox.information(self.window, 'Zuerst freie Zuordnung speichern', str(error)); return
        path, _ = QFileDialog.getOpenFileName(self.window, 'Aufnahme frei auswerten', '', 'CSV (*.csv)')
        if not path:
            return
        self.worker = AnalysisWorker(self.window.project_root, Path(path), profile, self)
        self.worker.succeeded.connect(lambda result: QDesktopServices.openUrl(QUrl.fromLocalFile(str(result))))
        self.worker.failed.connect(lambda message: QMessageBox.critical(self.window, 'Freie Auswertung fehlgeschlagen', message))
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.finished.connect(self.finished)
        self.worker.start(); self.refresh()

    def finished(self):
        self.worker = None; self.refresh()
