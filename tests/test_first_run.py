"""First-use state and recovery, using synthetic files and no network/model downloads."""

import contextlib
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import first_run
import semantic_search
import setup_runtime


def digest(value):
    return hashlib.sha256(value).hexdigest()


class FirstRunTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="auday-first-run-test-")
        self.root = Path(self.temporary.name)
        self.profile = self.root / "default"
        self.profile.mkdir()
        self.model = self.root / "model.gguf"
        self.server = self.root / "llama-server"
        self.model.write_bytes(b"synthetic model")
        self.server.write_bytes(b"synthetic runtime")
        self.library = self.profile / "library.sqlite"
        with contextlib.closing(sqlite3.connect(self.library)) as db:
            db.execute("CREATE TABLE records(id TEXT PRIMARY KEY, text TEXT)")
            db.execute("INSERT INTO records VALUES(?,?)", ("synthetic-record", "Keep this local record."))
            db.commit()
        self.before_library = self.library.read_bytes()
        self.progress = patch.object(first_run, "report")
        self.progress.start()
        self.addCleanup(self.progress.stop)
        self.tools = patch.object(first_run, "command_line_tools_status", return_value={"ready": False})
        self.tools.start()
        self.addCleanup(self.tools.stop)
        self.addCleanup(self.temporary.cleanup)

    def write_configuration(self, profile, model, server):
        value = {"modelPath": str(model), "serverPath": str(server)}
        (profile / "semantic-config.json").write_text(json.dumps(value), encoding="utf-8")
        return value

    def create_verified_previous_setup(self):
        # Non-default whitespace is deliberate: rollback must preserve the
        # verification identity as well as the logical JSON configuration.
        config = self.profile / "semantic-config.json"
        previous = json.dumps({
            "serverPath": str(self.server), "modelPath": str(self.model), "version": "old",
        }, separators=(",", ":")).encode()
        config.write_bytes(previous)
        state = {
            "semanticChoice": "enabled",
            "semanticVerification": {"configSHA256": digest(previous), "at": "2026-10-01T00:00:00Z"},
            "unrelatedUserPreference": "preserve",
        }
        state_path = self.profile / "setup-state.json"
        state_path.write_text(json.dumps(state))
        return previous, state_path.read_bytes()

    def test_fresh_state_and_saved_deferral_do_not_repeat_recommendation(self):
        first = first_run.setup_status(self.root)
        self.assertTrue(first["recommendSemantic"])
        self.assertFalse(first["paired"])
        self.assertFalse(first["semantic"]["verifiedBySetup"])
        first_run.defer_semantic(self.root)
        for _ in range(2):
            status = first_run.setup_status(self.root)
            self.assertFalse(status["recommendSemantic"])
            self.assertEqual(status["semantic"]["choice"], "deferred")
        self.assertEqual(self.library.read_bytes(), self.before_library)
        self.assertFalse((self.profile / "semantic-config.json").exists())

    def test_defer_preserves_existing_setup_and_other_profile(self):
        previous, _ = self.create_verified_previous_setup()
        other = self.root / "second"
        other.mkdir()
        (other / "setup-state.json").write_text('{"semanticChoice":"enabled"}')
        before_other = (other / "setup-state.json").read_bytes()
        first_run.defer_semantic(self.root)
        status = first_run.setup_status(self.root)
        self.assertFalse(status["recommendSemantic"])
        self.assertTrue(status["semantic"]["verifiedBySetup"])
        self.assertEqual((self.profile / "semantic-config.json").read_bytes(), previous)
        self.assertEqual((other / "setup-state.json").read_bytes(), before_other)
        self.assertEqual(
            json.loads((self.profile / "setup-state.json").read_text(encoding="utf-8"))["unrelatedUserPreference"],
            "preserve",
        )

    def test_semantic_setup_is_ready_only_after_smoke_and_can_follow_deferral(self):
        first_run.defer_semantic(self.root)
        visited = []

        def smoke(config):
            visited.append(config)
            during = first_run.setup_status(self.root)
            self.assertFalse(during["semantic"]["verifiedBySetup"])
            self.assertEqual(during["semantic"]["choice"], "requested")
            self.assertEqual(self.library.read_bytes(), self.before_library)
            return {"checksPassed": 3, "syntheticDataOnly": True}

        with patch.object(first_run, "require_supported_system") as required, \
                patch.object(first_run, "install_runtime", return_value=self.server), \
                patch.object(first_run, "download_asset", return_value=self.model), \
                patch.object(semantic_search, "configure", side_effect=self.write_configuration), \
                patch.object(first_run, "_verify_semantic", side_effect=smoke):
            result = first_run.setup_semantic(self.root)
        self.assertEqual(len(visited), 1)
        required.assert_any_call("13.3")
        self.assertTrue(result["semanticReady"])
        self.assertFalse(result["phoneConnectedBySetup"])
        status = first_run.setup_status(self.root)
        self.assertTrue(status["semantic"]["verifiedBySetup"])
        self.assertEqual(status["semantic"]["choice"], "enabled")
        self.assertFalse(status["recommendSemantic"])
        self.assertFalse(status["paired"])
        self.assertEqual(self.library.read_bytes(), self.before_library)

    def test_failed_smoke_removes_new_configuration_and_preserves_library(self):
        first_run.defer_semantic(self.root)
        with patch.object(first_run, "require_supported_system"), \
                patch.object(first_run, "install_runtime", return_value=self.server), \
                patch.object(first_run, "download_asset", return_value=self.model), \
                patch.object(semantic_search, "configure", side_effect=self.write_configuration), \
                patch.object(first_run, "_verify_semantic", side_effect=ValueError("synthetic smoke failure")):
            with self.assertRaisesRegex(ValueError, "synthetic smoke failure"):
                first_run.setup_semantic(self.root)
        self.assertFalse((self.profile / "semantic-config.json").exists())
        self.assertEqual(first_run.setup_status(self.root)["semantic"]["choice"], "requested")
        self.assertFalse(first_run.setup_status(self.root)["recommendSemantic"])
        self.assertEqual(first_run.setup_status(self.root)["nextStep"], "resume_semantic_setup")
        self.assertEqual(self.library.read_bytes(), self.before_library)
        self.assertFalse(first_run.setup_status(self.root)["semantic"]["verifiedBySetup"])
        self.assertEqual(list(self.profile.glob(".setup-*")), [])

    def test_download_failure_remembers_request_without_reasking(self):
        with patch.object(first_run, "require_supported_system"), \
                patch.object(first_run, "install_runtime", side_effect=ValueError("network unavailable")):
            with self.assertRaisesRegex(ValueError, "network unavailable"):
                first_run.setup_semantic(self.root)
        status = first_run.setup_status(self.root)
        self.assertFalse(status["recommendSemantic"])
        self.assertEqual(status["nextStep"], "resume_semantic_setup")
        first_run.defer_semantic(self.root)
        self.assertEqual(first_run.setup_status(self.root)["nextStep"], "continue_the_users_task")

    def test_failed_smoke_restores_previous_verified_configuration_exactly(self):
        config, state = self.create_verified_previous_setup()
        index = self.profile / "semantic.sqlite"
        index.write_bytes(b"synthetic previous complete index")
        with patch.object(first_run, "require_supported_system"), \
                patch.object(first_run, "install_runtime", return_value=self.server), \
                patch.object(first_run, "download_asset", return_value=self.model), \
                patch.object(semantic_search, "configure", side_effect=self.write_configuration), \
                patch.object(first_run, "_verify_semantic", side_effect=ValueError("synthetic smoke failure")):
            with self.assertRaisesRegex(ValueError, "synthetic smoke failure"):
                first_run.setup_semantic(self.root)
        self.assertEqual((self.profile / "semantic-config.json").read_bytes(), config)
        self.assertEqual((self.profile / "setup-state.json").read_bytes(), state)
        self.assertEqual(self.library.read_bytes(), self.before_library)
        self.assertEqual(index.read_bytes(), b"synthetic previous complete index")
        self.assertTrue(first_run.setup_status(self.root)["semantic"]["verifiedBySetup"])
        self.assertEqual(list(self.profile.glob(".setup-*")), [])

    def test_semantic_os_gate_precedes_any_download_or_configuration(self):
        with patch.object(setup_runtime.sys, "platform", "darwin"), \
                patch.object(setup_runtime.platform, "mac_ver", return_value=("13.2", ("", "", ""), "")), \
                patch.object(first_run, "install_runtime") as installed, \
                patch.object(first_run, "download_asset") as downloaded, \
                patch.object(semantic_search, "configure") as configured:
            setup_runtime.require_macos("13.0")
            with self.assertRaisesRegex(ValueError, "requires_macos_13.3"):
                first_run.setup_semantic(self.root)
        installed.assert_not_called()
        downloaded.assert_not_called()
        configured.assert_not_called()
        self.assertEqual(self.library.read_bytes(), self.before_library)

    def test_model_download_failure_preserves_existing_configuration(self):
        config, state = self.create_verified_previous_setup()
        with patch.object(first_run, "require_supported_system"), \
                patch.object(first_run, "install_runtime", return_value=self.server), \
                patch.object(first_run, "download_asset", side_effect=ValueError("download_hash_mismatch")), \
                patch.object(semantic_search, "configure") as configured, \
                patch.object(first_run, "_verify_semantic") as verified:
            with self.assertRaisesRegex(ValueError, "download_hash_mismatch"):
                first_run.setup_semantic(self.root)
        configured.assert_not_called()
        verified.assert_not_called()
        self.assertEqual((self.profile / "semantic-config.json").read_bytes(), config)
        self.assertEqual((self.profile / "setup-state.json").read_bytes(), state)
        self.assertTrue(first_run.setup_status(self.root)["semantic"]["verifiedBySetup"])

    def test_status_cannot_claim_verification_for_edited_config_or_missing_file(self):
        self.create_verified_previous_setup()
        self.assertTrue(first_run.setup_status(self.root)["semantic"]["verifiedBySetup"])
        config = self.profile / "semantic-config.json"
        config.write_bytes(config.read_bytes() + b"\n")
        self.assertFalse(first_run.setup_status(self.root)["semantic"]["verifiedBySetup"])
        self.create_verified_previous_setup()
        self.server.unlink()
        status = first_run.setup_status(self.root)
        self.assertFalse(status["semantic"]["filesPresent"])
        self.assertFalse(status["semantic"]["verifiedBySetup"])

    def test_current_integrity_detects_changed_model_and_runtime_libraries(self):
        library = self.root / "libsynthetic.dll"
        library.write_bytes(b"original runtime dependency")
        value = {
            "modelPath": str(self.model), "serverPath": str(self.server),
            "runtimeSHA256": semantic_search._runtime_identity(self.server),
        }
        config = self.profile / "semantic-config.json"
        config.write_text(json.dumps(value), encoding="utf-8")
        (self.profile / "setup-state.json").write_text(json.dumps({
            "semanticChoice": "enabled",
            "semanticVerification": {"configSHA256": digest(config.read_bytes())},
        }))
        model_bytes = self.model.read_bytes()
        with patch.object(semantic_search, "MODEL_SHA", digest(model_bytes)):
            self.assertTrue(first_run.setup_status(self.root)["semantic"]["currentFilesVerified"])
            self.model.write_bytes(b"changed model")
            changed = first_run.setup_status(self.root)["semantic"]
            self.assertTrue(changed["verifiedBySetup"])
            self.assertFalse(changed["currentFilesVerified"])
            self.model.write_bytes(model_bytes)
            library.write_bytes(b"changed runtime dependency")
            self.assertFalse(first_run.setup_status(self.root)["semantic"]["currentFilesVerified"])
        self.assertEqual(library.read_bytes(), b"changed runtime dependency")
        self.assertEqual(self.library.read_bytes(), self.before_library)

    def test_synthetic_verification_requires_correct_retrieval_for_every_query(self):
        class SyntheticEncoder:
            def __init__(self, vectors):
                self.vectors = iter(vectors)

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def encode(self, _text):
                return next(self.vectors)

        basis = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        with patch.object(semantic_search, "LocalEncoder",
                          return_value=SyntheticEncoder(basis + basis)):
            result = first_run._verify_semantic({})
        self.assertEqual(result["checksPassed"], 3)
        self.assertTrue(result["syntheticDataOnly"])
        with patch.object(semantic_search, "LocalEncoder",
                          return_value=SyntheticEncoder(basis + [basis[0]] * 3)):
            with self.assertRaisesRegex(ValueError, "semantic_setup_verification_failed"):
                first_run._verify_semantic({})

    def test_windows_semantic_status_uses_supported_system_gate(self):
        with patch.object(setup_runtime.sys, "platform", "win32"), \
                patch.object(setup_runtime, "windows_native_architecture", return_value="x86_64"), \
                patch.object(setup_runtime.sys, "getwindowsversion", create=True, return_value=SimpleNamespace(major=10)):
            status = first_run.setup_status(self.root)
        self.assertTrue(status["semantic"]["supported"])
        self.assertTrue(status["recommendSemantic"])
        self.assertGreater(status["semantic"]["runtimeArchiveBytes"], 0)

    def test_windows_arm64_semantic_status_selects_native_assets(self):
        with patch.object(setup_runtime.sys, "platform", "win32"), \
                patch.object(setup_runtime, "windows_native_architecture", return_value="arm64"), \
                patch.object(setup_runtime.sys, "getwindowsversion", create=True, return_value=SimpleNamespace(major=10, build=26100)):
            status = first_run.setup_status(self.root)
            expected = setup_runtime.runtime_asset("llama", "arm64", "windows")["size"]
        self.assertTrue(status["semantic"]["supported"])
        self.assertTrue(status["recommendSemantic"])
        self.assertEqual(status["semantic"]["runtimeArchiveBytes"], expected)

    def test_unsupported_system_status_does_not_select_runtime(self):
        with patch.object(first_run, "require_supported_system", side_effect=ValueError("unsupported")), \
                patch.object(first_run, "runtime_asset") as selected:
            status = first_run.setup_status(self.root)
        selected.assert_not_called()
        self.assertFalse(status["semantic"]["supported"])
        self.assertFalse(status["recommendSemantic"])
        self.assertIsNone(status["semantic"]["runtimeArchiveBytes"])

    def test_managed_python_recognizes_only_private_python_or_its_exact_link_venv(self):
        private = self.root / "runtime/python/cpython-private"
        link_python = self.root / "runtime/windows-link/venv/Scripts/python.exe"
        cases = (
            (private / "python.exe", private, True),
            (link_python, private, True),
            (link_python, self.root / "global-python", False),
            (self.root / "runtime/other/python.exe", private, False),
        )
        for executable, base_prefix, expected in cases:
            with self.subTest(executable=executable, base_prefix=base_prefix), \
                    patch.object(first_run.sys, "executable", str(executable)), \
                    patch.object(first_run.sys, "base_prefix", str(base_prefix)), \
                    patch.object(first_run, "venv_python", return_value=link_python):
                self.assertEqual(first_run._python_managed_by_setup(self.root), expected)

    def test_audio_model_recommendation_uses_portable_memory_query(self):
        for memory, expected in ((0, "small"), (8 * 1024**3, "small"), (16 * 1024**3, "turbo")):
            with patch.object(first_run, "physical_memory_bytes", return_value=memory):
                self.assertEqual(first_run.recommended_audio_model(), expected)

    def test_unicode_profile_paths_round_trip_as_utf8(self):
        root = self.root / "Auday 数据 日本語"
        first_run.defer_semantic(root)
        status = first_run.setup_status(root)
        self.assertEqual(status["semantic"]["choice"], "deferred")
        marker = root / "default" / "semantic-config.json"
        first_run._save(marker, {"modelPath": str(root / "模型.gguf")})
        self.assertEqual(first_run._load(marker)["modelPath"], str(root / "模型.gguf"))

    def test_invalid_profiles_cannot_write_outside_selected_data_root(self):
        for profile in ("../escape", "/absolute", "", "a/b", "a" * 65):
            with self.subTest(profile=profile):
                with self.assertRaisesRegex(ValueError, "invalid_profile_name"):
                    first_run.defer_semantic(self.root, profile)
        self.assertEqual(self.library.read_bytes(), self.before_library)
        self.assertFalse((self.root.parent / "escape").exists())


if __name__ == "__main__":
    unittest.main()
