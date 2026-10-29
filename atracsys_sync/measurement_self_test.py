"""335 Hz synthetic ring -> real frozen recorder -> matched pose/fiducial CSVs."""
import csv
import ctypes as C
import json
from pathlib import Path
import time
from types import SimpleNamespace as NS

from .measurement_channel import Header, Slot, Reader, HEADER_BYTES, CAPACITY, TOTAL_BYTES, packet


def run(output):
    from atracsys_tracker.recording import RecordingWorker
    from atracsys_tracker.adapters.native_bridge import NativeBridgeAdapter

    class SyntheticMemory:
        def __init__(self):
            self.data = bytearray(TOTAL_BYTES)
        def read(self, offset, length):
            return bytes(self.data[offset:offset+length])
        def close(self):
            pass

    output = Path(output)
    memory = SyntheticMemory()
    header = Header(magic=b'ATRMEAS1', version=1, header_bytes=HEADER_BYTES,
        slot_bytes=C.sizeof(Slot), capacity=CAPACITY, process_id=999999, state=2,
        connection_epoch=1, total_bytes=TOTAL_BYTES)
    memory.data[:64] = bytes(header)
    reader = Reader(memory,999999)
    info = dict(source='joint_measurement_ring_v1',sdk_correspondence_exported=True,
                assignment_method='sdk_fiducial_corresp', ring_drops_at_start=0)
    recorder = RecordingWorker(queue_size=3000)
    recorder._camera_source = NS(_camera_health=NS(snapshot=lambda:dict(stable=True,epoch=1)),
                                 _adapter=NS(measurement_info=lambda:info))
    recorder.start_session('SYNTHETIC_335Hz_joint',output,{},timestamp_prefix=False,sample_frequency_hz=335)
    marker_codec = NS(euler_sequence='xyz')
    convert = lambda m: NativeBridgeAdapter._marker(marker_codec,m)
    expected, produced = {}, 1005
    clock_start = round((recorder._started_monotonic+.1)*1e9)
    wall_start = time.perf_counter()
    try:
        for first in range(0,produced,17):
            last = min(first+17,produced)
            for index in range(first,last):
                slot = Slot(sequence=index+1,frame_counter=index,marker_count=3,fiducial_count=3,
                    device_timestamp_us=1000000+round(index*1e6/335),
                    computer_monotonic_ns=clock_start+round(index*1e9/335))
                for j, geometry in enumerate((299,302,351)):
                    marker=slot.markers[j]
                    marker.geometry_id=geometry; marker.tracking_id=index*10+j
                    marker.presence_mask=1
                    marker.rotation[0]=marker.rotation[4]=marker.rotation[8]=1.
                    marker.translation_mm[0]=float(j*100)
                    f=slot.fiducials[j]
                    f.observation.index=j
                    # Deliberately distinct from the pose origin: a fitted point
                    # substituted for a measured point must fail this test.
                    f.observation.x_mm=10.125+j
                    f.observation.y_mm=.25; f.observation.z_mm=700.5
                    f.observation.probability=1.
                    f.geometry_id=geometry; f.tracking_id=marker.tracking_id
                    f.geometry_index=0; f.assignment_count=1
                    expected[(index,geometry)]=(float(f.observation.x_mm),slot.device_timestamp_us,
                                                float(slot.computer_monotonic_ns)/1e9)
                offset=HEADER_BYTES+(index%CAPACITY)*C.sizeof(Slot)
                memory.data[offset:offset+C.sizeof(Slot)]=bytes(slot)
                header.write_sequence=index+1
                memory.data[:64]=bytes(header)
            for slot in reader.drain():
                assert recorder.enqueue(packet(slot,convert,reader.dropped))
            # Twenty image/display ticks per second, with 335 distinct original
            # measurement observations per second; no camera is instantiated.
            remaining=last/335-(time.perf_counter()-wall_start)
            if remaining>0:
                time.sleep(remaining)
    finally:
        recorder.stop()
    elapsed=time.perf_counter()-wall_start
    pose_path=output/'SYNTHETIC_335Hz_joint.csv'
    fid_path=pose_path.with_suffix('.fiducials.csv')
    with pose_path.open(encoding='utf-8-sig') as stream:
        poses=list(csv.DictReader(stream))
    with fid_path.open(encoding='utf-8-sig') as stream:
        fids=list(csv.DictReader(stream))
    manifest=json.loads(fid_path.with_suffix('.json').read_text())
    assert len(poses)==len(fids)==produced*3
    assert manifest['complete'] and manifest['frames']==produced and manifest['sdk_correspondence_exported']
    pose_keys={(int(r['frame_counter']),int(r['geometry_id'])):r for r in poses}
    for row in fids:
        key=(int(row['frame_counter']),int(row['geometry_id']))
        value,device,host=expected[key]
        assert float(row['x_mm'])==value and int(row['device_timestamp_us'])==device
        assert row['assignment_status']=='sdk_correspondence'
        assert int(row['geometry_fiducial_number'])==1
        assert float(row['computer_monotonic_s'])==host
        assert abs(float(pose_keys[key]['computer_monotonic_s'])-host)<5.1e-10
        assert row['device_timestamp_us']==pose_keys[key]['device_timestamp_us']
    return dict(passed=True,hardware_used=False,synthetic_frames=produced,
        simulated_measurement_hz=335,display_batch_hz=20,matched_pose_fiducial_rows=len(fids),
        wall_seconds=elapsed,ring_losses=reader.dropped)
