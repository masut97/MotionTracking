#pragma once
#include <Windows.h>
#include <TlHelp32.h>
#include <thread>
#include <stdexcept>

namespace atrbridge {
inline DWORD parent_process_id() {
    const HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snapshot == INVALID_HANDLE_VALUE) throw std::runtime_error("Cannot inspect bridge owner");
    PROCESSENTRY32W entry{};
    entry.dwSize = sizeof(entry);
    DWORD parent = 0;
    if (Process32FirstW(snapshot, &entry)) {
        do {
            if (entry.th32ProcessID == GetCurrentProcessId()) {
                parent = entry.th32ParentProcessID;
                break;
            }
        } while (Process32NextW(snapshot, &entry));
    }
    CloseHandle(snapshot);
    if (!parent) throw std::runtime_error("Bridge owner was not found");
    return parent;
}
inline void watch_parent(DWORD parent, UINT exit_code = 73) {
    // A process handle, unlike a recurring PID lookup, cannot attach to a reused PID.
    HANDLE owner = OpenProcess(SYNCHRONIZE, FALSE, parent);
    if (!owner) throw std::runtime_error("Cannot watch bridge owner");
    std::thread([owner, exit_code] {
        const DWORD result = WaitForSingleObject(owner, INFINITE);
        CloseHandle(owner);
        if (result == WAIT_OBJECT_0) TerminateProcess(GetCurrentProcess(), exit_code);
    }).detach();
}
} // namespace atrbridge
