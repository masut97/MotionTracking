#include "measurement_protocol.hpp"
#include "frame_policy.hpp"
#include <cstring>
#include <iostream>
using namespace atrbridge;
static_assert(sizeof(MeasurementHeader) == 64);
static_assert(sizeof(MeasurementFiducial) == 72);
static_assert(sizeof(MeasurementSlot) == 28200);
static_assert(offsetof(MeasurementSlot, device_timestamp_us) == 16);
static_assert(offsetof(MeasurementSlot, computer_monotonic_ns) == 24);
static_assert(offsetof(MeasurementSlot, markers) == 40);
static_assert(offsetof(MeasurementSlot, fiducials) == 9768);
static_assert(offsetof(MeasurementHeader, write_sequence) == 32);
static_assert(kMeasurementBytes == 28880896);
int main() {
    MeasurementSlot source{};
    source.sequence = 1050; source.frame_counter = 335;
    source.device_timestamp_us = 1234567; source.computer_monotonic_ns = 987654321;
    source.marker_count = 1; source.fiducial_count = 1;
    source.markers[0].translation_mm[0] = 10.f;
    auto& fid = source.fiducials[0];
    fid.observation.x_mm = 10.125f;
    assign_fiducial(fid, 351, 42, 5);
    assign_fiducial(fid, 351, 42, 5);
    if (fid.assignment_count != 1 || fid.geometry_index != 5) return 1;
    assign_fiducial(fid, 351, 43, 5);
    if (fid.assignment_count != 2 || fid.observation.x_mm != 10.125f) return 2;
    unsigned char bytes[sizeof(source)];
    std::memcpy(bytes, &source, sizeof(source));
    MeasurementSlot decoded{};
    std::memcpy(&decoded, bytes, sizeof(decoded));
    if (decoded.device_timestamp_us != 1234567 || decoded.computer_monotonic_ns != 987654321 ||
        decoded.fiducials[0].observation.x_mm != 10.125f || decoded.sequence != 1050) return 3;
    if (measurement_offset(1) != 4096 || measurement_offset(1025) != 4096 ||
        measurement_offset(1024)+sizeof(MeasurementSlot) != kMeasurementBytes) return 4;
    const std::uint64_t period = 1000000000 / 335;
    std::uint64_t next = 0;
    int accepted = 0;
    for (std::uint64_t i=0; i<1005; ++i) {
        const auto jitter = i%2 ? 150000 : -150000;
        const auto time = static_cast<std::uint64_t>(1000000000 + i*period + jitter);
        accepted += sample_due(time, next, period);
    }
    if (accepted != 1005) return 5;
    next = 0; accepted = 0;
    for (std::uint64_t i=0; i<2010; ++i) accepted += sample_due(1000000000+i*(period/2), next, period);
    if (accepted < 1004 || accepted > 1006) return 6;
    next = 0;
    if (!sample_due(1000000000,next,period) || !sample_due(2000000000,next,period) ||
         sample_due(2000000000,next,period)) return 7;
    // Real SDK timestamps are quantised in microseconds. Host arrivals can
    // fluctuate by milliseconds; that must not remove distinct 335 Hz frames.
    next = 0; accepted = 0;
    for (std::uint64_t i=0; i<1005; ++i) {
        const auto device_ns = (1000000 + (i*1000000+167)/335)*1000;
        accepted += sample_due(device_ns,next,period);
    }
    if (accepted != 1005) return 8;
    std::cout << "Synthetic joint pose/fiducial protocol passed\n";
}
