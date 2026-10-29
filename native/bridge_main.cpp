#include <Windows.h>

#include <ftkInterface.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <string>
#include <thread>
#include <vector>

#include "shared_protocol.hpp"
#include "frame_policy.hpp"
#include "measurement_memory.hpp"
#include "parent_watch.hpp"

namespace {

using namespace atrbridge;
using Clock = std::chrono::steady_clock;

struct Arguments {
    std::wstring mapping_name = L"Local\\AtracsysLiveTrackerBridgeV2";
    std::wstring sdk_root;
    std::wstring log_path;
    std::vector<std::wstring> geometries;
    int image_fps = 30;
    int pose_fps = 100;
};

std::string utf8(const std::wstring& value) {
    if (value.empty()) return {};
    const int size = WideCharToMultiByte(CP_UTF8, 0, value.data(), static_cast<int>(value.size()),
                                         nullptr, 0, nullptr, nullptr);
    std::string result(static_cast<std::size_t>(size), '\0');
    WideCharToMultiByte(CP_UTF8, 0, value.data(), static_cast<int>(value.size()), result.data(), size,
                        nullptr, nullptr);
    return result;
}

Arguments parse_args(int argc, wchar_t** argv) {
    Arguments args;
    wchar_t executable[MAX_PATH]{};
    GetModuleFileNameW(nullptr, executable, MAX_PATH);
    args.sdk_root = (std::filesystem::path(executable).parent_path().parent_path().parent_path()
                     / L"sdk").wstring();
    for (int i = 1; i < argc; ++i) {
        const std::wstring item(argv[i]);
        if (item == L"--mapping" && i + 1 < argc) args.mapping_name = argv[++i];
        else if (item == L"--sdk-root" && i + 1 < argc) args.sdk_root = argv[++i];
        else if (item == L"--log" && i + 1 < argc) args.log_path = argv[++i];
        else if (item == L"--geometry" && i + 1 < argc) args.geometries.emplace_back(argv[++i]);
        else if (item == L"--image-fps" && i + 1 < argc)
            args.image_fps = (std::clamp)(std::stoi(argv[++i]), 1, 1000);
        else if (item == L"--pose-fps" && i + 1 < argc)
            args.pose_fps = (std::clamp)(std::stoi(argv[++i]), 1, 1000);
    }
    return args;
}

class Log {
public:
    explicit Log(const std::wstring& path) {
        if (!path.empty()) stream_.open(std::filesystem::path(path), std::ios::app);
    }
    void write(const std::string& text) {
        if (!stream_) return;
        stream_ << text << '\n';
        stream_.flush();
    }
private:
    std::ofstream stream_;
};

std::uint64_t monotonic_ns() {
    return static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now().time_since_epoch()).count());
}

void copy_text(char* target, std::size_t capacity, const std::string& text) {
    if (capacity == 0) return;
    const auto count = (std::min)(capacity - 1, text.size());
    std::memcpy(target, text.data(), count);
    target[count] = '\0';
}

std::string last_sdk_error(ftkLibrary lib) {
    char message[1024]{};
    if (lib && ftkGetLastErrorString(lib, sizeof(message), message) == ftkError::FTK_OK) {
        return message;
    }
    return "Unbekannter Atracsys-SDK-Fehler";
}

struct DeviceResult {
    std::uint64_t serial = 0;
    std::int32_t type = -1;
};

void _CDECL_ enumerate_callback(uint64 serial, void* user, ftkDeviceType type) {
    auto* result = static_cast<DeviceResult*>(user);
    result->serial = serial;
    result->type = static_cast<std::int32_t>(type);
}

class SharedMemory {
public:
    explicit SharedMemory(const std::wstring& name) {
        const DWORD high = static_cast<DWORD>((kTotalBytes >> 32) & 0xffffffffu);
        const DWORD low = static_cast<DWORD>(kTotalBytes & 0xffffffffu);
        mapping_ = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, high, low, name.c_str());
        if (!mapping_) throw std::runtime_error("CreateFileMappingW fehlgeschlagen");
        base_ = static_cast<std::uint8_t*>(MapViewOfFile(mapping_, FILE_MAP_ALL_ACCESS, 0, 0, kTotalBytes));
        if (!base_) throw std::runtime_error("MapViewOfFile fehlgeschlagen");
        std::memset(base_, 0, kTotalBytes);
        header_ = reinterpret_cast<SharedHeader*>(base_);
        std::memcpy(header_->magic, kMagic, sizeof(kMagic));
        header_->version = kProtocolVersion;
        header_->header_bytes = sizeof(SharedHeader);
        header_->total_bytes = kTotalBytes;
        header_->process_id = GetCurrentProcessId();
        header_->state = static_cast<std::uint32_t>(BridgeState::Starting);
        for (std::size_t i = 0; i < kImageSlotCount; ++i) {
            meta(i)->state = 0;
        }
    }
    ~SharedMemory() {
        if (base_) UnmapViewOfFile(base_);
        if (mapping_) CloseHandle(mapping_);
    }
    SharedHeader* header() const { return header_; }
    ImageSlotMeta* meta(std::size_t slot) const {
        return reinterpret_cast<ImageSlotMeta*>(base_ + image_meta_offset(slot));
    }
    std::uint8_t* left_image(std::size_t slot) const { return base_ + left_image_offset(slot); }
    std::uint8_t* right_image(std::size_t slot) const { return base_ + right_image_offset(slot); }
    PoseSlot* pose(std::size_t slot) const {
        return reinterpret_cast<PoseSlot*>(base_ + kPoseRingOffset + slot * sizeof(PoseSlot));
    }
private:
    HANDLE mapping_ = nullptr;
    std::uint8_t* base_ = nullptr;
    SharedHeader* header_ = nullptr;
};

MarkerRecord marker_record(const ftkMarker64Fiducials& source) {
    MarkerRecord result{};
    result.geometry_id = source.geometryId;
    result.tracking_id = source.id;
    result.status = static_cast<std::uint32_t>(source.status);
    result.presence_mask = source.geometryPresenceMask;
    for (int row = 0; row < 3; ++row) {
        for (int col = 0; col < 3; ++col) result.rotation[row * 3 + col] = source.rotation[row][col];
        result.translation_mm[row] = source.translationMM[row];
    }
    result.registration_error_mm = source.registrationErrorMM;
    return result;
}

MarkerRecord marker_record(const ftkMarker& source) {
    MarkerRecord result{};
    result.geometry_id = source.geometryId;
    result.tracking_id = source.id;
    result.status = static_cast<std::uint32_t>(source.status);
    result.presence_mask = source.geometryPresenceMask;
    for (int row = 0; row < 3; ++row) {
        for (int col = 0; col < 3; ++col) result.rotation[row * 3 + col] = source.rotation[row][col];
        result.translation_mm[row] = source.translationMM[row];
    }
    result.registration_error_mm = source.registrationErrorMM;
    return result;
}

std::vector<MarkerRecord> collect_markers(const ftkFrameQuery* frame) {
    std::vector<MarkerRecord> markers;
    markers.reserve(frame->markersCount + frame->sixtyFourFiducialsMarkersCount);
    if (frame->markersStat == ftkQueryStatus::QS_OK) {
        for (std::uint32_t i = 0; i < frame->markersCount && markers.size() < kMaxMarkers; ++i)
            markers.push_back(marker_record(frame->markers[i]));
    }
    if (frame->sixtyFourFiducialsMarkersStat == ftkQueryStatus::QS_OK) {
        for (std::uint32_t i = 0; i < frame->sixtyFourFiducialsMarkersCount && markers.size() < kMaxMarkers; ++i)
            markers.push_back(marker_record(frame->sixtyFourFiducialsMarkers[i]));
    }
    return markers;
}

void attach_geometry_labels(const ftkFrameQuery* frame, ImageSlotMeta* meta) {
    auto label_correspondence = [&](std::uint32_t geometry_id, std::uint64_t mask, const std::uint32_t* corr,
                                    std::size_t count) {
        for (std::size_t geometry_index = 0; geometry_index < count; ++geometry_index) {
            if ((mask & (std::uint64_t{1} << geometry_index)) == 0) continue;
            const std::uint32_t fid_index = corr[geometry_index];
            if (fid_index >= frame->threeDFiducialsCount) continue;
            const auto& fid = frame->threeDFiducials[fid_index];
            if (fid.leftIndex < meta->left_point_count) meta->left_points[fid.leftIndex].geometry_id = geometry_id;
            if (fid.rightIndex < meta->right_point_count) meta->right_points[fid.rightIndex].geometry_id = geometry_id;
        }
    };
    if (frame->markersStat == ftkQueryStatus::QS_OK) {
        for (std::uint32_t i = 0; i < frame->markersCount; ++i) {
            const auto& marker = frame->markers[i];
            label_correspondence(marker.geometryId, marker.geometryPresenceMask, marker.fiducialCorresp,
                                 FTK_MAX_FIDUCIALS);
        }
    }
    if (frame->sixtyFourFiducialsMarkersStat == ftkQueryStatus::QS_OK) {
        for (std::uint32_t i = 0; i < frame->sixtyFourFiducialsMarkersCount; ++i) {
            const auto& marker = frame->sixtyFourFiducialsMarkers[i];
            label_correspondence(marker.geometryId, marker.geometryPresenceMask, marker.fiducialCorresp,
                                 FTK_MAX_FIDUCIALS_64);
        }
    }
}

void publish_frame(SharedMemory& shared, const ftkFrameQuery* frame, std::int64_t sequence) {
    const auto* header = frame->imageHeader;
    const std::size_t slot_index = static_cast<std::size_t>((sequence - 1) % kImageSlotCount);
    auto* slot = shared.meta(slot_index);
    InterlockedExchange(reinterpret_cast<volatile LONG*>(&slot->state), 1);

    slot->sequence = 0;
    slot->frame_counter = header->counter;
    slot->width = header->width;
    slot->height = header->height;
    slot->pixel_format = static_cast<std::uint32_t>(header->format);
    slot->stride_bytes = header->imageStrideInBytes;
    slot->device_timestamp_us = header->timestampUS;
    slot->computer_monotonic_ns = monotonic_ns();
    // Both pixel buffers originate from this single ftkFrameQuery and are
    // therefore published as one inseparable stereo pair.
    slot->strobe_left = frame->imageLeftStat == ftkQueryStatus::QS_OK ? 1 : 0;
    slot->strobe_right = frame->imageRightStat == ftkQueryStatus::QS_OK ? 1 : 0;
    const auto bytes = static_cast<std::size_t>(std::abs(header->imageStrideInBytes)) * header->height;
    slot->left_bytes = static_cast<std::uint32_t>((std::min)(bytes, kMaxImageBytes));
    slot->right_bytes = slot->left_bytes;
    if (frame->imageLeftStat == ftkQueryStatus::QS_OK && frame->imageLeftPixels)
        std::memcpy(shared.left_image(slot_index), frame->imageLeftPixels, slot->left_bytes);
    else slot->left_bytes = 0;
    if (frame->imageRightStat == ftkQueryStatus::QS_OK && frame->imageRightPixels)
        std::memcpy(shared.right_image(slot_index), frame->imageRightPixels, slot->right_bytes);
    else slot->right_bytes = 0;

    slot->left_point_count = (std::min)(frame->rawDataLeftCount, static_cast<std::uint32_t>(kMaxRawPoints));
    slot->right_point_count = (std::min)(frame->rawDataRightCount, static_cast<std::uint32_t>(kMaxRawPoints));
    for (std::uint32_t i = 0; i < slot->left_point_count; ++i)
        slot->left_points[i] = PointRecord{i, -1, frame->rawDataLeft[i].centerXPixels, frame->rawDataLeft[i].centerYPixels};
    for (std::uint32_t i = 0; i < slot->right_point_count; ++i)
        slot->right_points[i] = PointRecord{i, -1, frame->rawDataRight[i].centerXPixels, frame->rawDataRight[i].centerYPixels};

    slot->fiducial_count = (std::min)(frame->threeDFiducialsCount, static_cast<std::uint32_t>(kMaxFiducials));
    for (std::uint32_t i = 0; i < slot->fiducial_count; ++i) {
        const auto& source = frame->threeDFiducials[i];
        auto& target = slot->fiducials[i];
        target = {};
        target.index = i;
        target.left_index = source.leftIndex < frame->rawDataLeftCount ? static_cast<std::int32_t>(source.leftIndex) : -1;
        target.right_index = source.rightIndex < frame->rawDataRightCount ? static_cast<std::int32_t>(source.rightIndex) : -1;
        target.status = static_cast<std::uint32_t>(source.status);
        target.x_mm = source.positionMM.x; target.y_mm = source.positionMM.y; target.z_mm = source.positionMM.z;
        if (target.left_index >= 0) {
            target.left_x = frame->rawDataLeft[source.leftIndex].centerXPixels;
            target.left_y = frame->rawDataLeft[source.leftIndex].centerYPixels;
        }
        if (target.right_index >= 0) {
            target.right_x = frame->rawDataRight[source.rightIndex].centerXPixels;
            target.right_y = frame->rawDataRight[source.rightIndex].centerYPixels;
        }
        target.epipolar_error = source.epipolarErrorPixels;
        target.triangulation_error = source.triangulationErrorMM;
        target.probability = source.probability;
    }

    const auto markers = collect_markers(frame);
    slot->marker_count = static_cast<std::uint32_t>(markers.size());
    std::copy(markers.begin(), markers.end(), slot->markers);
    attach_geometry_labels(frame, slot);

    MemoryBarrier();
    InterlockedExchange64(reinterpret_cast<volatile LONG64*>(&slot->sequence), sequence);
    InterlockedExchange(reinterpret_cast<volatile LONG*>(&slot->state), 2);
    InterlockedExchange(reinterpret_cast<volatile LONG*>(&shared.header()->active_slot), static_cast<LONG>(slot_index));

    InterlockedExchange64(reinterpret_cast<volatile LONG64*>(&shared.header()->published_sequence), sequence);
    shared.header()->heartbeat_ns = monotonic_ns();
}

const PoseSlot* publish_pose(SharedMemory& shared, const ftkFrameQuery* frame) {
    const auto* header = frame->imageHeader;
    const auto markers = collect_markers(frame);
    const std::int64_t pose_sequence = shared.header()->pose_write_sequence + 1;
    auto* pose = shared.pose(static_cast<std::size_t>((pose_sequence - 1) % kPoseRingCapacity));
    pose->sequence = 0;
    pose->frame_counter = header->counter;
    pose->marker_count = static_cast<std::uint32_t>(markers.size());
    pose->device_timestamp_us = header->timestampUS;
    pose->computer_monotonic_ns = monotonic_ns();
    std::copy(markers.begin(), markers.end(), pose->markers);
    MemoryBarrier();
    InterlockedExchange64(reinterpret_cast<volatile LONG64*>(&pose->sequence), pose_sequence);
    InterlockedExchange64(reinterpret_cast<volatile LONG64*>(&shared.header()->pose_write_sequence), pose_sequence);
    shared.header()->heartbeat_ns = monotonic_ns();
    return pose;
}

void publish_measurement(MeasurementMemory& memory, const ftkFrameQuery* frame, const PoseSlot& pose) {
    const auto sequence = memory.header()->write_sequence + 1;
    auto* slot = memory.begin(sequence);
    slot->frame_counter = pose.frame_counter;
    slot->device_timestamp_us = pose.device_timestamp_us;
    slot->computer_monotonic_ns = pose.computer_monotonic_ns;
    slot->marker_count = pose.marker_count;
    std::copy(pose.markers, pose.markers + pose.marker_count, slot->markers);
    slot->flags = 0;
    slot->fiducial_count = frame->threeDFiducialsStat == ftkQueryStatus::QS_OK
        ? (std::min)(frame->threeDFiducialsCount, static_cast<std::uint32_t>(kMaxFiducials)) : 0;
    if (frame->threeDFiducialsStat != ftkQueryStatus::QS_OK) slot->flags |= kMissingFiducials;
    if (frame->threeDFiducialsCount > kMaxFiducials ||
        frame->markersCount + frame->sixtyFourFiducialsMarkersCount > kMaxMarkers)
        slot->flags |= kMeasurementTruncated;
    for (std::uint32_t i = 0; i < slot->fiducial_count; ++i) {
        auto& f = slot->fiducials[i];
        f = {};
        f.geometry_id = -1;
        f.geometry_index = -1;
        const auto& source = frame->threeDFiducials[i];
        auto& o = f.observation;
        o.index = i;
        o.status = static_cast<std::uint32_t>(source.status);
        o.x_mm = source.positionMM.x; o.y_mm = source.positionMM.y; o.z_mm = source.positionMM.z;
        o.left_index = o.right_index = -1;
        if (frame->rawDataLeftStat == ftkQueryStatus::QS_OK && source.leftIndex < frame->rawDataLeftCount) {
            o.left_index = static_cast<std::int32_t>(source.leftIndex);
            o.left_x = frame->rawDataLeft[source.leftIndex].centerXPixels;
            o.left_y = frame->rawDataLeft[source.leftIndex].centerYPixels;
        }
        if (frame->rawDataRightStat == ftkQueryStatus::QS_OK && source.rightIndex < frame->rawDataRightCount) {
            o.right_index = static_cast<std::int32_t>(source.rightIndex);
            o.right_x = frame->rawDataRight[source.rightIndex].centerXPixels;
            o.right_y = frame->rawDataRight[source.rightIndex].centerYPixels;
        }
        o.epipolar_error = source.epipolarErrorPixels;
        o.triangulation_error = source.triangulationErrorMM;
        o.probability = source.probability;
    }
    auto associate = [&](auto& marker, std::size_t count) {
        for (std::size_t i = 0; i < count; ++i) {
            if (!(marker.geometryPresenceMask & (std::uint64_t{1} << i))) continue;
            const auto index = marker.fiducialCorresp[i];
            if (index < slot->fiducial_count)
                assign_fiducial(slot->fiducials[index], marker.geometryId, marker.id, static_cast<std::uint32_t>(i));
            else
                slot->flags |= kInvalidCorrespondence;
        }
    };
    if (frame->markersStat == ftkQueryStatus::QS_OK)
        for (std::uint32_t i = 0; i < frame->markersCount; ++i) associate(frame->markers[i], FTK_MAX_FIDUCIALS);
    if (frame->sixtyFourFiducialsMarkersStat == ftkQueryStatus::QS_OK)
        for (std::uint32_t i = 0; i < frame->sixtyFourFiducialsMarkersCount; ++i)
            associate(frame->sixtyFourFiducialsMarkers[i], FTK_MAX_FIDUCIALS_64);
    memory.commit(slot, sequence);
}

int run_bridge(const Arguments& args, SharedMemory& shared, MeasurementMemory& measurements, HANDLE stop_event, Log& log) {
    const auto sdk_bin = std::filesystem::path(args.sdk_root) / L"bin";
    SetDllDirectoryW(sdk_bin.c_str());
    std::int64_t image_sequence = 0;
    while (WaitForSingleObject(stop_event, 0) != WAIT_OBJECT_0) {
        shared.header()->reserved0 = 0;
        shared.header()->state = static_cast<std::uint32_t>(BridgeState::Reconnecting);
        measurements.header()->state = 3;
        ftkBuffer init_errors{};
        ftkLibrary library = ftkInitExt(nullptr, &init_errors);
        if (!library) {
            const std::string message = std::string("SDK initialisieren: ") + init_errors.data;
            copy_text(shared.header()->last_error, sizeof(shared.header()->last_error), message);
            log.write(message);
            std::this_thread::sleep_for(std::chrono::milliseconds(750));
            continue;
        }
        DeviceResult device;
        if (ftkEnumerateDevices(library, enumerate_callback, &device) != ftkError::FTK_OK || device.serial == 0) {
            const auto message = last_sdk_error(library);
            copy_text(shared.header()->last_error, sizeof(shared.header()->last_error), message);
            log.write("Kamera suchen: " + message);
            ftkClose(&library);
            std::this_thread::sleep_for(std::chrono::milliseconds(750));
            continue;
        }
        shared.header()->serial_number = device.serial;
        shared.header()->device_type = device.type;
        ftkBuffer version{};
        ftkVersion(&version);
        copy_text(shared.header()->sdk_version, sizeof(shared.header()->sdk_version), version.data);

        bool geometry_ok = true;
        for (const auto& geometry : args.geometries) {
            const auto path = utf8(geometry);
            if (ftkRegisterRigidBody(library, device.serial, path.size(), path.c_str()) != ftkError::FTK_OK) {
                geometry_ok = false;
                log.write("Geometrie nicht registriert: " + path + " - " + last_sdk_error(library));
            }
        }
        if (!geometry_ok) copy_text(shared.header()->last_error, sizeof(shared.header()->last_error),
                                    "Mindestens eine Geometrie konnte nicht registriert werden");

        ftkFrameQuery* image_frame = ftkCreateFrame();
        ftkFrameQuery* measurement_frame = ftkCreateFrame();
        if (!image_frame || !measurement_frame ||
            ftkSetFrameOptions(true, 0, 256, 256, 256, 128, image_frame) != ftkError::FTK_OK ||
            ftkSetFrameOptions(false, 0, 256, 256, 256, 128, measurement_frame) != ftkError::FTK_OK) {
            copy_text(shared.header()->last_error, sizeof(shared.header()->last_error), "SDK-Framepuffer konnte nicht angelegt werden");
            if (image_frame) ftkDeleteFrame(image_frame);
            if (measurement_frame) ftkDeleteFrame(measurement_frame);
            ftkClose(&library);
            std::this_thread::sleep_for(std::chrono::milliseconds(750));
            continue;
        }

        shared.header()->state = static_cast<std::uint32_t>(BridgeState::Running);
        ++measurements.header()->connection_epoch;
        measurements.header()->state = 2;
        if (geometry_ok) shared.header()->last_error[0] = '\0';
        log.write("Bridge verbunden");
        int consecutive_errors = 0;
        int no_frame_count = 0;
        std::uint64_t overflow_count = 0;
        auto overflow_log = Clock::now();
        bool first_call = true;
        bool have_pose_counter = false;
        std::uint32_t last_pose_counter = 0;
        const auto image_interval = std::chrono::nanoseconds(1'000'000'000 / args.image_fps);
        const auto pose_interval = static_cast<std::uint64_t>(1'000'000'000 / args.pose_fps);
        auto next_image = Clock::now();
        std::uint64_t next_pose = 0;
        while (WaitForSingleObject(stop_event, 0) != WAIT_OBJECT_0) {
            const bool image_query = Clock::now() >= next_image;
            auto* frame = image_query ? image_frame : measurement_frame;
            if (first_call) log.write("Warte auf ersten ftkGetLastFrame-Aufruf");
            const ftkError status = ftkGetLastFrame(library, device.serial, frame, 250);
            if (first_call) {
                log.write("Erster ftkGetLastFrame beendet: Status " +
                          std::to_string(static_cast<int>(status)) + ", Headerstatus " +
                          std::to_string(static_cast<int>(frame->imageHeaderStat)));
                first_call = false;
            }
            if (status == ftkError::FTK_OK && frame->imageHeaderStat == ftkQueryStatus::QS_OK) {
                if (overflow_count) {
                    log.write("Segmentation recovered without reconnect; SDK overflow responses: " + std::to_string(overflow_count));
                    overflow_count = 0;
                    shared.header()->last_error[0] = '\0';
                }
                shared.header()->reserved0 = 0;
                consecutive_errors = 0;
                no_frame_count = 0;
                const auto counter = frame->imageHeader->counter;
                auto now = Clock::now();
                const auto now_ns = static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(now.time_since_epoch()).count());
                if ((!have_pose_counter || counter != last_pose_counter) && sample_due(now_ns, next_pose, pose_interval)) {
                    const auto* pose = publish_pose(shared, frame);
                    publish_measurement(measurements, frame, *pose);
                }
                last_pose_counter = counter;
                have_pose_counter = true;
                now = Clock::now();
                if (image_query && now >= next_image) {
                    if (image_sequence == 0) log.write("Publiziere ersten Shared-Memory-Bildframe");
                    publish_frame(shared, frame, ++image_sequence);
                    if (image_sequence == 1) log.write("Erster Shared-Memory-Bildframe publiziert");
                    while (next_image <= now) next_image += image_interval;
                }
                continue;
            }
            static_assert(static_cast<int>(ftkError::FTK_ERR_SEG_OVERFLOW) == 13);
            if (recoverable_image_error(static_cast<int>(status))) {
                ++overflow_count;
                no_frame_count = 0;
                shared.header()->native_dropped_frames++;
                // Liveness is NOT a new measurement. Never publish an invalid
                // frame, reuse its counters, or stamp an old pose as current.
                shared.header()->heartbeat_ns = monotonic_ns();
                MemoryBarrier();
                shared.header()->reserved0 = kSegmentationOverflow;
                copy_text(shared.header()->last_error, sizeof(shared.header()->last_error),
                          "SEGMENTATION_OVERFLOW: invalid image discarded; waiting for valid camera frames");
                auto now = Clock::now();
                if (overflow_count == 1 || now - overflow_log >= std::chrono::seconds(1)) {
                    log.write("Segmentation overflow; connection retained; SDK overflow responses: " + std::to_string(overflow_count));
                    overflow_log = now;
                }
                if (WaitForSingleObject(stop_event, 20) == WAIT_OBJECT_0) break;
                continue;
            }
            shared.header()->reserved0 = 0;
            if (status == ftkError::FTK_WAR_NO_FRAME) {
                if (++no_frame_count % 40 == 0) log.write("Noch kein Kameraframe (FTK_WAR_NO_FRAME)");
                if (no_frame_count < 120) {
                    std::this_thread::sleep_for(std::chrono::milliseconds(10));
                    continue;
                }
                log.write("Neuverbinden nach ausbleibenden Kameraframes");
                break;
            }
            ++consecutive_errors;
            shared.header()->native_dropped_frames++;
            const auto message = last_sdk_error(library);
            copy_text(shared.header()->last_error, sizeof(shared.header()->last_error), message);
            log.write("ftkGetLastFrame Status " + std::to_string(static_cast<int>(status)) +
                      ", Headerstatus " + std::to_string(static_cast<int>(frame->imageHeaderStat)) +
                      ": " + message);
            if (consecutive_errors >= 10) {
                log.write("Neuverbinden nach SDK-Fehler: " + message);
                break;
            }
        }
        ftkDeleteFrame(image_frame);
        ftkDeleteFrame(measurement_frame);
        ftkClose(&library);
    }
    shared.header()->state = static_cast<std::uint32_t>(BridgeState::Stopping);
    measurements.header()->state = 5;
    return 0;
}

}  // namespace

int wmain(int argc, wchar_t** argv) {
    try {
        const auto args = parse_args(argc, argv);
        Log log(args.log_path);
        watch_parent(parent_process_id());
        SharedMemory shared(args.mapping_name);
        MeasurementMemory measurements(args.mapping_name + L"_measurements_v1");
        const std::wstring stop_name = args.mapping_name + L"_stop";
        HANDLE stop_event = CreateEventW(nullptr, TRUE, FALSE, stop_name.c_str());
        if (!stop_event) throw std::runtime_error("Stop-Event konnte nicht angelegt werden");
        const int result = run_bridge(args, shared, measurements, stop_event, log);
        CloseHandle(stop_event);
        return result;
    } catch (const std::exception&) {
        return 2;
    }
}
