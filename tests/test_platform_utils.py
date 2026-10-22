"""Native OS boundaries: profile paths, process cleanup and Windows privacy."""

import ctypes
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import auday
import platform_utils as platform


class PlatformTests(unittest.TestCase):
    def test_windows_default_home_uses_local_application_data(self):
        with patch.object(platform, "is_windows", return_value=True), patch.dict(os.environ, {"LOCALAPPDATA": "C:/Synthetic Local Data"}):
            self.assertEqual(platform.default_home(), Path("C:/Synthetic Local Data/AudayWorkflow"))

    def test_unicode_private_directory_and_json_round_trip(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = platform.secure_directory(Path(temporary) / "资料 日本語 with spaces")
            app = auday.Auday(root)
            try:
                expected = {"service": "Auday 的 iPhone", "host": "127.0.0.1", "port": 1234}
                auday.atomic_json(app.root / "connection.json", expected)
                self.assertEqual(app.config(), expected)
            finally:
                app.close()

    def test_windows_bridge_never_requires_swift(self):
        with patch.object(auday, "is_windows", return_value=True), patch.object(auday, "bridge", side_effect=AssertionError("Swift used")):
            self.assertEqual(auday.bridge_command(), [sys.executable, str(auday.HERE / "windows_link.py")])

    def test_pair_window_uses_a_separate_console_without_password_arguments(self):
        with tempfile.TemporaryDirectory() as temporary:
            class Client:
                root = Path(temporary)
                def __init__(self, *_): pass
                def close(self): pass
            output = io.StringIO()
            with patch.object(auday, "is_windows", return_value=True), patch.object(auday, "Auday", Client), \
                 patch.object(sys, "argv", ["auday.py", "--home", temporary, "pair-window", "--service", "Auday 日本語"]), \
                 patch.object(sys, "stdout", output), patch.object(auday.subprocess, "Popen") as launched, \
                 patch.object(auday.subprocess, "CREATE_NEW_CONSOLE", 0x10, create=True):
                auday.main()
            self.assertEqual(json.loads(output.getvalue()), {"status": "awaiting_local_password_input"})
            command = launched.call_args.args[0]
            self.assertEqual(command[:2], [sys.executable, str(auday.HERE / "auday.py")])
            self.assertEqual(command[-2:], ["--service", "Auday 日本語"])
            self.assertIn("pair", command)
            self.assertNotIn("--password", command)
            self.assertEqual(launched.call_args.kwargs["creationflags"], 0x10)
            self.assertFalse((Path(temporary) / "pair.command").exists())

    def test_native_memory_measurement_is_plausible(self):
        self.assertGreater(platform.physical_memory_bytes(), 128 * 1024**2)

    def test_timeout_cleanup_stops_the_process_tree(self):
        program = (
            "import subprocess,sys,time; "
            "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']); "
            "print(child.pid,flush=True); time.sleep(60)"
        )
        process = platform.start_process([sys.executable, "-c", program], stdout=subprocess.PIPE, text=True)
        try:
            child_pid = int(process.stdout.readline().strip())
            platform.stop_process(process, timeout=3)
            self.assertIsNotNone(process.poll())
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and self._alive(child_pid):
                time.sleep(0.05)
            self.assertFalse(self._alive(child_pid), "A local tool child survived timeout cleanup")
        finally:
            platform.stop_process(process, timeout=3)
            process.stdout.close()

    @staticmethod
    def _alive(pid):
        if sys.platform == "win32":
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            handle = kernel.OpenProcess(0x1000, False, pid)
            if not handle: return False
            try:
                code = ctypes.c_uint32()
                return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
            finally:
                kernel.CloseHandle(handle)
        state = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True)
        return state.returncode == 0 and not state.stdout.strip().startswith("Z")

    @unittest.skipUnless(sys.platform == "win32", "Windows native DACL")
    def test_private_directory_has_a_protected_two_principal_acl(self):
        from ctypes import wintypes
        with tempfile.TemporaryDirectory() as temporary:
            directory = platform.secure_directory(Path(temporary) / "private")
            security = ctypes.WinDLL("advapi32", use_last_error=True)
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            security.GetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
                ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_void_p)]
            security.GetNamedSecurityInfoW.restype = wintypes.DWORD
            security.GetSecurityDescriptorControl.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint16), ctypes.POINTER(wintypes.DWORD)]
            kernel.LocalFree.argtypes = [ctypes.c_void_p]
            kernel.LocalFree.restype = ctypes.c_void_p
            acl, descriptor = ctypes.c_void_p(), ctypes.c_void_p()
            self.assertEqual(security.GetNamedSecurityInfoW(str(directory), 1, 4, None, None,
                ctypes.byref(acl), None, ctypes.byref(descriptor)), 0)
            try:
                control, revision = ctypes.c_uint16(), wintypes.DWORD()
                self.assertTrue(security.GetSecurityDescriptorControl(descriptor, ctypes.byref(control), ctypes.byref(revision)))
                self.assertTrue(control.value & 0x1000)
                # ACL header: byte revision, byte padding, WORD size, WORD AceCount.
                self.assertEqual(ctypes.c_uint16.from_address(acl.value + 4).value, 2)
            finally:
                kernel.LocalFree(descriptor)


if __name__ == "__main__":
    unittest.main()
