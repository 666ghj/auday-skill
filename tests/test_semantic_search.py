"""Index contracts use deterministic test vectors; model quality is a separate opt-in test."""

import contextlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import semantic_search as semantic


def library(root, rows, snapshot="synthetic-v1"):
    with sqlite3.connect(root / "library.sqlite") as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY,kind TEXT,text TEXT,date TEXT,body TEXT);
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT);
            DELETE FROM records;
            DELETE FROM metadata;
        """)
        for row in rows:
            db.execute("INSERT INTO records VALUES(?,?,?,?,?)",
                       (row["id"], row.get("kind", "transcript"), row.get("text", ""),
                        row.get("start", "2026-10-19T12:00:00+08:00"), json.dumps(row)))
        for key, value in {"snapshot": snapshot, "syncedAt": "2026-10-20T00:00:00Z"}.items():
            db.execute("INSERT INTO metadata VALUES(?,?)", (key, json.dumps(value)))


class TestEncoder:
    """Injectable test seam: validates vector ranking, not real semantic ability."""
    identity = "synthetic-encoder-v1"

    def __init__(self):
        self.calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def document_chunks(self, text, title):
        for start, end in semantic._character_chunks(text):
            yield start, end, text[start:end] + " " + title

    def encode(self, text):
        self.calls += 1
        vector = [0.0] * semantic.DIMENSIONS
        vector[0] = 1.0 if "prototype" in text or "样品" in text else 0.1
        vector[1] = 1.0 if "privacy" in text or "隐私" in text else 0.1
        vector[2] = 0.2
        return vector


class SemanticIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="auday-semantic-test-")
        self.root = Path(self.temp.name)
        self.rows = [
            {"id": "prototype", "text": "Bring a working prototype.", "kind": "transcript",
             "start": "2026-10-19T12:00:00+08:00", "state": "confirmed"},
            {"id": "privacy", "text": "Protect privacy.", "kind": "memory",
             "start": "2026-10-20T12:00:00+08:00", "state": "candidate"},
        ]
        library(self.root, self.rows)
        self.encoder = TestEncoder()
        self.progress = patch.object(semantic, "report")
        self.progress.start()

    def tearDown(self):
        self.progress.stop()
        self.temp.cleanup()

    def test_persisted_vectors_rank_without_keyword_overlap(self):
        built = semantic.build_index(self.root, encoder=self.encoder)
        self.assertEqual(built["encodedChunks"], 2)
        results = semantic.search(self.root, "样品", encoder=TestEncoder())
        self.assertEqual(results["records"][0]["id"], "prototype")
        self.assertNotIn("样品", results["records"][0]["text"])
        self.assertEqual(results["records"][0]["state"], "confirmed")
        self.assertEqual(results["records"][0]["chunk"]["startCharacter"], 0)
        self.assertEqual((self.root / "semantic.sqlite").stat().st_mode & 0o777, 0o600)

    def test_incremental_reuse_and_deleted_record_removal(self):
        semantic.build_index(self.root, encoder=self.encoder)
        self.assertFalse(semantic.build_index(self.root, encoder=self.encoder)["changed"])
        updated = [self.rows[0] | {"state": "superseded"}, {"id": "new", "text": "privacy policy"}]
        library(self.root, updated, "synthetic-v2")
        rebuilt = semantic.build_index(self.root, encoder=self.encoder)
        self.assertEqual(rebuilt["reusedRecords"], 1)
        self.assertEqual(rebuilt["encodedChunks"], 1)
        results = semantic.search(self.root, "样品", encoder=self.encoder)
        self.assertEqual(results["records"][0]["state"], "superseded")
        self.assertEqual({r["id"] for r in results["records"]}, {"prototype", "new"})

    def test_stale_source_detected_even_if_snapshot_metadata_is_unchanged(self):
        semantic.build_index(self.root, encoder=self.encoder)
        library(self.root, self.rows + [{"id": "extra", "text": "extra"}])
        with self.assertRaisesRegex(ValueError, "index_stale"):
            semantic.search(self.root, "样品", encoder=self.encoder)

    def test_freshness_only_change_keeps_index_and_updates_reported_time(self):
        semantic.build_index(self.root, encoder=self.encoder)
        with sqlite3.connect(self.root / "library.sqlite") as db:
            db.execute("UPDATE metadata SET value=? WHERE key='syncedAt'",
                       (json.dumps("2026-10-21T00:00:00Z"),))
        built = semantic.build_index(self.root, encoder=self.encoder)
        self.assertFalse(built["changed"])
        result = semantic.search(self.root, "样品", encoder=self.encoder)
        self.assertEqual(result["syncedAt"], "2026-10-21T00:00:00Z")

    def test_sync_can_write_during_encoding_but_changed_snapshot_is_not_published(self):
        original_encode = self.encoder.encode
        updated = False

        def encode_and_sync(text):
            nonlocal updated
            if not updated:
                updated = True
                # This immediate write would fail if indexing retained a read
                # lock on the original DELETE-journal database.
                library(self.root, self.rows, "synthetic-v2")
            return original_encode(text)

        with patch.object(self.encoder, "encode", side_effect=encode_and_sync):
            with self.assertRaisesRegex(ValueError, "source_changed_during_build"):
                semantic.build_index(self.root, encoder=self.encoder)
        self.assertFalse((self.root / "semantic.sqlite").exists())

    def test_unsynchronized_library_is_not_indexed(self):
        with sqlite3.connect(self.root / "library.sqlite") as db:
            db.execute("DELETE FROM metadata")
        with self.assertRaisesRegex(ValueError, "library_not_synced"):
            semantic.build_index(self.root, encoder=self.encoder)

    def test_model_mismatch_requires_new_generation(self):
        semantic.build_index(self.root, encoder=self.encoder)
        other = TestEncoder()
        other.identity = "synthetic-encoder-v2"
        with self.assertRaisesRegex(ValueError, "model_index_mismatch"):
            semantic.search(self.root, "样品", encoder=other)
        rebuilt = semantic.build_index(self.root, encoder=other)
        self.assertEqual(rebuilt["reusedRecords"], 0)
        self.assertEqual(rebuilt["encodedChunks"], 2)

    def test_failed_build_preserves_previous_file(self):
        semantic.build_index(self.root, encoder=self.encoder)
        before = (self.root / "semantic.sqlite").read_bytes()
        library(self.root, self.rows + [{"id": "extra", "text": "extra"}], "synthetic-v2")
        with patch.object(self.encoder, "encode", side_effect=RuntimeError("synthetic_failure")):
            with self.assertRaisesRegex(RuntimeError, "synthetic_failure"):
                semantic.build_index(self.root, encoder=self.encoder)
        self.assertEqual((self.root / "semantic.sqlite").read_bytes(), before)
        self.assertEqual(list(self.root.glob(".semantic-build-*")), [])

    def test_metadata_filters_and_one_result_per_record(self):
        library(self.root, [self.rows[0] | {"text": "prototype " * 300}, self.rows[1]])
        built = semantic.build_index(self.root, encoder=self.encoder)
        self.assertGreater(built["chunkCount"], 2)
        all_results = semantic.search(self.root, "样品", encoder=self.encoder)
        self.assertEqual(len(all_results["records"]), 2)
        filtered = semantic.search(self.root, "样品", kind="memory", date="2026-10-20", encoder=self.encoder)
        self.assertEqual([r["id"] for r in filtered["records"]], ["privacy"])
        with self.assertRaisesRegex(ValueError, "invalid_date"):
            semantic.search(self.root, "样品", date="%", encoder=self.encoder)

    def test_title_only_record_and_unicode_chunk_coverage(self):
        text = "中文🎤abc" * 1000
        chunks = list(semantic._character_chunks(text))
        covered = set()
        for start, end in chunks:
            covered.update(range(start, end))
            self.assertLessEqual(end - start, semantic.CHUNK_CHARS)
        self.assertEqual(covered, set(range(len(text))))
        library(self.root, [{"id": "title-only", "title": "prototype"}])
        semantic.build_index(self.root, encoder=self.encoder)
        result = semantic.search(self.root, "样品", encoder=self.encoder)
        self.assertEqual(result["records"][0]["text"], "prototype")

    def test_reject_invalid_vectors(self):
        for vector in ([0.0] * semantic.DIMENSIONS, [float("nan")] * semantic.DIMENSIONS, [1.0]):
            with self.assertRaisesRegex(ValueError, "invalid_vector"):
                semantic._pack(vector)
        with self.assertRaisesRegex(ValueError, "corrupt_vector"):
            semantic._unpack(b"invalid")

    def test_concurrent_build_lock(self):
        with semantic._build_lock(self.root):
            with self.assertRaisesRegex(ValueError, "index_busy"):
                semantic.build_index(self.root, encoder=self.encoder)


@unittest.skipUnless(os.environ.get("AUDAY_SEMANTIC_MODEL") and os.environ.get("AUDAY_SEMANTIC_SERVER"),
                     "Optional local model paths were not supplied; never downloads models")
class RealModelTests(unittest.TestCase):
    def test_multilingual_retrieval_and_token_budget(self):
        fixture = json.loads((Path(__file__).parent / "semantic-fixture.json").read_text())
        with tempfile.TemporaryDirectory(prefix="auday-semantic-real-") as directory:
            root = Path(directory)
            library(root, fixture["records"])
            semantic.configure(root, os.environ["AUDAY_SEMANTIC_MODEL"], os.environ["AUDAY_SEMANTIC_SERVER"])
            built = semantic.build_index(root)
            self.assertEqual(built["recordCount"], len(fixture["records"]))
            for query in fixture["queries"]:
                with self.subTest(direction=query["direction"], target=query["target"]):
                    result = semantic.search(root, query["text"], limit=1)
                    self.assertEqual(result["records"][0]["id"], query["target"])
            with semantic.LocalEncoder(semantic._configuration(root)) as encoder:
                # This long CJK input must be split, including its tail.
                text = "本地运行的模型保护私人谈话。" * 100
                chunks = list(encoder.document_chunks(text, "Synthetic long record"))
                self.assertGreater(len(chunks), 1)
                self.assertEqual(chunks[-1][1], len(text))
                for _, _, content in chunks:
                    self.assertLessEqual(len(encoder.tokens(content)), semantic.MAX_TOKENS)


if __name__ == "__main__":
    unittest.main()
