from contextlib import closing
import hashlib
import os
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from auday import Auday
from library_export import export_library, literal_block


class LibraryExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.app = Auday(self.root / "cache")
        self.database = self.app.root / "library.sqlite"
        self.destination = self.root / "readable"
        rows = [
            {"id": "synthetic-1", "kind": "transcript", "start": "2026-10-19T23:30:00-07:00",
             "timeZone": "America/Los_Angeles", "state": "saved", "text": "项目 launch",
             "payload": {"internal": "structured-payload-only"}},
            {"id": "synthetic-2", "kind": "memory", "start": "2026-10-20T09:00:00+08:00",
             "state": "candidate", "title": "A synthetic memory", "text": "Do not treat as confirmed",
             "sourceIDs": ["synthetic-1"]},
            {"id": "synthetic-3", "kind": "summary", "start": "../../outside",
             "state": "saved", "text": "```\n<script>synthetic</script>"},
        ]
        with self.app.db:
            for row in rows:
                self.app.db.execute("INSERT INTO records VALUES(?,?,?,?,?)", (
                    row["id"], row["kind"], row["text"], row["start"], json.dumps(row),
                ))
            self.app.db.executemany("INSERT INTO metadata VALUES(?,?)", [
                ("snapshot", json.dumps("a" * 64)), ("recordCount", "3"),
                ("syncedAt", json.dumps("2026-10-20T12:00:00Z")),
            ])
        self.app.close()

    def test_export_preserves_coverage_sources_dates_and_database(self):
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        events = []
        result = export_library(
            self.database, self.destination, "zh-CN",
            progress=lambda stage, **values: events.append(values),
        )
        self.assertEqual(result["recordCount"], 3)
        self.assertEqual(result["dayCount"], 3)
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).hexdigest(), before)
        first = (self.destination / "days/2026-10-19.md").read_text(encoding="utf-8")
        self.assertIn("项目 launch", first)
        self.assertIn("2026-10-19T23:30:00-07:00", first)
        self.assertNotIn("structured-payload-only", first)
        second = (self.destination / "days/2026-10-20.md").read_text(encoding="utf-8")
        self.assertIn("candidate", second)
        self.assertIn("synthetic-1", second)
        self.assertTrue((self.destination / "days/undated.md").is_file())
        self.assertEqual(events[-1]["completed"], 3)
        manifest = json.loads((self.destination / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(sum(item["records"] for item in manifest["files"]), 3)
        self.assertFalse(manifest["structuredPayloadIncluded"])
        self.assertFalse(manifest["audioFilesIncluded"])
        if os.name != "nt":
            self.assertEqual((self.destination.stat().st_mode & 0o777), 0o700)
        # Windows privacy uses the native protected DACL, separately exercised
        # in test_platform_utils rather than POSIX stat mode emulation.

    def test_existing_export_and_symlink_are_never_overwritten(self):
        self.destination.mkdir()
        edited = self.destination / "notes.md"
        edited.write_text("user edits")
        with self.assertRaisesRegex(ValueError, "export_destination_exists"):
            export_library(self.database, self.destination)
        self.assertEqual(edited.read_text(encoding="utf-8"), "user edits")
        link = self.root / "linked"
        link.symlink_to(self.root / "absent")
        with self.assertRaisesRegex(ValueError, "export_destination_exists"):
            export_library(self.database, link)
        self.assertTrue(link.is_symlink())

    def test_corrupt_record_leaves_no_partial_export(self):
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("UPDATE records SET body='invalid' WHERE id='synthetic-2'")
        with self.assertRaises(ValueError):
            export_library(self.database, self.destination)
        self.assertFalse(self.destination.exists())
        self.assertEqual(list(self.root.glob(".auday-export-*")), [])

    def test_source_markdown_cannot_escape_literal_block(self):
        block = literal_block("```\n![synthetic](https://example.invalid/image)\n<script>x</script>")
        self.assertTrue(block.startswith("````text\n"))
        self.assertTrue(block.endswith("\n````\n\n"))

    def test_cli_exports_offline_without_pairing(self):
        result = subprocess.run([
            sys.executable, str(ROOT / "scripts/auday.py"),
            "--home", str(self.root / "cache"), "export",
            "--output", str(self.destination), "--language", "zh-CN",
        ], capture_output=True, text=True, encoding="utf-8", check=True)
        self.assertEqual(json.loads(result.stdout)["recordCount"], 3)
        self.assertIn("Auday 文字资料库", (self.destination / "index.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
