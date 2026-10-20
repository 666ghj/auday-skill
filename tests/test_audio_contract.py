import sys, unittest, tempfile, hashlib, base64, json, contextlib, io, os
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from refine_audio import attach_speakers, source_spans
from auday import Auday


class AudioContractTests(unittest.TestCase):
    def test_overlap_does_not_assert_identity(self):
        rows = attach_speakers(
            [{"start": 0, "end": 10, "text": "synthetic"}],
            [
                {"start": 0, "end": 8, "speaker": "speaker_01"},
                {"start": 3, "end": 10, "speaker": "speaker_02"},
            ],
        )
        self.assertEqual(rows[0]["speaker"], "unknown")
        self.assertEqual(len(rows[0]["speakerCandidates"]), 2)

    def test_uncovered_segment_stays_unknown(self):
        self.assertEqual(
            attach_speakers([{"start": 0, "end": 10}], [])[0]["speaker"], "unknown"
        )

    def test_unambiguous_turn_attaches(self):
        self.assertEqual(
            attach_speakers(
                [{"start": 1, "end": 3}],
                [{"start": 0, "end": 5, "speaker": "speaker_01"}],
            )[0]["speaker"],
            "speaker_01",
        )

    def test_timeline_gap_does_not_compress_wall_clock(self):
        timeline = [
            {
                "fileStart": 0,
                "fileEnd": 5,
                "recordedStart": "2026-10-19T09:00:00+08:00",
            },
            {
                "fileStart": 5,
                "fileEnd": 10,
                "recordedStart": "2026-10-19T09:00:30+08:00",
            },
        ]
        spans = source_spans(4, 7, timeline)
        self.assertEqual(
            spans,
            [
                {
                    "start": "2026-10-19T09:00:04+08:00",
                    "end": "2026-10-19T09:00:05+08:00",
                },
                {
                    "start": "2026-10-19T09:00:30+08:00",
                    "end": "2026-10-19T09:00:32+08:00",
                },
            ],
        )

    def test_utc_z_timestamps_are_supported(self):
        spans = source_spans(
            0,
            1,
            [{"fileStart": 0, "fileEnd": 1, "recordedStart": "2026-01-01T09:00:00Z"}],
        )
        self.assertEqual(spans[0]["start"], "2026-01-01T09:00:00+00:00")

    def test_ambiguous_timezone_rejected(self):
        with self.assertRaises(ValueError):
            source_spans(
                0,
                1,
                [
                    {
                        "fileStart": 0,
                        "fileEnd": 1,
                        "recordedStart": "2026-10-19T09:00:00",
                    }
                ],
            )


class OnDemandAudioContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.body = b"synthetic-audio-payload" * 20000
        self.snapshot = hashlib.sha256(b"synthetic-snapshot").hexdigest()
        self.revision = hashlib.sha256(b"synthetic-source-revision").hexdigest()
        self.calls = []
        self.evictions = 0
        self.wrong_id = False
        self.offline = False
        self.current = True
        self.asset_id = "sample"
        self.recorded_start = "2026-10-20T09:00:00+09:00"
        self.app = Auday(self.temporary.name, transport=self.request)

    def tearDown(self):
        self.app.close()
        self.temporary.cleanup()

    def request(self, request):
        if self.offline:
            raise RuntimeError("offline")
        op = request["op"]
        self.calls.append(op)
        base = {"ok": True, "snapshot": self.snapshot, "sourceDeviceID": "synthetic-phone"}
        if op == "status":
            return {**base, "schemaVersion": 1, "access": "text-and-audio-read-only",
                    "recordCount": 0, "assetCount": 1, "capabilities": {"onDemandAudio": True},
                    "snapshotCapturedAt": "2026-10-20T00:00:00Z", "snapshotCurrent": self.current}
        if op == "list":
            return {**base, "records": [], "nextOffset": None}
        if op == "assets":
            return {**base, "assets": [{"id": self.asset_id, "preparation": "on-demand",
                    "contentRevision": self.revision}], "nextOffset": None}
        if op == "prepareAudio":
            return {**base, "asset": {"id": "wrong" if self.wrong_id else request["id"],
                    "sha256": hashlib.sha256(self.body).hexdigest(), "size": len(self.body),
                    "mime": "audio/x-caf", "duration": 3,
                    "timeline": [{"fileStart": 0, "fileEnd": 3,
                                  "recordedStart": self.recorded_start}]}}
        if op == "audio":
            if self.evictions:
                self.evictions -= 1
                return {"ok": False, "error": "audio_not_prepared"}
            offset = request["offset"]
            chunk = self.body[offset:offset + request["length"]]
            return {**base, "data": base64.b64encode(chunk).decode(), "offset": offset,
                    "size": len(self.body), "sha256": hashlib.sha256(self.body).hexdigest(),
                    "chunkSHA256": hashlib.sha256(chunk).hexdigest()}
        raise AssertionError(op)

    def test_sync_leaves_audio_unprepared_and_download_preserves_source_mapping(self):
        self.app.sync()
        self.assertEqual(self.calls, ["status", "list", "assets"])
        with patch.dict(os.environ, {"AUDAY_PROGRESS": "1"}), \
                contextlib.redirect_stderr(io.StringIO()) as progress:
            result = self.app.audio("sample")
        self.assertEqual(Path(result["path"]).read_bytes(), self.body)
        self.assertEqual(Path(result["path"]).suffix, ".caf")
        self.assertEqual(result["asset"]["timeline"][0]["recordedStart"], "2026-10-20T09:00:00+09:00")
        self.assertIn("Preparing requested audio on phone", progress.getvalue())
        self.assertIn(f"{len(self.body)}/{len(self.body)} bytes (100%)", progress.getvalue())
        self.assertIn("Audio saved and verified", progress.getvalue())
        self.assertNotIn(self.asset_id, progress.getvalue())
        self.assertNotIn(self.snapshot, progress.getvalue())
        self.offline = True
        self.assertTrue(self.app.audio("sample")["cacheHit"])

    def test_prepared_asset_identity_must_match_requested_asset(self):
        self.app.sync()
        self.wrong_id = True
        with self.assertRaisesRegex(ValueError, "invalid_prepared_audio"):
            self.app.audio("sample")
        self.assertNotIn("audio", self.calls)

    def test_identical_audio_in_two_recordings_keeps_independent_timeline_files(self):
        self.app.sync()
        first = self.app.audio("sample")
        self.asset_id = "another-recording"
        self.recorded_start = "2026-10-21T11:00:00+09:00"
        self.snapshot = hashlib.sha256(b"second-snapshot").hexdigest()
        self.app.sync()
        second = self.app.audio(self.asset_id)
        self.assertNotEqual(first["path"], second["path"])
        first_metadata = json.loads(Path(first["path"]).with_suffix(".asset.json").read_text())
        second_metadata = json.loads(Path(second["path"]).with_suffix(".asset.json").read_text())
        self.assertEqual(first_metadata["timeline"][0]["recordedStart"], "2026-10-20T09:00:00+09:00")
        self.assertEqual(second_metadata["timeline"][0]["recordedStart"], self.recorded_start)

    def test_eviction_has_one_bounded_repreparation(self):
        self.app.sync()
        self.evictions = 1
        self.assertFalse(self.app.audio("sample")["cacheHit"])
        self.assertEqual(self.calls.count("prepareAudio"), 2)

    def test_repeated_eviction_stops_instead_of_looping(self):
        self.app.sync()
        self.evictions = 2
        with self.assertRaisesRegex(ValueError, "audio_not_prepared"):
            self.app.audio("sample")
        self.assertEqual(self.calls.count("prepareAudio"), 2)

    def test_unchanged_snapshot_updates_source_freshness(self):
        self.app.sync()
        self.current = False
        self.assertFalse(self.app.sync()["sourceSnapshotCurrent"])
        self.assertEqual(self.calls.count("list"), 1)

    def reopened_transport(self, change_snapshot=False):
        state = {"ready": False, "preparations": 0}
        def request(payload):
            op = payload["op"]
            if op == "prepareAudio" and not state["ready"]:
                return {"ok": False, "error": "snapshot_not_ready"}
            if op == "prepareSnapshot":
                state["preparations"] += 1
                state["ready"] = True
                if change_snapshot:
                    self.snapshot = "b" * 64
                op = "status"
            if op == "status":
                reply = self.request({"op": "status"})
                reply["capabilities"]["onDemandSnapshot"] = True
                reply["snapshotState"] = "ready" if state["ready"] else "idle"
                return reply
            return self.request(payload)
        self.app.transport = request
        return state

    def test_audio_after_reopening_access_prepares_once(self):
        self.app.sync()
        state = self.reopened_transport()
        result = self.app.audio("sample")
        self.assertEqual(Path(result["path"]).read_bytes(), self.body)
        self.assertEqual(state["preparations"], 1)
        self.offline = True
        self.assertTrue(self.app.audio("sample")["cacheHit"])

    def test_audio_after_reopening_requires_the_cached_snapshot_version(self):
        self.app.sync()
        state = self.reopened_transport(change_snapshot=True)
        with self.assertRaisesRegex(ValueError, "snapshot_changed"):
            self.app.audio("sample")
        self.assertEqual(state["preparations"], 1)
        self.assertNotIn("audio", self.calls)


if __name__ == "__main__":
    unittest.main(verbosity=2)
