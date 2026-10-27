import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import managed_library
from managed_library import maintain_library


class ManagedLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="auday-managed-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.database = self.root / "library.sqlite"
        self.destination = self.root / "library"
        self.rows = [
            {"id": "synthetic/../1", "kind": "transcript", "text": "项目 launch",
             "start": "2026-10-19T23:30:00-07:00", "timeZone": "America/Los_Angeles",
             "state": "saved", "payload": {"original": [1, 2, 3]}},
            {"id": "synthetic-2", "kind": "memory", "text": "A candidate only",
             "start": "2026-10-20T09:00:00+08:00", "state": "candidate",
             "sourceIDs": ["synthetic/../1"]},
        ]
        self.metadata = {"snapshot": "a" * 64, "recordCount": 2, "sourceDeviceID": "synthetic-phone",
                         "syncedAt": "2026-10-20T12:00:00Z", "sourceSnapshotAt": "2026-10-20T11:59:00Z",
                         "sourceSnapshotCurrent": False, "access": "text-only-read-only"}
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.executescript("CREATE TABLE records(id TEXT PRIMARY KEY, date TEXT, body TEXT);"
                                     "CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT);")
        self.save()

    def save(self):
        self.metadata["recordCount"] = len(self.rows)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("DELETE FROM records")
            connection.execute("DELETE FROM metadata")
            connection.executemany("INSERT INTO records VALUES(?,?,?)", [
                (row["id"], row.get("start", ""), json.dumps(row)) for row in self.rows])
            connection.executemany("INSERT INTO metadata VALUES(?,?)", [
                (key, json.dumps(value)) for key, value in self.metadata.items()])

    def run_library(self, **kwargs):
        return maintain_library(self.database, self.destination, **kwargs)

    def manifest(self):
        return json.loads((self.destination / "manifest.json").read_text(encoding="utf-8"))

    def file(self, identity):
        entry = next(entry for entry in self.manifest()["records"] if entry["id"] == identity)
        return self.destination / entry["path"]

    def test_initial_view_preserves_payload_and_source_time_offline(self):
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        result = self.run_library(language="zh-CN")
        self.assertEqual(result["updatedRecords"], 2)
        self.assertEqual(before, hashlib.sha256(self.database.read_bytes()).hexdigest())
        file = self.file(self.rows[0]["id"])
        self.assertEqual(file.parent.parent.name, "2026-10-19")
        text = file.read_text(encoding="utf-8")
        for expected in ("项目 launch", "America/Los_Angeles", '"original"', "synthetic-phone", "synthetic/../1"):
            self.assertIn(expected, text)
        self.assertIn("candidate", self.file("synthetic-2").read_text(encoding="utf-8"))
        self.assertFalse(self.manifest()["sourceSnapshotCurrent"])
        self.assertEqual(self.manifest()["sourceSnapshotAt"], self.metadata["sourceSnapshotAt"])
        self.assertTrue((self.destination / "notes").is_dir())
        self.assertFalse((self.destination / ".pending").exists())
        if os.name != "nt":
            self.assertEqual(self.destination.stat().st_mode & 0o777, 0o700)
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)

    def test_unchanged_files_are_not_rewritten_even_after_new_snapshot(self):
        self.run_library()
        paths = [self.file(row["id"]) for row in self.rows]
        before = [path.stat().st_mtime_ns for path in paths]
        self.metadata["snapshot"] = "b" * 64
        self.metadata["syncedAt"] = "2026-10-21T12:00:00Z"
        self.save()
        result = self.run_library()
        self.assertEqual(result["updatedRecords"], 0)
        self.assertEqual([path.stat().st_mtime_ns for path in paths], before)
        self.assertEqual(self.manifest()["snapshot"], "b" * 64)
        unchanged = self.destination / "manifest.json"
        timestamp = unchanged.stat().st_mtime_ns
        self.run_library()
        self.assertEqual(unchanged.stat().st_mtime_ns, timestamp)

    def test_changed_removed_and_added_records_do_not_touch_notes(self):
        self.run_library()
        changed = self.file(self.rows[0]["id"])
        deleted = self.file("synthetic-2")
        user_file = self.destination / "notes" / "plan.md"
        user_file.write_text("My decisions", encoding="utf-8")
        self.rows[0]["text"] = "Updated source"
        self.rows.pop()
        self.rows.append({"id": "new", "kind": "summary", "text": "New", "start": "../../outside"})
        self.metadata["snapshot"] = "b" * 64
        self.save()
        result = self.run_library()
        self.assertEqual(result["updatedRecords"], 2)
        self.assertEqual(result["deletedRecords"], 1)
        self.assertIn("Updated source", changed.read_text(encoding="utf-8"))
        self.assertFalse(deleted.exists())
        archived = list((self.destination / "archive").rglob("*.md"))
        self.assertTrue(any("A candidate only" in path.read_text(encoding="utf-8") for path in archived))
        self.assertEqual(self.file("new").parent.parent.name, "undated")
        self.assertEqual(user_file.read_text(encoding="utf-8"), "My decisions")

    def test_date_change_archives_old_path_and_keeps_source_identity(self):
        self.run_library()
        original = self.file(self.rows[0]["id"])
        self.rows[0]["start"] = "2026-10-22T12:00:00Z"
        self.save()
        result = self.run_library()
        current = self.file(self.rows[0]["id"])
        self.assertEqual(current.name, original.name)
        self.assertNotEqual(current, original)
        self.assertFalse(original.exists())
        self.assertEqual(result["deletedRecords"], 0)
        self.assertGreaterEqual(result["archivedFiles"], 1)

    def test_user_edits_and_unowned_collisions_are_preserved(self):
        self.run_library()
        source = self.file(self.rows[0]["id"])
        source.write_text("My important annotation", encoding="utf-8")
        (self.destination / "README.md").write_text("My custom guide", encoding="utf-8")
        result = self.run_library()
        self.assertEqual(result["preservedEdits"], 2)
        copies = [p.read_text(encoding="utf-8") for p in (self.destination / "conflicts").rglob("*.md")]
        self.assertIn("My important annotation", copies)
        self.assertIn("My custom guide", copies)
        self.assertIn("项目 launch", source.read_text(encoding="utf-8"))

    def test_interruption_keeps_old_commit_marker_then_recovers(self):
        self.run_library()
        old = self.manifest()
        self.rows[0]["text"] = "Updated source"
        self.metadata["snapshot"] = "b" * 64
        self.save()
        original = managed_library._atomic_bytes
        def fail_manifest(path, data):
            if path == self.destination / "manifest.json":
                raise OSError("synthetic interruption")
            return original(path, data)
        with patch.object(managed_library, "_atomic_bytes", side_effect=fail_manifest):
            with self.assertRaisesRegex(OSError, "synthetic interruption"):
                self.run_library()
        self.assertEqual(self.manifest(), old)
        self.assertTrue((self.destination / ".pending/plan.json").is_file())
        result = self.run_library()
        self.assertTrue(result["recoveredInterruptedUpdate"])
        self.assertEqual(self.manifest()["snapshot"], "b" * 64)
        self.assertFalse((self.destination / ".pending").exists())
        for relative, digest in self.manifest()["files"].items():
            self.assertEqual(hashlib.sha256((self.destination / relative).read_bytes()).hexdigest(), digest)

    def test_cleanup_interruption_does_not_need_consumed_staging_files(self):
        self.run_library()
        self.rows[0]["text"] = "Updated"
        self.save()
        original = Path.unlink
        def fail_plan(path, *args, **kwargs):
            if path == self.destination / ".pending/plan.json":
                raise OSError("cleanup interrupted")
            return original(path, *args, **kwargs)
        with patch.object(Path, "unlink", fail_plan):
            with self.assertRaisesRegex(OSError, "cleanup interrupted"):
                self.run_library()
        self.assertTrue((self.destination / ".pending/plan.json").exists())
        result = self.run_library()
        self.assertTrue(result["recoveredInterruptedUpdate"])
        self.assertFalse((self.destination / ".pending").exists())

    def test_bad_record_does_not_publish_partial_library(self):
        self.run_library()
        old = self.manifest()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("UPDATE records SET body='invalid' WHERE id='synthetic-2'")
        with self.assertRaises(ValueError):
            self.run_library()
        self.assertEqual(self.manifest(), old)
        self.save()
        self.assertTrue(self.run_library()["recoveredInterruptedUpdate"])

    def test_symlink_in_managed_path_cannot_write_outside_library(self):
        self.run_library()
        source = self.file(self.rows[0]["id"])
        outside = self.root / "outside.md"
        outside.write_text("untouched", encoding="utf-8")
        source.unlink()
        try:
            source.symlink_to(outside)
        except OSError as error:
            self.skipTest(f"symlinks unavailable: {error}")
        with self.assertRaisesRegex(ValueError, "library_symlink_not_supported"):
            self.run_library()
        self.assertEqual(outside.read_text(encoding="utf-8"), "untouched")

    def test_manifest_cannot_claim_user_notes_or_parent_paths(self):
        self.run_library()
        for bad in ("notes/plan.md", "../outside.md", "/tmp/outside.md", "records\\outside.md"):
            with self.subTest(path=bad):
                manifest = self.manifest()
                original = dict(manifest["files"])
                manifest["files"][bad] = "a" * 64
                (self.destination / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "invalid_library_manifest"):
                    self.run_library()
                manifest["files"] = original
                (self.destination / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    def test_device_change_and_missing_snapshot_are_rejected(self):
        self.run_library()
        old = self.manifest()
        self.metadata["sourceDeviceID"] = "different-phone"
        self.save()
        with self.assertRaisesRegex(ValueError, "library_source_device_changed"):
            self.run_library()
        self.assertEqual(self.manifest(), old)
        self.metadata.pop("snapshot")
        self.save()
        with self.assertRaisesRegex(ValueError, "no_synced_snapshot"):
            self.run_library()

    def test_parallel_edit_of_unchanged_file_stops_commit_and_next_run_repairs(self):
        self.run_library()
        old = self.manifest()
        source = self.file(self.rows[0]["id"])
        self.metadata["snapshot"] = "b" * 64
        self.save()
        original = managed_library._atomic_bytes
        def edit_after_scan(path, data):
            if path == self.destination / ".pending/plan.json":
                source.write_text("Concurrent user annotation", encoding="utf-8")
            return original(path, data)
        with patch.object(managed_library, "_atomic_bytes", side_effect=edit_after_scan):
            with self.assertRaisesRegex(ValueError, "library_files_changed_during_update"):
                self.run_library()
        self.assertEqual(self.manifest(), old)
        self.assertEqual(source.read_text(encoding="utf-8"), "Concurrent user annotation")
        result = self.run_library()
        self.assertTrue(result["recoveredInterruptedUpdate"])
        copies = [p.read_text(encoding="utf-8") for p in (self.destination / "conflicts").rglob("*.md")]
        self.assertIn("Concurrent user annotation", copies)
        self.assertIn("项目 launch", source.read_text(encoding="utf-8"))

    @unittest.skipIf(os.name == "nt", "Windows editors may hold a non-renamable handle; update then fails safely")
    def test_saved_copy_keeps_edits_through_open_file_handle(self):
        self.run_library()
        source = self.file(self.rows[0]["id"])
        with source.open("a", encoding="utf-8") as editor:
            self.rows[0]["text"] = "Updated source"
            self.save()
            self.run_library()
            editor.write("\nLate editor text")
        copies = [p.read_text(encoding="utf-8") for p in (self.destination / "archive").rglob("*.md")]
        self.assertTrue(any("Late editor text" in text for text in copies))
        self.assertNotIn("Late editor text", source.read_text(encoding="utf-8"))

    def test_saved_language_is_reused_when_unspecified(self):
        self.run_library(language="zh-CN")
        self.assertEqual(self.run_library()["updatedRecords"], 0)
        self.assertEqual(self.manifest()["language"], "zh-CN")

    def test_source_content_is_inert_even_with_markdown_fences(self):
        self.rows[0]["text"] = "```\n![remote](https://example.invalid/image)\n<script>do evil</script>"
        self.save()
        self.run_library()
        content = self.file(self.rows[0]["id"]).read_text(encoding="utf-8")
        self.assertIn("````text\n```\n![remote]", content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
