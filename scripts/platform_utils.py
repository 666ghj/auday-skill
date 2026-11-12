"""Small OS adapters shared by the desktop client and local model runtimes."""

from contextlib import contextmanager
import ctypes
import errno
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def is_windows():
    return sys.platform == "win32"


def default_home():
    """Hidden per-user application directory: runtimes, caches, models and the
    saved data-root setting. Earlier versions also kept archives here."""
    if is_windows():
        local = os.environ.get("LOCALAPPDATA")
        return (Path(local) if local else Path.home() / "AppData" / "Local") / "AudayWorkflow"
    return Path.home() / "Library" / "Application Support" / "AudayWorkflow"


def visible_home():
    """Default archive location the user can find: a folder in their home
    directory, outside Documents/Desktop folders that iCloud or OneDrive often sync."""
    return Path.home() / "Auday"


def saved_data_root():
    """The archive location saved by `data-root --save`, if any (unvalidated)."""
    try:
        value = (default_home() / "data-root").read_text(encoding="utf-8-sig").split("\n", 1)[0].rstrip("\r")
    except OSError:
        return None
    return Path(value) if value and Path(value).is_absolute() else None


def support_root(root):
    """Where a data root keeps runtimes, caches and models.

    A root that already has its own runtime keeps using it (earlier custom
    roots and isolated test roots). Otherwise the default and saved archive
    locations hold only the archive, and their machinery stays in the hidden
    application directory. A root given only through --home or AUDAY_HOME is
    self-contained. Applying this twice gives the same result.
    """
    resolved = Path(root).expanduser().resolve()
    if (resolved / "runtime").is_dir() or resolved == default_home().expanduser().resolve():
        return resolved
    archives = [visible_home(), saved_data_root()]
    if any(path is not None and resolved == path.expanduser().resolve() for path in archives):
        return default_home().expanduser().resolve()
    return resolved

def venv_python(directory):
    return Path(directory) / ("Scripts/python.exe" if is_windows() else "bin/python")


def lock_file(stream, blocking=True):
    """Take an exclusive lock, with consistent contention errors on both OSes."""
    if not is_windows():
        import fcntl
        fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        return
    import msvcrt
    while True:
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError as error:
            if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK) and getattr(error, "winerror", None) not in (33, 36):
                raise
            if not blocking:
                raise BlockingIOError(errno.EAGAIN, "library_operation_busy") from None
            time.sleep(0.05)


def unlock_file(stream):
    if is_windows():
        import msvcrt
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream, fcntl.LOCK_UN)


@contextmanager
def exclusive_file_lock(path, blocking=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a+b") as stream:
        lock_file(stream, blocking=blocking)
        try:
            yield stream
        finally:
            unlock_file(stream)


def _restrict_windows_directory(path):
    """Protect an application-owned directory for this user and Local System."""
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    security.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    security.OpenProcessToken.restype = wintypes.BOOL
    security.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    security.GetTokenInformation.restype = wintypes.BOOL
    security.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    security.ConvertSidToStringSidW.restype = wintypes.BOOL
    security.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    security.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    security.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    security.SetFileSecurityW.restype = wintypes.BOOL
    token = wintypes.HANDLE()
    sid_text = wintypes.LPWSTR()
    descriptor = ctypes.c_void_p()
    try:
        if not security.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
            raise OSError("private_directory_token_unavailable")
        size = wintypes.DWORD()
        security.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        if not 0 < size.value < 1024 * 1024:
            raise OSError("private_directory_token_invalid")
        buffer = ctypes.create_string_buffer(size.value)
        if not security.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
            raise OSError("private_directory_user_unavailable")
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p)).contents
        if not security.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
            raise OSError("private_directory_user_invalid")
        sddl = "D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;" + sid_text.value + ")"
        if not security.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
            raise OSError("private_directory_acl_invalid")
        if not security.SetFileSecurityW(str(path), 0x80000004, descriptor):
            raise OSError("private_directory_acl_failed")
    finally:
        if descriptor:
            kernel.LocalFree(descriptor)
        if sid_text:
            kernel.LocalFree(ctypes.cast(sid_text, ctypes.c_void_p))
        if token:
            kernel.CloseHandle(token)


def secure_directory(path):
    """Create/protect a dedicated application directory, never a general parent."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if is_windows():
        _restrict_windows_directory(path)
    else:
        path.chmod(0o700)
    return path


def start_process(command, **kwargs):
    if is_windows():
        kwargs.pop("start_new_session", None)
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs.setdefault("start_new_session", True)
    return subprocess.Popen(command, **kwargs)


def stop_process(process, timeout=5):
    if process.poll() is not None:
        return
    if is_windows():
        # Use the OS executable directly; user PATH may be intentionally empty.
        taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
        try:
            subprocess.run([str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            pass
        if process.poll() is None:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=timeout)
            return
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    process.wait(timeout=timeout)


def physical_memory_bytes():
    if is_windows():
        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_uint32), ("load", ctypes.c_uint32)] + [
                (name, ctypes.c_uint64) for name in (
                    "total_physical", "available_physical", "total_page", "available_page",
                    "total_virtual", "available_virtual", "available_extended",
                )
            ]
        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if not ctypes.WinDLL("kernel32", use_last_error=True).GlobalMemoryStatusEx(ctypes.byref(status)):
            raise OSError("physical_memory_unavailable")
        return status.total_physical
    if sys.platform == "darwin":
        result = subprocess.run(["/usr/sbin/sysctl", "-n", "hw.memsize"],
                                capture_output=True, text=True, check=True, timeout=10)
        return int(result.stdout.strip())
    return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
