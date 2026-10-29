import ctypes as C
from types import SimpleNamespace as NS

import pytest

from atracsys_sync.measurement_channel import (
    Header, Marker, Fiducial, AssignedFiducial, Slot, Reader, HEADER_BYTES, CAPACITY, TOTAL_BYTES)
from atracsys_sync.fiducial_export import observation_rows, FiducialSession, ObservedAdapter


@pytest.mark.parametrize('frequency,kind,expected', [
    (335, 'MEASUREMENT_V1', 0), (100, 'MEASUREMENT_V1', 100),
    (20, 'MEASUREMENT_V1', 20), (335, 'GRAY8', 335), (0, 'MEASUREMENT_V1', 0)])
def test_maximum_joint_rate_only_bypasses_second_arrival_time_gate(frequency, kind, expected):
    from atracsys_sync.measurement_channel import enqueue_joint_frame
    worker = NS(_sample_frequency_hz=frequency)
    frame = NS(pixel_format=kind, computer_monotonic_s=123.456)
    def original(recorder, received):
        assert recorder._sample_frequency_hz == expected
        assert received is frame and received.computer_monotonic_s == 123.456
        return False  # Queue rejection must still propagate.
    assert enqueue_joint_frame(worker, frame, original) is False
    assert worker._sample_frequency_hz == frequency


def test_maximum_joint_rate_restores_request_after_enqueue_exception():
    from atracsys_sync.measurement_channel import enqueue_joint_frame
    worker = NS(_sample_frequency_hz=335)
    def fail(*args): raise OSError('queue failure')
    with pytest.raises(OSError, match='queue failure'):
        enqueue_joint_frame(worker, NS(pixel_format='MEASUREMENT_V1'), fail)
    assert worker._sample_frequency_hz == 335


class Memory:
    def __init__(self):
        self.data = bytearray(TOTAL_BYTES)
        self.header = Header(magic=b'ATRMEAS1', version=1, header_bytes=4096,
            slot_bytes=28200, capacity=1024, process_id=123, state=2, connection_epoch=1,
            total_bytes=28880896)
        self.sync()
    def sync(self):
        self.data[:64] = bytes(self.header)
    def read(self, offset, size):
        return bytes(self.data[offset:offset+size])
    def close(self):
        pass
    def write(self, number):
        slot = Slot(sequence=number, frame_counter=100+number,
                    device_timestamp_us=123000+number*2985, computer_monotonic_ns=100000000000+number*2985075,
                    marker_count=1, fiducial_count=1)
        slot.markers[0].geometry_id = 351
        slot.markers[0].tracking_id = 900+number
        slot.markers[0].presence_mask = 1 << 5
        slot.fiducials[0].observation.index = 0
        slot.fiducials[0].observation.x_mm = 12.375
        slot.fiducials[0].geometry_id = 351
        slot.fiducials[0].tracking_id = 900+number
        slot.fiducials[0].geometry_index = 5
        slot.fiducials[0].assignment_count = 1
        offset = HEADER_BYTES+((number-1)%CAPACITY)*C.sizeof(Slot)
        self.data[offset:offset+C.sizeof(Slot)] = bytes(slot)
        self.header.write_sequence = number
        self.sync()


def test_independent_wire_sizes_offsets_and_measurement_clock():
    assert (C.sizeof(Header),C.sizeof(Marker),C.sizeof(Fiducial),C.sizeof(AssignedFiducial),C.sizeof(Slot)) == (64,76,56,72,28200)
    assert Slot.device_timestamp_us.offset == 16
    assert Slot.computer_monotonic_ns.offset == 24
    assert Slot.fiducials.offset == 9768
    memory = Memory(); reader = Reader(memory,123)
    for n in range(1,336): memory.write(n)
    slots = reader.drain()
    assert len(slots) == 335 and reader.dropped == 0
    assert slots[0].device_timestamp_us == 125985
    assert slots[0].computer_monotonic_ns == 100002985075
    assert slots[0].fiducials[0].observation.x_mm == 12.375
    assert reader.drain() == []


@pytest.mark.parametrize('field,value', [('version',2),('slot_bytes',28199),('capacity',512),('process_id',124),('total_bytes',1)])
def test_incompatible_contract_is_rejected(field,value):
    memory = Memory(); setattr(memory.header,field,value); memory.sync()
    with pytest.raises(ValueError,match='Incompatible'): Reader(memory,123)


def test_ring_overrun_and_concurrent_replacement_never_reuse_old_samples():
    memory = Memory(); reader = Reader(memory,123)
    for n in range(1,1101): memory.write(n)
    slots = reader.drain()
    assert len(slots)==1024 and reader.dropped==76
    assert slots[0].sequence==77
    memory.write(1101)
    original = memory.read
    offset = HEADER_BYTES+((1101-1)%CAPACITY)*C.sizeof(Slot)
    def changing(at, size):
        if at == offset and size == 8:
            return (1102).to_bytes(8,'little',signed=True)
        return original(at,size)
    memory.read = changing
    assert reader.drain()==[] and reader.dropped==77


def test_discarded_idle_frames_are_not_recording_losses_and_reconnect_is_explicit():
    memory=Memory(); reader=Reader(memory,123)
    memory.write(2000); reader.discard()
    memory.write(2001)
    assert len(reader.drain())==1 and not reader.dropped
    memory.header.connection_epoch=2; memory.sync()
    with pytest.raises(RuntimeError,match='connection changed'): reader.drain()


def test_starting_mapping_can_transition_to_first_connection():
    memory=Memory(); memory.header.connection_epoch=0; memory.header.state=3; memory.sync()
    reader=Reader(memory,123)
    assert reader.drain()==[]
    memory.header.connection_epoch=1; memory.header.state=2; memory.write(1)
    assert len(reader.drain())==1


def measured(count=1, flags=0, dropped=0):
    m=NS(geometry_id=351, tracking_id=42, presence_mask=1<<5)
    f=NS(index=7,position_mm=(12.375,1.,2.),status='OK',left_index=0,right_index=0,
         epipolar_error_px=.02,triangulation_error_mm=.03,probability=.99)
    return NS(frame_counter=335, device_timestamp_us=1234567, computer_monotonic_s=100.1,
        width=0,pixel_format='MEASUREMENT_V1',markers=(m,),fiducials=(f,),
        native_owner=NS(sdk_correspondences={7:(351,42,5,count)},measurement_flags=flags,ring_drops=dropped))


def test_sdk_assignment_uses_measured_value_without_geometry_projection():
    # No rotation/translation/geometry coordinates are supplied: SDK assignment
    # and literal measured positions must suffice, without a nearest-point fit.
    row=observation_rows(measured(),{})[0]
    assert row['geometry_fiducial_number']==6 and row['geometry_id']==351
    assert row['assignment_status']=='sdk_correspondence' and row['x_mm']==12.375
    ambiguous=observation_rows(measured(count=2),{})[0]
    assert 'geometry_id' not in ambiguous and ambiguous['visible']==1


@pytest.mark.parametrize('flags,drops,complete',[(0,0,True),(1,0,False),(2,0,False),(0,3,False)])
def test_fast_export_finalization_and_explicit_incompleteness(tmp_path,flags,drops,complete):
    s=FiducialSession(tmp_path/'fast.csv','session',100.,{},dict(
        source='joint_measurement_ring_v1',sdk_correspondence_exported=True,ring_drops_at_start=0))
    s.offer(measured(flags=flags,dropped=drops))
    info=s.close()
    assert info['complete'] is complete
    assert info['assigned_observations']==1
    assert info['measurement_ring_drops']==drops


def test_adapter_opens_discards_and_closes_only_its_own_channel(monkeypatch):
    import atracsys_sync.measurement_channel as channel
    memory=Memory(); memory.closed=False
    memory.close=lambda:setattr(memory,'closed',True)
    names=[]
    def open_existing(name):
        names.append(name)
        return memory
    monkeypatch.setattr(channel,'ExistingMapping',open_existing)
    monkeypatch.setattr(channel,'packet',lambda slot, convert, dropped:(slot.frame_counter,dropped))
    underlying=NS(_mapping_name='Local\\owned',_process=NS(pid=123),_marker=lambda m:m,
                  connect=lambda:'connected',disconnect=lambda:None,
                  discard_recording_frames=lambda:None,has_separate_pose_stream=True)
    wrapper=ObservedAdapter(underlying,NS())
    assert wrapper.connect()=='connected'
    assert names==['Local\\owned_measurements_v1']
    memory.write(2000); wrapper.discard_recording_frames()
    memory.write(2001)
    assert wrapper.drain_recording_frames()==((2101,0),)
    assert wrapper.measurement_info()['sdk_correspondence_exported']
    wrapper.disconnect()
    assert memory.closed and wrapper.fast is None


def test_older_bridge_keeps_explicit_image_fallback(monkeypatch):
    import atracsys_sync.measurement_channel as channel
    def missing(name):raise FileNotFoundError('No new channel')
    monkeypatch.setattr(channel,'ExistingMapping',missing)
    underlying=NS(_mapping_name='Local\\old',_process=NS(pid=123),connect=lambda:None,
        drain_recording_frames=lambda:('legacy',),has_separate_pose_stream=True)
    wrapper=ObservedAdapter(underlying,NS())
    wrapper.connect()
    assert wrapper.measurement_info()=={}
    assert wrapper.drain_recording_frames()==('legacy',)
