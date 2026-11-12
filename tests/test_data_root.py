"""One persistent data root shared by every entrypoint, and no silent second library."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from auday import Auday, atomic_json
import data_root
from test_incremental_sync import IncrementalPeer, record


class DataRootTestCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="auday-data-root-记忆 ")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.default = self.base / "platform default"
        self.workspace = self.base / "workspace" / "auday"
        default = patch.object(data_root, "default_home", return_value=self.default)
        default.start()
        self.addCleanup(default.stop)
        environment = patch.dict(os.environ, {"AUDAY_PROGRESS": "0"})
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop(data_root.ENVIRONMENT_VARIABLE, None)

    def synced_library(self, home, paired=True):
        app = Auday(home, transport=IncrementalPeer([record("one"), record("two")]))
        try:
            app.sync()
            if paired:
                atomic_json(app.root / "connection.json", {"host": "192.0.2.1", "port": 1})
        finally:
            app.close()


class ResolutionTests(DataRootTestCase):
    def test_option_then_environment_then_saved_then_default(self):
        self.assertEqual(data_root.resolve_home(), (self.default, "default"))
        self.workspace.mkdir(parents=True)
        data_root.save_home(self.workspace)
        self.assertEqual(data_root.resolve_home(), (self.workspace, "saved"))
        environment = self.base / "from environment"
        with patch.dict(os.environ, {"AUDAY_HOME": str(environment)}):
            self.assertEqual(data_root.resolve_home(), (environment, "environment"))
            explicit = self.base / "one command"
            self.assertEqual(data_root.resolve_home(explicit), (explicit, "option"))

    def test_entrypoint_passing_the_configured_root_keeps_its_source(self):
        self.workspace.mkdir(parents=True)
        data_root.save_home(self.workspace)
        self.assertEqual(data_root.resolve_home(self.workspace)[1], "saved")
        self.assertEqual(data_root.resolve_home(self.default)[1], "option")

    def test_setting_is_one_absolute_line_and_saving_the_default_clears_it(self):
        self.workspace.mkdir(parents=True)
        data_root.save_home(self.workspace)
        self.assertEqual(data_root.setting_path().read_text(encoding="utf-8"), str(self.workspace) + "\n")
        data_root.save_home(self.default)
        self.assertFalse(data_root.setting_path().exists())
        data_root.clear_home()

    def test_unavailable_saved_root_is_refused_instead_of_recreated(self):
        self.workspace.mkdir(parents=True)
        data_root.save_home(self.workspace)
        self.workspace.rename(self.workspace.with_name("moved"))
        with self.assertRaisesRegex(ValueError, "saved_data_root_unavailable"):
            data_root.resolve_home()
        self.assertFalse(self.workspace.exists())
        explicit = self.base / "explicit"
        self.assertEqual(data_root.resolve_home(explicit), (explicit, "option"))

    def test_relative_environment_or_saved_root_is_rejected(self):
        with patch.dict(os.environ, {"AUDAY_HOME": "relative/root"}):
            with self.assertRaisesRegex(ValueError, "auday_home_must_be_an_absolute"):
                data_root.resolve_home()
        self.default.mkdir(parents=True)
        data_root.setting_path().write_text("relative/root\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "saved_data_root_must_be_absolute"):
            data_root.resolve_home()


class SecondLibraryTests(DataRootTestCase):
    def test_pairing_an_empty_root_is_refused_while_the_profile_lives_elsewhere(self):
        self.synced_library(self.workspace)
        data_root.save_home(self.workspace)
        # Merely opening the default root creates an empty database; that alone
        # is neither a pairing nor a synchronized library.
        Auday(self.default).close()
        with self.assertRaisesRegex(ValueError, "another_library_exists: " + str(self.workspace)):
            data_root.ensure_single_library(self.default, "default")
        data_root.ensure_single_library(self.default, "default", allow_new=True)
        data_root.ensure_single_library(self.workspace, "default")
        self.assertEqual(data_root.other_libraries(self.workspace, "default"), [])

    def test_other_profiles_and_unknown_roots_do_not_block(self):
        self.synced_library(self.workspace)
        data_root.ensure_single_library(self.default, "default")  # workspace is not a known root
        self.workspace.parent.mkdir(parents=True, exist_ok=True)
        data_root.save_home(self.workspace)
        data_root.ensure_single_library(self.default, "second-phone")
        with self.assertRaisesRegex(ValueError, "another_library_exists"):
            data_root.ensure_single_library(self.default, "default")

    def test_a_synchronized_but_unpaired_copy_counts(self):
        self.synced_library(self.default, paired=False)
        self.assertEqual(
            data_root.other_libraries(self.workspace, "default"),
            [{"dataRoot": str(self.default), "profile": "default", "paired": False, "synchronized": True}],
        )


class CommandLineTests(DataRootTestCase):
    """The issue's sequence, run through the real CLI with an isolated user home."""

    def run_cli(self, *arguments):
        home = self.base / "user"
        environment = {
            key: value for key, value in os.environ.items()
            if key not in ("AUDAY_HOME", "PYTHONPATH", "PYTHONHOME")
        }
        environment.update(HOME=str(home), USERPROFILE=str(home), LOCALAPPDATA=str(home / "local"),
                           AUDAY_PROGRESS="0", PYTHONUTF8="1")
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "auday.py"), *arguments],
            env=environment, capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        return result.returncode, json.loads(result.stdout.strip().splitlines()[-1])

    def test_saved_root_is_used_by_later_commands_and_a_second_pairing_is_refused(self):
        self.synced_library(self.workspace)
        code, saved = self.run_cli("--home", str(self.workspace), "data-root", "--save")
        self.assertEqual(code, 0)
        self.assertEqual(saved["withoutHomeOption"]["source"], "saved")
        self.assertEqual(saved["profileState"], {"paired": True, "synchronized": True})

        code, status = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertEqual(status["dataRoot"], str(self.workspace))
        self.assertEqual(status["dataRootSource"], "saved")
        self.assertEqual(status["cache"]["recordCount"], 2)

        code, cleared = self.run_cli("data-root", "--clear")
        self.assertEqual(code, 0)
        self.assertIsNone(cleared["savedDataRoot"])
        self.assertEqual(cleared["source"], "default")
        self.assertEqual(cleared["otherLibraries"], [])  # the workspace is no longer a known root

        self.run_cli("--home", str(self.workspace), "data-root", "--save")
        code, override = self.run_cli("--home", str(self.base / "isolated"), "status")
        self.assertEqual((code, override["dataRootSource"], override["cache"]), (0, "option", {}))
        self.assertEqual(override["otherLibraries"][0]["dataRoot"], str(self.workspace))

        code, refused = self.run_cli("--home", str(self.base / "isolated"), "pair", "--host", "192.0.2.1", "--port", "1")
        self.assertEqual(code, 1)
        self.assertRegex(refused["error"], "^another_library_exists: ")

        code, unpaired = self.run_cli("--home", str(self.base / "isolated"), "sync")
        self.assertEqual(code, 1)
        self.assertRegex(unpaired["error"], "^not_paired_here: ")

    def test_doctor_reports_the_effective_root(self):
        code, report = self.run_cli("--home", str(self.workspace), "doctor")
        self.assertEqual(code, 0)
        self.assertEqual((report["dataRoot"], report["dataRootSource"]), (str(self.workspace), "option"))


@unittest.skipUnless(sys.platform == "darwin", "bootstrap.sh is the macOS system-shell entrypoint")
class ShellEntrypointTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="auday-shell-root-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.home = self.base / "user"
        self.default = self.home / "Library" / "Application Support" / "AudayWorkflow"
        self.default.mkdir(parents=True)

    def check(self, *arguments, environment=None):
        result = subprocess.run(
            ["/bin/sh", str(SCRIPTS / "bootstrap.sh"), *arguments, "check"],
            env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(self.home), **(environment or {})},
            capture_output=True, text=True, encoding="utf-8", timeout=20,
        )
        return result.returncode, result.stdout, result.stderr

    def test_shell_entrypoint_uses_the_same_order_as_python(self):
        saved = self.base / 'saved "quoted" 记忆'
        saved.mkdir()
        (self.default / "data-root").write_text(str(saved) + "\n", encoding="utf-8")
        for arguments, environment, expected in (
            ((), None, (str(saved), "saved")),
            ((), {"AUDAY_HOME": "/env/root"}, ("/env/root", "environment")),
            (("--home", "/explicit/root"), {"AUDAY_HOME": "/env/root"}, ("/explicit/root", "option")),
        ):
            with self.subTest(arguments=arguments, environment=environment):
                code, stdout, stderr = self.check(*arguments, environment=environment)
                self.assertEqual(code, 0, stderr)
                payload = json.loads(stdout)
                self.assertEqual((payload["dataRoot"], payload["dataRootSource"]), expected)
                self.assertFalse(payload["downloadsStarted"])
        (self.default / "data-root").unlink()
        code, stdout, _ = self.check()
        self.assertEqual(json.loads(stdout)["dataRootSource"], "default")

    def test_missing_saved_root_stops_before_creating_anything(self):
        missing = self.base / "disconnected drive" / "auday"
        (self.default / "data-root").write_text(str(missing) + "\n", encoding="utf-8")
        code, stdout, stderr = self.check()
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("saved data root is unavailable", stderr)
        self.assertFalse(missing.parent.exists())


if __name__ == "__main__":
    unittest.main()
