import base64
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from auday import Auday


class SyntheticPeer:
    def __init__(self):
        self.record = {
            "id": "reflection:synthetic", "kind": "reflection", "text": "Saved summary",
            "payload": {"sources": [
                {"snippetID": f"synthetic-{i}", "quote": "合成引文" * 100}
                for i in range(2500)
            ]},
        }
        self.body = json.dumps(self.record, ensure_ascii=False, sort_keys=True).encode()
        self.digest = hashlib.sha256(self.body).hexdigest()
        self.snapshot = "a" * 64
        self.state = "idle"
        self.prepare_calls = 0
        self.polls = 0
        self.chunk_offsets = []
        self.interrupt_after = None
        self.tamper = None
        self.fail_preparation = False
        self.max_frame = 0

    def __call__(self, request):
        reply = {"ok": True, "schemaVersion": 1, "sourceDeviceID": "synthetic", "snapshot": self.snapshot}
        op = request["op"]
        if op in ("status", "prepareSnapshot"):
            if op == "prepareSnapshot" and self.state != "ready":
                self.prepare_calls += 1
                self.state, self.polls = "preparing", 2
            elif op == "status" and self.state == "preparing":
                self.polls -= 1
                if self.polls == 0:
                    self.state = "failed" if self.fail_preparation else "ready"
            reply.update(
                access="text-only-read-only", snapshotState=self.state,
                recordCount=1 if self.state == "ready" else None,
                assetCount=0 if self.state == "ready" else None,
                capabilities={"onDemandSnapshot": True, "chunkedRecords": True},
                snapshotCurrent=self.state == "ready",
            )
        elif op == "list":
            assert request.get("allowRecordChunks") is True
            reply.update(records=[{
                "id": self.record["id"],
                "recordTransfer": {"encoding": "json-utf8", "size": len(self.body), "sha256": self.digest},
            }], nextOffset=None)
        elif op == "assets":
            reply.update(assets=[], nextOffset=None)
        elif op == "recordChunk":
            assert request["snapshot"] == self.snapshot
            if self.interrupt_after is not None and len(self.chunk_offsets) == self.interrupt_after:
                raise ConnectionError("synthetic_interruption")
            offset, length = request["offset"], request["length"]
            self.chunk_offsets.append(offset)
            data = self.body[offset:offset + length]
            reply.update(id=self.record["id"], data=base64.b64encode(data).decode(),
                         offset=offset, size=len(self.body), sha256=self.digest,
                         chunkSHA256=hashlib.sha256(data).hexdigest())
            if self.tamper:
                self.tamper(reply)
        else:
            raise AssertionError(op)
        self.max_frame = max(self.max_frame, len(json.dumps(reply).encode()))
        return reply


class RecordTransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="auday-record-contract-")
        self.peer = SyntheticPeer()
        self.app = Auday(self.temp.name, transport=self.peer)
        # A previously committed local snapshot must survive any failed sync.
        with self.app.db:
            self.app.db.execute(
                "INSERT INTO records VALUES(?,?,?,?,?)",
                ("previous", "memory", "Previous saved value", "", json.dumps({"id": "previous", "kind": "memory"})),
            )

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def sync(self):
        with patch("auday.time.sleep", return_value=None):
            return self.app.sync()

    def assert_old_cache(self):
        self.assertEqual(self.app.read("previous")["record"]["id"], "previous")

    def test_on_demand_preparation_and_large_unicode_record_roundtrip(self):
        self.assertGreater(len(self.peer.body), 2 * 1024 * 1024)
        result = self.sync()
        self.assertEqual(result["recordCount"], 1)
        self.assertEqual(self.peer.prepare_calls, 1)
        self.assertEqual(self.app.read(self.peer.record["id"])["record"], self.peer.record)
        self.assertLess(self.peer.max_frame, 2 * 1024 * 1024)
        self.assertFalse(list((self.app.root / "record-staging").glob("*.partial")))
        calls = len(self.peer.chunk_offsets)
        self.assertEqual(self.sync()["downloadedRecords"], 0)
        self.assertEqual(len(self.peer.chunk_offsets), calls)

    def test_interruption_resumes_at_verified_prefix_without_replacing_cache(self):
        self.peer.interrupt_after = 2
        with self.assertRaises(ConnectionError):
            self.sync()
        self.assert_old_cache()
        self.peer.interrupt_after = None
        self.sync()
        self.assertEqual(self.peer.chunk_offsets[2], 2 * 262144)
        self.assertEqual(self.app.read(self.peer.record["id"])["record"], self.peer.record)

    def test_corrupt_chunk_is_rejected_before_cache_commit(self):
        self.peer.tamper = lambda reply: reply.update(chunkSHA256="0" * 64)
        with self.assertRaisesRegex(ValueError, "record_chunk_hash_mismatch"):
            self.sync()
        self.assert_old_cache()

    def test_changed_snapshot_stops_chunk_download(self):
        self.peer.tamper = lambda reply: reply.update(snapshot="b" * 64)
        with self.assertRaisesRegex(ValueError, "record_chunk_metadata_mismatch"):
            self.sync()
        self.assert_old_cache()

    def test_corrupt_partial_fails_final_hash_then_can_restart(self):
        directory = self.app.root / "record-staging"
        directory.mkdir()
        (directory / (self.peer.digest + ".partial")).write_bytes(b"corrupt-prefix")
        with self.assertRaisesRegex(ValueError, "record_hash_mismatch"):
            self.sync()
        self.assert_old_cache()
        self.peer.chunk_offsets.clear()
        self.sync()
        self.assertEqual(self.peer.chunk_offsets[0], 0)

    def test_failed_preparation_does_not_loop_or_replace_cache(self):
        self.peer.fail_preparation = True
        with self.assertRaisesRegex(ValueError, "snapshot_preparation_failed"):
            self.sync()
        self.assertEqual(self.peer.prepare_calls, 1)
        self.assertEqual(self.peer.chunk_offsets, [])
        self.assert_old_cache()

    def test_preparation_wait_has_a_deadline(self):
        with patch("auday.time.monotonic", side_effect=[0, 121]):
            with self.assertRaisesRegex(ValueError, "snapshot_preparation_timed_out"):
                self.sync()
        self.assertEqual(self.peer.prepare_calls, 1)
        self.assert_old_cache()

    def test_hydrated_record_still_enforces_text_only_scope(self):
        self.peer.record["audioAssetIDs"] = ["synthetic-audio"]
        self.peer.body = json.dumps(self.peer.record, ensure_ascii=False, sort_keys=True).encode()
        self.peer.digest = hashlib.sha256(self.peer.body).hexdigest()
        with self.assertRaisesRegex(ValueError, "text_scope_contains_audio_reference"):
            self.sync()
        self.assert_old_cache()

    def test_partial_metadata_limits_are_validated(self):
        for size in (True, -1, 128 * 1024 * 1024 + 1):
            with self.subTest(size=size), self.assertRaisesRegex(ValueError, "invalid_record_transfer"):
                self.app.complete_record(
                    {"id": "synthetic", "recordTransfer": {
                        "encoding": "json-utf8", "size": size, "sha256": "a" * 64,
                    }}, "b" * 64, {"chunkedRecords": True}, [],
                )
