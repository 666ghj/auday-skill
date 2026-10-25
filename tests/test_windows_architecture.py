"""Native architecture selection must not inherit an agent's emulation mode."""

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import windows_architecture as architecture


class WindowsArchitectureTests(unittest.TestCase):
    def test_native_arm64_wins_over_x64_process_and_environment(self):
        def query(handle, process, native):
            self.assertEqual(handle, 123)
            ctypes.cast(process, ctypes.POINTER(wintypes.USHORT))[0] = 0x8664
            ctypes.cast(native, ctypes.POINTER(wintypes.USHORT))[0] = 0xAA64
            return True

        kernel = SimpleNamespace(GetCurrentProcess=Mock(return_value=123), IsWow64Process2=Mock(side_effect=query))
        with patch.object(architecture.sys, "platform", "win32"), \
                patch.object(architecture.ctypes, "WinDLL", create=True, return_value=kernel), \
                patch.dict(os.environ, {"PROCESSOR_ARCHITECTURE": "AMD64", "PROCESSOR_ARCHITEW6432": "AMD64"}):
            self.assertEqual(architecture.native_architecture(), "arm64")

    def test_native_x64_and_unsupported_machine_types(self):
        self.assertEqual(architecture.machine_architecture(0x8664), "x86_64")
        for machine in (0, 0x14C, 0x1C4):
            with self.subTest(machine=machine), self.assertRaisesRegex(ValueError, "x64_or_arm64"):
                architecture.machine_architecture(machine)

    def test_failed_native_query_does_not_guess_from_environment(self):
        kernel = SimpleNamespace(GetCurrentProcess=Mock(return_value=123), IsWow64Process2=Mock(return_value=False))
        with patch.object(architecture.sys, "platform", "win32"), \
                patch.object(architecture.ctypes, "WinDLL", create=True, return_value=kernel), \
                patch.dict(os.environ, {"PROCESSOR_ARCHITECTURE": "AMD64"}):
            with self.assertRaisesRegex(ValueError, "native_architecture_unavailable"):
                architecture.native_architecture()

    def test_only_old_windows_can_use_legacy_x64_fallback(self):
        with patch.object(architecture.sys, "platform", "win32"), \
                patch.object(architecture.ctypes, "WinDLL", create=True, return_value=SimpleNamespace()), \
                patch.dict(os.environ, {"PROCESSOR_ARCHITECTURE": "x86", "PROCESSOR_ARCHITEW6432": "AMD64"}), \
                patch.object(architecture.sys, "getwindowsversion", create=True, return_value=SimpleNamespace(build=15063)):
            self.assertEqual(architecture.native_architecture(), "x86_64")
        with patch.object(architecture.sys, "platform", "win32"), \
                patch.object(architecture.ctypes, "WinDLL", create=True, return_value=SimpleNamespace()), \
                patch.object(architecture.sys, "getwindowsversion", create=True, return_value=SimpleNamespace(build=26100)):
            with self.assertRaisesRegex(ValueError, "native_architecture_unavailable"):
                architecture.native_architecture()


if __name__ == "__main__":
    unittest.main()
