import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from install import install, package_files
from verify_public import inspect_blob


class InstallTests(unittest.TestCase):
    def test_clean_install_is_self_contained(self):
        with tempfile.TemporaryDirectory(prefix="auday-install-test-") as folder:
            destination = Path(folder) / "agent skills" / "auday"
            result = install(destination)
            self.assertEqual(result["version"], (ROOT / "VERSION").read_text().strip())
            self.assertTrue(
                (destination / "scripts/link/Sources/AudayLink/Link.swift").is_file()
            )
            self.assertFalse((destination / ".git").exists())
            self.assertFalse((destination / "tests").exists())
            self.assertFalse(
                (destination / "scripts/link/Sources/FixtureLibrary").exists()
            )
            self.assertFalse((destination / "scripts/link/.build").exists())
            self.assertFalse((destination / "scripts/.venv").exists())
            process = subprocess.run(
                [sys.executable, str(destination / "scripts/auday.py"),
                 "--home", str(Path(folder) / "empty-data"), "doctor"],
                cwd=folder,
                capture_output=True,
                text=True,
                check=True,
            )
            doctor = json.loads(process.stdout)
            self.assertEqual(doctor["version"], result["version"])
            self.assertEqual(doctor["phoneSupport"], "compatible-build-required")
            self.assertFalse(doctor["capabilities"]["semantic"]["configured"])
            self.assertFalse(doctor["capabilities"]["audio"]["installed"])
            self.assertFalse(doctor["capabilities"]["paired"])
            self.assertTrue((destination / "install-receipt.json").is_file())

    def test_existing_destination_is_preserved(self):
        with tempfile.TemporaryDirectory(prefix="auday-install-test-") as folder:
            destination = Path(folder) / "auday"
            destination.mkdir()
            marker = destination / "user.txt"
            marker.write_text("keep")
            with self.assertRaises(ValueError):
                install(destination)
            self.assertEqual(marker.read_text(), "keep")

    def test_symlink_destination_is_not_followed(self):
        with tempfile.TemporaryDirectory(prefix="auday-install-test-") as folder:
            target = Path(folder) / "existing"
            target.mkdir()
            link = Path(folder) / "auday"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaises(ValueError):
                install(link)
            self.assertEqual(list(target.iterdir()), [])

    def test_public_guard_rejects_private_artifacts_and_tokens(self):
        self.assertIn(
            "private-media-model-or-database", inspect_blob("sample.wav", b"fake")
        )
        self.assertIn(
            "private-or-generated-directory",
            inspect_blob(".private/example.txt", b"fake"),
        )
        self.assertIn(
            "github-token", inspect_blob("example.txt", ("ghp_" + "x" * 40).encode())
        )
        machine_path = ("/" + "Users" + "/" + "someone" + "/private-data").encode()
        self.assertIn(
            "machine-specific-absolute-path", inspect_blob("example.txt", machine_path)
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
