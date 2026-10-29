"""Explicit hooks into the inspected installed version; original modules stay intact."""
from __future__ import annotations

import time


def install():
    from atracsys_tracker import recording
    from atracsys_tracker.ui.main_window import MainWindow
    from atracsys_tracker.ui import main_window
    from .external_control import RegistryControl
    from .layout import organize_window
    from .analysis_selection import populate
    from .panel import ScriptPanel
    from .adapter import validate_tracker_contract
    from atracsys_tracker.adapters.native_bridge import NativeBridgeAdapter
    from .network import discover_camera
    from atracsys_tracker.acquisition import AcquisitionWorker
    from atracsys_tracker.adapters.base import FrameTimeoutError
    from .camera_health import CameraGuard, GuardedAdapter, StreamHealth
    from .overlay_performance import install as install_fast_outlines
    from .fiducial_export import ObservedAdapter, FiducialSession, load_geometries

    Worker = recording.RecordingWorker
    if getattr(Worker, "_measurement_extension_installed", False):
        return
    install_fast_outlines()
    main_window.WindowsRegistryControl = RegistryControl
    validate_tracker_contract(recording, MainWindow, NativeBridgeAdapter)
    from .instance_visibility import install as install_instance_visibility
    install_instance_visibility(MainWindow)

    start_original = Worker.start_session
    enqueue_original = Worker.enqueue
    write_original = Worker._write_frame
    stop_original = Worker.stop
    window_init = MainWindow.__init__
    window_start = MainWindow._start_recording
    window_toggle = MainWindow._toggle_recording
    window_refresh = MainWindow._refresh_recording_status
    window_close = MainWindow.closeEvent
    bridge_connect = NativeBridgeAdapter.connect
    new_adapter = AcquisitionWorker._new_adapter
    launch_analysis = MainWindow._maybe_launch_analysis

    def guarded_adapter(self, *args, **kwargs):
        if not hasattr(self, "_camera_health"):
            self._camera_health = StreamHealth()
        return ObservedAdapter(GuardedAdapter(new_adapter(self, *args, **kwargs),
                                             self._camera_health, FrameTimeoutError), self)

    def maybe_analyze(self, *args, **kwargs):
        if getattr(self.recorder, "_camera_abort", False):
            return
        return launch_analysis(self, *args, **kwargs)

    def connect_bridge(self, *args, **kwargs):
        discover_camera(self.sdk_root)
        return bridge_connect(self, *args, **kwargs)

    extra_columns = ["shared_sample_index", "time_since_start_s", "synchronization"]
    recording.CSV_COLUMNS.extend(c for c in extra_columns if c not in recording.CSV_COLUMNS)

    def start_session(self, *args, **kwargs):
        group = getattr(self, "_script_group", None)
        context = None
        fiducial_definitions, fiducial_info = {}, {}
        root = getattr(self, '_coordinate_project_root', None)
        if root is not None:
            # Read local files BEFORE establishing either recording clock.
            # The fixed RecordingWorker contract puts metadata third, or in a keyword.
            from .coordinate_context import snapshot
            metadata = kwargs.get('metadata', args[2] if len(args) > 2 else {})
            names = metadata.get('geometry_files', ()) if isinstance(metadata, dict) else ()
            context = snapshot(root, names)
            fiducial_definitions, fiducial_info = load_geometries(root, names)
        try:
            # Hold the existing re-entrant lock until auxiliary writers are initialized.
            # The acquisition and write hooks acquire it before consuming a frame.
            with self._lock:
                health = self._camera_source._camera_health.snapshot()
                if not health["stable"]:
                    raise RuntimeError("Kameradaten sind noch nicht stabil; bitte erneut starten.")
                self._camera_epoch = health["epoch"]
                self._camera_last_sample = time.monotonic()
                self._camera_abort = False
                self._overflow_interval_open = False
                self._sync_ticks = {}
                result = start_original(self, *args, **kwargs)
                camera_adapter = getattr(self._camera_source, '_adapter', None)
                info = getattr(camera_adapter, 'measurement_info', lambda: {})()
                fiducial_info.update(info)
                self._fiducial_session = FiducialSession(
                    self._csv_path, self._session_id, self._started_monotonic,
                    fiducial_definitions, fiducial_info)
                self._metadata['fiducial_export'] = {
                    'csv': str(self._fiducial_session.path),
                    'manifest': str(self._fiducial_session.manifest),
                    'source': fiducial_info.get('source', 'received_stereo_image_frames'),
                    'sampling': fiducial_info.get('sampling', 'Independent image rate, not high-rate pose samples'),
                    'coordinates': 'Measured camera coordinates in mm; no synthesized positions',
                    'point_numbering': ('Original SDK correspondences' if info else
                                        'Unique geometric matching, not original SDK correspondences')}
                if group is not None:
                    self._script_started_counter = time.perf_counter()
                    group.begin_recording(self._csv_path, self._session_id, self._script_started_counter)
                    self._metadata["measurement_scripts"] = {
                        "manifest": str(group.metadata_path), "hardware_synchronized": False,
                        "mode": group.synchronization, "clock": "Windows QueryPerformanceCounter"}
                if context is not None:
                    self._metadata['coordinate_context'] = context
                return result
        except Exception:
            if self.stats().state != "Gestoppt":
                stop_original(self)
            fiducials = getattr(self, '_fiducial_session', None)
            self._fiducial_session = None
            if fiducials is not None:
                fiducials.info['error'] = fiducials.info['error'] or 'Recording preparation did not complete.'
                fiducials.close()
            if group is not None:
                group.stop()
            raise

    def enqueue(self, frame):
        with self._lock:
            group = getattr(self, "_script_group", None)
            if frame.computer_monotonic_s < getattr(self, "_started_monotonic", 0):
                return False
            from .measurement_channel import enqueue_joint_frame
            accepted = enqueue_joint_frame(self, frame, enqueue_original)
            if accepted:
                self._camera_last_sample = time.monotonic()
            if accepted and group is not None and group.recording:
                tick = group.frame(frame, reference_time_s=time.perf_counter())
                self._sync_ticks[(frame.frame_counter, frame.computer_monotonic_s)] = tick
            return accepted

    def write_frame(self, writer, frame):
        fiducials = getattr(self, '_fiducial_session', None)
        if fiducials is not None and getattr(frame, 'pixel_format', '') == 'MEASUREMENT_V1':
            fiducials.offer(frame)
            if fiducials.info['measurement_ring_drops']:
                self._metadata['camera_data_incomplete'] = True
            if fiducials.info['incomplete_sdk_frames']:
                self._metadata['fiducial_data_incomplete'] = True
        with self._lock:
            group = getattr(self, "_script_group", None)
            tick = getattr(self, "_sync_ticks", {}).pop((frame.frame_counter, frame.computer_monotonic_s), None)
        if tick is None:
            return write_original(self, writer, frame)
        synchronization = group.synchronization if group is not None else "not_synchronized"

        class Writer:
            def writerow(_, row):
                row.update(computer_monotonic_s=tick["computer_monotonic_s"],
                           shared_sample_index=tick["sample_index"], time_since_start_s=tick["time_s"],
                           synchronization=synchronization)
                return writer.writerow(row)

        return write_original(self, Writer(), frame)

    def stop(self, *args, **kwargs):
        fiducials = getattr(self, '_fiducial_session', None)
        try:
            result = stop_original(self, *args, **kwargs)
        finally:
            self._fiducial_session = None
            if fiducials is not None:
                try:
                    report = fiducials.close()
                    if not report['complete']:
                        self._last_error = 'Fiducial-Export unvollständig; siehe .fiducials.json.'
                except OSError as error:
                    self._last_error = f'Fiducial-Export konnte nicht finalisiert werden: {error}'
        group = getattr(self, "_script_group", None)
        if group is not None:
            group.stop(tracker_stats=result)
            problems = [s.snapshot() for s in group.sources if s.error or s.rows != group.sample_index]
            if problems:
                self._last_error = "Messskript-Aufnahme unvollständig. Details in der .scripts.json-Datei."
            self._script_group = None
            return self.stats()
        return self.stats() if fiducials is not None else result

    def init_window(self, *args, **kwargs):
        window_init(self, *args, **kwargs)
        self.recorder._camera_source = self.acquisition
        self.acquisition._fiducial_recorder = self.recorder
        self.recorder._coordinate_project_root = self.project_root
        self.camera_guard = CameraGuard(self)
        self.script_panel = ScriptPanel(self, window_start)
        self.centralWidget().layout().insertWidget(3, self.script_panel)
        organize_window(self, self.script_panel)
        from .measurement_ui import MeasurementRateController
        self.measurement_rate_controller = MeasurementRateController(self)
        self.setWindowTitle(self.windowTitle() + " · Messskripte")

    def start_window(self, *args, **kwargs):
        free = getattr(self, 'free_analysis_controller', None)
        if free is not None and free.busy():
            self._set_recording_message('Bitte die freie Zuordnung/Auswertung zuerst abschließen.', 'ready')
            return
        updater = getattr(self, "update_controller", None)
        if updater is not None and updater.busy():
            self._set_recording_message("Bitte das laufende Update abwarten.", "ready")
            return
        return self.script_panel.start_recording(*args, **kwargs)

    def toggle_window(self):
        if self.script_panel.pending is not None:
            self.script_panel.cancel()
            return
        return window_toggle(self)

    def refresh_window(self, *args, **kwargs):
        result = window_refresh(self, *args, **kwargs)
        panel = getattr(self, "script_panel", None)
        if panel is not None and panel.pending is not None:
            self._set_recording_message("Messgeräte werden vorbereitet …", "ready")
        zwick = getattr(self, 'zwick_controller', None)
        if zwick is not None:
            zwick.refresh()
        return result

    def poll_external(self, now):
        controller = getattr(self, 'zwick_controller', None)
        if controller is not None:
            controller.poll(now)

    def close_window(self, event):
        free = getattr(self, 'free_analysis_controller', None)
        if free is not None and free.busy():
            self._set_recording_message('Freie Auswertung läuft. Bitte vor dem Schließen den Abschluss abwarten.', 'ready')
            event.ignore()
            return
        updater = getattr(self, "update_controller", None)
        if updater is not None and updater.busy():
            updater.status.setText("Update läuft. Bitte vor dem Schließen den Abschluss abwarten.")
            event.ignore()
            return
        try:
            window_close(self, event)
        finally:
            self.script_panel.shutdown()

    Worker.start_session = start_session
    Worker.enqueue = enqueue
    Worker._write_frame = write_frame
    Worker.stop = stop
    Worker._measurement_extension_installed = True
    MainWindow.__init__ = init_window
    MainWindow._start_recording = start_window
    MainWindow._toggle_recording = toggle_window
    MainWindow._refresh_recording_status = refresh_window
    MainWindow._poll_external_control = poll_external
    MainWindow.closeEvent = close_window
    MainWindow._maybe_launch_analysis = maybe_analyze
    MainWindow._populate_analysis_scripts = populate
    AcquisitionWorker._new_adapter = guarded_adapter
    NativeBridgeAdapter.connect = connect_bridge
