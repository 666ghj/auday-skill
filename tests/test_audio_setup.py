"""Isolated setup contracts. Real downloads are separate, explicit checks."""

import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import tarfile
import subprocess
import zipfile
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
        (source / "LICENSE").write_text("Synthetic test license")
        (source / "samples").mkdir()
        (source / "samples/jfk.wav").write_bytes(b"synthetic sample")
        self.downloads = []
        self.verification_reply = {"ok": True, "segments": 1, "speakerCount": 1}
        self.verification_text = "Ask what you can do for your country."

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
            managed_python_version=Mock(return_value="3.12.15"),
        )

        def run_transcription(config, audio, output, **kwargs):
            output = Path(output)
            output.mkdir(parents=True, exist_ok=True)
            (output / "result.json").write_text(json.dumps({
                "segments": [{"text": self.verification_text}],
            }))
            return self.verification_reply

        self.patches = [
            patch.dict(sys.modules, {"setup_runtime": self.shared}),
            patch.object(audio_setup.platform, "system", return_value="Darwin"),
            patch.object(audio_setup.platform, "machine", return_value="arm64"),
            patch.object(audio_setup, "_check_build_tools"),
            patch.object(audio_setup, "_command"),
            patch.object(audio_setup, "_probe_python",
                         side_effect=[str(self.ffmpeg), str(self.cmake)]),
            patch.object(audio_setup, "_run_transcription", side_effect=run_transcription),
            patch.object(audio_setup, "venv_python", return_value=self.python),
            patch.object(audio_setup, "_check_windows_audio_cpu"),
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
        self.verification_reply = {"ok": True, "segments": 0}
        with self.assertRaisesRegex(RuntimeError, "no_transcript"):
            audio_setup.setup_audio(self.root, diarize=False)
        self.assertFalse((self.runtime / "config.json").exists())
        self.assertFalse(audio_setup.audio_status(self.root)["installed"])

    def test_wrong_sample_words_do_not_advertise_ready_state(self):
        self.verification_text = "Unrelated transcription"
        with self.assertRaisesRegex(RuntimeError, "expected_sample_words_missing"):
            audio_setup.setup_audio(self.root, diarize=False)
        self.assertFalse((self.runtime / "config.json").exists())

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
            "CPATH": "/custom/headers", "MAKEFLAGS": "CC=/custom/clang",
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
        assets = [manifest["whisperSource"], manifest["whisperWindows"],
                  manifest["windowsRuntime"],
                  *manifest["models"].values(), *manifest["diarization"].values()]
        for asset in assets:
            self.assertRegex(asset["sha256"], r"^[a-f0-9]{64}$")
            self.assertGreater(asset["size"], 0)
            self.assertTrue(asset["url"].startswith("https://"))
            self.assertNotIn("/main/", asset["url"])
            self.assertTrue(asset["license"])
            self.assertTrue(asset["licenseURL"].startswith("https://"))

    def test_windows_uses_private_prebuilt_and_default_diarization(self):
        self.shared.managed_python_version.return_value = "3.13.16"
        win_python = self.runtime / "venv/Scripts/python.exe"
        win_python.parent.mkdir(parents=True)
        win_python.write_bytes(b"synthetic-python")
        with patch.object(audio_setup.platform, "system", return_value="Windows"), \
                patch.object(audio_setup.platform, "machine", return_value="AMD64"), \
                patch.object(audio_setup, "venv_python", return_value=win_python), \
                patch.object(audio_setup, "_prepare_windows_whisper", return_value=self.whisper) as install:
            result = audio_setup.setup_audio(self.root)
        self.assertTrue(result["installed"])
        self.assertTrue(result["diarizationReady"])
        self.mocks[3].assert_not_called()
        install.assert_called_once()
        command = self.mocks[4].call_args[0][0]
        self.assertIn("3.13.16", command)
        self.assertIn("--no-build", command)
        self.assertIn("diarization", command)
        self.assertIn("ffmpeg-win*.exe", self.mocks[5].call_args[0][1])
        self.mocks[5].assert_called_once()
        config = json.loads((self.runtime / "config.json").read_text())
        self.assertEqual(config["python"], str(win_python))
        self.assertEqual(config["platform"], "windows-x86_64")
        self.assertEqual(config["engineArchiveSHA256"], audio_setup.MANIFEST["whisperWindows"]["sha256"])

    def test_windows_explicit_opt_out_installs_no_speaker_models_or_packages(self):
        with patch.object(audio_setup.platform, "system", return_value="Windows"), \
                patch.object(audio_setup.platform, "machine", return_value="AMD64"), \
                patch.object(audio_setup, "_prepare_windows_whisper", return_value=self.whisper):
            result = audio_setup.setup_audio(self.root, diarize=False)
        self.assertTrue(result["installed"])
        self.assertFalse(result["diarizationReady"])
        self.assertEqual(len(self.downloads), 1)
        self.assertNotIn("--extra", self.mocks[4].call_args[0][0])
        self.mocks[3].assert_not_called()

    def test_windows_unsupported_audio_cpu_stops_before_downloads(self):
        self.mocks[8].side_effect = RuntimeError("windows_audio_requires_avx2_cpu")
        with patch.object(audio_setup.platform, "system", return_value="Windows"), \
                patch.object(audio_setup.platform, "machine", return_value="AMD64"):
            with self.assertRaisesRegex(RuntimeError, "audio_requires_avx2"):
                audio_setup.setup_audio(self.root)
        self.shared.ensure_uv.assert_not_called()
        self.shared.download_asset.assert_not_called()
        self.mocks[4].assert_not_called()

    def test_unsupported_host_stops_before_installation(self):
        with patch.object(audio_setup.platform, "system", return_value="Windows"), \
                patch.object(audio_setup.platform, "machine", return_value="ARM64"):
            with self.assertRaisesRegex(RuntimeError, "requires_macos_or_windows_x64"):
                audio_setup.setup_audio(self.root)
        self.shared.ensure_uv.assert_not_called()
        self.shared.download_asset.assert_not_called()


class WindowsEngineInstallationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "LICENSE").write_text("Synthetic MIT license")
        self.runtime = self.root / "runtime/audio"
        self.runtime.mkdir(parents=True)
        self.manifest = json.loads(json.dumps(audio_setup.MANIFEST))
        self.archives = {}
        for label in ("whisperWindows", "windowsRuntime"):
            asset = self.manifest[label]
            archive = self.root / (label + ".zip")
            with zipfile.ZipFile(archive, "w") as output:
                for item in asset["files"]:
                    body = ("synthetic-" + item["filename"]).encode()
                    item["sha256"] = hashlib.sha256(body).hexdigest()
                    item["size"] = len(body)
                    output.writestr(item["member"], body)
                output.writestr("debug_nonredist/forbidden.dll", b"not installed")
            self.archives[asset["url"]] = archive
        def unpack(archive, destination):
            destination.mkdir()
            with zipfile.ZipFile(archive) as source:
                source.extractall(destination)
            return destination
        self.shared = SimpleNamespace(
            download_asset=Mock(side_effect=lambda url, *a, **kw: self.archives[url]),
            extract_zip=Mock(side_effect=unpack),
            file_hash=lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        )
        def install_crt(root, destination, *, openmp=False):
            self.assertTrue(openmp)
            self.assertTrue(destination.is_relative_to(root))
            asset = self.manifest["windowsRuntime"]
            with zipfile.ZipFile(self.archives[asset["url"]]) as archive:
                for item in asset["files"]:
                    (destination / item["filename"]).write_bytes(archive.read(item["member"]))
        self.crt = SimpleNamespace(ensure_windows_crt=Mock(side_effect=install_crt))
        self.patches = [patch.dict(sys.modules, {"setup_runtime": self.shared, "windows_crt": self.crt}),
                        patch.object(audio_setup, "MANIFEST", self.manifest)]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_installs_only_verified_release_allowlist_and_reuses_it(self):
        executable = audio_setup._prepare_windows_whisper(self.runtime, self.source)
        self.assertTrue(executable.is_file())
        destination = executable.parent.parent
        names = {path.name for path in executable.parent.iterdir()}
        expected = {item["filename"] for key in ("whisperWindows", "windowsRuntime")
                    for item in self.manifest[key]["files"]}
        self.assertEqual(names, expected)
        self.assertFalse(list(destination.rglob("forbidden.dll")))
        self.assertTrue((destination / "WHISPER-LICENSE").is_file())
        self.shared.download_asset.reset_mock()
        self.assertEqual(audio_setup._prepare_windows_whisper(self.runtime, self.source), executable)
        self.shared.download_asset.assert_not_called()

    def test_corrupt_existing_engine_is_preserved_without_downloading(self):
        executable = audio_setup._prepare_windows_whisper(self.runtime, self.source)
        dll = executable.parent / "vcomp140.dll"
        dll.write_bytes(b"changed")
        self.shared.download_asset.reset_mock()
        with self.assertRaisesRegex(ValueError, "existing_audio_engine_incomplete"):
            audio_setup._prepare_windows_whisper(self.runtime, self.source)
        self.assertEqual(dll.read_bytes(), b"changed")
        self.shared.download_asset.assert_not_called()

    def test_wrong_member_hash_never_promotes_installation(self):
        self.manifest["whisperWindows"]["files"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "file_verification_failed"):
            audio_setup._prepare_windows_whisper(self.runtime, self.source)
        self.assertFalse((self.runtime / self.manifest["whisperWindows"]["directory"]).exists())


class WindowsVerificationAssetTests(unittest.TestCase):
    def test_windows_extracts_only_license_and_sample_from_verified_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            archive = runtime / "source.tar.gz"
            prefix = audio_setup.MANIFEST["whisperSource"]["directory"]
            with tarfile.open(archive, "w:gz") as tar:
                for name, body in (("LICENSE", b"synthetic license"),
                                   ("samples/jfk.wav", b"synthetic audio"),
                                   ("unrelated/" + "long-" * 40 + ".cpp", b"unused source")):
                    member = tarfile.TarInfo(prefix + "/" + name)
                    member.size = len(body)
                    tar.addfile(member, io.BytesIO(body))
            shared = SimpleNamespace(download_asset=Mock(return_value=archive), extract_tar=Mock())
            with patch.dict(sys.modules, {"setup_runtime": shared}):
                source = audio_setup._ensure_source(runtime, windows=True)
                self.assertEqual((source / "samples/jfk.wav").read_bytes(), b"synthetic audio")
                self.assertFalse((source / "unrelated").exists())
                shared.extract_tar.assert_not_called()
                shared.download_asset.reset_mock()
                self.assertEqual(audio_setup._ensure_source(runtime, windows=True), source)
                shared.download_asset.assert_not_called()


class WindowsAudioCPUCheckTests(unittest.TestCase):
    def test_supported_avx2_uses_official_feature_id(self):
        library = Mock()
        library.IsProcessorFeaturePresent.return_value = 1
        with patch.object(audio_setup.ctypes, "WinDLL", create=True, return_value=library) as load:
            audio_setup._check_windows_audio_cpu()
        load.assert_called_once_with("kernel32", use_last_error=True)
        library.IsProcessorFeaturePresent.assert_called_once_with(40)

    def test_missing_avx2_error_keeps_text_features_available(self):
        library = Mock()
        library.IsProcessorFeaturePresent.return_value = 0
        with patch.object(audio_setup.ctypes, "WinDLL", create=True, return_value=library):
            with self.assertRaisesRegex(RuntimeError, "text synchronization and export remain available"):
                audio_setup._check_windows_audio_cpu()


class AudioProcessLifecycleTests(unittest.TestCase):
    def test_setup_timeout_stops_the_whole_process_tree(self):
        process = Mock()
        process.wait.side_effect = subprocess.TimeoutExpired("synthetic", 1)
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(audio_setup, "start_process", return_value=process) as start, \
                patch.object(audio_setup, "stop_process") as stop, \
                patch.object(audio_setup.time, "monotonic", side_effect=[0, 1, 1801]):
            with self.assertRaisesRegex(TimeoutError, "audio_setup_timed_out"):
                audio_setup._command(["synthetic"], Path(tmp) / "setup.log", {}, "test")
        start.assert_called_once()
        stop.assert_called_once_with(process)

    def test_transcription_timeout_stops_the_whole_process_tree(self):
        process = Mock()
        process.communicate.side_effect = subprocess.TimeoutExpired("synthetic", 21600)
        config = {"python": "python", "model": "model", "ffmpeg": "ffmpeg", "whisperCLI": "whisper"}
        with patch.object(audio_setup, "start_process", return_value=process) as start, \
                patch.object(audio_setup, "stop_process") as stop:
            with self.assertRaises(subprocess.TimeoutExpired):
                audio_setup._run_transcription(config, "input.wav", "out", diarize=False)
        stop.assert_called_once_with(process)
        self.assertEqual(start.call_args.kwargs["encoding"], "utf-8")


if __name__ == "__main__":
    unittest.main(verbosity=2)
