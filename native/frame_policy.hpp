#pragma once
#include <cstdint>
namespace atrbridge {
// Opt-in diagnostic carried in the otherwise unused v2 header word.
constexpr std::uint32_t kSegmentationOverflow = 0x5347000d;
constexpr bool recoverable_image_error(int sdk_status) { return sdk_status == 13; }
inline bool sample_due(std::uint64_t now, std::uint64_t& next, std::uint64_t period) {
    if (!next) { next = now + period; return true; }
    const auto latest = now + period / 4;
    if (latest < next) return false;
    next += ((latest-next)/period + 1) * period;
    return true;
}
}
