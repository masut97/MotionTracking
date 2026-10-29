import csv
import json
import threading
from types import SimpleNamespace as NS

import numpy as np
import pytest

from atracsys_sync.fiducial_export import FiducialSession, observation_rows


def marker(geometry=351, tracking=7, shift=(10., 20., 1000.), mask=3):
    return NS(geometry_id=geometry, tracking_id=tracking, presence_mask=mask,
              rotation_matrix=np.eye(3), position_mm=shift)


def fid(index, position, status='OK'):
    return NS(index=index, position_mm=position, status=status, left_index=index,
              right_index=index, epipolar_error_px=.03, triangulation_error_mm=.04, probability=.99)


def frame(fids=(), markers=(), counter=1, stamp=123456789):
    return NS(width=2048, frame_counter=counter, device_timestamp_us=stamp,
              computer_monotonic_s=100.1256789, fiducials=fids, markers=markers)


DEFS = {351: np.array([[0., 0., 0.], [20., 0., 0.]])}


def test_export_uses_measured_values_not_rigid_predicted_positions():
    measured = (10.2, 20.1, 1000.03)
    rows = observation_rows(frame([fid(13, measured)], [marker()]), DEFS)
    matched = next(r for r in rows if r.get('visible'))
    assert matched['geometry_id'] == 351 and matched['geometry_fiducial_number'] == 1
    assert matched['frame_fiducial_index'] == 13
    assert tuple(matched[k] for k in ('x_mm', 'y_mm', 'z_mm')) == measured
    missing = next(r for r in rows if r.get('geometry_fiducial_number') == 2)
    assert missing['visible'] == 0 and 'x_mm' not in missing


def test_cannot_force_ambiguous_or_distant_observations_into_marker():
    for fids, markers in [([fid(1, (10, 20, 1000))], [marker(), marker(tracking=8)]),
                          ([fid(1, (10, 20, 1000)), fid(2, (10.1, 20, 1000))], [marker()])]:
        rows = observation_rows(frame(fids, markers), DEFS)
        assert not any(r.get('assignment_status') == 'unique_geometric_match' for r in rows)
    row = observation_rows(frame([fid(1, (100, 200, 300))], [marker()]), DEFS)[0]
    assert row['assignment_status'] == 'unassigned' and 'geometry_id' not in row


def test_presence_mask_and_invalid_status_never_create_positions():
    rows = observation_rows(frame([fid(1, (10, 20, 1000))], [marker(mask=2)]), DEFS)
    assert rows[0]['assignment_status'] == 'unassigned'
    assert next(r for r in rows if r.get('geometry_fiducial_number') == 1)['assignment_status'] == 'not_in_marker_presence_mask'
    rows = observation_rows(frame([fid(1, (10, 20, 1000), '0x00000001')], [marker()]), DEFS)
    assert rows[0]['visible'] == 0 and 'x_mm' not in rows[0]


def test_rigid_transform_only_names_local_ball_and_number_is_stable():
    m = marker()
    m.rotation_matrix = np.array([[0., -1., 0.], [1., 0., 0.], [0., 0., 1.]])
    rows = observation_rows(frame([fid(52, (10.05, 40., 1000.))], [m]), DEFS)
    assert rows[0]['geometry_fiducial_number'] == 2
    assert rows[0]['frame_fiducial_index'] == 52


def test_session_finalizes_without_next_recording_and_preserves_exact_clock(tmp_path):
    primary = tmp_path / 'test.csv'
    primary.write_text('existing raw data')
    s = FiducialSession(primary, 'session-1', 100., DEFS, {})
    f = frame([fid(13, (10.2, 20.1, 1000.03))], [marker()])
    s.offer(f)
    s.offer(f)  # Repeated image must not be stamped as a new measurement.
    pose = frame(counter=2)
    pose.width = 0
    s.offer(pose)
    stale = frame(counter=3)
    stale.computer_monotonic_s = 99.
    s.offer(stale)
    result = s.close()
    assert result['complete'] and result['frames'] == 1
    assert json.loads(s.manifest.read_text())['finalized']
    rows = list(csv.DictReader(s.path.open(encoding='utf-8-sig')))
    assert int(rows[0]['device_timestamp_us']) == f.device_timestamp_us
    assert float(rows[0]['computer_monotonic_s']) == f.computer_monotonic_s
    assert primary.read_text() == 'existing raw data'
    before = s.path.read_bytes()
    s.close()
    s.offer(frame(counter=4))
    assert s.path.read_bytes() == before
    with pytest.raises(FileExistsError):
        FiducialSession(primary, 'session-2', 100., DEFS, {})


def test_writer_failure_is_explicit_and_main_data_untouched(tmp_path):
    primary = tmp_path / 'test.csv'
    primary.write_text('keep')
    s = FiducialSession(primary, 'session', 100., DEFS, {})
    class Failing:
        def writerow(self, row):
            raise OSError('injected disk failure')
    s.writer = Failing()
    s.offer(frame())
    result = s.close()
    assert not result['complete'] and not result['finalized']
    assert 'injected disk failure' in result['error']
    assert primary.read_text() == 'keep'


def test_full_queue_never_blocks_capture_and_reports_loss(tmp_path):
    s = FiducialSession(tmp_path/'queue.csv', 'session', 100., DEFS, {}, capacity=1)
    entered, release = threading.Event(), threading.Event()
    original = s.writer
    class Paused:
        def writerow(self, row):
            entered.set()
            assert release.wait(5)
            original.writerow(row)
    s.writer = Paused()
    s.offer(frame(counter=1))
    assert entered.wait(2)
    s.offer(frame(counter=2))
    s.offer(frame(counter=3))
    release.set()
    result = s.close()
    assert result['queue_drops'] == 1
    assert result['finalized'] and not result['complete']
    assert result['frames'] == 2
