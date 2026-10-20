"""Synthetic progress contracts: measured counts, bounded output, JSON isolation."""

import contextlib
import io
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import progress


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"AUDAY_PROGRESS": "1"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        progress._last_stage = None
        progress._last_emitted_at = 0.0

    def test_counts_are_throttled_but_stage_changes_and_completion_are_not(self):
        stderr, stdout = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout), \
                patch.object(progress, "monotonic", side_effect=[0, 0.1, 1.1, 1.2, 1.3]):
            progress.report("Downloading audio", 0, 100, "bytes")
            progress.report("Downloading audio", 25, 100, "bytes")
            progress.report("Downloading audio", 50, 100, "bytes")
            progress.report("Downloading audio", 100, 100, "bytes")
            progress.report("Verifying downloaded audio")
        lines = stderr.getvalue().splitlines()
        self.assertEqual(len(lines), 4)
        self.assertIn("0/100 bytes (0%)", lines[0])
        self.assertIn("50/100 bytes (50%)", lines[1])
        self.assertIn("100/100 bytes (100%)", lines[2])
        self.assertEqual(lines[3], "[Auday] Verifying downloaded audio")
        self.assertEqual(stdout.getvalue(), "")

    def test_unknown_duration_has_no_percentage_and_output_is_flushed(self):
        stream = Mock()
        with contextlib.redirect_stderr(stream):
            progress.report("Transcribing audio with Whisper.cpp")
        rendered = "".join(call.args[0] for call in stream.write.call_args_list)
        self.assertEqual(rendered, "[Auday] Transcribing audio with Whisper.cpp\n")
        stream.flush.assert_called_once()

    def test_zero_items_has_no_percentage_and_opt_out_is_silent(self):
        stream = io.StringIO()
        with contextlib.redirect_stderr(stream):
            progress.report("Receiving audio catalog", 0, 0, "assets")
        self.assertEqual(stream.getvalue(), "[Auday] Receiving audio catalog: 0/0 assets\n")
        with patch.dict(os.environ, {"AUDAY_PROGRESS": "0"}), \
                contextlib.redirect_stderr(io.StringIO()) as muted:
            progress.report("Receiving text records", 1, 1, "records", force=True)
        self.assertEqual(muted.getvalue(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
