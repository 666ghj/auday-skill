"""Synthetic contract tests for manifest-based, snapshot-bound text updates."""

import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from auday import Auday


def record(identity, text=None, **values):
    return {
        "id": identity,
        "kind": "transcript",
        "text": text if text is not None else "Synthetic text " + identity,
        "start": "2026-10-28T09:00:00+08:00",
        "timeZone": "Asia/Shanghai",
        **values,
    }


class IncrementalPeer:
    """Keep immutable synthetic generations, including distinct wire JSON bytes."""

    def __init__(self, rows, incremental=True):
        self.incremental = incremental
        self.source = "synthetic-incremental-phone"
        self.generations = {}
        self.leases = {}
        self.calls = []
        self.body_ids = []
        self.chunk_offsets = []
        self.descriptor_only = set()
        self.mutate = None
        self.before_data = None
        self.publish(rows)

    def publish(self, rows, access="text-only-read-only"):
        rows = sorted(copy.deepcopy(rows), key=lambda row: row["id"])
        # Compact JSON with original property order intentionally differs from
        # the desktop's sorted, spaced serialization. Hash the exact wire bytes.
        bodies = {
            row["id"]: json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            for row in rows
        }
        identity = hashlib.sha256(
            access.encode() + b"\0" + b"\0".join(bodies.values())
        ).hexdigest()
        self.generations[identity] = {"rows": rows, "bodies": bodies, "access": access}
        self.snapshot = identity

    @staticmethod
    def descriptor(identity, body):
        return {"id": identity, "recordTransfer": {
            "encoding": "json-utf8", "size": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
        }}

    def reset_observations(self):
        self.calls.clear()
        self.body_ids.clear()
        self.chunk_offsets.clear()

    def __call__(self, request):
        self.calls.append(copy.deepcopy(request))
        operation = request["op"]
        snapshot = request.get("snapshot", self.snapshot)
        token = request.get("lease")
        if token is not None and operation == "status":
            snapshot = self.leases[token]
        if operation == "prepareSnapshot":
            snapshot = self.snapshot
            if request.get("useSnapshotLease"):
                token = str(uuid.uuid4())
                self.leases[token] = snapshot
        generation = self.generations[snapshot]
        reply = {
            "ok": True, "schemaVersion": 1, "snapshot": snapshot,
            "sourceDeviceID": self.source,
        }
        if operation in ("status", "prepareSnapshot"):
            capabilities = {
                "onDemandSnapshot": True, "chunkedRecords": True,
                "snapshotLeases": True,
            }
            if self.incremental:
                capabilities["incrementalRecords"] = True
            reply.update(
                access=generation["access"], snapshotState="ready",
                snapshotCurrent=snapshot == self.snapshot,
                snapshotCapturedAt="2026-10-28T01:00:00Z",
                recordCount=len(generation["rows"]), assetCount=0,
                capabilities=capabilities,
            )
            if token is not None:
                reply["lease"] = token
        elif operation == "acquireSnapshot":
            token = str(uuid.uuid4())
            self.leases[token] = snapshot
            reply["lease"] = token
        elif operation == "releaseSnapshot":
            if self.leases.pop(request["lease"], None) is None:
                raise AssertionError("unknown synthetic lease")
        else:
            if self.leases.get(token) != snapshot:
                raise AssertionError("data request lost its captured snapshot lease")
            if self.before_data is not None:
                self.before_data(request)
            if operation in ("recordManifest", "list"):
                start = request["offset"]
                page = generation["rows"][start:start + 100]
                if operation == "recordManifest":
                    if not self.incremental:
                        raise AssertionError("incremental operation sent to legacy companion")
                    values = [self.descriptor(row["id"], generation["bodies"][row["id"]]) for row in page]
                else:
                    values = copy.deepcopy(page)
                    self.body_ids.extend(row["id"] for row in page)
                reply.update(records=values, nextOffset=(
                    start + len(page) if start + len(page) < len(generation["rows"]) else None
                ))
            elif operation == "readRecords":
                identities = request["ids"]
                if not self.incremental:
                    raise AssertionError("incremental operation sent to legacy companion")
                if not 1 <= len(identities) <= 100 or len(set(identities)) != len(identities):
                    raise AssertionError("invalid synthetic batch request")
                values = []
                for identity in identities:
                    body = generation["bodies"][identity]
                    value = self.descriptor(identity, body)
                    if identity not in self.descriptor_only and len(body) < 65536:
                        value["data"] = base64.b64encode(body).decode("ascii")
                    values.append(value)
                self.body_ids.extend(identities)
                reply["records"] = values
            elif operation == "recordChunk":
                identity = request["id"]
                body = generation["bodies"][identity]
                offset, length = request["offset"], request["length"]
                chunk = body[offset:offset + length]
                self.chunk_offsets.append((identity, offset))
                reply.update(
                    id=identity, offset=offset, size=len(body),
                    sha256=hashlib.sha256(body).hexdigest(),
                    chunkSHA256=hashlib.sha256(chunk).hexdigest(),
                    data=base64.b64encode(chunk).decode("ascii"),
                )
            elif operation == "assets":
                reply.update(assets=[], nextOffset=None)
            else:
                raise AssertionError(operation)
        if self.mutate is not None:
            self.mutate(request, reply)
        return reply


class IncrementalSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="auday-incremental-synthetic-")
        self.rows = [record("stable"), record("edited"), record("removed")]
        self.peer = IncrementalPeer(self.rows)
        self.app = Auday(self.temp.name, transport=self.peer)
        self.progress = patch.dict(os.environ, {"AUDAY_PROGRESS": "0"})
        self.progress.start()

    def tearDown(self):
        self.progress.stop()
        self.app.close()
        self.temp.cleanup()

    def committed_state(self):
        return (
            list(self.app.db.execute("SELECT * FROM records ORDER BY id")),
            list(self.app.db.execute("SELECT * FROM assets ORDER BY id")),
            self.app.metadata(),
        )

    def prepare_update(self):
        self.app.sync()
        previous = self.committed_state()
        self.peer.publish([record("stable"), record("edited", "Updated 合成正文"), record("added")])
        self.peer.reset_observations()
        return previous

    def test_initial_sync_and_only_new_or_changed_bodies_on_next_update(self):
        first = self.app.sync()
        self.assertEqual(first["requestedMode"], "incremental")
        self.assertEqual(first["effectiveMode"], "incremental")
        self.assertEqual(first["downloadedRecords"], 3)
        self.assertEqual(first["reusedRecords"], 0)
        self.assertEqual(set(self.peer.body_ids), {"stable", "edited", "removed"})
        self.peer.publish([record("stable"), record("edited", "Updated 合成正文"), record("added")])
        self.peer.reset_observations()
        result = self.app.sync()
        self.assertEqual(result["downloadedRecords"], 2)
        self.assertEqual(result["reusedRecords"], 1)
        self.assertEqual(result["changedRecords"], 2)
        self.assertEqual(result["deletedRecords"], 1)
        self.assertEqual(set(self.peer.body_ids), {"edited", "added"})
        self.assertEqual(self.app.read("edited")["record"]["text"], "Updated 合成正文")
        with self.assertRaisesRegex(ValueError, "record_not_found"):
            self.app.read("removed")
        self.assertFalse(self.peer.leases)

    def test_wire_hashes_survive_reopen_without_rehashing_reserialized_json(self):
        self.app.sync()
        local = self.app.db.execute("SELECT body FROM records WHERE id='stable'").fetchone()[0].encode()
        remote = self.peer.generations[self.peer.snapshot]["bodies"]["stable"]
        self.assertNotEqual(hashlib.sha256(local).digest(), hashlib.sha256(remote).digest())
        self.app.close()
        self.app = Auday(self.temp.name, transport=self.peer)
        self.peer.publish([*self.rows, record("new")])
        self.peer.reset_observations()
        result = self.app.sync()
        self.assertEqual(self.peer.body_ids, ["new"])
        self.assertEqual(result["reusedRecords"], 3)

    def test_unchanged_snapshot_transfers_no_bodies(self):
        self.app.sync()
        self.peer.reset_observations()
        result = self.app.sync()
        self.assertEqual(result["downloadedRecords"], 0)
        self.assertEqual(result["reusedRecords"], 3)
        self.assertEqual(result["changedRecords"], 0)
        self.assertEqual(result["deletedRecords"], 0)
        self.assertEqual(self.peer.body_ids, [])
        self.assertFalse(self.peer.leases)

    def test_explicit_full_retransfers_every_body_even_for_unchanged_snapshot(self):
        self.app.sync()
        self.peer.reset_observations()
        result = self.app.sync(mode="full")
        self.assertEqual(result["requestedMode"], "full")
        self.assertEqual(result["effectiveMode"], "full")
        self.assertEqual(result["downloadedRecords"], 3)
        self.assertEqual(result["reusedRecords"], 0)
        self.assertEqual(result["changedRecords"], 0)
        self.assertEqual(sorted(self.peer.body_ids), ["edited", "removed", "stable"])

    def test_legacy_companion_falls_back_explicitly_without_new_operations(self):
        self.peer.incremental = False
        result = self.app.sync()
        self.assertEqual(result["requestedMode"], "incremental")
        self.assertEqual(result["effectiveMode"], "full")
        self.assertTrue(result["fallbackReason"])
        self.assertEqual(result["downloadedRecords"], 3)
        self.assertNotIn("recordManifest", [call["op"] for call in self.peer.calls])
        self.assertNotIn("readRecords", [call["op"] for call in self.peer.calls])

    def test_legacy_full_mode_also_ignores_snapshot_shortcut(self):
        self.peer.incremental = False
        self.app.sync()
        self.peer.reset_observations()
        result = self.app.sync(mode="full")
        self.assertEqual(result["downloadedRecords"], 3)
        self.assertEqual(len(self.peer.body_ids), 3)

    def test_local_body_corruption_is_repaired_with_same_snapshot(self):
        self.app.sync()
        damaged = record("stable", "Locally corrupted synthetic content")
        with self.app.db:
            self.app.db.execute("UPDATE records SET body=? WHERE id='stable'", (json.dumps(damaged),))
        self.peer.reset_observations()
        result = self.app.sync()
        self.assertEqual(self.peer.body_ids, ["stable"])
        self.assertEqual(result["downloadedRecords"], 1)
        self.assertEqual(self.app.read("stable")["record"], record("stable"))

    def test_missing_local_record_is_repaired_with_same_snapshot(self):
        self.app.sync()
        with self.app.db:
            self.app.db.execute("DELETE FROM records WHERE id='stable'")
        self.peer.reset_observations()
        result = self.app.sync()
        self.assertEqual(self.peer.body_ids, ["stable"])
        self.assertEqual(result["recordCount"], 3)
        self.assertEqual(self.app.read("stable")["record"], record("stable"))

    def test_empty_remote_snapshot_removes_only_the_query_projection(self):
        self.app.sync()
        audio = self.app.root / "audio" / "synthetic-downloaded.bin"
        audio.parent.mkdir()
        audio.write_bytes(b"independent verified synthetic audio")
        self.peer.publish([])
        self.peer.reset_observations()
        result = self.app.sync()
        self.assertEqual(result["recordCount"], 0)
        self.assertEqual(result["downloadedRecords"], 0)
        self.assertEqual(result["deletedRecords"], 3)
        self.assertEqual(self.app.db.execute("SELECT COUNT(*) FROM records").fetchone()[0], 0)
        self.assertEqual(audio.read_bytes(), b"independent verified synthetic audio")

    def test_pagination_and_batches_cover_complete_manifest(self):
        rows = [record(f"synthetic-{number:04d}") for number in range(237)]
        self.peer.publish(rows)
        result = self.app.sync()
        self.assertEqual(result["downloadedRecords"], len(rows))
        pages = [call["offset"] for call in self.peer.calls if call["op"] == "recordManifest"]
        self.assertEqual(pages, [0, 100, 200])
        batches = [call["ids"] for call in self.peer.calls if call["op"] == "readRecords"]
        self.assertTrue(batches)
        self.assertTrue(all(1 <= len(batch) <= 100 for batch in batches))
        self.assertEqual(sorted(identity for batch in batches for identity in batch), sorted(row["id"] for row in rows))
        self.assertEqual(len(self.peer.body_ids), len(set(self.peer.body_ids)))

    def test_descriptor_only_batch_entries_use_verified_record_chunks(self):
        large = record("large", "合成 Unicode " * 90000)
        self.peer.publish([record("small"), large])
        self.peer.descriptor_only.add("small")
        result = self.app.sync()
        self.assertEqual(result["downloadedRecords"], 2)
        self.assertEqual(self.app.read("large")["record"], large)
        self.assertEqual({identity for identity, _ in self.peer.chunk_offsets}, {"large", "small"})
        self.assertGreater(len(self.peer.chunk_offsets), 2)

    def test_new_phone_arrivals_do_not_change_captured_manifest(self):
        old_snapshot = self.peer.snapshot
        switched = False

        def publish_during_transfer(request):
            nonlocal switched
            if request["op"] == "recordManifest" and not switched:
                switched = True
                self.peer.publish([*self.rows, record("later")])

        self.peer.before_data = publish_during_transfer
        result = self.app.sync()
        self.assertEqual(result["snapshot"], old_snapshot)
        self.assertEqual(result["recordCount"], 3)
        self.assertFalse(result["sourceSnapshotCurrent"])
        self.assertEqual(sum(call["op"] == "prepareSnapshot" for call in self.peer.calls), 1)
        with self.assertRaisesRegex(ValueError, "record_not_found"):
            self.app.read("later")
        self.assertFalse(self.peer.leases)

    def test_reduced_scope_commits_phone_projection_without_audio_references(self):
        rows = [record("stable", audioAssetIDs=["synthetic-audio"])]
        self.peer.publish(rows, access="text-and-audio-read-only")
        self.app.sync()
        self.peer.publish([record("stable")], access="text-only-read-only")
        self.peer.reset_observations()
        result = self.app.sync()
        self.assertEqual(result["access"], "text-only-read-only")
        self.assertEqual(result["downloadedRecords"], 1)
        self.assertNotIn("audioAssetIDs", self.app.read("stable")["record"])

    def test_text_only_body_cannot_smuggle_audio_references(self):
        previous = self.prepare_update()
        self.peer.publish([record("bad", audioAssetIDs=["synthetic-audio"])])
        with self.assertRaises(ValueError):
            self.app.sync()
        self.assertEqual(self.committed_state(), previous)
        self.assertFalse(self.peer.leases)

    def test_changed_device_is_rejected_without_replacing_library(self):
        previous = self.prepare_update()
        self.peer.source = "another-synthetic-phone"
        with self.assertRaisesRegex(ValueError, "source_device_changed"):
            self.app.sync()
        self.assertEqual(self.committed_state(), previous)

    def test_corrupt_inline_bytes_do_not_commit_partial_update_or_deletions(self):
        previous = self.prepare_update()

        def corrupt(request, reply):
            if request["op"] == "readRecords":
                reply["records"][0]["data"] = base64.b64encode(b"{}").decode("ascii")

        self.peer.mutate = corrupt
        with self.assertRaises(ValueError):
            self.app.sync()
        self.assertEqual(self.committed_state(), previous)
        self.assertFalse(self.peer.leases)

    def test_invalid_manifest_entries_do_not_commit_or_fall_back_silently(self):
        previous = self.prepare_update()
        invalid = [
            lambda rows: rows.append(copy.deepcopy(rows[0])),
            lambda rows: rows.pop(),
            lambda rows: rows[0]["recordTransfer"].update(size=True),
            lambda rows: rows[0]["recordTransfer"].update(size=0),
            lambda rows: rows[0]["recordTransfer"].update(size=128 * 1024 * 1024 + 1),
            lambda rows: rows[0]["recordTransfer"].update(sha256="not-a-sha256"),
            lambda rows: rows[0]["recordTransfer"].update(encoding="other"),
        ]
        for change in invalid:
            with self.subTest(change=change):
                self.peer.reset_observations()

                def corrupt(request, reply):
                    if request["op"] == "recordManifest":
                        change(reply["records"])

                self.peer.mutate = corrupt
                with self.assertRaises(ValueError):
                    self.app.sync()
                self.assertEqual(self.committed_state(), previous)
                self.assertFalse(self.peer.leases)
                self.assertNotIn("list", [call["op"] for call in self.peer.calls])

    def test_manifest_cannot_switch_snapshot_mid_page(self):
        previous = self.prepare_update()

        def corrupt(request, reply):
            if request["op"] == "recordManifest":
                reply["snapshot"] = "0" * 64

        self.peer.mutate = corrupt
        with self.assertRaises(ValueError):
            self.app.sync()
        self.assertEqual(self.committed_state(), previous)
        self.assertFalse(self.peer.leases)

    def test_incomplete_or_unrequested_batch_is_rejected_atomically(self):
        previous = self.prepare_update()
        alterations = [
            lambda values: values.pop(),
            lambda values: values.append(copy.deepcopy(values[0])),
            lambda values: values[0].update(id="unrequested-synthetic-id"),
            lambda values: values[0]["recordTransfer"].update(sha256="0" * 64),
        ]
        for change in alterations:
            with self.subTest(change=change):
                def corrupt(request, reply):
                    if request["op"] == "readRecords":
                        change(reply["records"])

                self.peer.mutate = corrupt
                with self.assertRaises(ValueError):
                    self.app.sync()
                self.assertEqual(self.committed_state(), previous)
                self.assertFalse(self.peer.leases)

    def test_interrupted_download_does_not_commit_manifest_deletions(self):
        previous = self.prepare_update()

        def interrupt(request, reply):
            if request["op"] == "readRecords":
                raise ConnectionError("synthetic interruption")

        self.peer.mutate = interrupt
        with self.assertRaises(ConnectionError):
            self.app.sync()
        self.assertEqual(self.committed_state(), previous)
        self.assertFalse(self.peer.leases)

    def test_unknown_mode_is_rejected_before_contacting_phone(self):
        with self.assertRaises(ValueError):
            self.app.sync(mode="unexpected")
        self.assertEqual(self.peer.calls, [])


if __name__ == "__main__":
    unittest.main()
