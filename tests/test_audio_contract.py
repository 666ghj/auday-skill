import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from refine_audio import attach_speakers, source_spans


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
