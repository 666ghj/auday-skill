"""Isolated setup contracts. Real downloads are separate, explicit checks."""

import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import audio_setup


class AudioSetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.runtime = self.root / "runtime/audio"
        self.venv = self.runtime / "venv/bin"
        self.venv.mkdir(parents=True)
        self.python = self.venv / "python"
        self.ffmpeg = self.venv / "ffmpeg"
        self.cmake = self.venv / "cmake"
        build = "build-" + audio_setup.MANIFEST["whisperSource"]["revision"]
        self.whisper = self.runtime / build / "bin/whisper-cli"
        self.whisper.parent.mkdir(parents=True)
        for path in (self.python, self.ffmpeg, self.cmake, self.whisper):
            path.write_bytes(b"test-runtime")
        source = self.runtime / audio_setup.MANIFEST["whisperSource"]["directory"]
        source.mkdir()
        (source / "CMakeLists.txt").touch()
        self.downloads = []

        def download(url, destination, checksum, **kwargs):
            destination = Path(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"synthetic-model")
            self.downloads.append((url, destination, checksum, kwargs))
            return destination

        self.shared = SimpleNamespace(
            ensure_uv=Mock(return_value=self.runtime / "uv"),
            managed_env=Mock(return_value={"PATH": "/usr/bin:/bin"}),
            download_asset=Mock(side_effect=download),
            extract_tar=Mock(),
        )
        self.patches = [
            patch.dict(sys.modules, {"setup_runtime": self.shared}),
            patch.object(audio_setup.platform, "system", return_value="Darwin"),
            patch.object(audio_setup.platform, "machine", return_value="arm64"),
            patch.object(audio_setup, "_check_build_tools"),
            patch.object(audio_setup, "_command"),
            patch.object(audio_setup, "_probe_python",
                         side_effect=[str(self.ffmpeg), str(self.cmake)]),
            patch.object(audio_setup, "_run_transcription",
                         return_value={"ok": True, "segments": 1, "speakerCount": 1}),
        ]
        self.mocks = [p.start() for p in self.patches]
        for p in self.patches:
            self.addCleanup(p.stop)

    def test_fresh_default_installs_one_asr_and_both_speaker_models(self):
        result = audio_setup.setup_audio(self.root)
        self.assertTrue(result["installed"])
        self.assertTrue(result["diarizationReady"])
        self.assertEqual(len(self.downloads), 3)
        self.assertIn("small-q5_1", self.downloads[0][0])
        self.assertNotIn("turbo", " ".join(x[0] for x in self.downloads))
        command = self.mocks[4].call_args[0][0]
        self.assertEqual(command[-2:], ["--extra", "diarization"])
        self.assertIn("--managed-python", command)
        self.assertEqual(
            self.mocks[4].call_args[0][2]["UV_PROJECT_ENVIRONMENT"],
            str(self.runtime / "venv"),
        )
        self.assertTrue(self.mocks[6].call_args.kwargs["diarize"])
        self.assertNotIn("Homebrew", str(command))

    def test_explicit_opt_out_skips_speaker_downloads_and_dependencies(self):
        result = audio_setup.setup_audio(self.root, diarize=False)
        self.assertTrue(result["installed"])
        self.assertFalse(result["diarizationReady"])
        self.assertEqual(len(self.downloads), 1)
        self.assertNotIn("--extra", self.mocks[4].call_args[0][0])
        self.assertFalse(self.mocks[6].call_args.kwargs["diarize"])
        config = json.loads((self.runtime / "config.json").read_text())
        self.assertNotIn("segmentationModel", config)
        self.assertNotIn("embeddingModel", config)

    def test_turbo_choice_does_not_download_small_model(self):
        audio_setup.setup_audio(self.root, model="turbo", diarize=False)
        self.assertEqual(len(self.downloads), 1)
        self.assertIn("turbo-q5_0", self.downloads[0][0])

    def test_failed_verification_does_not_advertise_ready_state(self):
        self.mocks[6].return_value = {"ok": True, "segments": 0}
        with self.assertRaisesRegex(RuntimeError, "no_transcript"):
            audio_setup.setup_audio(self.root, diarize=False)
        self.assertFalse((self.runtime / "config.json").exists())
        self.assertFalse(audio_setup.audio_status(self.root)["installed"])

    def test_missing_tools_fail_before_model_or_runtime_downloads(self):
        self.mocks[3].side_effect = RuntimeError("apple_command_line_tools_required")
        with self.assertRaisesRegex(RuntimeError, "apple_command_line_tools"):
            audio_setup.setup_audio(self.root)
        self.shared.ensure_uv.assert_not_called()
        self.shared.download_asset.assert_not_called()
        self.mocks[4].assert_not_called()

    def test_status_is_read_only_and_does_not_initialize(self):
        untouched = self.root / "empty"
        result = audio_setup.audio_status(untouched)
        self.assertFalse(result["installed"])
        self.assertFalse(untouched.exists())
        self.shared.ensure_uv.assert_not_called()

    def test_malformed_config_does_not_break_status(self):
        for payload in ([], None, {"schemaVersion": 1, "python": None}):
            (self.runtime / "config.json").write_text(json.dumps(payload))
            self.assertFalse(audio_setup.audio_status(self.root)["installed"])

    def test_native_environment_excludes_user_compiler_overrides(self):
        env = audio_setup._native_environment({
            "CC": "/custom/clang", "CXX": "/custom/clang++",
            "CMAKE_TOOLCHAIN_FILE": "/custom/toolchain",
            "CMAKE_GENERATOR": "Ninja", "PATH": "/custom/bin",
            "CPATH": "/custom/headers",
        })
        self.assertEqual(env["CC"], "/usr/bin/clang")
        self.assertEqual(env["CXX"], "/usr/bin/clang++")
        self.assertNotIn("/custom", str(env))

    def test_transcribe_refuses_missing_default_speaker_setup_without_download(self):
        audio_setup.setup_audio(self.root, diarize=False)
        self.shared.download_asset.reset_mock()
        with self.assertRaisesRegex(RuntimeError, "speaker_setup_required"):
            audio_setup.transcribe(self.root, self.root / "sample.wav", self.root / "out")
        self.shared.download_asset.assert_not_called()

    def test_explicit_transcription_opt_out_uses_prepared_runtime(self):
        audio_setup.setup_audio(self.root, diarize=False)
        self.mocks[6].reset_mock()
        self.shared.download_asset.reset_mock()
        audio_setup.transcribe(
            self.root, self.root / "sample.wav", self.root / "out",
            language="zh", diarize=False,
        )
        self.assertFalse(self.mocks[6].call_args.kwargs["diarize"])
        self.assertEqual(self.mocks[6].call_args.kwargs["language"], "zh")
        self.shared.download_asset.assert_not_called()

    def test_manifests_pin_remote_bytes_and_identify_licenses(self):
        manifest = audio_setup.MANIFEST
        assets = [manifest["whisperSource"],
                  *manifest["models"].values(), *manifest["diarization"].values()]
        for asset in assets:
            self.assertRegex(asset["sha256"], r"^[a-f0-9]{64}$")
            self.assertGreater(asset["size"], 0)
            self.assertTrue(asset["url"].startswith("https://"))
            self.assertNotIn("/main/", asset["url"])
            self.assertTrue(asset["license"])
            self.assertTrue(asset["licenseURL"].startswith("https://"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
