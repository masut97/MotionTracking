"""End-to-end mock-only check inside the actual packaged Qt application."""
import csv
import json
from pathlib import Path
import time
import traceback


class _MemoryRegistry:
    """No Windows registry or real external software is touched by this test."""
    HKEY_CURRENT_USER=1; KEY_READ=1; KEY_WRITE=2
    REG_SZ=1; REG_DWORD=4; REG_QWORD=11
    def __init__(self): self.data={}
    def CreateKeyEx(self,*_args): return self
    def OpenKey(self,*_args): return self
    def __enter__(self): return self
    def __exit__(self,*_args): pass
    def QueryValueEx(self,_key,name):
        if name not in self.data: raise FileNotFoundError(name)
        return self.data[name]
    def SetValueEx(self,_key,name,_zero,kind,value): self.data[name]=(value,kind)


def run(output: Path):
    output.mkdir(parents=True, exist_ok=True)
    import faulthandler
    with (output/'native-fault.log').open('w', encoding='utf-8') as fault_log:
        faulthandler.enable(file=fault_log, all_threads=True)
        try:
            return _run(output)
        finally:
            faulthandler.disable()


def _run(output):
    from PySide6.QtCore import QPoint, QRect, Qt, QTimer
    from PySide6.QtWidgets import QApplication, QToolButton, QPushButton, QLabel
    from atracsys_tracker import app
    from atracsys_tracker.ui.main_window import MainWindow
    from .protocol import hardware_requirement
    root = app.project_root()
    root_config_path = root / "config.json"
    original_root_config = root_config_path.read_bytes()
    settings_path = root / "measurement_scripts.json"
    original_settings = settings_path.read_bytes() if settings_path.exists() else None
    config = json.loads((root / "config.json").read_text(encoding="utf-8"))
    config.update(adapter_mode="mock", native_bridge_enabled=False, registry_control_enabled=False,
                  auto_analysis_enabled=False, measurement_directory=str(output), log_directory=str(output / "logs"))
    config_path = output / "test_config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    original_init = MainWindow.__init__
    from atracsys_tracker.adapters.mock import MockAdapter
    original_mock_read = MockAdapter.read_frame
    expected_fiducials = {}

    def capture_mock_fiducials(self, *args, **kwargs):
        frame = original_mock_read(self, *args, **kwargs)
        for fiducial in frame.fiducials:
            expected_fiducials[(frame.frame_counter, frame.device_timestamp_us, fiducial.index)] = tuple(fiducial.position_mm)
        return frame

    MockAdapter.read_frame = capture_mock_fiducials
    windows = []
    errors = []
    recording_name = "gui_test_" + str(time.time_ns())
    completed_cycles = []

    def protect(callback):
        def call():
            try:
                callback()
            except Exception:
                errors.append(traceback.format_exc())
        return call

    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        from .project_identity import NAME, VERSION
        assert self.windowTitle() == f'{NAME} - Version {VERSION}'
        assert any(label.text() == 'MOTION TRACKING' for label in self.findChildren(QLabel))
        assert not self.windowIcon().isNull()
        from .step_import_self_test import run as test_step_import
        self._step_import_test=test_step_import(output/'synthetic_step_import',root,self)
        from .qt_event_self_test import run as test_qt_event_scope
        self._qt_event_test=test_qt_event_scope(output/'synthetic_qt_events')
        self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        windows.append(self)
        self.record_name_edit.setText(recording_name)
        self.record_directory_edit.setText(str(output))
        self.record_frequency_spin.setValue(20)
        for row, spec in enumerate(self.script_panel.specs):
            self.script_panel.table.item(row, 0).setCheckState(Qt.CheckState.Checked if spec.simulated else Qt.CheckState.Unchecked)
        assert self.workspace_tabs.count() == 4
        tab_names = tuple(self.workspace_tabs.tabText(i).split(" (")[0] for i in range(4))
        assert tab_names == ("Live", "Messgeräte", "Auswertung", "Einstellungen"), tab_names
        assert self.findChild(QPushButton, 'stopRecordingButton') is not None
        assert self.record_frequency_spin.lineEdit().isReadOnly() is False
        self.record_frequency_spin.lineEdit().setText('20')
        self.record_frequency_spin.interpretText()
        assert self.record_frequency_spin.value() == 20
        measurement_menu = self.findChild(QToolButton, "measurementMenuButton")
        workspace_menu = self.findChild(QToolButton, "workspaceMenuButton")
        assert measurement_menu is not None and measurement_menu.menu() is not None
        assert workspace_menu is not None and len(workspace_menu.menu().actions()) == 2
        assert self.findChild(QPushButton, "softwareUpdateButton") is not None
        from PySide6.QtWidgets import QCheckBox
        profiles = self.findChild(QCheckBox, "analysisProfilesToggle")
        assert profiles is not None and not profiles.isChecked()
        combo = self.analysis_combo
        selected_analysis = combo.currentData()
        base_choices = {Path(combo.itemData(i)).name for i in range(1, combo.count())}
        assert {'auto_auswertung.py', 'rom_auswertung.py', 'trochanter_auswertung.py', 'toggling_auswertung.py', 'free_auswertung.py'} <= base_choices
        assert len(base_choices) <= 5, base_choices
        # Exercise newly selectable analysis with synthetic poses only. In
        # particular this verifies SciPy modules in the frozen offline runtime.
        import sys
        from types import SimpleNamespace
        import numpy as np
        sys.path.insert(0, str(root / 'analysis_scripts'))
        from _lib.toggling import motion as toggling_motion, cycles as toggling_cycles
        synthetic_t = np.arange(600)*.01
        synthetic_signal = .4*np.sin(2*np.pi*synthetic_t*2)
        synthetic_poses = {marker: tuple(SimpleNamespace(
            translation_mm=(float(v) if marker == 342 else 0., 0., 0.),
            rotation=np.eye(3)) for v in synthetic_signal) for marker in (340,341,342)}
        synthetic_curves, _ = toggling_motion(
            SimpleNamespace(times_s=synthetic_t, poses=synthetic_poses), 340,341,342,[0,0,50],0)
        np.testing.assert_allclose(synthetic_curves['compensated_screw_point'][:,0], synthetic_signal, atol=1e-10)
        assert any(row['accepted'] for row in toggling_cycles(synthetic_t, synthetic_curves, 2)['rows'])
        short_gap_mask = np.arange(len(synthetic_t)) % 25 != 12
        short_gap_cycles = toggling_cycles(synthetic_t[short_gap_mask],
            {key: values[short_gap_mask] for key, values in synthetic_curves.items()}, 2)
        short_gap_good = [row for row in short_gap_cycles['rows'] if row['accepted']]
        assert len(short_gap_good) >= 4
        assert abs(np.median([row['raw']['compensated_screw_point'][0] for row in short_gap_good])-.8) < .02
        profiles.setChecked(True)
        expanded = {Path(combo.itemData(i)).name for i in range(1, combo.count())}
        assert {'Auswertung_299_331_332.py', 'Auswertung_299_331_333.py', 'Auswertung_340_341_342.py'} <= expanded
        assert not any(name.endswith('_core.py') for name in expanded)
        assert combo.currentData() == selected_analysis
        profiles.setChecked(False)
        assert combo.currentData() == selected_analysis
        assert {Path(combo.itemData(i)).name for i in range(1, combo.count())} == base_choices
        from .free_analysis_ui import ProfileDialog, backend, save_profile
        from PySide6.QtGui import QCloseEvent
        assert self.findChild(QPushButton, 'freeAnalysisSetupButton') is not None
        assert self.findChild(QPushButton, 'freeAnalysisCsvButton') is not None
        sample_profile = dict(version=1, name='General profile test', reference=101, baseline_seconds=0,
            markers={'101':'Reference', '102':'Object A', '103':'Object B', '104':'Object C'},
            comparisons=[dict(name='B relative to A', anchor=102, target=103, outputs=['tx','rotation_axis'], axis=[0,0,1]),
                         dict(name='C relative to reference', anchor=101, target=104, outputs=['path'], axis=[1,0,0])])
        editor = ProfileDialog(root, sample_profile, self)
        assert editor.value() == backend(root).validate_profile(sample_profile)
        stored = output/'test_free_profile.json'
        save_profile(stored, editor.value(), backend(root).validate_profile)
        assert backend(root).load_profile(stored) == editor.value()
        editor.deleteLater()
        visible = {'299:10':'Reference instance', '301:20':'First 301', '301:21':'Second 301',
                   '302:30':'First 302', '302:31':'Second 302'}
        instances = ProfileDialog(root, parent=self, visible_choices=lambda: visible)
        assert instances.markers.rowCount() == 5
        instances.add_pair(dict(name='Two instances', anchor='301:20', target='301:21', outputs=['tx'], axis=[0,0,1]))
        instances.reference.setCurrentIndex(instances.reference.findData(299))
        assert instances.value()['comparisons'][0]['target'] == '301:21'
        # Exercise the actual buttons, not only programmatically populated rows.
        add_marker_button = next(b for b in instances.findChildren(QPushButton) if b.text() == 'Marker hinzufügen')
        add_pair_button = next(b for b in instances.findChildren(QPushButton) if b.text() == 'Vergleich hinzufügen')
        add_marker_button.click()  # all visible markers are already listed
        assert instances.markers.rowCount() == 5
        assert instances.reference.currentData() == 299
        instances.markers.selectRow(4); instances.remove_rows(instances.markers)
        assert instances.reference.currentData() == 299
        QTimer.singleShot(0, lambda: QApplication.activeModalWidget().accept())
        add_marker_button.click()
        assert instances.markers.rowCount() == 5
        assert instances.reference.currentData() == 299
        assert instances.pairs.cellWidget(0, 1).currentData() == '301:20'
        assert instances.pairs.cellWidget(0, 2).currentData() == '301:21'
        # A duplicate/incomplete edit must not empty otherwise valid choices.
        instances.add_marker('301:20', 'Duplicate test')
        assert instances.reference.currentData() == 299
        assert instances.pairs.cellWidget(0, 2).currentData() == '301:21'
        instances.markers.removeRow(5); instances.refresh_choices()
        assert not instances.error.text(), 'Corrected duplicate left a stale error message'
        add_pair_button.click()
        assert instances.pairs.rowCount() == 2
        for column, identity in ((1, '302:30'), (2, '302:31')):
            choice = instances.pairs.cellWidget(1, column)
            assert choice.findData(identity) >= 0
            choice.setCurrentIndex(choice.findData(identity))
        assert len(instances.value()['comparisons']) == 2
        instances.resize(1100, 800); instances.show(); QApplication.processEvents()
        instances.grab().save(str(output/'free-instances.png'))
        visible.pop('301:21')
        instances.use_visible()
        target = instances.pairs.cellWidget(0, 2)
        assert target.currentData() == '301:21'
        assert not target.model().item(target.currentIndex()).isEnabled()
        assert instances.reference.currentData() == 299
        try:
            instances.value()
        except ValueError:
            pass
        else:
            raise AssertionError('Invisible instance accepted')
        instances.close(); instances.deleteLater()
        # Explicit user conversion preserves roles/names despite per-frame ID changes.
        live = {'299:10':'299', '302:20':'302', '351:30':'351'}
        volatile = dict(version=1, name='Rebind', reference='299:10', baseline_seconds=0,
            markers={'299:10':'Reference', '302:20':'Base', '351:30':'Object'},
            comparisons=[dict(name='Object vs base', anchor='302:20', target='351:30', outputs=['tx'], axis=[0,0,1])])
        rebinding = ProfileDialog(root, volatile, self, visible_choices=lambda: live)
        rebinding.use_unique_geometries()
        rebound = rebinding.value()
        assert rebound['markers'] == {'299':'Reference', '302':'Base', '351':'Object'}
        assert rebound['reference'] == 299
        assert rebound['comparisons'][0]['anchor'] == 302
        assert rebound['comparisons'][0]['target'] == 351
        live.clear(); live.update({'299:400':'299', '302:500':'302', '351:600':'351'})
        assert rebinding.value() == rebound
        rebinding.use_visible()
        assert rebinding.markers.rowCount() == 3
        live['351:601'] = 'Second 351'
        try:
            rebinding.value()
        except ValueError:
            pass
        else:
            raise AssertionError('Geometry binding accepted simultaneous ambiguous instances')
        rebinding.deleteLater()
        csv_rebinding = ProfileDialog(root, volatile, self)
        csv_rebinding.apply_csv_choices({'299':'Reference', '302':'Base', '351':'Object'})
        assert csv_rebinding.value() == rebound
        assert csv_rebinding.markers.rowCount() == 3
        csv_rebinding.deleteLater()
        controller = self.free_analysis_controller
        original_start = self.script_panel.start_recording
        calls = []
        self.script_panel.start_recording = lambda *_a, **_k: calls.append(True)
        try:
            controller.editing = True
            self._start_recording('ui')
            assert not calls, 'Recording started during profile editing'
            close = QCloseEvent(); self.closeEvent(close)
            assert not close.isAccepted(), 'Window closed during free-analysis activity'
            controller.editing = False
            self._start_recording('ui')
            assert calls == [True]
        finally:
            controller.editing = False
            self.script_panel.start_recording = original_start
        assert not self.workspace_tabs.isAncestorOf(self.marker_scroll)
        assert self.marker_scroll.parentWidget() is self.marker_status_footer

        deadline = None

        def recording_diagnostics():
            panel = self.script_panel
            return {"recorder": repr(self.recorder.stats()),
                    "panel_message": panel.message.text(),
                    "pending": repr(panel.pending),
                    "preparation_elapsed_s": time.monotonic() - panel.test_started,
                    "button": self.record_button.text(),
                    "sources": [source.snapshot() for source in panel.group.sources] if panel.group else []}

        def capture(filename):
            assert self.marker_status_footer.isVisible()
            assert self.marker_scroll.isVisible()
            footer = self.marker_status_footer
            bounds = QRect(footer.mapTo(self, QPoint(0, 0)), footer.size())
            visible_child = self.childAt(bounds.center())
            geometry = {"window": self.rect().getRect(), "footer": bounds.getRect(),
                        "tabs": self.workspace_tabs.geometry().getRect(),
                        "center_child": visible_child.objectName() if visible_child else None,
                        "center_child_type": type(visible_child).__name__ if visible_child else None}
            (output / (filename + ".layout.json")).write_text(json.dumps(geometry), encoding="utf-8")
            assert self.rect().contains(bounds), geometry
            assert visible_child is footer or footer.isAncestorOf(visible_child), geometry
            self.grab().save(str(output / filename))

        def begin_second():
            first_manifest = output / (recording_name + ".scripts.json")
            fiducial_manifest = output / (recording_name + '.fiducials.json')
            assert json.loads(fiducial_manifest.read_text())['complete'], 'Fiducial export not finalized at stop'
            assert (output / (recording_name + ".json")).exists()
            first_bytes = first_manifest.read_bytes()
            assert json.loads(first_bytes)["state"] == "complete"
            first_stamp = first_manifest.stat().st_mtime_ns
            completed_cycles.append(recording_name)
            second_name = recording_name + "_second"
            self.record_name_edit.setText(second_name)
            self._start_recording("ui")
            assert self.record_button.text() == "Aufnahme starten"
            assert not self.record_button.isEnabled()
            assert self.stop_record_button.isEnabled()
            assert "Fehler" not in measurement_menu.text()
            second_deadline = time.monotonic() + 16

            def finish_second():
                stats = self.recorder.stats()
                if stats.state != "Gestoppt" and stats.frame_count >= 20:
                    assert first_manifest.read_bytes() == first_bytes
                    assert first_manifest.stat().st_mtime_ns == first_stamp
                    self._stop_recording("ui")
                    assert (output / (second_name + ".json")).exists()
                    second_manifest = json.loads((output / (second_name + ".scripts.json")).read_text(encoding="utf-8"))
                    assert second_manifest["state"] == "complete"
                    completed_cycles.append(second_name)
                    # Cancelling a new preparation must not claim that the
                    # preceding measurement is the newly saved test.
                    from .external_control import reported_state
                    cancelled_name=recording_name+'_cancelled'
                    self.record_name_edit.setText(cancelled_name)
                    self._start_recording('ui')
                    assert self.script_panel.pending is not None
                    self.zwick_controller.stop()
                    assert self.script_panel.pending is None
                    assert reported_state(self.recorder.stats(),False,self.zwick_controller.cancelled)=='Gestoppt'
                    assert not (output/(cancelled_name+'.csv')).exists()
                    self.close()
                elif time.monotonic() >= second_deadline:
                    errors.append("Zweite Aufnahme wurde nicht rechtzeitig bereit: " + json.dumps(recording_diagnostics(), ensure_ascii=False))
                    self.close()
                else:
                    QTimer.singleShot(200, protect(finish_second))
            QTimer.singleShot(200, protect(finish_second))

        def capture_tabs_and_stop():
            from dataclasses import replace
            states = tuple(self.marker_registry.states())
            first = next(state for state in states if state.visible)
            sibling = replace(first, identity_id=first.identity_id+100000,
                              pose=replace(first.pose, tracking_id=first.pose.tracking_id+100000))
            self._sync_marker_cards(states+(sibling,))
            assert first.identity_key in self._marker_cards
            assert sibling.identity_key in self._marker_cards
            self._remove_marker(first.identity_key)
            self._sync_marker_cards(states+(sibling,))
            assert first.identity_key not in self._marker_cards
            assert sibling.identity_key in self._marker_cards
            assert first.pose.geometry_id not in self._dismissed_geometry_ids
            assert self.restore_markers_button.isVisible()
            assert self.recorder.stats().state != 'Gestoppt'
            self._remove_marker(sibling.identity_key)
            self._restore_removed_markers()
            assert first.identity_key in self._marker_cards
            assert sibling.identity_key not in self._marker_cards
            assert not self._dismissed_markers

            def capture_analysis():
                capture("analysis.png")
                assert self.findChild(QPushButton, "softwareUpdateButton") is not None

            def capture_settings():
                self.workspace_tabs.setCurrentIndex(3)
                capture('settings.png')
                button=self.findChild(QPushButton,'softwareUpdateButton')
                assert button.isVisible()
                assert button.grab().save(str(output/'update-button.png'))

            def external_stop():
                memory=self._external_control.registry
                memory.data['RecordingFrequencyHz']=('invalid but stop must work',memory.REG_SZ)
                memory.data['StopRecord']=(1,memory.REG_DWORD)
                self.zwick_controller.poll()
                assert self.recorder.stats().state=='Gestoppt'
                assert self._external_control.values()['StopRecord']==0
                assert self._external_control.values()['StartRecord']==0
                self._external_control.write_frequency(20)

            self.workspace_tabs.setCurrentIndex(0)
            capture("live.png")
            QTimer.singleShot(250, protect(lambda: self.workspace_tabs.setCurrentIndex(1)))
            QTimer.singleShot(500, protect(lambda: capture("running.png")))
            QTimer.singleShot(750, protect(lambda: self.workspace_tabs.setCurrentIndex(2)))
            QTimer.singleShot(1000, protect(capture_analysis))
            QTimer.singleShot(1200, protect(capture_settings))
            QTimer.singleShot(1350, protect(lambda: self.workspace_tabs.setCurrentIndex(0)))
            QTimer.singleShot(1400, protect(external_stop))
            QTimer.singleShot(1750, protect(lambda: capture("stopped.png")))
            QTimer.singleShot(1950, protect(begin_second))

        def wait_for_recording():
            stats = self.recorder.stats()
            if stats.state != "Gestoppt" and stats.frame_count >= 20 and any(state.visible for state in self.marker_registry.states()):
                capture_tabs_and_stop()
            elif deadline is not None and time.monotonic() >= deadline:
                self.grab().save(str(output / "failed.png"))
                errors.append("Aufnahme wurde im UI-Test nicht innerhalb von 16 Sekunden bereit: " + json.dumps(recording_diagnostics(), ensure_ascii=False))
                self.close()
            else:
                QTimer.singleShot(200, protect(wait_for_recording))

        def begin_first():
            nonlocal deadline
            from .external_control import RegistryControl
            memory=_MemoryRegistry()
            self._external_control=RegistryControl(r'Software\IsolatedTest\Control',registry=memory)
            memory.data.update(RecordingName=(recording_name,memory.REG_SZ),
                RecordingDirectory=(str(output),memory.REG_SZ),RecordingFrequencyHz=('20',memory.REG_SZ))
            self.zwick_controller.poll()  # observe reset before accepting an edge
            assert self._external_control.values()['AppReady']==1
            self._external_control.write_start_record(True)
            self.zwick_controller.poll()
            assert self.script_panel.pending is not None
            assert not self.record_name_edit.isEnabled()
            assert not self.record_directory_edit.isEnabled()
            memory.data['RecordingName']=('must_not_change_active_session',memory.REG_SZ)
            memory.data['RecordingFrequencyHz']=('invalid while preparing',memory.REG_SZ)
            self.zwick_controller.poll()
            assert self.record_name_edit.text()==recording_name
            assert self.record_frequency_spin.value()==20
            deadline = time.monotonic() + 16

        QTimer.singleShot(1000, protect(begin_first))
        QTimer.singleShot(1200, protect(wait_for_recording))

    MainWindow.__init__ = init
    try:
        result = app.main(["--config", str(config_path), "--mode", "mock", "--auto-quit", "40"])
        if errors:
            raise AssertionError("\n".join(errors))
        assert len(completed_cycles) == 2, completed_cycles
        csv_path = output / (recording_name + ".csv")
        tracker_rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
        assert tracker_rows, "Keine Tracker-Daten"
        by_index = {}
        for row in tracker_rows:
            index = row["shared_sample_index"]
            assert index, "Gemeinsame Sample-ID fehlt"
            assert row["synchronization"] == "simulation"
            by_index[index] = row
        manifest = json.loads(csv_path.with_suffix(".scripts.json").read_text(encoding="utf-8"))
        tracker_metadata = json.loads(csv_path.with_suffix(".json").read_text(encoding="utf-8"))
        import hashlib
        for suffix in ('', '_second'):
            metadata = json.loads((output/(recording_name+suffix+'.json')).read_text(encoding='utf-8'))
            fiducial_info = json.loads((output/(recording_name+suffix+'.fiducials.json')).read_text())
            assert fiducial_info['complete'] and fiducial_info['frames'] > 0, fiducial_info
            assert fiducial_info['sdk_correspondence_exported'] is False
            fiducial_rows = list(csv.DictReader((output/(recording_name+suffix+'.fiducials.csv')).open(encoding='utf-8-sig')))
            measured_fiducials = [row for row in fiducial_rows if row['frame_fiducial_index'] and row['visible']=='1']
            assert measured_fiducials, 'No measured fiducial positions exported'
            for row in measured_fiducials:
                key = tuple(int(row[k]) for k in ('frame_counter', 'device_timestamp_us', 'frame_fiducial_index'))
                assert tuple(float(row[k]) for k in ('x_mm','y_mm','z_mm')) == expected_fiducials[key]
            assert metadata['fiducial_export']['source'] == 'received_stereo_image_frames'
            context = metadata['coordinate_context']
            assert 'error' not in context, context
            assert context['geometry_sha256']
            for name, digest in context['geometry_sha256'].items():
                assert digest == hashlib.sha256((root/'geometries'/name).read_bytes()).hexdigest()
            local_setup = root/'analysis_scripts/toggling_config.local.json'
            if local_setup.is_file():
                assert context['toggling_setup'] == json.loads(local_setup.read_text(encoding='utf-8'))
                from toggling_auswertung import verify_coordinate_frame
                assert verify_coordinate_frame(output/(recording_name+suffix+'.csv'), context['toggling_setup'])
        assert manifest["state"] == "complete", manifest
        expected_scripts = len([spec for spec in windows[0].script_panel.specs if spec.simulated])
        assert expected_scripts >= 1, "Kein Demo-Messskript installiert"
        assert len(manifest["scripts"]) == expected_scripts, manifest
        script_rows = []
        for script in manifest["scripts"]:
            rows = list(csv.DictReader(Path(script["path"]).open(encoding="utf-8")))
            # The existing tracker omits marker rows when no marker is visible.
            # Auxiliary sources must still contain one row for EVERY recorded frame.
            assert len(rows) == tracker_metadata["frame_count"], (len(rows), tracker_metadata["frame_count"])
            script_rows.append(rows)
            for row in rows:
                if row["sample_index"] not in by_index:
                    continue
                tracker = by_index[row["sample_index"]]
                assert row["computer_monotonic_s"] == tracker["computer_monotonic_s"]
                assert row["frame_counter"] == tracker["frame_counter"]
                assert row["tracker_device_timestamp_us"] == tracker["device_timestamp_us"]
                assert row["time_s"] == tracker["time_since_start_s"]
                assert row["synchronization"] == "simulation"
        for rows in script_rows[1:]:
            assert [row["computer_monotonic_s"] for row in script_rows[0]] == [row["computer_monotonic_s"] for row in rows]
        # Exercise the complete analysis exporter inside the frozen runtime,
        # with changing SDK IDs but exactly one geometry pose per frame.
        import numpy as np
        from toggling_auswertung import analyze as analyze_toggling, load_settings as toggling_settings
        synthetic = output/'SYNTHETIC_toggling.csv'
        columns = ['frame_counter','time_since_start_s','geometry_id','tracking_id','visible',
                   'x_mm','y_mm','z_mm', *[f'r{i}{j}' for i in range(3) for j in range(3)]]
        with synthetic.open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=columns); writer.writeheader()
            for frame in range(600):
                for marker in (340,341,342):
                    writer.writerow(dict(frame_counter=frame, time_since_start_s=5+frame*.01,
                        geometry_id=marker, tracking_id=frame*1000+marker, visible=1,
                        x_mm=float(.4*np.sin(2*np.pi*frame*.02)) if marker==342 else 0., y_mm=0.,z_mm=0.,
                        **{f'r{i}{j}':int(i==j) for i in range(3) for j in range(3)}))
        fixture_setup = dict(reference=340,bone=341,screw=342,baseline_seconds=0.,
                             screw_point_mm=[0,0,50],point_name='Synthetic point',marker_attachment='rigid_screw_shaft')
        fixture_context = json.loads(json.dumps(tracker_metadata['coordinate_context']))
        if 'geometry342.json5' in fixture_context['geometry_sha256']:
            fixture_setup['screw_geometry_sha256'] = fixture_context['geometry_sha256']['geometry342.json5']
        fixture_context['toggling_setup'] = fixture_setup
        synthetic.with_suffix('.json').write_text(json.dumps({'adapter_mode':'synthetic fixture',
            'coordinate_context':fixture_context}),encoding='utf-8')
        figure = analyze_toggling(synthetic,toggling_settings(recorded=fixture_setup), output/'toggling_report',2.)
        analysis_record = json.loads((figure.parent/'report.json').read_text(encoding='utf-8'))
        assert analysis_record['complete_samples'] == 600
        assert any(row['accepted'] for row in analysis_record['cycles']['rows'])
        assert 'Synthetic point relative to bone' in figure.read_text(encoding='utf-8')
        with (figure.parent/'compensated_screw_point_peak_envelopes.csv').open(encoding='utf-8-sig') as stream:
            exported_peaks = [row for row in csv.DictReader(stream)
                              if row['method'] == 'raw' and row['channel'] == 'x_mm']
        assert exported_peaks
        assert all(.38 < float(row['maximum']) < .42 and -.42 < float(row['minimum']) < -.38
                   for row in exported_peaks)
        # Run the real frozen analysis launcher in a fresh process. The imports
        # above must not hide missing sibling-module paths in a selected script.
        import subprocess
        import sys
        entry = output/'synthetic_toggling_entry.py'
        entry.write_text('import runpy, webbrowser\n'
                         'webbrowser.open = lambda *args, **kwargs: False\n'
                         f'runpy.run_path({str(root / "analysis_scripts/toggling_auswertung.py")!r}, run_name="__main__")\n',
                         encoding='utf-8')
        child = subprocess.run([sys.executable, '--run-analysis', str(entry), '--analysis-input', str(synthetic)],
                               cwd=output, capture_output=True, timeout=45,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        assert child.returncode == 0, (child.stdout+child.stderr).decode('utf-8', errors='replace')
        child_result = json.loads(child.stdout.decode('utf-8').strip().splitlines()[-1])
        child_figure = Path(child_result['figure_html'])
        assert child_figure.is_file()
        assert json.loads((child_figure.parent/'report.json').read_text())['complete_samples'] == 600
        software_sources = [spec for spec in windows[0].script_panel.specs
                            if spec.sync_mode == "best_effort_host_time_v1"]
        assert software_sources, "Kein Software-Sync-Messskript installiert"
        assert not hardware_requirement(software_sources, "native")
        from .measurement_self_test import run as joint_measurement_test
        joint_report = joint_measurement_test(output/'joint_measurements')
        report = {"passed": True, "frames": tracker_metadata["frame_count"], "marker_rows": len(tracker_rows),
                  "step_import":windows[0]._step_import_test,
                  "qt_event_scope":windows[0]._qt_event_test,
                  "joint_measurement_synthetic_test": joint_report,
                  "script_count": len(manifest["scripts"]), "frequency_hz": 20,
                  "exact_reference_timestamps": True, "hardware_synchronized": False,
                  "measured_fiducial_positions_and_immediate_finalization": True,
                  "recording_cycles": len(completed_cycles), "json_final_immediately_after_stop": True,
                  "marker_footer_visible_in_all_tabs": True,
                  "curated_analysis_selection_and_profile_toggle": True,
                  "toggling_synthetic_point_motion_and_cycles": True,
                  "recorded_coordinate_context": True,
                  "toggling_frozen_report_generation": True,
                  "toggling_frozen_standalone_entrypoint": True,
                  "toggling_peak_envelope_exports": True,
                  "toggling_short_sampling_gaps": True,
                  "free_profile_editor_save_and_activity_guards": True,
                  "zwick_start_stop_and_frozen_parameters": True,
                  "separate_start_stop_and_settings_tab": True,
                  "cancelled_preparation_not_reported_as_saved": True,
                  "source_processes_stopped": all(s.process.poll() is not None for s in windows[0].script_panel.group.sources)}
        (output / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return result
    except Exception:
        (output / "result.json").write_text(json.dumps({"passed": False, "error": traceback.format_exc()}, indent=2), encoding="utf-8")
        return 1
    finally:
        MainWindow.__init__ = original_init
        MockAdapter.read_frame = original_mock_read
        root_config_path.write_bytes(original_root_config)
        if original_settings is None:
            settings_path.unlink(missing_ok=True)
        else:
            settings_path.write_bytes(original_settings)
