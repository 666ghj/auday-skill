"""Read the native Windows architecture, including from an emulated process."""

import ctypes
from ctypes import wintypes
import os
import sys


def machine_architecture(machine):
    # IMAGE_FILE_MACHINE_AMD64 and IMAGE_FILE_MACHINE_ARM64.
    try:
        return {0x8664: "x86_64", 0xAA64: "arm64"}[machine]
    except KeyError:
        raise ValueError("windows_x64_or_arm64_required") from None


def native_architecture():
    if sys.platform != "win32":
        raise ValueError("windows_required")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    try:
        query = kernel.IsWow64Process2
    except AttributeError:
        # This API arrived in Windows 10 1709. Older supported x64 Windows
        # cannot be an ARM64 host running an emulated x64 process.
        if sys.getwindowsversion().build >= 16299:
            raise ValueError("windows_native_architecture_unavailable") from None
        architecture = os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get("PROCESSOR_ARCHITECTURE")
        if (architecture or "").upper() == "AMD64":
            return "x86_64"
        raise ValueError("windows_x64_or_arm64_required") from None
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    query.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.USHORT), ctypes.POINTER(wintypes.USHORT)]
    query.restype = wintypes.BOOL
    process, native = wintypes.USHORT(), wintypes.USHORT()
    if not query(kernel.GetCurrentProcess(), ctypes.byref(process), ctypes.byref(native)):
        raise ValueError("windows_native_architecture_unavailable")
    return machine_architecture(native.value)
