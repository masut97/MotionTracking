#pragma once
#include "shared_protocol.hpp"

namespace atrbridge {
constexpr std::uint32_t kMeasurementVersion = 1;
constexpr std::size_t kMeasurementCapacity = 1024;
constexpr std::size_t kMeasurementHeaderBytes = 4096;
constexpr std::uint32_t kMissingFiducials = 1;
constexpr std::uint32_t kMeasurementTruncated = 2;
constexpr std::uint32_t kInvalidCorrespondence = 4;

#pragma pack(push, 1)
struct MeasurementHeader {
    char magic[8];  // ATRMEAS1
    std::uint32_t version;
    std::uint32_t header_bytes;
    std::uint32_t slot_bytes;
    std::uint32_t capacity;
    std::uint32_t process_id;
    std::uint32_t state;
    std::int64_t write_sequence;
    std::uint64_t heartbeat_ns;
    std::uint64_t connection_epoch;
    std::uint64_t total_bytes;
};
struct MeasurementFiducial {
    FiducialRecord observation;
    std::int32_t geometry_id;
    std::uint32_t tracking_id;
    std::int32_t geometry_index;  // Zero based; -1 without an SDK correspondence.
    std::uint32_t assignment_count;
};
struct MeasurementSlot {
    std::int64_t sequence;
    std::uint32_t frame_counter;
    std::uint32_t marker_count;
    std::uint64_t device_timestamp_us;
    std::uint64_t computer_monotonic_ns;
    std::uint32_t fiducial_count;
    std::uint32_t flags;
    MarkerRecord markers[kMaxMarkers];
    MeasurementFiducial fiducials[kMaxFiducials];
};
#pragma pack(pop)

constexpr std::size_t kMeasurementBytes = kMeasurementHeaderBytes + kMeasurementCapacity * sizeof(MeasurementSlot);
inline std::size_t measurement_offset(std::int64_t sequence) {
    return kMeasurementHeaderBytes + static_cast<std::size_t>((sequence-1) % kMeasurementCapacity) * sizeof(MeasurementSlot);
}
inline void assign_fiducial(MeasurementFiducial& f, std::uint32_t geometry,
                           std::uint32_t tracking, std::uint32_t index) {
    if (f.assignment_count && f.geometry_id == static_cast<std::int32_t>(geometry) &&
        f.tracking_id == tracking && f.geometry_index == static_cast<std::int32_t>(index)) return;
    if (!f.assignment_count) {
        f.geometry_id = static_cast<std::int32_t>(geometry);
        f.tracking_id = tracking;
        f.geometry_index = static_cast<std::int32_t>(index);
    }
    ++f.assignment_count;
}
} // namespace atrbridge
