#include "parent_watch.hpp"
#include <string>

std::wstring executable() {
    wchar_t path[32768]{};
    GetModuleFileNameW(nullptr, path, 32768);
    return std::wstring(L"\"") + path + L"\"";
}
bool launch(std::wstring command, DWORD flags, PROCESS_INFORMATION& process) {
    STARTUPINFOW start{}; start.cb = sizeof(start);
    return CreateProcessW(nullptr, command.data(), nullptr, nullptr, FALSE,
                          flags | CREATE_NO_WINDOW, nullptr, nullptr, &start, &process) != FALSE;
}
int wmain(int argc, wchar_t** argv) {
    if (argc == 4 && std::wstring(argv[1]) == L"--child") {
        atrbridge::watch_parent(static_cast<DWORD>(std::stoul(argv[2])));
        HANDLE ready = OpenEventW(EVENT_MODIFY_STATE, FALSE, argv[3]);
        if (!ready) return 10;
        SetEvent(ready); CloseHandle(ready);
        Sleep(INFINITE); // Models an indefinitely blocked vendor call, without loading an SDK.
    }
    if (argc == 3 && std::wstring(argv[1]) == L"--owner") {
        atrbridge::watch_parent(atrbridge::parent_process_id());
        const std::wstring prefix = argv[2];
        HANDLE mapping = OpenFileMappingW(FILE_MAP_WRITE, FALSE, (prefix+L"_pid").c_str());
        auto* pid = static_cast<DWORD*>(MapViewOfFile(mapping, FILE_MAP_WRITE, 0, 0, sizeof(DWORD)));
        PROCESS_INFORMATION child{};
        if (!pid || !launch(executable()+L" --child "+std::to_wstring(GetCurrentProcessId())+
                            L" "+prefix+L"_ready", CREATE_SUSPENDED, child)) return 11;
        *pid = child.dwProcessId;
        MemoryBarrier(); ResumeThread(child.hThread);
        CloseHandle(child.hThread); CloseHandle(child.hProcess);
        UnmapViewOfFile(pid); CloseHandle(mapping);
        Sleep(INFINITE);
    }
    const auto prefix = L"Local\\AtrOwnerTest_"+std::to_wstring(GetCurrentProcessId())+L"_"+std::to_wstring(GetTickCount64());
    HANDLE ready = CreateEventW(nullptr, TRUE, FALSE, (prefix+L"_ready").c_str());
    HANDLE mapping = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0, sizeof(DWORD), (prefix+L"_pid").c_str());
    auto* pid = static_cast<DWORD*>(MapViewOfFile(mapping, FILE_MAP_READ, 0, 0, sizeof(DWORD)));
    PROCESS_INFORMATION owner{};
    int result = 1;
    HANDLE child = nullptr;
    if (ready && pid && launch(executable()+L" --owner "+prefix, 0, owner)) {
        if (WaitForSingleObject(ready, 5000) == WAIT_OBJECT_0) {
            child = OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE, FALSE, *pid);
            if (child && WaitForSingleObject(child, 0) == WAIT_TIMEOUT) {
                TerminateProcess(owner.hProcess, 55);
                if (WaitForSingleObject(child, 3000) == WAIT_OBJECT_0) {
                    DWORD code = 0; GetExitCodeProcess(child, &code);
                    result = code == 73 ? 0 : 2;
                }
            }
        }
    }
    if (child) { if (WaitForSingleObject(child, 0) == WAIT_TIMEOUT) TerminateProcess(child, 90); CloseHandle(child); }
    if (owner.hProcess) { TerminateProcess(owner.hProcess, 90); CloseHandle(owner.hProcess); CloseHandle(owner.hThread); }
    if (pid) UnmapViewOfFile(pid);
    if (mapping) CloseHandle(mapping);
    if (ready) CloseHandle(ready);
    return result;
}
