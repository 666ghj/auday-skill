"""The durable file view is a recoverable stage after a verified source sync."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from auday import Auday
from test_incremental_sync import IncrementalPeer, record


class SyncLibraryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="auday-sync-library-synthetic-")
        self.addCleanup(temporary.cleanup)
        self.peer = IncrementalPeer([record("stable"), record("removed")])
        self.app = Auday(temporary.name, transport=self.peer)
        self.addCleanup(self.app.close)
        progress = patch.dict(os.environ, {"AUDAY_PROGRESS": "0"})
        progress.start()
        self.addCleanup(progress.stop)

    def manifest(self):
        return json.loads((self.app.root / "library" / "manifest.json").read_text(encoding="utf-8"))

    def test_sync_publishes_files_and_preserves_user_work_and_language(self):
        first = self.app.sync()
        self.assertEqual(first["libraryStatus"], "ready")
        self.assertEqual(first["library"]["snapshot"], first["snapshot"])
        self.app.library(language="zh-CN")
        manifest = self.manifest()
        stable = self.app.root / "library" / next(row["path"] for row in manifest["records"] if row["id"] == "stable")
        timestamp = stable.stat().st_mtime_ns
        notes = self.app.root / "library" / "notes" / "project.md"
        notes.write_text("User-authored project notes", encoding="utf-8")
        self.peer.publish([record("stable"), record("added")])
        result = self.app.sync()
        self.assertEqual(result["libraryStatus"], "ready")
        self.assertEqual(result["library"]["updatedRecords"], 1)
        self.assertEqual(result["library"]["deletedRecords"], 1)
        self.assertEqual(self.manifest()["language"], "zh-CN")
        self.assertEqual(stable.stat().st_mtime_ns, timestamp)
        self.assertEqual(notes.read_text(encoding="utf-8"), "User-authored project notes")
        self.assertTrue(list((self.app.root / "library" / "archive").rglob("*.md")))
        self.assertEqual(self.app.metadata()["lastSync"]["downloadedRecords"], 1)
        self.assertEqual(set(result["timings"]), {"prepareSeconds", "transferAndCommitSeconds", "librarySeconds"})

    def test_file_failure_keeps_verified_database_and_offline_recovery_needs_no_phone(self):
        with patch("managed_library.maintain_library", side_effect=OSError("synthetic disk failure")):
            result = self.app.sync()
        self.assertEqual(result["libraryStatus"], "failed")
        self.assertEqual(result["libraryError"], "OSError")
        self.assertEqual(self.app.read("stable")["record"]["id"], "stable")
        self.assertFalse(self.peer.leases)
        with patch.object(self.app, "request", side_effect=AssertionError("offline means no phone")):
            recovered = self.app.library()
        self.assertEqual(recovered["recordCount"], 2)
        self.assertEqual(recovered["snapshot"], result["snapshot"])

    def test_unchanged_sync_repairs_missing_file_without_body_downloads(self):
        self.app.sync()
        target = self.app.root / "library" / self.manifest()["records"][0]["path"]
        target.unlink()
        self.peer.reset_observations()
        result = self.app.sync()
        self.assertTrue(target.exists())
        self.assertEqual(result["library"]["updatedRecords"], 1)
        self.assertEqual(result["downloadedRecords"], 0)
        self.assertNotIn("recordManifest", [call["op"] for call in self.peer.calls])

    def test_older_sqlite_cache_can_create_files_without_sync_or_models(self):
        saved = record("legacy")
        metadata = {"snapshot": "a" * 64, "recordCount": 1, "assetCount": 0,
                    "sourceDeviceID": "synthetic-legacy-phone", "access": "text-only-read-only"}
        with self.app.db:
            self.app.db.execute("INSERT INTO records VALUES(?,?,?,?,?)",
                                (saved["id"], saved["kind"], saved["text"], saved["start"], json.dumps(saved)))
            self.app.db.executemany("INSERT INTO metadata VALUES(?,?)", [(key, json.dumps(value)) for key, value in metadata.items()])
        with patch.object(self.app, "request", side_effect=AssertionError("offline means no phone")):
            result = self.app.library(language="zh-CN")
        self.assertEqual(result["recordCount"], 1)
        self.assertEqual(self.manifest()["records"][0]["id"], "legacy")


if __name__ == "__main__":
    unittest.main()
