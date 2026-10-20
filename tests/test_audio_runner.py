"""Synthetic runner contracts; no model downloads or real inference."""

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import refine_audio


class AudioRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.audio = self.root / "synthetic.wav"
        with wave.open(str(self.audio), "wb") as output:
            output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            output.writeframes(b"\0\0" * 32000)
        self.model = self.root / "asr.bin"
        self.segmentation = self.root / "segmentation.onnx"
        self.embedding = self.root / "embedding.onnx"
        for path in (self.model, self.segmentation, self.embedding):
            path.write_bytes(b"synthetic-model")
        self.output = self.root / "result"
        self.arguments = [
            "refine_audio.py", "--audio", str(self.audio),
            "--model", str(self.model), "--output", str(self.output),
        ]

    def synthetic_process(self, command, **kwargs):
        if command[0] == "ffmpeg":
            shutil.copyfile(self.audio, command[-1])
        elif command[0] == "whisper-cli":
            prefix = command[command.index("-of") + 1]
            Path(prefix + ".json").write_text(json.dumps({
                "transcription": [
                    {"offsets": {"from": 0, "to": 1000}, "text": "First voice"},
                    {"offsets": {"from": 1000, "to": 2000}, "text": "Second voice"},
                ],
            }))
        else:
            self.fail("Unexpected subprocess")

    def execute(self, extra=()):
        stream, progress_stream = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", self.arguments + list(extra)), \
                patch.object(refine_audio.shutil, "which", return_value="/synthetic/tool"), \
                patch.object(refine_audio.subprocess, "run",
                             side_effect=self.synthetic_process) as processes, \
                patch.dict(os.environ, {"AUDAY_PROGRESS": "1"}), \
                contextlib.redirect_stdout(stream), contextlib.redirect_stderr(progress_stream):
            refine_audio.main()
        self.progress = progress_stream.getvalue()
        return json.loads(stream.getvalue()), processes

    def test_default_runs_diarization_and_preserves_source(self):
        source = self.audio.read_bytes()
        turns = [
            SimpleNamespace(start=0, end=1, speaker=0),
            SimpleNamespace(start=1, end=2, speaker=1),
        ]
        engine = Mock(return_value=SimpleNamespace(
            sample_rate=16000,
            process=lambda samples: SimpleNamespace(sort_by_start_time=lambda: turns),
        ))
        sherpa = SimpleNamespace(
            __version__="synthetic",
            OfflineSpeakerDiarization=engine,
            OfflineSpeakerDiarizationConfig=Mock(
                return_value=SimpleNamespace(validate=lambda: True)),
            OfflineSpeakerSegmentationModelConfig=Mock(),
            OfflineSpeakerSegmentationPyannoteModelConfig=Mock(),
            SpeakerEmbeddingExtractorConfig=Mock(),
            FastClusteringConfig=Mock(),
        )
        with patch.dict(sys.modules, {
            "sherpa_onnx": sherpa,
            "soundfile": SimpleNamespace(read=lambda *a, **kw: ([0.0], 16000)),
        }):
            reply, processes = self.execute([
                "--segmentation-model", str(self.segmentation),
                "--embedding-model", str(self.embedding),
            ])
        self.assertTrue(reply["ok"])
        self.assertEqual(processes.call_count, 2)
        engine.assert_called_once()
        result = json.loads((self.output / "result.json").read_text())
        self.assertEqual(result["diarization"]["status"], "completed")
        self.assertEqual(result["diarization"]["speakerCount"], 2)
        self.assertEqual(
            [row["speaker"] for row in result["segments"]],
            ["speaker_01", "speaker_02"],
        )
        self.assertIn("speaker_01", (self.output / "transcript.md").read_text())
        self.assertEqual(self.audio.read_bytes(), source)
        self.assertIn("Identifying anonymous speaker turns", self.progress)
        self.assertIn("Speaker diarization complete", self.progress)
        self.assertIn("Local audio analysis complete", self.progress)
        self.assertNotIn("First voice", self.progress)
        self.assertNotIn(str(self.root), self.progress)
        self.assertNotIn("%", self.progress)

    def test_explicit_opt_out_needs_no_speaker_models_or_packages(self):
        with patch.dict(sys.modules, {"sherpa_onnx": None, "soundfile": None}):
            reply, processes = self.execute(["--no-diarize"])
        self.assertTrue(reply["ok"])
        self.assertEqual(processes.call_count, 2)
        result = json.loads((self.output / "result.json").read_text())
        self.assertEqual(result["diarization"]["status"], "not_requested")
        transcript = (self.output / "transcript.md").read_text()
        self.assertIn("- [00:00.00] First voice", transcript)
        self.assertNotIn("unknown：", transcript)
        self.assertNotIn("speaker_01", transcript)
        self.assertIn("Transcribing audio with Whisper.cpp", self.progress)
        self.assertIn("Transcription complete", self.progress)
        self.assertNotIn("Identifying anonymous speaker turns", self.progress)

    def test_default_missing_models_stops_before_transcription(self):
        stream = io.StringIO()
        with patch.object(sys, "argv", self.arguments), \
                patch.object(refine_audio.subprocess, "run") as processes, \
                contextlib.redirect_stdout(stream), self.assertRaises(SystemExit) as caught:
            refine_audio.main()
        self.assertEqual(caught.exception.code, 1)
        self.assertIn("--no-diarize", json.loads(stream.getvalue())["error"])
        processes.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_missing_weight_file_stops_before_transcription(self):
        arguments = self.arguments + [
            "--segmentation-model", str(self.root / "absent.onnx"),
            "--embedding-model", str(self.embedding),
        ]
        stream = io.StringIO()
        with patch.object(sys, "argv", arguments), \
                patch.object(refine_audio.subprocess, "run") as processes, \
                contextlib.redirect_stdout(stream), self.assertRaises(SystemExit):
            refine_audio.main()
        self.assertEqual(json.loads(stream.getvalue())["error"], "local_diarization_weights_missing")
        processes.assert_not_called()
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
