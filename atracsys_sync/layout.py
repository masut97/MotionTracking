"""A calmer tabbed workspace around the installed tracker's existing widgets."""
from __future__ import annotations

import re
import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QThread, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (QFileDialog, QFrame, QGroupBox,
    QHBoxLayout, QInputDialog, QLabel, QMenu, QMessageBox, QPushButton,
    QTabWidget, QToolButton, QVBoxLayout, QWidget)
from .drag_drop_scope import install_drop_filters, owned_event_type


ROM_MODES = ("FE", "LB", "AR", "AS", "LS", "AC")


EXTRA_STYLE = """
QTabWidget#workspaceTabs::pane {
    border: 1px solid #26364b; border-radius: 9px; background: #0b111a;
}
QTabBar::tab {
    background: #101925; color: #9eafc4; border: 1px solid #26364b;
    border-bottom: none; padding: 9px 22px; margin-right: 3px;
    border-top-left-radius: 7px; border-top-right-radius: 7px;
}
QTabBar::tab:selected { background: #182a3f; color: #ffffff; border-color: #3f638a; }
QTabBar::tab:hover:!selected { background: #162235; color: #dfe8f4; }
QToolButton#workspaceMenuButton, QToolButton#measurementMenuButton {
    background: #182538; border: 1px solid #30445e; border-radius: 7px;
    padding: 7px 12px; color: #e8eef7;
}
QToolButton#workspaceMenuButton:hover, QToolButton#measurementMenuButton:hover {
    background: #23344c; border-color: #46698e;
}
QToolButton::menu-indicator { width: 0px; }
QMenu { background: #111c2a; color: #e8eef7; border: 1px solid #30445e; padding: 5px; }
QMenu::item { padding: 7px 24px 7px 10px; border-radius: 5px; }
QMenu::item:selected { background: #276fd4; }
QMenu::item:disabled { color: #718299; }
QGroupBox#analysisWorkspace { font-weight: 650; margin-top: 12px; padding-top: 14px; }
QGroupBox#retrospectiveWorkspace { font-weight: 650; margin-top: 12px; padding-top: 14px; }
QFrame#retrospectiveDropZone {
    background: #101b29; border: 2px dashed #42658a; border-radius: 10px;
}
QFrame#retrospectiveDropZone[dragActive="true"] {
    background: #17304a; border-color: #42a5ff;
}
QLabel#retrospectiveDropTitle { color: #eaf3ff; font-size: 15px; font-weight: 700; }
QLabel#retrospectiveDropHint { color: #9eafc4; }
QLabel#retrospectiveStatus { color: #8fc6ff; }
"""


def _mode_from_filename(path):
    normalized = re.sub(r"[^a-z0-9]+", "_", path.stem.lower()).strip("_")
    for mode in ROM_MODES:
        if re.search(rf"(^|_){mode.lower()}($|_)", normalized):
            return mode
    return None


class _RetrospectiveWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, jobs, parent=None):
        super().__init__(parent)
        self.jobs = jobs

    def run(self):
        try:
            application_dir = Path(__file__).resolve().parents[2]
            analysis_dir = application_dir / "analysis_scripts"
            if str(analysis_dir) not in sys.path:
                sys.path.insert(0, str(analysis_dir))
            import rom_auswertung

            figures = []
            for path, mode in self.jobs:
                result = rom_auswertung.main([
                    str(path), "--bewegung", mode, "--ohne-zwick", "--no-open"
                ])
                if result:
                    raise RuntimeError(
                        f"The analysis of {path.name} ended with code {result}."
                    )
                figure = (
                    path.parent
                    / f"{path.stem}_ROM_auswertung"
                    / f"{path.stem}_{mode}_ROM_figure.html"
                )
                if not figure.is_file():
                    raise FileNotFoundError(
                        f"The recalculated figure was not found: {figure}"
                    )
                figures.append(figure)
            self.succeeded.emit(figures)
        except Exception as error:
            self.failed.emit(str(error))


class _RetrospectiveController(QObject):
    def __init__(self, window, tabs, analysis_index, status, select_button, drop_zone):
        super().__init__(window)
        self.window = window
        self.tabs = tabs
        self.analysis_index = analysis_index
        self.status = status
        self.select_button = select_button
        self.drop_zone = drop_zone
        self.worker = None
        self._drop_targets=install_drop_filters(window,drop_zone,self,QWidget)
        select_button.clicked.connect(self.select_files)

    @staticmethod
    def _csv_paths(mime_data):
        if not mime_data.hasUrls():
            return []
        paths = []
        for url in mime_data.urls():
            path = Path(url.toLocalFile())
            if path.is_file() and path.suffix.lower() == ".csv":
                paths.append(path.resolve())
        return paths

    def _highlight(self, enabled):
        self.drop_zone.setProperty("dragActive", enabled)
        self.drop_zone.style().unpolish(self.drop_zone)
        self.drop_zone.style().polish(self.drop_zone)

    def eventFilter(self, watched, event):
        event_type = owned_event_type(watched,event,self.window,QWidget)
        if event_type not in (
            QEvent.Type.DragEnter,
            QEvent.Type.DragMove,
            QEvent.Type.DragLeave,
            QEvent.Type.Drop,
        ):
            return False
        if event_type == QEvent.Type.DragLeave:
            self._highlight(False)
            return False
        paths = self._csv_paths(event.mimeData())
        if event_type in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
            if paths:
                self.tabs.setCurrentIndex(self.analysis_index)
                self._highlight(True)
                event.acceptProposedAction()
                return True
            return False
        self._highlight(False)
        if paths:
            event.acceptProposedAction()
            self.start(paths)
            return True
        return False

    def select_files(self):
        selected, _filter = QFileDialog.getOpenFileNames(
            self.window,
            "Select historical Atracsys ROM CSV",
            "",
            "CSV files (*.csv);;All files (*.*)",
        )
        if selected:
            self.start([Path(path).resolve() for path in selected])

    def _select_mode(self, path):
        selected, accepted = QInputDialog.getItem(
            self.window,
            "Select motion type",
            f"{path.name}\n\nWhich motion type was measured?",
            ROM_MODES,
            0,
            False,
        )
        return str(selected) if accepted else None

    def start(self, paths):
        free = getattr(self.window, 'free_analysis_controller', None)
        if free is not None and free.busy():
            QMessageBox.information(self.window, 'Auswertung läuft', 'Bitte die freie Zuordnung/Auswertung zuerst abschließen.')
            return
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(
                self.window,
                "Analysis in progress",
                "Please wait until the current recalculation has finished.",
            )
            return
        raw_paths = [path for path in paths if not path.name.lower().endswith("_rom_bewegung.csv")]
        if not raw_paths:
            QMessageBox.warning(
                self.window,
                "No raw data file",
                "Please drop the original Atracsys CSV, not the generated "
                "ROM_bewegung.csv file.",
            )
            return
        jobs = []
        for path in raw_paths:
            mode = _mode_from_filename(path) or self._select_mode(path)
            if mode is None:
                continue
            jobs.append((path, mode))
        if not jobs:
            return
        self.tabs.setCurrentIndex(self.analysis_index)
        self.status.setText(
            f"Recalculating {len(jobs)} file{'s' if len(jobs) != 1 else ''} …"
        )
        self.select_button.setEnabled(False)
        self.worker = _RetrospectiveWorker(jobs, self)
        self.worker.succeeded.connect(self._finished)
        self.worker.failed.connect(self._failed)
        self.worker.finished.connect(self._worker_finished)
        self.worker.start()

    def _finished(self, figures):
        self.status.setText(
            f"Done – {len(figures)} analysis result{'s' if len(figures) != 1 else ''} recalculated."
        )
        for figure in figures:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(figure)))

    def _failed(self, message):
        self.status.setText("Recalculation failed.")
        QMessageBox.critical(self.window, "Analysis error", message)

    def _worker_finished(self):
        self.select_button.setEnabled(True)


def _remove_layout(parent, target):
    for index in range(parent.count()):
        item = parent.itemAt(index)
        if item.layout() is target:
            return parent.takeAt(index).layout()
    return None


def _tool_dropdown(toolbar):
    layout = toolbar.layout()
    buttons = {button.text(): button for button in toolbar.findChildren(
        QPushButton, options=Qt.FindChildOption.FindDirectChildrenOnly)}
    advanced = [buttons.get("STEP → JSON5"), buttons.get("Marker kalibrieren")]
    advanced = [button for button in advanced if button is not None]
    if not advanced:
        return None
    insertion = min(layout.indexOf(button) for button in advanced)
    menu = QMenu(toolbar)
    for button in advanced:
        action = menu.addAction(button.text())
        action.triggered.connect(lambda _checked=False, source=button: source.click())
        layout.removeWidget(button)
        button.hide()
    tool = QToolButton(toolbar)
    tool.setObjectName("workspaceMenuButton")
    tool.setText("Tools  ▾")
    tool.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    tool.setMenu(menu)
    tool.setToolTip("Import or calibrate markers")
    layout.insertWidget(insertion, tool)
    return tool


def _measurement_dropdown(window, panel, tabs, tab_index):
    bar = window.findChild(QWidget, "recordingBar")
    row = bar.layout().itemAt(0).layout()
    button = QToolButton(bar)
    button.setObjectName("measurementMenuButton")
    button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    menu = QMenu(button)
    button.setMenu(menu)

    def selected_rows():
        return [row_index for row_index in range(panel.table.rowCount())
                if panel.table.item(row_index, 0).checkState() == Qt.CheckState.Checked]

    def update_label():
        selected = selected_rows()
        states = [panel.table.item(row_index, 1).text() for row_index in selected]
        problem = any(state == "Fehler" or state.startswith("Datenlücke") for state in states)
        suffix = " · Error" if problem else ""
        button.setText(f"Messgeräte: {len(selected)}{suffix}  ▾")
        details = [f"{panel.specs[row_index].name}: {panel.table.item(row_index, 1).text()}"
                   for row_index in selected]
        button.setToolTip("\n".join(details) if details else "No devices selected")
        tabs.setTabText(tab_index, f"Messgeräte ({len(selected)})")

    def rebuild_menu():
        menu.clear()
        busy = not panel.table.isEnabled()
        for row_index, spec in enumerate(panel.specs):
            action = QAction(spec.name, menu)
            action.setCheckable(True)
            action.setChecked(panel.table.item(row_index, 0).checkState() == Qt.CheckState.Checked)
            action.setEnabled(not busy)
            action.triggered.connect(
                lambda checked, row_index=row_index: panel.table.item(row_index, 0).setCheckState(
                    Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked))
            menu.addAction(action)
        menu.addSeparator()
        manage = menu.addAction("Messgeräte verwalten …")
        manage.triggered.connect(lambda: tabs.setCurrentIndex(tab_index))

    menu.aboutToShow.connect(rebuild_menu)
    record_index = row.indexOf(window.record_button)
    row.insertWidget(max(0, record_index), button)
    panel.overview_refresh = update_label
    update_label()
    return button


def organize_window(window, panel):
    """Move existing widgets into three tabs without recreating their behavior."""
    central = window.centralWidget()
    outer = central.layout()
    toolbar = window.findChild(QWidget, "cameraToolbar")
    recording_bar = window.findChild(QWidget, "recordingBar")
    if toolbar is None or recording_bar is None:
        raise RuntimeError("This tracker version does not expose the expected interface areas.")

    # Preserve the status header and the compact first recording row outside the tabs.
    outer.removeWidget(toolbar)
    outer.removeWidget(panel)
    outer.removeWidget(window.camera_splitter)
    outer.removeWidget(window.marker_scroll)
    marker_heading = next((outer.itemAt(index).layout() for index in range(outer.count())
                           if outer.itemAt(index).layout() is not None), None)
    if marker_heading is None or _remove_layout(outer, marker_heading) is None:
        raise RuntimeError("The marker heading could not be moved into the Live view.")

    tabs = QTabWidget(central)
    tabs.setObjectName("workspaceTabs")
    tabs.setDocumentMode(True)

    live_page = QWidget(tabs)
    live_layout = QVBoxLayout(live_page)
    live_layout.setContentsMargins(8, 8, 8, 8)
    live_layout.setSpacing(8)
    live_layout.addWidget(toolbar)
    live_layout.addWidget(window.camera_splitter, 1)
    tabs.addTab(live_page, "Live")

    devices_page = QWidget(tabs)
    devices_layout = QVBoxLayout(devices_page)
    devices_layout.setContentsMargins(8, 8, 8, 8)
    panel.setTitle("VERBINDUNGEN UND ZEITABGLEICH")
    panel.setMaximumHeight(380)
    devices_layout.addWidget(panel)
    devices_layout.addStretch(1)
    devices_index = tabs.addTab(devices_page, "Messgeräte")

    analysis_page = QWidget(tabs)
    analysis_layout = QVBoxLayout(analysis_page)
    analysis_layout.setContentsMargins(12, 12, 12, 12)
    analysis_group = QGroupBox("AUTOMATIC ANALYSIS", analysis_page)
    analysis_group.setObjectName("analysisWorkspace")
    group_layout = QVBoxLayout(analysis_group)
    explanation = QLabel(
        "An analysis script can run automatically after each fully saved recording.")
    explanation.setObjectName("mutedLabel")
    explanation.setWordWrap(True)
    group_layout.addWidget(explanation)
    from .update_ui import UpdateController
    window.update_controller = UpdateController(window, analysis_layout)
    record_layout = recording_bar.layout()
    if record_layout.count() > 1 and record_layout.itemAt(1).layout() is not None:
        analysis_options = record_layout.takeAt(1).layout()
        group_layout.addLayout(analysis_options)
    from .analysis_selection import add_profile_toggle
    add_profile_toggle(window, group_layout)
    analysis_layout.addWidget(analysis_group)

    retrospective_group = QGroupBox("RECALCULATE HISTORICAL ROM FILES", analysis_page)
    retrospective_group.setObjectName("retrospectiveWorkspace")
    retrospective_layout = QVBoxLayout(retrospective_group)
    retrospective_explanation = QLabel(
        "Drop historical five-marker Atracsys CSV files here. FE/LB is detected "
        "from the filename; the app asks when the filename is ambiguous."
    )
    retrospective_explanation.setObjectName("mutedLabel")
    retrospective_explanation.setWordWrap(True)
    retrospective_layout.addWidget(retrospective_explanation)
    drop_zone = QFrame(retrospective_group)
    drop_zone.setObjectName("retrospectiveDropZone")
    drop_zone.setMinimumHeight(118)
    drop_layout = QVBoxLayout(drop_zone)
    drop_title = QLabel("Drop CSV files here", drop_zone)
    drop_title.setObjectName("retrospectiveDropTitle")
    drop_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
    drop_hint = QLabel("or select files – the recalculated figure opens automatically", drop_zone)
    drop_hint.setObjectName("retrospectiveDropHint")
    drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
    drop_layout.addStretch(1)
    drop_layout.addWidget(drop_title)
    drop_layout.addWidget(drop_hint)
    drop_layout.addStretch(1)
    retrospective_layout.addWidget(drop_zone)
    action_row = QHBoxLayout()
    select_button = QPushButton("Select historical CSV …", retrospective_group)
    status = QLabel("Ready for historical Atracsys CSV files.", retrospective_group)
    status.setObjectName("retrospectiveStatus")
    action_row.addWidget(select_button)
    action_row.addWidget(status, 1)
    retrospective_layout.addLayout(action_row)
    analysis_layout.addWidget(retrospective_group)
    analysis_layout.addStretch(1)
    analysis_index = tabs.addTab(analysis_page, "Auswertung")

    outer.addWidget(tabs, 1)
    marker_footer = QWidget(central)
    marker_footer.setObjectName("markerStatusFooter")
    footer_layout = QVBoxLayout(marker_footer)
    footer_layout.setContentsMargins(8, 4, 8, 0)
    footer_layout.setSpacing(6)
    footer_layout.addLayout(marker_heading)
    footer_layout.addWidget(window.marker_scroll)
    outer.addWidget(marker_footer)
    window.marker_status_footer = marker_footer
    _tool_dropdown(toolbar)
    _measurement_dropdown(window, panel, tabs, devices_index)
    window.workspace_tabs = tabs
    from .zwick_ui import add_settings
    add_settings(window, tabs)
    window.setAcceptDrops(True)
    window.retrospective_controller = _RetrospectiveController(
        window, tabs, analysis_index, status, select_button, drop_zone
    )
    window.setStyleSheet(window.styleSheet() + EXTRA_STYLE)
