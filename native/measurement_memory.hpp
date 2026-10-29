#pragma once
#include <Windows.h>
#include <cstring>
#include <stdexcept>
#include <string>
#include "measurement_protocol.hpp"

namespace atrbridge {
class MeasurementMemory {
public:
    explicit MeasurementMemory(const std::wstring& name) {
        mapping_ = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0,
                                     static_cast<DWORD>(kMeasurementBytes), name.c_str());
        if (!mapping_) throw std::runtime_error("Cannot create measurement mapping");
        data_ = static_cast<unsigned char*>(MapViewOfFile(mapping_, FILE_MAP_ALL_ACCESS, 0, 0, kMeasurementBytes));
        if (!data_) { CloseHandle(mapping_); throw std::runtime_error("Cannot map measurement channel"); }
        std::memset(data_, 0, kMeasurementBytes);
        auto* h = header();
        h->version = kMeasurementVersion;
        h->header_bytes = kMeasurementHeaderBytes;
        h->slot_bytes = sizeof(MeasurementSlot);
        h->capacity = kMeasurementCapacity;
        h->process_id = GetCurrentProcessId();
        h->state = 1;
        h->total_bytes = kMeasurementBytes;
        MemoryBarrier();
        std::memcpy(h->magic, "ATRMEAS1", 8);
    }
    ~MeasurementMemory() { UnmapViewOfFile(data_); CloseHandle(mapping_); }
    MeasurementMemory(const MeasurementMemory&) = delete;
    MeasurementMemory& operator=(const MeasurementMemory&) = delete;
    MeasurementHeader* header() { return reinterpret_cast<MeasurementHeader*>(data_); }
    MeasurementSlot* begin(std::int64_t sequence) {
        auto* slot = reinterpret_cast<MeasurementSlot*>(data_ + measurement_offset(sequence));
        InterlockedExchange64(reinterpret_cast<volatile LONG64*>(&slot->sequence), 0);
        return slot;
    }
    void commit(MeasurementSlot* slot, std::int64_t sequence) {
        MemoryBarrier();
        InterlockedExchange64(reinterpret_cast<volatile LONG64*>(&slot->sequence), sequence);
        header()->heartbeat_ns = slot->computer_monotonic_ns;
        InterlockedExchange64(reinterpret_cast<volatile LONG64*>(&header()->write_sequence), sequence);
    }
private:
    HANDLE mapping_ = nullptr;
    unsigned char* data_ = nullptr;
};
} // namespace atrbridge
