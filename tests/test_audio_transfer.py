"""Audio transfer size negotiation and multi-asset fetches, with a synthetic phone."""

import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from auday import Auday

KIB = 1024


class SyntheticPhone:
    """An in-memory companion; `accepted` is the chunk size it really allows."""

    def __init__(self, bodies, advertised=False, accepted=256 * KIB, leases=False):
        self.bodies = bodies
        self.advertised = advertised
        self.accepted = accepted
        self.leases = leases
        self.snapshot = hashlib.sha256(b"synthetic-snapshot").hexdigest()
        self.calls = []
        self.audio_lengths = []
        self.unavailable = set()
        self.failures = []
        self.offline = False

    def __call__(self, request):
        if self.offline:
            raise OSError("synthetic_disconnect")
        op = request["op"]
        self.calls.append(op)
        base = {"ok": True, "snapshot": self.snapshot, "sourceDeviceID": "synthetic-phone"}
        if op == "status":
            capabilities = {"onDemandAudio": True}
            if self.advertised is not False:
                capabilities["largeAudioChunks"] = self.advertised
            if self.leases:
                capabilities["snapshotLeases"] = True
            return {**base, "schemaVersion": 1, "access": "text-and-audio-read-only", "recordCount": 0,
                    "assetCount": len(self.bodies), "capabilities": capabilities, "snapshotCurrent": True}
        if op == "list":
            return {**base, "records": [], "nextOffset": None}
        if op == "assets":
            return {**base, "nextOffset": None, "assets": [
                {"id": identity, "preparation": "on-demand",
                 "contentRevision": hashlib.sha256(identity.encode()).hexdigest()}
                for identity in sorted(self.bodies)
            ]}
        if op == "acquireSnapshot":
            return {**base, "lease": str(uuid.uuid4())}
        if op == "releaseSnapshot":
            return base
        body = self.bodies[request["id"]]
        if op == "prepareAudio":
            if request["id"] in self.unavailable:
                return {"ok": False, "error": "audio_unavailable"}
            return {**base, "asset": {
                "id": request["id"], "sha256": hashlib.sha256(body).hexdigest(), "size": len(body),
                "mime": "audio/x-caf", "duration": 3,
                "timeline": [{"fileStart": 0, "fileEnd": 3, "recordedStart": "2026-10-20T09:00:00+09:00"}],
            }}
        if op == "audio":
            self.audio_lengths.append(request["length"])
            if self.failures:
                return {"ok": False, "error": self.failures.pop(0)}
            if request["length"] > self.accepted:
                return {"ok": False, "error": "invalid_range"}
            chunk = body[request["offset"]:request["offset"] + request["length"]]
            return {**base, "data": base64.b64encode(chunk).decode(), "offset": request["offset"],
                    "size": len(body), "sha256": hashlib.sha256(body).hexdigest(),
                    "chunkSHA256": hashlib.sha256(chunk).hexdigest()}
        raise AssertionError(op)


def synthetic_bytes(label, size):
    block = hashlib.sha256(label.encode()).digest()
    return (block * (size // len(block) + 1))[:size]


class AudioTransferTests(unittest.TestCase):
    def client(self, phone):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        app = Auday(temporary.name, transport=phone)
        self.addCleanup(app.close)
        app.sync()
        return app

    def test_large_chunks_reduce_requests_without_changing_the_file(self):
        body = synthetic_bytes("first", 2 * 1024 * KIB + 300 * KIB)
        phone = SyntheticPhone({"first": body}, advertised=True, accepted=1024 * KIB)
        result = self.client(phone).audio("first")
        self.assertEqual(Path(result["path"]).read_bytes(), body)
        self.assertEqual(phone.audio_lengths, [1024 * KIB, 1024 * KIB, 300 * KIB])
        self.assertEqual(result["transferRequests"], 3)
        self.assertEqual(result["transferredBytes"], len(body))
        self.assertEqual(set(result["timings"]), {"prepareSeconds", "transferSeconds", "verifySeconds"})

    def test_phone_without_the_capability_keeps_the_original_size(self):
        body = synthetic_bytes("first", 600 * KIB)
        phone = SyntheticPhone({"first": body})
        self.client(phone).audio("first")
        self.assertEqual(phone.audio_lengths, [256 * KIB, 256 * KIB, 88 * KIB])

    def test_only_the_boolean_capability_enables_large_chunks(self):
        body = synthetic_bytes("first", 300 * KIB)
        for advertised in (1, "true", 8 * 1024 * KIB, None):
            with self.subTest(advertised=advertised):
                phone = SyntheticPhone({"first": body}, advertised=advertised, accepted=8 * 1024 * KIB)
                self.client(phone).audio("first")
                self.assertEqual(phone.audio_lengths, [256 * KIB, 44 * KIB])

    def test_phone_rejecting_the_cached_limit_continues_with_the_original_size(self):
        bodies = {name: synthetic_bytes(name, 700 * KIB) for name in ("first", "second")}
        phone = SyntheticPhone(bodies, advertised=True, accepted=256 * KIB)
        app = self.client(phone)
        self.assertEqual(Path(app.audio("first")["path"]).read_bytes(), bodies["first"])
        self.assertEqual(phone.audio_lengths, [700 * KIB, 256 * KIB, 256 * KIB, 188 * KIB])
        phone.audio_lengths.clear()
        self.assertEqual(Path(app.audio("second")["path"]).read_bytes(), bodies["second"])
        self.assertEqual(phone.audio_lengths, [256 * KIB, 256 * KIB, 188 * KIB])

    def test_link_too_slow_for_a_large_frame_continues_with_the_original_size(self):
        body = synthetic_bytes("first", 700 * KIB)
        phone = SyntheticPhone({"first": body}, advertised=True, accepted=1024 * KIB)
        phone.failures = ["awaiting_phone_or_wrong_password"]
        result = self.client(phone).audio("first")
        self.assertEqual(Path(result["path"]).read_bytes(), body)
        self.assertEqual(phone.audio_lengths, [700 * KIB, 256 * KIB, 256 * KIB, 188 * KIB])

    def test_original_size_failure_is_not_retried(self):
        phone = SyntheticPhone({"first": synthetic_bytes("first", 700 * KIB)})
        phone.failures = ["awaiting_phone_or_wrong_password"]
        with self.assertRaisesRegex(ValueError, "awaiting_phone_or_wrong_password"):
            self.client(phone).audio("first")
        self.assertEqual(phone.audio_lengths, [256 * KIB])

    def test_batch_reuses_cache_and_continues_past_one_unavailable_asset(self):
        bodies = {name: synthetic_bytes(name, 300 * KIB) for name in ("a", "b", "c")}
        phone = SyntheticPhone(bodies)
        app = self.client(phone)
        app.audio("a")
        phone.unavailable.add("b")
        phone.calls.clear()
        summary = app.audio_batch(["a", "b", "c", "c", "missing"])
        self.assertEqual((summary["requested"], summary["cached"], summary["downloaded"]), (4, 1, 1))
        self.assertEqual(summary["failed"], [{"id": "b", "error": "audio_unavailable"},
                                             {"id": "missing", "error": "asset_not_in_synced_snapshot"}])
        self.assertEqual((summary["stopped"], summary["remaining"], summary["complete"]), (None, 0, False))
        self.assertEqual(summary["bytesDownloaded"], 300 * KIB)
        self.assertEqual([row["id"] for row in summary["assets"]], ["a", "c"])
        self.assertEqual(Path(summary["assets"][1]["path"]).read_bytes(), bodies["c"])

    def test_whole_catalogue_batch_resumes_after_the_phone_disappears(self):
        bodies = {name: synthetic_bytes(name, 600 * KIB) for name in ("a", "b", "c")}
        phone = SyntheticPhone(bodies)
        app = self.client(phone)
        answer = phone.__call__

        def disappearing(request):
            if request["op"] == "audio" and request["id"] == "b" and request["offset"]:
                raise OSError("synthetic_disconnect")
            return answer(request)

        app.transport = disappearing
        stopped = app.audio_batch()
        self.assertEqual((stopped["downloaded"], stopped["stopped"], stopped["remaining"]), (1, "OSError", 2))
        self.assertFalse(stopped["complete"])
        self.assertNotIn("assets", stopped)
        partial = list((app.root / "audio").glob("*.partial"))
        self.assertEqual([path.stat().st_size for path in partial], [256 * KIB])

        app.transport = phone
        phone.audio_lengths.clear()
        resumed = app.audio_batch()
        self.assertEqual((resumed["cached"], resumed["downloaded"], resumed["complete"]), (1, 2, True))
        self.assertEqual(resumed["bytesDownloaded"], 2 * 600 * KIB - 256 * KIB)
        saved = sorted(path.read_bytes() for path in (app.root / "audio").glob("*.caf"))
        self.assertEqual(saved, sorted(bodies.values()))

    def test_cached_batch_needs_no_phone(self):
        bodies = {name: synthetic_bytes(name, 100 * KIB) for name in ("a", "b")}
        phone = SyntheticPhone(bodies, leases=True)
        app = self.client(phone)
        self.assertTrue(app.audio_batch()["complete"])
        phone.offline = True
        summary = app.audio_batch()
        self.assertEqual((summary["cached"], summary["downloaded"], summary["complete"]), (2, 0, True))

    def test_text_only_scope_refuses_a_batch_instead_of_reporting_nothing_to_do(self):
        app = self.client(SyntheticPhone({}))
        with app.db:
            app.db.execute("INSERT OR REPLACE INTO metadata VALUES('access', '\"text-only-read-only\"')")
        for identities in (None, ["a"]):
            with self.assertRaisesRegex(ValueError, "audio_access_denied"):
                app.audio_batch(identities)

    def test_batch_holds_one_snapshot_lease_for_every_asset(self):
        bodies = {name: synthetic_bytes(name, 100 * KIB) for name in ("a", "b", "c")}
        phone = SyntheticPhone(bodies, leases=True)
        app = self.client(phone)
        phone.calls.clear()
        self.assertTrue(app.audio_batch()["complete"])
        self.assertEqual(phone.calls.count("acquireSnapshot"), 1)
        self.assertEqual(phone.calls[-1], "releaseSnapshot")
        self.assertEqual(phone.calls.count("releaseSnapshot"), 1)
        self.assertIsNone(app.snapshot_lease)

    def test_command_line_keeps_the_single_result_and_summarizes_several(self):
        bodies = {name: synthetic_bytes(name, 100 * KIB) for name in ("a", "b")}
        app = self.client(SyntheticPhone(bodies))
        self.assertTrue(app.audio_batch()["complete"])

        def command(*arguments):
            # Every file is cached, so no phone or pairing is involved.
            result = subprocess.run(
                [sys.executable, "-X", "utf8", str(ROOT / "scripts/auday.py"), "--home", str(app.root.parent),
                 "audio", *arguments],
                capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            return result.returncode, json.loads(result.stdout)

        code, single = command("a")
        self.assertEqual((code, single["cacheHit"]), (0, True))
        self.assertEqual(Path(single["path"]).read_bytes(), bodies["a"])
        code, several = command("a", "b")
        self.assertEqual((code, several["cached"], several["complete"]), (0, 2, True))
        self.assertEqual([row["id"] for row in several["assets"]], ["a", "b"])
        code, whole = command("--all")
        self.assertEqual((code, whole["requested"], whole["complete"]), (0, 2, True))
        self.assertNotIn("assets", whole)
        code, partial = command("a", "missing")
        self.assertEqual((code, partial["complete"]), (1, False))
        self.assertEqual(partial["failed"], [{"id": "missing", "error": "asset_not_in_synced_snapshot"}])
        for arguments in ((), ("a", "--all")):
            code, refused = command(*arguments)
            self.assertEqual((code, refused["error"]), (1, "audio_requires_asset_ids_or_all"))


if __name__ == "__main__":
    unittest.main()
