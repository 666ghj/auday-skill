"""Manual Apple Watch marks across the compatible wire format and desktop views."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from auday import Auday
from library_export import export_library
import semantic_search
from test_incremental_sync import IncrementalPeer, record
from test_semantic_search import TestEncoder


def manual_mark(number, sources):
    mark_id = f"00000000-0000-4000-8000-{number:012d}"
    return record(
        f"mark:{mark_id}", title="Manual Mark", text="Manual mark during recording", kind="highlight",
        state="user-marked", start=f"2026-10-28T09:00:{number:02d}+08:00", sourceIDs=sources,
        payload={"contentType": "manual-mark", "markID": mark_id,
                 "sessionID": "00000000-0000-4000-8000-000000000000", "timelineOffsetSeconds": number},
    )


class ManualMarkTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="auday-synthetic-marks-")
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.first = manual_mark(1, ["transcript:one"])
        self.second = manual_mark(2, [])
        self.speech = record(
            "transcript:one", text="Original words from the recording",
            payload={"manualMarks": [{"id": self.first["id"], "start": self.first["start"]}]},
        )
        self.highlight = record(
            "highlight:one", kind="highlight", text="Selected part of the recording",
            payload={"manualMarkIDs": [self.first["payload"]["markID"]]},
        )
        self.peer = IncrementalPeer([self.highlight, self.speech, self.first, self.second])
        self.app = Auday(self.home, transport=self.peer)
        self.addCleanup(self.app.close)
        self.app.sync()

    def test_keyword_cli_paginates_manual_marks_separately_from_highlights(self):
        page = subprocess.run([
            sys.executable, str(ROOT / "scripts/auday.py"), "--home", str(self.home),
            "search", "--kind", "mark", "--date", "2026-10-28", "--limit", "1",
        ], capture_output=True, check=True, encoding="utf-8")
        first_page = json.loads(page.stdout)
        self.assertEqual([r["id"] for r in first_page["records"]], [self.first["id"]])
        self.assertEqual(first_page["records"][0]["displayKind"], "mark")
        self.assertEqual(first_page["records"][0]["kind"], "highlight")
        self.assertEqual(first_page["nextOffset"], 1)
        second_page = self.app.search("recording", kind="mark", offset=first_page["nextOffset"], limit=1)
        self.assertEqual([r["id"] for r in second_page["records"]], [self.second["id"]])
        self.assertIsNone(second_page["nextOffset"])
        self.assertEqual([r["id"] for r in self.app.search("recording", kind="highlight")["records"]],
                         [self.highlight["id"]])
        for source in (self.first, self.second, self.speech, self.highlight):
            self.assertEqual(self.app.read(source["id"])["record"], source)

    def test_incremental_mark_addition_and_removal_update_semantic_search_and_library(self):
        encoder = TestEncoder()
        semantic_search.build_index(self.app.root, encoder=encoder)
        library = self.app.root / "library"
        records = json.loads((library / "manifest.json").read_text(encoding="utf-8"))["records"]
        files = {r["id"]: library / r["path"] for r in records}
        self.assertEqual(files[self.highlight["id"]].parent.name, "highlight")
        self.assertEqual(files[self.second["id"]].parent.name, "highlight")
        self.assertIn(self.second["id"], files[self.second["id"]].read_text(encoding="utf-8"))

        third = manual_mark(3, [])
        self.peer.publish([self.highlight, self.speech, self.first, third])
        result = self.app.sync()
        self.assertEqual((result["downloadedRecords"], result["deletedRecords"]), (1, 1))
        self.assertFalse(files[self.second["id"]].exists())
        with self.assertRaisesRegex(ValueError, "record_not_found"):
            self.app.read(self.second["id"])
        index = semantic_search.build_index(self.app.root, encoder=encoder)
        self.assertEqual(index["reusedRecords"], 3)
        for kind, expected in (("mark", {self.first["id"], third["id"]}), ("highlight", {self.highlight["id"]})):
            self.assertEqual({r["id"] for r in self.app.search(kind=kind)["records"]}, expected)
            matches = semantic_search.search(self.app.root, "recording", kind=kind, encoder=encoder)
            self.assertEqual({r["id"] for r in matches["records"]}, expected)
            self.assertEqual({r["kind"] for r in matches["records"]}, {"highlight"})
            self.assertEqual({r.get("displayKind", r["kind"]) for r in matches["records"]}, {kind})

    def test_offline_library_preserves_wire_group_and_mark_details(self):
        library = self.app.root / "library"
        notes = library / "notes" / "user.md"
        notes.write_text("User notes on this recording", encoding="utf-8")
        result = self.app.library()
        self.assertEqual(result["updatedRecords"], 0)
        manifest = json.loads((library / "manifest.json").read_text(encoding="utf-8"))
        entry = next(r for r in manifest["records"] if r["id"] == self.first["id"])
        self.assertEqual(entry["kind"], "highlight")
        group = library / "records" / "2026-10-28" / "highlight"
        mark_file = library / entry["path"]
        self.assertEqual(mark_file.parent, group)
        group_index = (group / "index.md").read_text(encoding="utf-8")
        self.assertIn(f"[{self.first['title']}]({mark_file.name})", group_index)
        mark_content = mark_file.read_text(encoding="utf-8")
        for value in (self.first["title"], self.first["id"], self.first["state"], "manual-mark"):
            self.assertIn(value, mark_content)
        self.assertEqual(self.app.read(self.first["id"])["record"], self.first)
        self.assertEqual(notes.read_text(encoding="utf-8"), "User notes on this recording")

    def test_daily_export_distinguishes_marks_from_ai_highlights(self):
        for language, label in (("en", "Mark"), ("zh-CN", "标记")):
            with self.subTest(language=language):
                dest = self.home / language
                export_library(self.app.root / "library.sqlite", dest, language)
                manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
                self.assertEqual(manifest["recordKinds"], {"mark": 2, "highlight": 1, "transcript": 1})
                day = (dest / "days/2026-10-28.md").read_text(encoding="utf-8")
                mark_headings = [line for line in day.splitlines() if line.startswith("## ") and label.lower() in line.lower()]
                self.assertEqual(len(mark_headings), 2)
                self.assertIn("Manual marks:" if language == "en" else "关联手动标记:", day)
                for source in (self.first, self.second):
                    self.assertIn(source["id"], day)
                    self.assertIn(source["start"], day)


if __name__ == "__main__":
    unittest.main()
