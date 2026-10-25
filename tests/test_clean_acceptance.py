"""Tests for the acceptance harness, without downloading optional runtimes."""

import base64
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from check_clean_setup import (
    FORBIDDEN_TOOLS, audit_windows_architecture, audit_windows_dependencies, clean_environment,
    install_windows_tool_traps, pe_architecture, pe_imports, verify_windows_host,
)


def pe_fixture(path, imports, delayed=(), *, machine=0x8664):
    """Minimal PE32+ layout with ordinary/delay import tables and DLL names."""
    data = bytearray(4096)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", data, 0x84, machine)
    struct.pack_into("<H", data, 0x86, 1)
    struct.pack_into("<H", data, 0x94, 240)
    optional = 0x98
    struct.pack_into("<H", data, optional, 0x20B)
    struct.pack_into("<Q", data, optional + 24, 0x140000000)
    section = optional + 240
    struct.pack_into("<IIII", data, section + 8, 0xE00, 0x1000, 0xE00, 0x200)
    names = 0x800
    for directory, table, stride, dlls in ((1, 0x200, 20, imports), (13, 0x500, 32, delayed)):
        if not dlls:
            continue
        struct.pack_into("<II", data, optional + 112 + directory * 8,
                         table + 0xE00, (len(dlls) + 1) * stride)
        for index, dll in enumerate(dlls):
            entry = table + index * stride
            if directory == 13:
                struct.pack_into("<II", data, entry, 1, names + 0xE00)
            else:
                struct.pack_into("<I", data, entry + 12, names + 0xE00)
            content = dll.encode("ascii") + b"\0"
            data[names:names + len(content)] = content
            names += len(content)
    path.write_bytes(data)
    return path


class CleanAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="acceptance-tests-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_windows_environment_has_no_user_cache_or_developer_path(self):
        env, home, sentinel = clean_environment(self.root, {
            "PATH": r"C:\developer\python;C:\developer\ffmpeg",
            "SystemRoot": r"C:\Windows", "HOME": r"C:\personal",
            "USERPROFILE": r"C:\personal", "LOCALAPPDATA": r"C:\personal\cache",
            "HF_HOME": "personal-models", "UV_CACHE_DIR": "personal-uv",
            "PYTHONPATH": "personal-python", "PIP_INDEX_URL": "private-index",
            "CONDA_PREFIX": "personal-conda", "IMAGEIO_FFMPEG_EXE": "personal-ffmpeg",
        }, windows=True)
        self.assertNotIn("developer", env["PATH"])
        for key in ("HF_HOME", "UV_CACHE_DIR", "PYTHONPATH", "PIP_INDEX_URL",
                    "CONDA_PREFIX", "IMAGEIO_FFMPEG_EXE"):
            self.assertNotIn(key, env)
        for key in ("HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "TEMP", "XDG_CACHE_HOME"):
            self.assertTrue(Path(env[key]).is_relative_to(self.root))
        self.assertIn(" ", str(home))
        self.assertTrue((self.root / "forbidden-tools/python.cmd").is_file())
        self.assertFalse(sentinel.exists())

    def test_native_traps_compile_in_ascii_temp_but_execute_at_unicode_path(self):
        import subprocess
        output = self.root / "acceptance 用户"
        output.mkdir()
        env, _, sentinel = clean_environment(output, {"SystemRoot": r"C:\Windows"}, windows=True)
        original_temp = env["TEMP"]
        compiler_directories = []

        def fake_run(command, **options):
            if "-EncodedCommand" in command:
                program = base64.b64decode(command[-1]).decode("utf-16-le")
                self.assertTrue(program.isascii())
                directory = Path(options["cwd"])
                self.assertTrue(str(directory).isascii())
                self.assertEqual(options["env"]["TEMP"], str(directory))
                self.assertEqual(options["env"]["TMP"], str(directory))
                (directory / "forbidden.exe").write_bytes(b"MZsynthetic-trap")
                compiler_directories.append(directory)
                return subprocess.CompletedProcess(command, 0)
            self.assertFalse(str(command[0]).isascii())
            self.assertEqual(options["env"]["TEMP"], original_temp)
            Path(options["env"]["AUDAY_FORBIDDEN_TOOL_SENTINEL"]).write_text("forbidden")
            return subprocess.CompletedProcess(command, 97)

        with patch("check_clean_setup.subprocess.run", side_effect=fake_run):
            install_windows_tool_traps("powershell.exe", env, build_parent=self.root)
        self.assertEqual(env["TEMP"], original_temp)
        self.assertFalse(sentinel.exists())
        self.assertTrue(all(not path.exists() for path in compiler_directories))
        for tool in FORBIDDEN_TOOLS:
            self.assertEqual((output / "forbidden-tools" / (tool + ".exe")).read_bytes(),
                             b"MZsynthetic-trap")
        self.assertFalse((output / "forbidden-tools/trap-proof").exists())

    @unittest.skipIf(os.name == "nt", "POSIX shell trap")
    def test_posix_poison_records_accidental_global_tool_use(self):
        import subprocess
        env, home, sentinel = clean_environment(self.root, {"PATH": "/user/tools"}, windows=False)
        result = subprocess.run(["uv", "--version"], env=env, cwd=home)
        self.assertEqual(result.returncode, 97)
        self.assertEqual(sentinel.read_text(), "forbidden")

    def test_import_parser_covers_regular_and_delayed_dependencies(self):
        path = pe_fixture(self.root / "engine.exe", ["KERNEL32.dll", "VCRUNTIME140.dll"],
                          ["MSVCP140.dll"])
        self.assertEqual(pe_imports(path), ["kernel32.dll", "msvcp140.dll", "vcruntime140.dll"])

    def test_architecture_parser_distinguishes_native_windows_binaries(self):
        for machine, architecture in ((0x8664, "x86_64"), (0xAA64, "arm64"), (0xA64E, "arm64x")):
            with self.subTest(architecture=architecture):
                path = pe_fixture(self.root / "engine.exe", [], machine=machine)
                self.assertEqual(pe_architecture(path), architecture)
        path = pe_fixture(self.root / "engine.exe", [], machine=0x14C)
        with self.assertRaisesRegex(ValueError, "unsupported_pe_machine"):
            pe_architecture(path)

    def test_windows_host_rejects_emulated_python_on_native_arm64(self):
        python = pe_fixture(self.root / "python.exe", [])
        with patch("check_clean_setup.windows_native_architecture", return_value="arm64"), \
                patch("check_clean_setup.sys.executable", str(python)):
            with self.assertRaisesRegex(AssertionError, "Expected native arm64 Python"):
                verify_windows_host("arm64")
        pe_fixture(python, [], machine=0xAA64)
        with patch("check_clean_setup.windows_native_architecture", return_value="arm64"), \
                patch("check_clean_setup.sys.executable", str(python)):
            self.assertEqual(verify_windows_host("arm64"), {"host": "arm64", "python": "arm64"})
            with self.assertRaisesRegex(AssertionError, "Expected x86_64 Windows host"):
                verify_windows_host("x86_64")

    def test_runtime_audit_rejects_x64_executable_on_arm64(self):
        executable = pe_fixture(self.root / "llama-server.exe", [])
        with self.assertRaisesRegex(AssertionError, "Expected native arm64 executable"):
            audit_windows_architecture([executable], self.root, "arm64")
        pe_fixture(executable, [], machine=0xAA64)
        self.assertEqual(audit_windows_architecture([executable], self.root, "arm64"), [
            {"binary": "llama-server.exe", "architecture": "arm64"},
        ])

    def test_native_dependency_audit_rejects_foreign_dll_and_accepts_arm64x_crt(self):
        engine = pe_fixture(self.root / "engine.exe", ["VCRUNTIME140.dll"], machine=0xAA64)
        dependency = pe_fixture(self.root / "VCRUNTIME140.dll", [])
        with self.assertRaisesRegex(AssertionError, "Wrong native dependency architecture"):
            audit_windows_dependencies([engine], self.root, self.root / "Windows", "arm64")
        pe_fixture(dependency, [], machine=0xA64E)
        report = audit_windows_dependencies([engine], self.root, self.root / "Windows", "arm64")
        self.assertEqual({row["architecture"] for row in report}, {"arm64", "arm64x"})

    def test_dependency_audit_rejects_system_only_visual_cpp_runtime(self):
        system = self.root / "Windows"
        (system / "System32").mkdir(parents=True)
        (system / "System32/vcruntime140.dll").touch()
        managed = self.root / "managed"
        managed.mkdir()
        engine = pe_fixture(managed / "engine.exe", ["VCRUNTIME140.dll"])
        with self.assertRaisesRegex(AssertionError, "Redistributable must be app-local"):
            audit_windows_dependencies([engine], managed, system)

    def test_dependency_audit_recurses_app_local_dlls_and_allows_os_apis(self):
        system = self.root / "Windows"
        (system / "System32").mkdir(parents=True)
        (system / "System32/kernel32.dll").touch()
        managed = self.root / "managed"
        managed.mkdir()
        engine = pe_fixture(managed / "engine.exe", ["VCRUNTIME140.dll", "KERNEL32.dll"])
        pe_fixture(managed / "VCRUNTIME140.dll", ["api-ms-win-crt-runtime-l1-1-0.dll"])
        report = audit_windows_dependencies([engine], managed, system)
        self.assertEqual({row["binary"] for row in report}, {"engine.exe", "VCRUNTIME140.dll"})


if __name__ == "__main__":
    unittest.main()
