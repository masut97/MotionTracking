"""Read-only joint pose/fiducial ring. No SDK/device calls occur in this module."""
import ctypes as C
import os
from types import SimpleNamespace

CAPACITY = 1024
HEADER_BYTES = 4096
MAGIC = b'ATRMEAS1'
SUFFIX = '_measurements_v1'


class Packed(C.LittleEndianStructure):
    _pack_ = 1


class Marker(Packed):
    _fields_ = [('geometry_id', C.c_uint32), ('tracking_id', C.c_uint32),
                ('status', C.c_uint32), ('reserved0', C.c_uint32), ('presence_mask', C.c_uint64),
                ('rotation', C.c_float*9), ('translation_mm', C.c_float*3), ('registration_error_mm', C.c_float)]


class Fiducial(Packed):
    _fields_ = [('index', C.c_uint32), ('left_index', C.c_int32), ('right_index', C.c_int32),
                ('status', C.c_uint32)] + [(k, C.c_float) for k in
                ('x_mm','y_mm','z_mm','left_x','left_y','right_x','right_y',
                 'epipolar_error','triangulation_error','probability')]


class AssignedFiducial(Packed):
    _fields_ = [('observation', Fiducial), ('geometry_id', C.c_int32),
                ('tracking_id', C.c_uint32), ('geometry_index', C.c_int32), ('assignment_count', C.c_uint32)]


class Slot(Packed):
    _fields_ = [('sequence', C.c_int64), ('frame_counter', C.c_uint32), ('marker_count', C.c_uint32),
                ('device_timestamp_us', C.c_uint64), ('computer_monotonic_ns', C.c_uint64),
                ('fiducial_count', C.c_uint32), ('flags', C.c_uint32),
                ('markers', Marker*128), ('fiducials', AssignedFiducial*256)]


class Header(Packed):
    _fields_ = [('magic', C.c_char*8), ('version', C.c_uint32), ('header_bytes', C.c_uint32),
                ('slot_bytes', C.c_uint32), ('capacity', C.c_uint32),
                ('process_id', C.c_uint32), ('state', C.c_uint32), ('write_sequence', C.c_int64),
                ('heartbeat_ns', C.c_uint64), ('connection_epoch', C.c_uint64), ('total_bytes', C.c_uint64)]


TOTAL_BYTES = HEADER_BYTES + CAPACITY*C.sizeof(Slot)


class ExistingMapping:
    """Open an existing mapping only; never create a phantom empty channel."""
    def __init__(self, name):
        if os.name != 'nt':
            raise OSError('Windows shared memory required')
        k = self.api = C.WinDLL('kernel32', use_last_error=True)
        k.OpenFileMappingW.argtypes = [C.c_uint32, C.c_int, C.c_wchar_p]
        k.OpenFileMappingW.restype = C.c_void_p
        k.MapViewOfFile.argtypes = [C.c_void_p, C.c_uint32, C.c_uint32, C.c_uint32, C.c_size_t]
        k.MapViewOfFile.restype = C.c_void_p
        k.UnmapViewOfFile.argtypes = [C.c_void_p]
        k.CloseHandle.argtypes = [C.c_void_p]
        self.pointer, self.handle = None, k.OpenFileMappingW(4, False, name)
        if not self.handle:
            raise C.WinError(C.get_last_error())
        self.pointer = k.MapViewOfFile(self.handle, 4, 0, 0, TOTAL_BYTES)
        if not self.pointer:
            self.close()
            raise OSError('Cannot map the joint measurement channel')

    def read(self, offset, size):
        if not self.pointer or offset < 0 or size < 0 or offset+size > TOTAL_BYTES:
            raise OSError('Invalid measurement mapping read')
        return C.string_at(self.pointer+offset, size)

    def close(self):
        if self.pointer:
            self.api.UnmapViewOfFile(self.pointer)
            self.pointer = None
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


class Reader:
    def __init__(self, mapping, process_id):
        self.mapping, self.process_id = mapping, process_id
        self.dropped = 0
        header = self.header()
        self.sequence, self.epoch = int(header.write_sequence), int(header.connection_epoch)

    def header(self):
        header = Header.from_buffer_copy(self.mapping.read(0, C.sizeof(Header)))
        if (header.magic != MAGIC or header.version != 1 or header.header_bytes != HEADER_BYTES
                or header.slot_bytes != C.sizeof(Slot) or header.capacity != CAPACITY
                or header.total_bytes != TOTAL_BYTES or header.process_id != self.process_id):
            raise ValueError('Incompatible joint measurement channel; no data consumed')
        return header

    def drain(self):
        header = self.header()
        if not self.epoch and not self.sequence and header.state == 2:
            self.epoch = int(header.connection_epoch)
        if header.connection_epoch != self.epoch or header.write_sequence < self.sequence:
            raise RuntimeError('SDK measurement connection changed; reconnect before recording again')
        if header.state != 2:
            return []
        newest = int(header.write_sequence)
        first = max(self.sequence+1, newest-CAPACITY+1, 1)
        self.dropped += max(0, first-self.sequence-1)
        result = []
        for sequence in range(first, newest+1):
            offset = HEADER_BYTES + ((sequence-1) % CAPACITY)*C.sizeof(Slot)
            raw = self.mapping.read(offset, C.sizeof(Slot))
            slot = Slot.from_buffer_copy(raw)
            # Both the copied sequence and the post-copy sequence must match.
            after = int.from_bytes(self.mapping.read(offset, 8), 'little', signed=True)
            if slot.sequence != sequence or after != sequence:
                self.dropped += 1
                continue
            if slot.marker_count > 128 or slot.fiducial_count > 256:
                raise ValueError('Measurement channel contains invalid counts')
            result.append(slot)
        self.sequence = newest
        return result

    def close(self):
        self.mapping.close()

    def discard(self):
        header = self.header()
        self.sequence, self.epoch = int(header.write_sequence), int(header.connection_epoch)


def packet(slot, convert_marker, dropped):
    """Owned Python observations; never retain a view into the producer's ring."""
    from atracsys_tracker.models import FramePacket, FiducialObservation
    observations, assignments = [], {}
    for i in range(slot.fiducial_count):
        assigned = slot.fiducials[i]
        f = assigned.observation
        observations.append(FiducialObservation(
            index=int(f.index), position_mm=(float(f.x_mm),float(f.y_mm),float(f.z_mm)),
            left_index=int(f.left_index), right_index=int(f.right_index),
            left_px=(float(f.left_x),float(f.left_y)) if f.left_index >= 0 else None,
            right_px=(float(f.right_x),float(f.right_y)) if f.right_index >= 0 else None,
            epipolar_error_px=float(f.epipolar_error), triangulation_error_mm=float(f.triangulation_error),
            probability=float(f.probability), status='OK' if not f.status else f'0x{f.status:08x}'))
        assignments[int(f.index)] = (int(assigned.geometry_id), int(assigned.tracking_id),
                                     int(assigned.geometry_index), int(assigned.assignment_count))
    return FramePacket(frame_counter=int(slot.frame_counter), device_timestamp_us=int(slot.device_timestamp_us),
        computer_monotonic_s=float(slot.computer_monotonic_ns)/1e9, width=0, height=0,
        pixel_format='MEASUREMENT_V1', image_stride_bytes=0, left_image=None, right_image=None,
        markers=tuple(convert_marker(slot.markers[i]) for i in range(slot.marker_count)),
        fiducials=tuple(observations), native_owner=SimpleNamespace(
            sdk_correspondences=assignments, measurement_flags=int(slot.flags), ring_drops=dropped))
