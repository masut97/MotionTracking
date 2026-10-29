"""Measured stereo fiducial sidecar; independent of the high-rate pose ring.

Coordinates always come from triangulated observations, never from a rigid pose.
Geometry poses are used only for explicitly labelled, conservative point naming.
"""
import csv
import hashlib
import json
from pathlib import Path
import queue
import threading
import os
from types import SimpleNamespace

import numpy as np


COLUMNS = ('session_id', 'frame_counter', 'device_timestamp_us', 'computer_monotonic_s',
           'time_since_start_s', 'geometry_id', 'tracking_id', 'geometry_fiducial_number',
           'frame_fiducial_index', 'visible', 'assignment_status', 'assignment_distance_mm',
           'x_mm', 'y_mm', 'z_mm', 'left_index', 'right_index', 'epipolar_error_px',
           'triangulation_error_mm', 'probability', 'status')
MATCH_LIMIT_MM = 1.0


def sdk_observation_rows(frame, definitions):
    """Use the SDK correspondence table from this exact measurement frame."""
    assignments = frame.native_owner.sdk_correspondences
    markers = {(m.geometry_id, m.tracking_id): m for m in frame.markers}
    used, rows = set(), []
    for f in frame.fiducials:
        valid = str(f.status).strip().casefold() in {'ok','0','0x00000000'} and np.isfinite(f.position_mm).all()
        row = dict(frame_fiducial_index=f.index, visible=int(valid), status=f.status,
                   left_index=f.left_index, right_index=f.right_index, epipolar_error_px=f.epipolar_error_px,
                   triangulation_error_mm=f.triangulation_error_mm, probability=f.probability)
        if valid:
            row.update(zip(('x_mm','y_mm','z_mm'), f.position_mm))
        geometry, tracking, index, count = assignments.get(f.index, (-1,0,-1,0))
        marker = markers.get((geometry, tracking))
        if (valid and count == 1 and marker is not None and 0 <= index < 64
                and marker.presence_mask & (1 << index)):
            row.update(geometry_id=geometry, tracking_id=tracking, geometry_fiducial_number=index+1,
                       assignment_status='sdk_correspondence')
            used.add((geometry, tracking, index+1))
        else:
            row['assignment_status'] = ('invalid_observation' if not valid else
                                        'ambiguous_sdk_correspondence' if count > 1 else 'unassigned')
        rows.append(row)
    for (geometry, tracking), marker in markers.items():
        for index in range(len(definitions.get(geometry, ()))):
            if (geometry, tracking, index+1) not in used:
                rows.append(dict(geometry_id=geometry, tracking_id=tracking, geometry_fiducial_number=index+1,
                    visible=0, assignment_status='not_uniquely_assigned' if marker.presence_mask & (1 << index)
                    else 'not_in_marker_presence_mask'))
    return rows or [dict(visible=0, assignment_status='no_triangulated_fiducials')]


def load_geometries(root, names):
    """Read exact local definitions before starting either recording clock."""
    root = Path(root)
    config = json.loads((root / 'config.json').read_text(encoding='utf-8-sig'))
    directory = Path(config.get('geometry_directory') or 'geometries')
    directory = directory if directory.is_absolute() else root / directory
    definitions, hashes, errors = {}, {}, []
    for name in names:
        try:
            if Path(name).name != name or '/' in name or '\\' in name:
                raise ValueError('Expected a plain geometry filename')
            path = directory / name
            raw = path.read_bytes()
            payload = json.loads(raw.decode('utf-8-sig'))
            geometry = payload['geometry']
            marker = int(geometry['id'])
            points = np.asarray([f['position'] for f in payload['fiducials']], dtype=float)
            if points.shape != (int(geometry['count']), 3) or not np.isfinite(points).all():
                raise ValueError('Invalid fiducial coordinates/count')
            if marker in definitions:
                raise ValueError('Duplicate geometry identifier')
            definitions[marker] = points
            hashes[name] = hashlib.sha256(raw).hexdigest()
        except (OSError, ValueError, KeyError, TypeError) as error:
            errors.append(f'{name}: {error}')
    return definitions, dict(geometry_sha256=hashes, geometry_errors=errors,
                            nominal_image_limit_hz=config.get('native_image_fps_limit'))


def observation_rows(frame, definitions):
    """Return measured positions with unique reciprocal matches inside 1 mm.

    Local numbering is geometrically inferred (1-based), NOT the SDK's original
    correspondence table. Every candidate within the limit counts as ambiguity;
    a nearest-neighbour choice is never forced. Presence masks are respected.
    """
    if hasattr(getattr(frame, 'native_owner', None), 'sdk_correspondences'):
        return sdk_observation_rows(frame, definitions)
    expected = []
    for marker in frame.markers:
        points = definitions.get(marker.geometry_id)
        if points is None:
            continue
        rotation = np.asarray(marker.rotation_matrix)
        position = np.asarray(marker.position_mm)
        if not np.isfinite(rotation).all() or not np.isfinite(position).all():
            continue
        for index, point in enumerate(points):
            present = bool(marker.presence_mask & (1 << index))
            expected.append((marker.geometry_id, marker.tracking_id, index + 1,
                             rotation @ point + position, present))
    observations = tuple(frame.fiducials)
    valid = [str(f.status).strip().casefold() in {'ok', '0', '0x00000000'}
             and np.isfinite(f.position_mm).all() for f in observations]
    distances = np.full((len(observations), len(expected)), np.inf)
    if observations and expected:
        measured = np.asarray([f.position_mm for f in observations])
        predicted = np.asarray([e[3] for e in expected])
        distances = np.linalg.norm(measured[:, None, :] - predicted[None, :, :], axis=2)
        distances[~np.asarray(valid), :] = np.inf
        distances[:, ~np.asarray([e[4] for e in expected])] = np.inf
    candidates = distances <= MATCH_LIMIT_MM
    used = set()
    rows = []
    for i, f in enumerate(observations):
        options = np.flatnonzero(candidates[i])
        row = dict(frame_fiducial_index=f.index, visible=int(valid[i]), status=f.status,
                   left_index=f.left_index, right_index=f.right_index,
                   epipolar_error_px=f.epipolar_error_px,
                   triangulation_error_mm=f.triangulation_error_mm, probability=f.probability)
        if valid[i]:
            row.update(zip(('x_mm', 'y_mm', 'z_mm'), f.position_mm))
        if len(options) == 1 and np.sum(candidates[:, options[0]]) == 1:
            j = int(options[0])
            used.add(j)
            marker, tracking, number, _, _ = expected[j]
            row.update(geometry_id=marker, tracking_id=tracking,
                       geometry_fiducial_number=number,
                       assignment_status='unique_geometric_match', assignment_distance_mm=distances[i, j])
        else:
            row['assignment_status'] = ('invalid_observation' if not valid[i] else
                                        'ambiguous' if len(options) else 'unassigned')
        rows.append(row)
    for j, (marker, tracking, number, _, present) in enumerate(expected):
        if j not in used:
            rows.append(dict(geometry_id=marker, tracking_id=tracking,
                             geometry_fiducial_number=number, visible=0,
                             assignment_status='not_uniquely_assigned' if present else 'not_in_marker_presence_mask'))
    if not rows:
        rows.append(dict(visible=0, assignment_status='no_triangulated_fiducials'))
    return rows


class FiducialSession:
    def __init__(self, csv_path, session_id, started, definitions, info, capacity=512):
        self.path = Path(csv_path).with_suffix('.fiducials.csv')
        self.manifest = self.path.with_suffix('.json')
        self.session_id, self.started = session_id, started
        self.definitions = definitions
        self.info = dict(info, source='received_stereo_image_frames', coordinate_system='camera_mm',
                         assignment_method='unique_reciprocal_geometric_match',
                         assignment_limit_mm=MATCH_LIMIT_MM, numbering='1-based geometry-file order',
                         sdk_correspondence_exported=False,
                         sampling='Image stream only; independent of pose/recording frequency. No interpolation.',
                         timestamp_policy='Original image-frame device timestamp, counter and host timestamp.',
                         csv=str(self.path), frames=0, rows=0, queue_drops=0, error='',
                         assigned_observations=0, unassigned_observations=0,
                         invalid_observations=0, measurement_ring_drops=0,
                         incomplete_sdk_frames=0, finalized=False, complete=False)
        self.info.update(info)
        self.queue = queue.Queue(capacity)
        self.lock = threading.Lock()
        self.closed = False
        self.last_identity = None
        # Reserve both outputs exclusively. Never replace a pre-existing sidecar.
        self.meta_stream = self.manifest.open('x', encoding='utf-8')
        try:
            self.stream = self.path.open('x', encoding='utf-8-sig', newline='')
        except Exception:
            self.meta_stream.close()
            raise
        self.writer = csv.DictWriter(self.stream, fieldnames=COLUMNS)
        self.writer.writeheader()
        self.thread = threading.Thread(target=self._run, name='FiducialWriter', daemon=True)
        self.thread.start()

    def offer(self, frame):
        # Pose-ring frames carry no raw observations. Do not synthesize them.
        joint = getattr(frame, 'pixel_format', '') == 'MEASUREMENT_V1'
        if ((not frame.width and not joint) or frame.computer_monotonic_s < self.started
                or self.info['source'] == 'joint_measurement_ring_v1' and not joint):
            return
        with self.lock:
            identity = (frame.frame_counter, frame.device_timestamp_us)
            if self.closed or self.info['error'] or identity == self.last_identity:
                return
            self.last_identity = identity
            # Strip image buffers/native ownership before queueing: bounded small
            # immutable observation snapshots cannot retain full stereo frames.
            snapshot = SimpleNamespace()
            snapshot.frame_counter, snapshot.device_timestamp_us = identity
            snapshot.computer_monotonic_s = frame.computer_monotonic_s
            snapshot.markers, snapshot.fiducials = tuple(frame.markers), tuple(frame.fiducials)
            snapshot.native_owner = getattr(frame, 'native_owner', None)
            if joint:
                self.info['measurement_ring_drops'] = max(self.info['measurement_ring_drops'],
                    max(0, snapshot.native_owner.ring_drops-self.info.get('ring_drops_at_start', 0)))
                self.info['incomplete_sdk_frames'] += int(bool(snapshot.native_owner.measurement_flags))
            try:
                self.queue.put_nowait(snapshot)
            except queue.Full:
                self.info['queue_drops'] += 1

    def _run(self):
        try:
            while True:
                frame = self.queue.get()
                if frame is None:
                    break
                if self.info['error']:
                    continue
                try:
                    common = dict(session_id=self.session_id, frame_counter=frame.frame_counter,
                                  device_timestamp_us=frame.device_timestamp_us,
                                  computer_monotonic_s=frame.computer_monotonic_s,
                                  time_since_start_s=frame.computer_monotonic_s-self.started)
                    for row in observation_rows(frame, self.definitions):
                        self.writer.writerow(dict(common, **row))
                        self.info['rows'] += 1
                        if 'frame_fiducial_index' in row:
                            key = ('assigned_observations' if row['assignment_status'] in {'unique_geometric_match','sdk_correspondence'}
                                   else 'unassigned_observations' if row['visible'] else 'invalid_observations')
                            self.info[key] += 1
                    self.info['frames'] += 1
                except Exception as error:
                    self.info['error'] = str(error)
            self.stream.flush()
            os.fsync(self.stream.fileno())
        except Exception as error:
            self.info['error'] = str(error)
        finally:
            self.stream.close()

    def close(self):
        with self.lock:
            if self.closed:
                return self.info
            self.closed = True
        try:
            self.queue.put(None, timeout=3)
        except queue.Full:
            self.info['error'] = 'Fiducial writer did not drain its queue before stop.'
        self.thread.join(timeout=15)
        if self.thread.is_alive():
            self.info['error'] = 'Fiducial writer did not finish before stop timeout.'
        self.info['finalized'] = not self.info['error'] and not self.thread.is_alive()
        self.info['complete'] = (self.info['finalized'] and self.info['frames'] > 0
                                 and not self.info['queue_drops'] and not self.info['measurement_ring_drops']
                                 and not self.info['incomplete_sdk_frames'])
        json.dump(self.info, self.meta_stream, indent=2)
        self.meta_stream.flush()
        os.fsync(self.meta_stream.fileno())
        self.meta_stream.close()
        return self.info


class ObservedAdapter:
    def __init__(self, adapter, worker):
        self.adapter, self.worker = adapter, worker
        self.fast = None

    def __getattr__(self, name):
        return getattr(self.adapter, name)

    def connect(self, *args, **kwargs):
        self._close_fast()
        result = self.adapter.connect(*args, **kwargs)
        name = getattr(self.adapter, '_mapping_name', None)
        process = getattr(self.adapter, '_process', None)
        if name and process is not None:
            from .measurement_channel import ExistingMapping, Reader, SUFFIX
            mapping = None
            try:
                mapping = ExistingMapping(name+SUFFIX)
                self.fast = Reader(mapping, process.pid)
            except (OSError, ValueError):
                if mapping is not None:
                    mapping.close()
        return result

    def _close_fast(self):
        if self.fast is not None:
            self.fast.close()
            self.fast = None

    def disconnect(self):
        self._close_fast()
        return self.adapter.disconnect()

    def drain_recording_frames(self):
        if self.fast is None:
            drain = getattr(self.adapter, 'drain_recording_frames', None)
            return drain() if drain is not None else ()
        from .measurement_channel import packet
        return tuple(packet(slot, self.adapter._marker, self.fast.dropped) for slot in self.fast.drain())

    def discard_recording_frames(self):
        if self.fast is not None:
            self.fast.discard()
        discard = getattr(self.adapter, 'discard_recording_frames', None)
        if discard is not None:
            discard()

    @property
    def has_separate_pose_stream(self):
        return self.fast is not None or getattr(self.adapter, 'has_separate_pose_stream', False)

    def measurement_info(self):
        if self.fast is None:
            return {}
        return dict(source='joint_measurement_ring_v1', sdk_correspondence_exported=True,
            assignment_method='sdk_fiducial_corresp', ring_drops_at_start=self.fast.dropped,
            sampling='Exact accepted pose-recording frames; no image-rate limit or interpolation.',
            timestamp_policy='Identical original device/host timestamps and frame counters as the pose frame.')

    def read_frame(self, *args, **kwargs):
        frame = self.adapter.read_frame(*args, **kwargs)
        recorder = getattr(self.worker, '_fiducial_recorder', None)
        session = getattr(recorder, '_fiducial_session', None)
        if session is not None:
            session.offer(frame)
        return frame
