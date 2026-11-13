"""One persistent, visible archive location shared by every entrypoint.

Every test runs against an isolated user home (HOME, USERPROFILE and
LOCALAPPDATA), never this computer's real Auday data.
"""

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
import audio_setup
import data_root
import platform_utils
import setup_runtime
from test_incremental_sync import IncrementalPeer, record


def isolated_environment(user):
    environment = {key: value for key, value in os.environ.items()
                   if key not in (data_root.ENVIRONMENT_VARIABLE, platform_utils.RUNTIME_ROOT_VARIABLE,
                                  "PYTHONPATH", "PYTHONHOME")}
    environment.update(HOME=str(user), USERPROFILE=str(user), LOCALAPPDATA=str(user / "local"),
                       AUDAY_PROGRESS="0", PYTHONUTF8="1")
    return environment


class DataRootTestCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="auday-data-root-记忆 ")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.user = self.base / "user"
        self.user.mkdir()
        environment = patch.dict(os.environ, isolated_environment(self.user), clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.application = platform_utils.default_home().resolve()
        self.visible = (self.user / "Auday").resolve()
        self.workspace = self.base / "workspace" / "auday"

    def synced_library(self, home, paired=True):
        app = Auday(home, transport=IncrementalPeer([record("one"), record("two")]))
        try:
            app.sync()
            if paired:
                atomic_json(app.root / "connection.json", {"host": "192.0.2.1", "port": 1})
        finally:
            app.close()


class DefaultLocationTests(DataRootTestCase):
    def test_new_installation_uses_a_visible_folder_and_keeps_runtimes_hidden(self):
        self.assertEqual(data_root.resolve_home(), (self.visible, "default"))
        self.assertEqual(self.visible, (self.user / "Auday").resolve())
        self.assertNotEqual(self.visible, self.application)
        self.assertEqual(platform_utils.support_root(self.visible), self.application)
        self.assertEqual(platform_utils.support_root(self.application), self.application)
        self.assertEqual(platform_utils.support_root(self.workspace), self.workspace.resolve())
        environment = setup_runtime.managed_env(self.visible)
        self.assertTrue(Path(environment["UV_PYTHON_INSTALL_DIR"]).is_relative_to(self.application))
        self.assertTrue(Path(setup_runtime.managed_env(self.workspace)["UV_CACHE_DIR"]).is_relative_to(self.workspace.resolve()))
        _, runtime, models = audio_setup._paths(self.visible)
        self.assertTrue(runtime.is_relative_to(self.application) and models.is_relative_to(self.application))

    def test_existing_installation_keeps_its_archive_until_the_user_moves_it(self):
        self.synced_library(self.application)
        self.assertEqual(data_root.resolve_home(), (self.application, "legacy"))
        self.assertEqual(platform_utils.support_root(self.application), self.application)
        # Choosing the visible folder is explicit, and is then remembered.
        data_root.save_home(self.visible)
        self.assertEqual(data_root.resolve_home(), (self.visible, "saved"))
        data_root.clear_home()
        self.synced_library(self.visible)
        self.assertEqual(data_root.resolve_home(), (self.visible, "default"))

    def test_saved_locations_hold_only_the_archive_unless_they_already_have_a_runtime(self):
        usb = self.base / "USB drive" / "Auday"
        data_root.save_home(usb)
        self.assertEqual(platform_utils.support_root(usb), self.application)
        self.assertTrue(Path(setup_runtime.managed_env(usb)["UV_PYTHON_INSTALL_DIR"]).is_relative_to(self.application))
        # A root given only for one command (an isolated test root) is self-contained.
        isolated = self.base / "isolated"
        self.assertEqual(platform_utils.support_root(isolated), isolated.resolve())
        # Earlier versions kept runtimes inside a custom root; keep using them.
        (self.workspace / "runtime" / "python").mkdir(parents=True)
        data_root.save_home(self.workspace)
        self.assertEqual(platform_utils.support_root(self.workspace), self.workspace.resolve())

    def test_a_phone_profile_named_runtime_does_not_move_the_runtimes(self):
        Auday(self.visible, profile="runtime").close()
        self.assertTrue((self.visible / "runtime" / "library.sqlite").is_file())
        self.assertEqual(platform_utils.support_root(self.visible), self.application)

    def test_the_entrypoint_decision_is_followed_instead_of_derived_again(self):
        chosen = self.base / "decided by the entrypoint"
        with patch.dict(os.environ, {platform_utils.RUNTIME_ROOT_VARIABLE: str(chosen)}):
            for root in (self.visible, self.workspace, self.application):
                self.assertEqual(platform_utils.support_root(root), chosen.resolve())
            self.assertTrue(Path(setup_runtime.managed_env(self.visible)["UV_CACHE_DIR"]).is_relative_to(chosen.resolve()))
        with patch.dict(os.environ, {platform_utils.RUNTIME_ROOT_VARIABLE: "relative"}):
            self.assertEqual(platform_utils.support_root(self.visible), self.application)

    def test_saving_a_location_leaves_the_users_folder_permissions_alone(self):
        shared = self.base / "folder the user already shares"
        shared.mkdir(mode=0o755)
        before = shared.stat().st_mode
        with patch.object(data_root, "secure_directory", wraps=platform_utils.secure_directory) as protect:
            data_root.save_home(shared)
        protect.assert_called_once_with(self.application)
        self.assertEqual(shared.stat().st_mode, before)
        self.assertEqual(data_root.resolve_home(), (shared.resolve(), "saved"))

    def test_runtime_alone_at_the_old_location_does_not_keep_it(self):
        (self.application / "runtime" / "python").mkdir(parents=True)
        self.assertEqual(data_root.resolve_home(), (self.visible, "default"))


class ResolutionTests(DataRootTestCase):
    def test_option_then_environment_then_saved(self):
        self.workspace.mkdir(parents=True)
        data_root.save_home(self.workspace)
        self.assertEqual(data_root.resolve_home(), (self.workspace.resolve(), "saved"))
        environment = self.base / "from environment"
        with patch.dict(os.environ, {"AUDAY_HOME": str(environment)}):
            self.assertEqual(data_root.resolve_home(), (environment, "environment"))
            explicit = self.base / "one command"
            self.assertEqual(data_root.resolve_home(explicit), (explicit, "option"))

    def test_entrypoint_passing_the_configured_root_keeps_its_source(self):
        self.assertEqual(data_root.resolve_home(self.visible)[1], "default")
        self.workspace.mkdir(parents=True)
        data_root.save_home(self.workspace)
        self.assertEqual(data_root.resolve_home(self.workspace)[1], "saved")
        self.assertEqual(data_root.resolve_home(self.visible)[1], "option")

    def test_setting_is_one_absolute_line_and_saving_the_default_clears_it(self):
        self.workspace.mkdir(parents=True)
        data_root.save_home(self.workspace)
        self.assertEqual(data_root.setting_path().read_text(encoding="utf-8"), str(self.workspace.resolve()) + "\n")
        data_root.save_home(self.visible)
        self.assertFalse(data_root.setting_path().exists())

    def test_relative_environment_or_saved_root_is_rejected(self):
        with patch.dict(os.environ, {"AUDAY_HOME": "relative/root"}):
            with self.assertRaisesRegex(ValueError, "auday_home_must_be_an_absolute"):
                data_root.resolve_home()
        self.application.mkdir(parents=True)
        data_root.setting_path().write_text("relative/root\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "saved_data_root_must_be_absolute"):
            data_root.resolve_home()


class UserDecisionTests(DataRootTestCase):
    def test_missing_saved_root_asks_the_user_and_creates_nothing(self):
        self.workspace.mkdir(parents=True)
        data_root.save_home(self.workspace)
        self.workspace.rename(self.workspace.with_name("unplugged"))
        with self.assertRaises(data_root.DecisionRequired) as raised:
            data_root.resolve_home()
        payload = raised.exception.payload()
        self.assertTrue(payload["needsUserDecision"])
        self.assertEqual(payload["error"], "saved_data_root_unavailable")
        self.assertEqual(payload["dataRoot"], str(self.workspace.resolve()))
        self.assertEqual([choice["choice"] for choice in payload["choices"]],
                         ["reconnect_and_retry", "use_another_location", "return_to_default_location"])
        self.assertFalse(self.workspace.exists())
        explicit = self.base / "explicit"
        self.assertEqual(data_root.resolve_home(explicit), (explicit, "option"))

    def test_pairing_an_empty_root_asks_while_the_profile_lives_elsewhere(self):
        self.synced_library(self.visible)
        Auday(self.workspace).close()  # an opened, empty database is not a library
        with self.assertRaises(data_root.DecisionRequired) as raised:
            data_root.ensure_single_library(self.workspace, "default")
        payload = raised.exception.payload()
        self.assertEqual(payload["error"], "another_library_exists")
        self.assertEqual(payload["otherLibraries"][0]["dataRoot"], str(self.visible))
        self.assertEqual([choice["choice"] for choice in payload["choices"]],
                         ["use_existing_library", "start_separate_library"])
        data_root.ensure_single_library(self.workspace, "default", allow_new=True)
        data_root.ensure_single_library(self.workspace, "second-phone")
        data_root.ensure_single_library(self.visible, "default")

    def test_upgraded_installation_is_protected_from_a_second_library(self):
        self.synced_library(self.application)
        with self.assertRaises(data_root.DecisionRequired):
            data_root.ensure_single_library(self.visible, "default")

    def test_a_synchronized_but_unpaired_copy_counts(self):
        self.synced_library(self.visible, paired=False)
        self.assertEqual(
            data_root.other_libraries(self.workspace, "default"),
            [{"dataRoot": str(self.visible), "profile": "default", "paired": False, "synchronized": True}],
        )


class CommandLineTests(DataRootTestCase):
    """The issue's sequence through the real CLI with the isolated home."""

    def run_cli(self, *arguments):
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "auday.py"), *arguments],
            env=isolated_environment(self.user), capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        return result.returncode, json.loads(result.stdout.strip().splitlines()[-1])

    def test_new_installation_reports_the_visible_location(self):
        code, status = self.run_cli("status")
        self.assertEqual((code, status["dataRoot"], status["dataRootSource"]), (0, str(self.visible), "default"))
        self.assertTrue((self.visible / "default").is_dir())
        self.assertFalse((self.visible / "runtime").exists())
        code, location = self.run_cli("data-root")
        self.assertEqual((location["runtimeRoot"], location["defaultDataRoot"]), (str(self.application), str(self.visible)))

    def test_saved_root_is_used_by_later_commands_and_a_second_pairing_asks(self):
        self.synced_library(self.workspace)
        code, saved = self.run_cli("--home", str(self.workspace), "data-root", "--save")
        self.assertEqual(code, 0)
        self.assertEqual(saved["withoutHomeOption"]["source"], "saved")
        self.assertEqual(saved["profileState"], {"paired": True, "synchronized": True})
        self.assertEqual(saved["runtimeRoot"], str(self.application))

        code, status = self.run_cli("status")
        self.assertEqual((code, status["dataRootSource"], status["cache"]["recordCount"]), (0, "saved", 2))

        code, override = self.run_cli("--home", str(self.base / "isolated"), "status")
        self.assertEqual((code, override["dataRootSource"], override["cache"]), (0, "option", {}))
        self.assertEqual(override["otherLibraries"][0]["dataRoot"], str(self.workspace.resolve()))

        untouched = self.base / "never opened"
        code, asked = self.run_cli("--home", str(untouched), "pair", "--host", "192.0.2.1", "--port", "1")
        self.assertEqual((code, asked["error"], asked["needsUserDecision"]), (1, "another_library_exists", True))
        code, unpaired = self.run_cli("--home", str(untouched), "sync")
        self.assertEqual((code, unpaired["error"], unpaired["needsUserDecision"]), (1, "not_paired_here", True))
        self.assertEqual(unpaired["dataRoot"], str(untouched.resolve()))
        self.assertFalse(untouched.exists(), "asking the user created a library")
        code, invalid = self.run_cli("--home", str(untouched), "--profile", "../escape", "sync")
        self.assertEqual((code, invalid["error"]), (1, "invalid_profile_name"))

    def test_disconnected_saved_root_asks_and_can_return_to_the_default(self):
        self.synced_library(self.workspace)
        self.run_cli("--home", str(self.workspace), "data-root", "--save")
        self.workspace.rename(self.workspace.with_name("unplugged"))
        code, asked = self.run_cli("status")
        self.assertEqual((code, asked["error"], asked["needsUserDecision"]), (1, "saved_data_root_unavailable", True))
        self.assertFalse(self.workspace.exists())
        code, cleared = self.run_cli("data-root", "--clear")
        self.assertEqual((code, cleared["source"], cleared["savedDataRoot"]), (0, "default", None))
        self.assertEqual(self.run_cli("status")[1]["dataRoot"], str(self.visible))

    def test_doctor_reports_where_the_archive_and_runtimes_live(self):
        code, report = self.run_cli("doctor")
        self.assertEqual(code, 0)
        self.assertEqual((report["dataRoot"], report["dataRootSource"], report["runtimeRoot"]),
                         (str(self.visible), "default", str(self.application)))


class NoHomeFolderTests(unittest.TestCase):
    """Service accounts and stripped environments may have no home folder at all."""

    def test_explicit_roots_still_work_and_defaults_report_a_clear_error(self):
        with tempfile.TemporaryDirectory(prefix="auday-no-home-") as folder, \
                patch.dict(os.environ, {"AUDAY_PROGRESS": "0"}), \
                patch.object(Path, "home", side_effect=RuntimeError("Could not determine home directory.")):
            for name in ("HOME", "USERPROFILE", "LOCALAPPDATA", "AUDAY_HOME"):
                os.environ.pop(name, None)
            root = Path(folder) / "explicit root"
            self.assertEqual(platform_utils.support_root(root), root.resolve())
            self.assertTrue(Path(setup_runtime.managed_env(root)["UV_CACHE_DIR"]).is_relative_to(root.resolve()))
            self.assertEqual(data_root.resolve_home(root), (root, "option"))
            with self.assertRaisesRegex(ValueError, "^home_directory_unavailable"):
                data_root.resolve_home()
            data_root.ensure_single_library(root, "default")
            described = data_root.describe(root, "option", "default")
            self.assertIsNone(described["defaultDataRoot"])
            self.assertEqual(described["withoutHomeOption"]["error"], "home_directory_unavailable")
            Auday(root).close()


@unittest.skipUnless(sys.platform == "darwin", "bootstrap.sh is the macOS system-shell entrypoint")
class ShellEntrypointTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="auday-shell-root-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.home = self.base / "user"
        self.application = self.home / "Library" / "Application Support" / "AudayWorkflow"
        self.application.mkdir(parents=True)

    def bootstrap(self, *arguments, environment=None):
        result = subprocess.run(
            ["/bin/sh", str(SCRIPTS / "bootstrap.sh"), *arguments],
            env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(self.home), **(environment or {})},
            capture_output=True, text=True, encoding="utf-8", timeout=20,
        )
        return result.returncode, json.loads(result.stdout) if result.stdout.strip() else None, result.stderr

    def located(self, *arguments, environment=None):
        code, payload, stderr = self.bootstrap(*arguments, "check", environment=environment)
        self.assertEqual(code, 0, stderr)
        self.assertFalse(payload["downloadsStarted"])
        return payload["dataRoot"], payload["dataRootSource"]

    def test_shell_entrypoint_uses_the_same_order_as_python(self):
        self.assertEqual(self.located(), (str(self.home / "Auday"), "default"))
        (self.application / "default").mkdir()
        (self.application / "default" / "connection.json").write_text("{}", encoding="utf-8")
        self.assertEqual(self.located(), (str(self.application), "legacy"))
        saved = self.base / 'saved "quoted" 记忆'
        saved.mkdir()
        (self.application / "data-root").write_text(str(saved) + "\n", encoding="utf-8")
        self.assertEqual(self.located(), (str(saved), "saved"))
        self.assertEqual(self.located(environment={"AUDAY_HOME": "/env/root"}), ("/env/root", "environment"))
        self.assertEqual(self.located("--home", "/explicit/root", environment={"AUDAY_HOME": "/env/root"}),
                         ("/explicit/root", "option"))
        self.assertEqual(self.located("--home", "/explicit/root", "--profile", "phone"), ("/explicit/root", "option"))
        self.assertFalse((self.home / "Auday").exists())

    def test_missing_saved_root_asks_creates_nothing_and_can_be_cleared_without_a_runtime(self):
        missing = self.base / "disconnected drive" / "auday"
        (self.application / "data-root").write_text(str(missing) + "\n", encoding="utf-8")
        code, payload, _ = self.bootstrap("status")
        self.assertEqual((code, payload["error"], payload["needsUserDecision"]), (1, "saved_data_root_unavailable", True))
        self.assertEqual(payload["dataRoot"], str(missing))
        self.assertEqual(len(payload["choices"]), 3)
        self.assertFalse(missing.parent.exists())
        code, payload, _ = self.bootstrap("--profile", "phone", "sync")
        self.assertEqual((code, payload["error"]), (1, "saved_data_root_unavailable"))
        code, cleared, stderr = self.bootstrap("--profile", "phone", "data-root", "--clear")
        self.assertEqual((code, cleared["savedDataRoot"]), (0, None), stderr)
        self.assertFalse((self.application / "data-root").exists())
        (self.application / "data-root").write_text(str(missing) + "\n", encoding="utf-8")
        code, cleared, stderr = self.bootstrap("data-root", "--clear")
        self.assertEqual((code, cleared["savedDataRoot"]), (0, None), stderr)
        self.assertFalse((self.application / "data-root").exists())
        self.assertFalse((self.application / "runtime").exists())
        self.assertEqual(self.located(), (str(self.home / "Auday"), "default"))

    def fake_runtime(self, root):
        """A stand-in uv and Python, so the real script runs to its final exec offline."""
        with (SCRIPTS / "runtime-assets.tsv").open(encoding="utf-8") as manifest:
            rows = [line.rstrip("\n").split("\t") for line in manifest]
        for row in rows:
            if row[0] == "uv" and row[9] == "macos":
                directory = root / "runtime" / row[5]
                (directory / row[6]).mkdir(parents=True)
                (directory / ".auday-archive-sha256").write_text(row[3] + "\n", encoding="utf-8")
                uv = directory / row[6] / row[7]
                uv.write_text('#!/bin/sh\nprintf \'%s\\n\' "$UV_PYTHON_INSTALL_DIR/fake/bin/python3"\n', encoding="utf-8")
                uv.chmod(0o755)
        python = root / "runtime" / "python" / "fake" / "bin" / "python3"
        python.parent.mkdir(parents=True)
        # Passes the version probe, then reports what the client would receive.
        python.write_text('#!/bin/sh\n[ "$1" = -I ] && exit 0\nshift\nprintf \'%s\\n\' "$AUDAY_RUNTIME_ROOT" "$@"\n',
                          encoding="utf-8")
        python.chmod(0o755)

    def launched(self, *arguments):
        """(runtime root, client arguments) the real entrypoint hands to the client."""
        result = subprocess.run(
            ["/bin/sh", str(SCRIPTS / "bootstrap.sh"), *arguments],
            # An unreachable proxy keeps a wrong decision from downloading a real runtime.
            env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(self.home),
                 "https_proxy": "http://127.0.0.1:9", "AUDAY_RUNTIME_ROOT": "/stale/value"},
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        return lines[0], lines[1:]

    def test_entrypoint_keeps_runtimes_out_of_archive_locations_and_tells_the_client(self):
        self.fake_runtime(self.application)
        visible, application = str((self.home / "Auday").resolve()), str(self.application.resolve())
        self.assertEqual(self.launched(), (application, ["--home", visible, "setup"]))
        # A phone profile that happens to be called runtime is not a runtime.
        (self.home / "Auday" / "runtime").mkdir()
        self.assertEqual(self.launched("--profile", "runtime", "status"),
                         (application, ["--home", visible, "--profile", "runtime", "status"]))
        # Choosing a new location, with or without --profile, installs nothing inside it.
        for prefix in ((), ("--profile", "phone")):
            chosen = self.base / ("external drive " + str(len(prefix))) / "Auday"
            runtime, forwarded = self.launched("--home", str(chosen), *prefix, "data-root", "--save")
            self.assertEqual(runtime, application)
            self.assertEqual(forwarded, ["--home", str(chosen.resolve()), *prefix, "data-root", "--save"])
            self.assertEqual(list(chosen.iterdir()), [])
            # Once saved, later commands keep the archive there and the runtime hidden.
            (self.application / "data-root").write_text(str(chosen) + "\n", encoding="utf-8")
            self.assertEqual(self.launched("status"), (application, ["--home", str(chosen.resolve()), "status"]))
            (self.application / "data-root").unlink()

    def test_entrypoint_keeps_a_root_that_already_has_its_own_runtime(self):
        own = self.base / "self contained"
        self.fake_runtime(own)
        (self.application / "data-root").write_text(str(own) + "\n", encoding="utf-8")
        self.assertEqual(self.launched("status"), (str(own.resolve()), ["--home", str(own.resolve()), "status"]))


if __name__ == "__main__":
    unittest.main()
