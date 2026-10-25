"""Windows bootstrap contracts; no model downloads or global installations."""

from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import bootstrap_windows
import setup_runtime as runtime

POWERSHELL = (str(Path(os.environ.get("SystemRoot", r"C:\Windows")) /
                  "System32/WindowsPowerShell/v1.0/powershell.exe") if sys.platform == "win32"
              else os.environ.get("AUDAY_TEST_POWERSHELL") or shutil.which("pwsh"))


class WindowsLauncherTests(unittest.TestCase):
    def setUp(self):
        native = patch.object(runtime, "windows_native_architecture", return_value="x86_64")
        native.start()
        self.addCleanup(native.stop)

    def test_launcher_uses_connection_interpreter_and_preserves_arguments_and_exit(self):
        with tempfile.TemporaryDirectory(prefix="auday-launcher-") as folder:
            root = Path(folder) / "space and Unicode \u8bb0\u5fc6"
            arguments = ["--home", str(root), "--profile", "test-name", "search", 'a "quoted" query']
            interpreter = root / "runtime/windows-link/venv/Scripts/python.exe"
            with patch.object(bootstrap_windows.sys, "platform", "win32"), \
                    patch.object(bootstrap_windows, "ensure_windows_link", return_value=interpreter) as ensure, \
                    patch.object(bootstrap_windows.subprocess, "run", return_value=SimpleNamespace(returncode=23)) as run:
                self.assertEqual(bootstrap_windows.main(arguments), 23)
            ensure.assert_called_once_with(root.resolve(), python_path=sys.executable)
            self.assertEqual(run.call_args.args[0], [str(interpreter), str(SCRIPTS / "auday.py"), *arguments])
            self.assertEqual(run.call_args.kwargs["env"]["UV_PYTHON"], "3.13.16")

    def test_connection_setup_installs_only_hash_locked_wheels_and_reuses_probe(self):
        with tempfile.TemporaryDirectory(prefix="auday-link-setup-") as folder:
            root = Path(folder)
            scripts = root / "synthetic-scripts"
            scripts.mkdir()
            (scripts / "requirements-windows-link.txt").write_text("synthetic locked requirements\n")
            utilities = SimpleNamespace(
                secure_directory=lambda path: path.mkdir(parents=True, exist_ok=True),
                exclusive_file_lock=lambda path: nullcontext(),
            )
            python = runtime.windows_link_python(root)
            calls = []

            def run(command, **kwargs):
                calls.append((command, kwargs))
                if "venv" in command:
                    python.parent.mkdir(parents=True)
                    python.write_bytes(b"synthetic Python")
                return SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True, "tlsPSK": True, "interpreterPlatform": "win-amd64"}))

            with patch.object(runtime.sys, "platform", "win32"), \
                    patch.object(runtime, "HERE", scripts), \
                    patch.dict(sys.modules, {"platform_utils": utilities}), \
                    patch.object(runtime.subprocess, "run", side_effect=run), \
                    patch.object(runtime, "report"):
                self.assertEqual(runtime.ensure_windows_link(root, root / "uv.exe", root / "base-python.exe"), python)
                self.assertEqual(runtime.ensure_windows_link(root, root / "uv.exe", root / "base-python.exe"), python)
            self.assertEqual(len(calls), 4)  # venv, locked install, probe, cached probe
            install = calls[1][0]
            self.assertIn("--require-hashes", install)
            self.assertEqual(install[install.index("--only-binary") + 1], ":all:")
            self.assertIn("--no-deps", install)
            self.assertNotIn("torch", " ".join(install))
            for command, kwargs in calls:
                self.assertTrue(kwargs["check"])
                self.assertEqual(kwargs["env"]["UV_PYTHON"], "3.13.16")
                self.assertNotIn("--system", command)

    def test_arm64_connection_setup_uses_pure_python_dependencies_and_native_probe(self):
        with tempfile.TemporaryDirectory(prefix="auday-arm64-link-") as folder:
            root = Path(folder)
            scripts = root / "synthetic-scripts"
            scripts.mkdir()
            (scripts / "requirements-windows-link.txt").write_text("synthetic lock\n")
            build_lock = scripts / "requirements-windows-arm64-build.txt"
            build_lock.write_text("synthetic build lock\n")
            utilities = SimpleNamespace(
                secure_directory=lambda path: path.mkdir(parents=True, exist_ok=True),
                exclusive_file_lock=lambda path: nullcontext(),
            )
            python = runtime.windows_link_python(root)

            def run(command, **kwargs):
                if "venv" in command:
                    python.parent.mkdir(parents=True)
                    python.write_bytes(b"synthetic ARM64 Python")
                return SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True, "tlsPSK": True, "interpreterPlatform": "win-arm64"}))

            with patch.object(runtime.sys, "platform", "win32"), \
                    patch.object(runtime, "windows_native_architecture", return_value="arm64"), \
                    patch.object(runtime, "HERE", scripts), \
                    patch.dict(sys.modules, {"platform_utils": utilities}), \
                    patch.object(runtime.subprocess, "run", side_effect=run) as process, \
                    patch("windows_link_dependencies.install_arm64_link_dependencies") as install, \
                    patch.object(runtime, "report"):
                self.assertEqual(runtime.ensure_windows_link(root, root / "uv.exe", root / "base-python.exe"), python)
                self.assertEqual(runtime.ensure_windows_link(root, root / "uv.exe", root / "base-python.exe"), python)
                install.assert_called_once()
                self.assertEqual(install.call_args.args[0], python)
                self.assertEqual(install.call_args.kwargs["uv"], root / "uv.exe")
                self.assertEqual(process.call_count, 3)  # venv, fresh probe, cached probe
                marker = json.loads((root / "runtime/windows-link/.auday-link-runtime.json").read_text())
                self.assertEqual(marker["architecture"], "arm64")
                self.assertEqual(marker["buildRequirementsSHA256"], runtime.file_hash(build_lock))
                build_lock.write_text("changed build dependency\n")
                with self.assertRaisesRegex(ValueError, "existing_windows_link_runtime_incomplete"):
                    runtime.ensure_windows_link(root)
                self.assertTrue(python.is_file())

    def test_arm64_probe_rejects_emulated_x64_python(self):
        result = SimpleNamespace(stdout=json.dumps({"ok": True, "tlsPSK": True, "interpreterPlatform": "win-amd64"}))
        with patch.object(runtime.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(ValueError, "runtime_architecture_mismatch"):
                runtime._probe_windows_link(Path("python.exe"), {}, architecture="arm64")

    def test_connection_setup_preserves_existing_incomplete_directory(self):
        with tempfile.TemporaryDirectory(prefix="auday-link-preserve-") as folder:
            root = Path(folder)
            scripts = root / "synthetic-scripts"
            scripts.mkdir()
            (scripts / "requirements-windows-link.txt").write_text("synthetic lock\n")
            destination = root / "runtime/windows-link"
            destination.mkdir(parents=True)
            personal = destination / "keep.txt"
            personal.write_text("preserve")
            utilities = SimpleNamespace(secure_directory=lambda path: None,
                                        exclusive_file_lock=lambda path: nullcontext())
            with patch.object(runtime.sys, "platform", "win32"), \
                    patch.object(runtime, "HERE", scripts), \
                    patch.dict(sys.modules, {"platform_utils": utilities}), \
                    patch.object(runtime.subprocess, "run") as run:
                with self.assertRaisesRegex(ValueError, "existing_windows_link_runtime_incomplete"):
                    runtime.ensure_windows_link(root)
                run.assert_not_called()
            self.assertEqual(personal.read_text(), "preserve")


@unittest.skipUnless(POWERSHELL, "PowerShell is only needed for its bootstrap acceptance tests")
class PowerShellBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="auday-PS-space-\u8bb0\u5fc6-")
        self.root = Path(self.temporary.name).resolve()
        self.addCleanup(self.temporary.cleanup)

    def powershell(self, body, *, environment=None, prelude=""):
        bootstrap = str(SCRIPTS / "bootstrap.ps1").replace("'", "''")
        script = self.root / "test script.ps1"
        # Windows PowerShell 5.1 needs a BOM to read non-ASCII source reliably.
        script.write_text(f"{prelude}\n. '{bootstrap}'\n{body}\n", encoding="utf-8-sig")
        return subprocess.run(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)],
            env=environment, capture_output=True, text=True, encoding="utf-8", timeout=30,
        )

    def literal(self, value):
        return "'" + str(value).replace("'", "''") + "'"

    def test_native_architecture_ignores_emulated_shell_environment(self):
        result = self.powershell(
            "function Import-Module { }\n"
            "function Get-CimInstance { [pscustomobject] @{ Architecture = 12 } }\n"
            "$env:PROCESSOR_ARCHITECTURE = 'AMD64'\n"
            "$env:PROCESSOR_ARCHITEW6432 = 'AMD64'\n"
            "Get-AudayArchitecture"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "arm64")

    def test_native_architecture_rejects_missing_and_unsupported_cpu_data(self):
        for output in ("$null", "[pscustomobject] @{ Architecture = 0 }",
                       "[pscustomobject] @{ Architecture = 9 }; [pscustomobject] @{ Architecture = 12 }"):
            with self.subTest(output=output):
                result = self.powershell(
                    "function Import-Module { }\n"
                    "function Get-CimInstance { " + output + " }\n"
                    "$env:PROCESSOR_ARCHITECTURE = 'AMD64'\n"
                    "Get-AudayArchitecture"
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertRegex(result.stderr, "windows_(native_architecture_unavailable|x64_or_arm64_required)")

    def test_private_environment_does_not_mutate_current_process(self):
        environment = {**os.environ, "UV_PYTHON": "poison-python", "PIP_INDEX_URL": "https://invalid.example",
                       "PYTHONHOME": "poison-home", "PYTHONPATH": "poison-path", "VIRTUAL_ENV": "poison-venv"}
        result = self.powershell(
            f"$private = Get-AudayPrivateEnvironment {self.literal(self.root)}\n"
            "@{ private = $private; originalPython = $env:UV_PYTHON } | ConvertTo-Json -Depth 4 -Compress",
            environment=environment,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["originalPython"], "poison-python")
        self.assertEqual(payload["private"]["UV_PYTHON"], "3.13.16")
        for name in ("PIP_INDEX_URL", "PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
            self.assertNotIn(name, payload["private"])
        self.assertTrue(payload["private"]["UV_CACHE_DIR"].startswith(str(self.root)))

    def test_windows_command_line_quoting_matches_python_subprocess(self):
        arguments = ["plain", "", "space path", 'a"quote', "ends\\", 'slashes\\\\"quote', "\u8bb0\u5fc6"]
        serialized = "@( " + ", ".join(self.literal(value) for value in arguments) + " )"
        result = self.powershell(f"@({serialized} | ForEach-Object {{ ConvertTo-AudayNativeArgument $_ }}) | ConvertTo-Json -Compress")
        self.assertEqual(result.returncode, 0, result.stderr)
        quoted = json.loads(result.stdout)
        # Always-quoted equivalent of Python's canonical Windows quoting.
        for value, actual in zip(arguments, quoted):
            expected = subprocess.list2cmdline([value])
            if not expected.startswith('"'):
                expected = '"' + expected + ('\\' if expected.endswith('\\') else '') + '"'
            self.assertEqual(actual, expected)

    def test_safe_zip_retains_dlls_and_rejects_windows_aliases(self):
        archive = self.root / "archive.zip"
        cases = [
            ([('tool.exe', b'executable', 0o100755), ('runtime.dll', b'library', 0o100644)], True),
            ([('../outside', b'escape', 0o100644)], False),
            ([('x.exe', b'first', 0o100644), ('X.EXE', b'second', 0o100644)], False),
            ([('CON.txt', b'reserved', 0o100644)], False),
            ([('link', b'target', 0o120777)], False),
            ([('dir', b'file', 0o100644), ('dir/child', b'child', 0o100644)], False),
        ]
        for entries, valid in cases:
            with self.subTest(entries=entries):
                with zipfile.ZipFile(archive, "w") as output:
                    for name, content, mode in entries:
                        member = zipfile.ZipInfo(name)
                        member.external_attr = mode << 16
                        output.writestr(member, content)
                target = self.root / "extracted"
                result = self.powershell(
                    f"Expand-AudaySafeZip {self.literal(archive)} {self.literal(target)}\n'complete'"
                )
                self.assertEqual(result.returncode == 0, valid, result.stderr)
                if valid:
                    self.assertEqual((target / "runtime.dll").read_bytes(), b"library")
                    shutil.rmtree(target)
                else:
                    self.assertFalse(target.exists())
                    self.assertFalse((self.root / "outside").exists())

    def test_verified_cached_download_is_reused_without_network(self):
        target = self.root / "cached.zip"
        target.write_bytes(b"synthetic archive")
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        result = self.powershell(
            f"Get-AudayVerifiedDownload 'https://invalid.example/archive' {self.literal(target)} '{digest}' {target.stat().st_size}"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(result.stdout.strip()), target)

    def test_cached_verification_and_manifest_loading_need_no_module_discovery(self):
        target = self.root / "cached-without-autoload.zip"
        target.write_bytes(b"synthetic archive without module auto-loading")
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        manifest = SCRIPTS / "runtime-assets.tsv"
        result = self.powershell(
            "function Get-FileHash { throw 'unexpected_Get_FileHash_dependency' }\n"
            f"$cached = Get-AudayVerifiedDownload 'https://invalid.example/archive' {self.literal(target)} '{digest}' {target.stat().st_size}\n"
            f"$assets = @(Import-Csv -LiteralPath {self.literal(manifest)} -Delimiter \"`t\" | Where-Object {{ $_.component -eq 'uv' -and $_.platform -eq 'windows' }})\n"
            "@{ cached = $cached; windowsAssets = $assets.Count; autoLoading = $PSModuleAutoLoadingPreference } | ConvertTo-Json -Compress",
            prelude="$PSModuleAutoLoadingPreference = 'None'\n$env:PSModulePath = ''",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(Path(payload["cached"]), target)
        self.assertEqual(payload["windowsAssets"], 2)
        self.assertEqual(payload["autoLoading"], "None")

    def test_cached_download_with_matching_size_still_requires_matching_hash(self):
        target = self.root / "modified-cache.zip"
        original = b"original synthetic archive"
        target.write_bytes(b"x" * len(original))
        result = self.powershell(
            "function Get-FileHash { throw 'unexpected_Get_FileHash_dependency' }\n"
            f"Get-AudayVerifiedDownload 'https://invalid.example/archive' {self.literal(target)} '{hashlib.sha256(original).hexdigest()}' {len(original)}",
            prelude="$PSModuleAutoLoadingPreference = 'None'\n$env:PSModulePath = ''",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("existing_component_hash_mismatch", result.stderr)
        self.assertEqual(target.read_bytes(), b"x" * len(original))
        self.assertFalse(target.with_name(target.name + ".partial").exists())

    @unittest.skipUnless(sys.platform == "win32", "Windows OS-only entrypoint")
    def test_check_creates_nothing_with_no_python_uv_or_developer_tools(self):
        data_root = self.root / "uncreated data \u8bb0\u5fc6"
        environment = {**os.environ, "PATH": str(self.root / "empty-path")}
        result = subprocess.run(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
             str(SCRIPTS / "bootstrap.ps1"), "--home", str(data_root), "--profile", "synthetic", "check"],
            env=environment, capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["downloadsStarted"])
        self.assertFalse(data_root.exists())

    @unittest.skipUnless(sys.platform == "win32", "native Windows argv and stream behavior")
    def test_native_process_forwards_arguments_stdout_and_exit_status(self):
        helper = self.root / "native argv.py"
        helper.write_text("import json,sys; print(json.dumps(sys.argv[1:])); sys.exit(23)\n")
        arguments = [str(helper), 'space path', 'a "quote"', 'tail\\', '\u8bb0\u5fc6']
        command_args = "@( " + ", ".join(self.literal(value) for value in arguments) + " )"
        result = self.powershell(
            f"$environment = Get-AudayPrivateEnvironment {self.literal(self.root)}\n"
            f"$result = Invoke-AudayProcess {self.literal(sys.executable)} {command_args} $environment\n"
            "exit $result.ExitCode"
        )
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual(json.loads(result.stdout), arguments[1:])


if __name__ == "__main__":
    unittest.main()
