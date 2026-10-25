"""App-local Microsoft runtime preparation with synthetic archives and no downloads."""

import copy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import setup_runtime
import windows_crt


class WindowsCRTTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="auday-crt-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "数据 日本語"
        self.root.mkdir()
        self.destination = self.root / "runtime" / "llama" / "bin"
        self.archive = self.root / "synthetic.vsix"
        files = []
        with zipfile.ZipFile(self.archive, "w") as archive:
            for name in (*windows_crt.CORE_FILES, "vcomp140.dll"):
                data = ("synthetic-release-" + name).encode()
                member = "Contents/release/" + name
                archive.writestr(member, data)
                files.append({"filename": name, "member": member, "size": len(data),
                              "sha256": hashlib.sha256(data).hexdigest()})
            archive.writestr("Contents/debug_nonredist/msvcp140d.dll", b"debug fixture")
        self.asset = {"url": "https://example.invalid/runtime.vsix", "sha256": "a" * 64,
                      "size": self.archive.stat().st_size, "files": files}
        self.manifest = patch.object(windows_crt, "CRT_ASSET", self.asset)
        self.manifest.start()
        self.addCleanup(self.manifest.stop)
        self.download = patch.object(setup_runtime, "download_asset", return_value=self.archive)
        self.downloaded = self.download.start()
        self.addCleanup(self.download.stop)

    def arm64_fixture(self):
        archive_path = self.root / "arm64.vsix"
        asset = copy.deepcopy(self.asset)
        asset.update(url="https://example.invalid/arm64-runtime.vsix", sha256="b" * 64, files=[])
        with zipfile.ZipFile(archive_path, "w") as archive:
            for name in windows_crt.ARM64_CORE_FILES:
                data = ("synthetic-arm64-" + name).encode()
                member = "Contents/arm64/release/" + name
                archive.writestr(member, data)
                asset["files"].append({"filename": name, "member": member, "size": len(data),
                                       "sha256": hashlib.sha256(data).hexdigest()})
        asset["size"] = archive_path.stat().st_size
        return asset, archive_path

    def test_installs_only_required_release_dlls_and_reuses_verified_files(self):
        paths = windows_crt.ensure_windows_crt(self.root, self.destination)
        self.assertEqual({path.name for path in paths}, set(windows_crt.CORE_FILES))
        self.assertEqual({path.name for path in self.destination.iterdir()}, set(windows_crt.CORE_FILES))
        for path in paths:
            self.assertEqual(path.read_bytes(), ("synthetic-release-" + path.name).encode())
        self.assertEqual(windows_crt.ensure_windows_crt(self.root, self.destination), paths)
        self.downloaded.assert_called_once()
        self.assertFalse((self.root / "models").exists())

    def test_audio_can_request_openmp_without_installing_any_other_components(self):
        paths = windows_crt.ensure_windows_crt(self.root, self.destination, openmp=True)
        self.assertEqual({path.name for path in paths}, {*windows_crt.CORE_FILES, "vcomp140.dll"})
        self.assertFalse(list(self.destination.glob("*d.dll")))
        self.assertFalse((self.root / "runtime" / "audio").exists())

    def test_existing_modified_dll_is_preserved_and_never_overwritten(self):
        self.destination.mkdir(parents=True)
        changed = self.destination / "msvcp140.dll"
        changed.write_bytes(b"different installation")
        with self.assertRaisesRegex(ValueError, "existing_windows_runtime_library_mismatch"):
            windows_crt.ensure_windows_crt(self.root, self.destination)
        self.assertEqual(changed.read_bytes(), b"different installation")
        self.downloaded.assert_not_called()

    def test_archive_member_hash_mismatch_publishes_no_dlls(self):
        self.asset["files"][1]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "library_verification_failed"):
            windows_crt.ensure_windows_crt(self.root, self.destination)
        self.assertFalse(list(self.destination.glob("*.dll")))
        self.assertFalse(list(self.destination.parent.glob(".windows-crt-*")))

    def test_outside_destination_is_rejected_before_download(self):
        outside = self.root.parent / "external"
        with self.assertRaisesRegex(ValueError, "destination_outside_private_setup"):
            windows_crt.ensure_windows_crt(self.root, outside)
        self.downloaded.assert_not_called()
        self.assertFalse(outside.exists())

    def test_arm64_selects_its_own_release_files_and_archive(self):
        asset, archive = self.arm64_fixture()
        self.downloaded.return_value = archive
        with patch.object(windows_crt, "ARM64_CRT_ASSET", asset):
            paths = windows_crt.ensure_windows_crt(self.root, self.destination, architecture="arm64")
            self.assertEqual(windows_crt.crt_asset("arm64"), asset)
            self.assertEqual(windows_crt.ensure_windows_crt(
                self.root, self.destination, architecture="arm64"), paths)
        self.assertEqual({path.name for path in paths}, set(windows_crt.ARM64_CORE_FILES))
        self.assertEqual({path.name for path in self.destination.iterdir()}, set(windows_crt.ARM64_CORE_FILES))
        for path in paths:
            self.assertEqual(path.read_bytes(), ("synthetic-arm64-" + path.name).encode())
        self.downloaded.assert_called_once()
        self.assertEqual(self.downloaded.call_args.args[0], asset["url"])
        self.assertEqual(self.downloaded.call_args.args[2], asset["sha256"])

    def test_arm64_refuses_to_reuse_x64_dlls(self):
        windows_crt.ensure_windows_crt(self.root, self.destination)
        before = {path.name: path.read_bytes() for path in self.destination.iterdir()}
        self.downloaded.reset_mock()
        asset, _ = self.arm64_fixture()
        with patch.object(windows_crt, "ARM64_CRT_ASSET", asset):
            with self.assertRaisesRegex(ValueError, "existing_windows_runtime_library_mismatch"):
                windows_crt.ensure_windows_crt(self.root, self.destination, architecture="arm64")
        self.assertEqual({path.name: path.read_bytes() for path in self.destination.iterdir()}, before)
        self.downloaded.assert_not_called()

    def test_arm64_does_not_fall_back_to_x64_openmp(self):
        asset, _ = self.arm64_fixture()
        with patch.object(windows_crt, "ARM64_CRT_ASSET", asset):
            with self.assertRaisesRegex(ValueError, "windows_runtime_manifest_incomplete"):
                windows_crt.ensure_windows_crt(self.root, self.destination, architecture="arm64", openmp=True)
        self.downloaded.assert_not_called()
        self.assertFalse(self.destination.exists())

    def test_unknown_architecture_fails_without_download_or_setup(self):
        with self.assertRaisesRegex(ValueError, "unsupported_windows_runtime_architecture"):
            windows_crt.ensure_windows_crt(self.root, self.destination, architecture="aarch32")
        self.downloaded.assert_not_called()
        self.assertFalse(self.destination.exists())


if __name__ == "__main__":
    unittest.main()
