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
        self.texts = ["First voice", "Second voice"]
        self.arguments = [
            "refine_audio.py", "--audio", str(self.audio),
            "--model", str(self.model), "--output", str(self.output),
        ]

    def synthetic_process(self, command, **kwargs):
        def child_path(value):
            path = Path(value)
            return Path(kwargs["cwd"]) / path if kwargs.get("cwd") and not path.is_absolute() else path
        if command[0] == "ffmpeg":
            shutil.copyfile(self.audio, child_path(command[-1]))
        elif command[0] == "whisper-cli":
            prefix = command[command.index("-of") + 1]
            child_path(prefix + ".json").write_text(json.dumps({
                "transcription": [
                    {"offsets": {"from": 0, "to": 1000}, "text": self.texts[0]},
                    {"offsets": {"from": 1000, "to": 2000}, "text": self.texts[1]},
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

    def execute_engine_json(self, body, *, windows=False):
        def process(command, **kwargs):
            if command[0] != "whisper-cli":
                return self.synthetic_process(command, **kwargs)
            self.assertIn("-oj", command)
            self.assertNotIn("-ojf", command)
            prefix = command[command.index("-of") + 1]
            path = Path(kwargs["cwd"]) / prefix if kwargs.get("cwd") else Path(prefix)
            path.with_suffix(".json").write_bytes(body)

        stream, progress = io.StringIO(), io.StringIO()
        code = 0
        with patch.object(sys, "argv", self.arguments + ["--no-diarize"]), \
                patch.object(refine_audio.shutil, "which", return_value="/synthetic/tool"), \
                patch.object(refine_audio.subprocess, "run", side_effect=process), \
                patch.object(refine_audio, "is_windows", return_value=windows), \
                patch.dict(os.environ, {"AUDAY_PROGRESS": "1"}), \
                contextlib.redirect_stdout(stream), contextlib.redirect_stderr(progress):
            try:
                refine_audio.main()
            except SystemExit as error:
                code = error.code
        self.progress = progress.getvalue()
        self.assertEqual((self.output / "whisper.json").read_bytes(), body)
        return code, json.loads(stream.getvalue())

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

    def test_cjk_transcript_survives_malformed_utf8_from_engine(self):
        def process(command, **kwargs):
            if command[0] != "whisper-cli":
                return self.synthetic_process(command, **kwargs)
            self.assertIn("-oj", command)
            self.assertNotIn("-ojf", command)
            prefix = command[command.index("-of") + 1]
            path = Path(kwargs["cwd"]) / prefix if kwargs.get("cwd") else Path(prefix)
            body = json.dumps({"transcription": [
                {"offsets": {"from": 0, "to": 1000}, "text": "こんにちは"},
                {"offsets": {"from": 1000, "to": 2000}, "text": "FRAGMENT"},
            ]}, ensure_ascii=False).encode("utf-8")
            # A lone lead byte of a three-byte character, as a split token emits.
            path.with_suffix(".json").write_bytes(body.replace(b"FRAGMENT", b"\xe3\x81"))
        stream, progress = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", self.arguments + ["--no-diarize"]), \
                patch.object(refine_audio.shutil, "which", return_value="/synthetic/tool"), \
                patch.object(refine_audio.subprocess, "run", side_effect=process), \
                patch.dict(sys.modules, {"sherpa_onnx": None, "soundfile": None}), \
                patch.dict(os.environ, {"AUDAY_PROGRESS": "1"}), \
                contextlib.redirect_stdout(stream), contextlib.redirect_stderr(progress):
            refine_audio.main()
        self.assertTrue(json.loads(stream.getvalue())["ok"])
        result = json.loads((self.output / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["segments"][0]["text"], "こんにちは")
        self.assertEqual(result["segments"][1]["text"], "\ufffd")
        self.assertTrue(any("invalid UTF-8" in note for note in result["notes"]))
        transcript = (self.output / "transcript.md").read_text(encoding="utf-8")
        self.assertIn("こんにちは", transcript)
        self.assertIn("invalid UTF-8", transcript)
        self.assertIn("invalid UTF-8", progress.getvalue())
        self.assertNotIn("こんにちは", progress.getvalue())

    def test_split_token_bytes_preserve_complete_cjk_segments_and_timing(self):
        texts = ["今日は晴れです。", "今天讨论项目。 한국어도 보존합니다."]
        raw = json.dumps({"transcription": [
            {"offsets": {"from": 0, "to": 1000}, "text": texts[0],
             "tokens": [{"text": "TOKEN_LEAD"}, {"text": "TOKEN_TAIL"}]},
            {"offsets": {"from": 1000, "to": 2000}, "text": texts[1]},
        ]}, ensure_ascii=False).encode("utf-8")
        raw = raw.replace(b"TOKEN_LEAD", b"\xe3").replace(b"TOKEN_TAIL", b"\x81\x93")
        original = self.audio.read_bytes()
        for windows in (False, True):
            with self.subTest(windows=windows):
                self.output = self.root / ("windows-token-output" if windows else "mac-token-output")
                self.arguments[-1] = str(self.output)
                code, reply = self.execute_engine_json(raw, windows=windows)
                self.assertEqual(code, 0)
                self.assertTrue(reply["ok"])
                result = json.loads((self.output / "result.json").read_text(encoding="utf-8"))
                self.assertEqual([row["text"] for row in result["segments"]], texts)
                self.assertEqual([(row["start"], row["end"]) for row in result["segments"]], [(0, 1), (1, 2)])
                self.assertTrue(any("invalid UTF-8" in note for note in result["notes"]))
                self.assertEqual(self.audio.read_bytes(), original)
                self.assertNotIn(str(self.root), self.progress)
                self.assertTrue(all(text not in self.progress for text in texts))

    def test_valid_utf8_cjk_and_literal_replacement_character_do_not_warn(self):
        texts = ["中文、日本語、한국어。", "The literal replacement character is \ufffd."]
        raw = json.dumps({"transcription": [
            {"offsets": {"from": index * 1000, "to": (index + 1) * 1000}, "text": text}
            for index, text in enumerate(texts)
        ]}, ensure_ascii=False).encode("utf-8")
        code, reply = self.execute_engine_json(raw)
        self.assertEqual(code, 0)
        self.assertTrue(reply["ok"])
        result = json.loads((self.output / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["schemaVersion"], 1)
        self.assertEqual([row["text"] for row in result["segments"]], texts)
        self.assertFalse(any("invalid UTF-8" in note for note in result["notes"]))
        self.assertNotIn("invalid UTF-8", (self.output / "transcript.md").read_text(encoding="utf-8"))
        self.assertNotIn("invalid UTF-8", self.progress)

    def test_corrupt_or_missing_transcription_structure_cannot_succeed_as_empty(self):
        bodies = [
            b'{"transcrip\xe3\x81tion": []}',
            b'{}',
            b'[]',
            b'{"transcription": null}',
            b'{"transcription": {}}',
            b'{"transcription": ""}',
            b'{"transcription": false}',
            b'{"transcription": [{',
        ]
        for index, raw in enumerate(bodies):
            with self.subTest(body=raw):
                self.output = self.root / f"invalid-engine-{index}"
                self.arguments[-1] = str(self.output)
                code, reply = self.execute_engine_json(raw)
                self.assertEqual(code, 1)
                self.assertFalse(reply["ok"])
                self.assertFalse((self.output / "result.json").exists())
                self.assertFalse((self.output / "transcript.md").exists())

    def test_valid_empty_transcription_remains_a_successful_empty_result(self):
        code, reply = self.execute_engine_json(b'{"transcription": []}')
        self.assertEqual(code, 0)
        self.assertTrue(reply["ok"])
        self.assertEqual(reply["segments"], 0)
        result = json.loads((self.output / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["segments"], [])
        self.assertFalse(any("invalid UTF-8" in note for note in result["notes"]))

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

    def test_windows_unicode_output_uses_ascii_native_arguments_and_utf8_results(self):
        self.output = self.root / "录音 日本語 output"
        self.arguments[-1] = str(self.output)
        self.texts = ["今天讨论项目。", "明日の予定です。"]
        source = self.audio.read_bytes()
        with patch.object(refine_audio, "is_windows", return_value=True):
            reply, processes = self.execute(["--no-diarize"])
        self.assertTrue(reply["ok"])
        self.assertEqual(self.audio.read_bytes(), source)
        for call in processes.call_args_list:
            command = call.args[0]
            self.assertEqual(call.kwargs["cwd"], self.output.resolve())
            for argument in command:
                self.assertTrue(argument.isascii(), argument)
        self.assertFalse(list(self.output.glob(".native-*")))
        self.assertIn(self.texts[0], (self.output / "transcript.md").read_text(encoding="utf-8"))
        self.assertIn(self.texts[1], (self.output / "result.json").read_text(encoding="utf-8"))

    def test_windows_cross_volume_aliases_are_removed_after_failure(self):
        self.output.mkdir()
        source = self.model.read_bytes()
        previous = Path.cwd()
        with patch.object(refine_audio, "is_windows", return_value=True), \
                patch.object(refine_audio.os, "link", side_effect=OSError("different volume")):
            with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                with refine_audio._native_inputs({"model": self.model}, self.output) as native:
                    with refine_audio._working_directory(native["cwd"]):
                        self.assertFalse(Path(native["model"]).is_absolute())
                        self.assertTrue(native["model"].isascii())
                        self.assertEqual(Path(native["model"]).read_bytes(), source)
                        raise RuntimeError("synthetic failure")
        self.assertEqual(Path.cwd(), previous)
        self.assertEqual(self.model.read_bytes(), source)
        self.assertFalse(list(self.output.glob(".native-*")))



if __name__ == "__main__":
    unittest.main(verbosity=2)
