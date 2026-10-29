"""Windows process-lifetime GUI lock shared by all installation directories."""
import ctypes as C
from ctypes import wintypes as W
import os
from pathlib import PureWindowsPath

MUTEX_NAME = r'Global\MotionTracking.GUI.43d9be77-15a3-44f5-9369-6057f9acda9a'
AUXILIARY = {'--sync-worker', '--launch-after-exit', '--sync-self-test',
             '--sync-camera-self-test', '--run-analysis'}


def is_auxiliary(argv):
    return len(argv) >= 3 and argv[1] in AUXILIARY


class InstanceGuard:
    def __init__(self, name=MUTEX_NAME):
        self.name, self.handle, self.owned = name, None, False
        self.api = C.WinDLL('kernel32', use_last_error=True)
        self.api.CreateMutexW.argtypes = [C.c_void_p, W.BOOL, W.LPCWSTR]
        self.api.CreateMutexW.restype = W.HANDLE
        self.api.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
        self.api.WaitForSingleObject.restype = W.DWORD
        self.api.ReleaseMutex.argtypes = [W.HANDLE]
        self.api.CloseHandle.argtypes = [W.HANDLE]

    def acquire(self):
        if self.handle is not None:
            raise RuntimeError('Instance guard already used')
        self.handle = self.api.CreateMutexW(None, False, self.name)
        if not self.handle:
            raise C.WinError(C.get_last_error())
        result = self.api.WaitForSingleObject(self.handle, 0)
        if result in (0, 0x80):  # Acquired or abandoned after a crashed owner.
            self.owned = True
            return True
        self.close()
        if result != 258:  # WAIT_TIMEOUT means another GUI owns the lock.
            raise OSError('Cannot check the Motion Tracking instance lock')
        return False

    def close(self):
        if self.handle is not None:
            if self.owned:
                self.api.ReleaseMutex(self.handle)
            self.api.CloseHandle(self.handle)
            self.handle, self.owned = None, False


def existing_window():
    """Also recognise a visible pre-lock installation without touching devices."""
    user = C.WinDLL('user32', use_last_error=True)
    kernel = C.WinDLL('kernel32', use_last_error=True)
    callback_type = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
    user.EnumWindows.argtypes = [callback_type, W.LPARAM]
    user.IsWindowVisible.argtypes = [W.HWND]
    user.GetWindowTextLengthW.argtypes = [W.HWND]
    user.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, C.c_int]
    user.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
    kernel.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    kernel.OpenProcess.restype = W.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]
    kernel.CloseHandle.argtypes = [W.HANDLE]
    found = []

    @callback_type
    def visit(hwnd, _):
        if not user.IsWindowVisible(hwnd):
            return True
        title = C.create_unicode_buffer(user.GetWindowTextLengthW(hwnd)+1)
        user.GetWindowTextW(hwnd, title, len(title))
        if not title.value.startswith(('Motion Tracking', 'Atracsys Marker Tracking')):
            return True
        pid = W.DWORD()
        user.GetWindowThreadProcessId(hwnd, C.byref(pid))
        if pid.value == os.getpid():
            return True
        process = kernel.OpenProcess(0x1000, False, pid.value)
        if process:
            try:
                name = C.create_unicode_buffer(32768); size = W.DWORD(len(name))
                if (kernel.QueryFullProcessImageNameW(process, 0, name, C.byref(size)) and
                    PureWindowsPath(name.value).name.casefold() in
                    {'atracsyslivetracker_messskripte.exe', 'motiontracking.exe'}):
                    found.append(hwnd)
                    return False
            finally:
                kernel.CloseHandle(process)
        return True

    user.EnumWindows(visit, 0)
    return found[0] if found else None


def activate(hwnd):
    user = C.WinDLL('user32', use_last_error=True)
    user.IsIconic.argtypes = [W.HWND]
    user.ShowWindowAsync.argtypes = [W.HWND, C.c_int]
    user.SetForegroundWindow.argtypes = [W.HWND]
    user.FlashWindow.argtypes = [W.HWND, W.BOOL]
    if user.IsIconic(hwnd):
        user.ShowWindowAsync(hwnd, 9)
    if not user.SetForegroundWindow(hwnd):
        user.FlashWindow(hwnd, True)


def notify(text):
    user = C.WinDLL('user32', use_last_error=True)
    user.MessageBoxW.argtypes = [W.HWND, W.LPCWSTR, W.LPCWSTR, W.UINT]
    user.MessageBoxW(None, text, 'Motion Tracking', 0x40)


def run_gui(run):
    """Acquire before importing GUI/SDK code; fail closed on lock errors."""
    guard = InstanceGuard()
    try:
        try:
            acquired = guard.acquire()
        except OSError:
            notify('Motion Tracking konnte die Startsperre nicht prüfen.\n'
                   'Bitte prüfen, ob die Software bereits geöffnet ist. Es wird keine weitere Instanz gestartet.')
            return 1
        window = existing_window()
        if window:
            activate(window)
            return 0
        if not acquired:
            notify('Motion Tracking läuft bereits oder startet gerade.\n'
                   'Es wird keine zweite Instanz gestartet.')
            return 0
        return run()
    finally:
        guard.close()
