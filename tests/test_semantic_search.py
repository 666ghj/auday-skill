"""Index contracts use deterministic test vectors; model quality is a separate opt-in test."""

import contextlib
import json
import io
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import semantic_search as semantic


def library(root, rows, snapshot="synthetic-v1"):
    with contextlib.closing(sqlite3.connect(root / "library.sqlite")) as db:
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
        db.commit()


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
        if os.name != "nt":
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
        with contextlib.closing(sqlite3.connect(self.root / "library.sqlite")) as db:
            db.execute("UPDATE metadata SET value=? WHERE key='syncedAt'",
                       (json.dumps("2026-10-21T00:00:00Z"),))
            db.commit()
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
        with contextlib.closing(sqlite3.connect(self.root / "library.sqlite")) as db:
            db.execute("DELETE FROM metadata")
            db.commit()
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
            with self.assertRaisesRegex(ValueError, "index_busy"):
                semantic.search(self.root, "sample", encoder=self.encoder)
            with self.assertRaisesRegex(ValueError, "index_busy"):
                semantic.status(self.root)

    def test_unicode_path_can_publish_and_replace_index(self):
        root = self.root / "Auday 数据 日本語"
        root.mkdir()
        library(root, self.rows)
        semantic.build_index(root, encoder=self.encoder)
        library(root, self.rows + [{"id": "extra", "text": "privacy"}], "synthetic-v2")
        semantic.build_index(root, encoder=self.encoder)
        self.assertEqual(len(semantic.search(root, "sample", encoder=self.encoder)["records"]), 3)

    def test_all_sqlite_connections_are_closed_before_replace_and_failure_cleanup(self):
        semantic.build_index(self.root, encoder=self.encoder)
        library(self.root, self.rows + [{"id": "extra", "text": "privacy"}], "synthetic-v2")
        opened = []
        original_connect = sqlite3.connect
        original_replace = os.replace

        def connect(*args, **kwargs):
            result = original_connect(*args, **kwargs)
            opened.append(result)
            return result

        def replace(*args, **kwargs):
            for connection in opened:
                with self.assertRaises(sqlite3.ProgrammingError):
                    connection.execute("SELECT 1")
            return original_replace(*args, **kwargs)

        with patch.object(semantic.sqlite3, "connect", side_effect=connect), \
                patch.object(semantic.os, "replace", side_effect=replace):
            semantic.build_index(self.root, encoder=self.encoder)
        opened.clear()
        library(self.root, self.rows + [{"id": "other", "text": "other"}], "synthetic-v3")
        original_unlink = os.unlink

        def unlink(path, *args, **kwargs):
            if ".semantic-build-" in str(path):
                for connection in opened:
                    with self.assertRaises(sqlite3.ProgrammingError):
                        connection.execute("SELECT 1")
            return original_unlink(path, *args, **kwargs)

        with patch.object(semantic.sqlite3, "connect", side_effect=connect), \
                patch.object(semantic.os, "unlink", side_effect=unlink), \
                patch.object(self.encoder, "encode", side_effect=RuntimeError("synthetic_failure")):
            with self.assertRaisesRegex(RuntimeError, "synthetic_failure"):
                semantic.build_index(self.root, encoder=self.encoder)
        self.assertFalse(list(self.root.glob(".semantic-build-*")))


class LocalRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="auday-local-runtime-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "数据 日本語"
        self.root.mkdir()
        self.model = self.root / "模型.gguf"
        self.server = self.root / "llama-server.exe"
        self.model.write_bytes(b"synthetic model")
        self.server.write_bytes(b"synthetic runtime")
        self.config = {
            "modelPath": str(self.model), "serverPath": str(self.server), "policy": semantic.POLICY,
            "runtimeSHA256": semantic._runtime_identity(self.server),
        }
        self.process = Mock()
        self.process.poll.return_value = None
        self.process.stderr = io.BytesIO()
        self.progress = patch.object(semantic, "report")
        self.progress.start()
        self.addCleanup(self.progress.stop)

    def test_runtime_identity_covers_dlls_and_nested_cpu_backends(self):
        original = semantic._runtime_identity(self.server)
        (self.root / "llama.dll").write_bytes(b"library")
        with_dll = semantic._runtime_identity(self.server)
        self.assertNotEqual(original, with_dll)
        backends = self.root / "backends"
        backends.mkdir()
        backend = backends / "cpu.DLL"
        backend.write_bytes(b"backend")
        with_backend = semantic._runtime_identity(self.server)
        self.assertNotEqual(with_dll, with_backend)
        backend.write_bytes(b"changed backend")
        self.assertNotEqual(with_backend, semantic._runtime_identity(self.server))

    def test_windows_runtime_uses_authenticated_loopback_and_cleans_up(self):
        captured = {}

        def start(command, **kwargs):
            captured["command"] = command
            captured["kwargs"] = kwargs
            captured["tokenFile"] = Path(kwargs["cwd"]) / command[command.index("--api-key-file") + 1]
            captured["token"] = captured["tokenFile"].read_text(encoding="utf-8")
            return self.process

        with patch.object(semantic, "MODEL_SHA", semantic._sha(self.model)), \
                patch.object(semantic, "is_windows", return_value=True), \
                patch.object(semantic, "start_process", side_effect=start), \
                patch.object(semantic, "stop_process") as stopped, \
                patch.object(semantic.LocalEncoder, "_api", return_value={"status": "ok"}):
            with semantic.LocalEncoder(self.config) as encoder:
                command = captured["command"]
                self.assertEqual(command[command.index("--host") + 1], "127.0.0.1")
                self.assertEqual(command[command.index("-m") + 1], "model.gguf")
                self.assertEqual(command[command.index("--api-key-file") + 1], "auth")
                alias = Path(captured["kwargs"]["cwd"]) / "model.gguf"
                self.assertEqual(alias.read_bytes(), self.model.read_bytes())
                self.assertEqual(int(command[command.index("--port") + 1]), encoder.loopback_port)
                self.assertGreater(encoder.loopback_port, 0)
                self.assertIn("--offline", command)
                self.assertIn("--no-webui", command)
                self.assertIn("--no-cache-prompt", command)
                self.assertIn("--no-repack", command)
                self.assertNotIn(captured["token"], command)
                self.assertEqual(encoder.token, captured["token"])
                self.assertGreater(len(encoder.token), 30)
                self.assertTrue(captured["tokenFile"].is_file())
        stopped.assert_called_once_with(self.process, timeout=5)
        self.assertFalse(captured["tokenFile"].parent.exists())

    def test_start_failure_and_timeout_remove_private_temp_directory(self):
        for fails_to_start in (True, False):
            encoder = semantic.LocalEncoder(self.config)
            directories = []
            real_secure = semantic.secure_directory

            def secure(path):
                directories.append(path)
                return real_secure(path)

            with self.subTest(fails_to_start=fails_to_start), \
                    patch.object(semantic, "MODEL_SHA", semantic._sha(self.model)), \
                    patch.object(semantic, "is_windows", return_value=True), \
                    patch.object(semantic, "secure_directory", side_effect=secure), \
                    patch.object(semantic, "start_process", side_effect=OSError("launch") if fails_to_start else None,
                                 return_value=self.process), \
                    patch.object(semantic, "stop_process") as stopped, \
                    patch.object(semantic.time, "monotonic", side_effect=[0, 61]):
                with self.assertRaises((OSError, TimeoutError)):
                    encoder.__enter__()
                self.assertIsNone(encoder.directory)
                self.assertTrue(directories)
                self.assertFalse(directories[0].exists())
                self.assertEqual(stopped.call_count, 0 if fails_to_start else 1)

    def test_windows_unicode_paths_use_private_relative_aliases_with_copy_fallback(self):
        original_model = self.model.read_bytes()
        captured = {}

        def start(command, **kwargs):
            captured["directory"] = Path(kwargs["cwd"])
            self.assertTrue(captured["directory"].is_dir())
            self.assertEqual(command[command.index("-m") + 1], "model.gguf")
            self.assertEqual(command[command.index("--api-key-file") + 1], "auth")
            self.assertEqual((captured["directory"] / "model.gguf").read_bytes(), original_model)
            return self.process

        with patch.object(semantic, "MODEL_SHA", semantic._sha(self.model)), \
                patch.object(semantic, "is_windows", return_value=True), \
                patch.object(semantic.os, "link", side_effect=OSError("different volume")), \
                patch.object(semantic, "start_process", side_effect=start), \
                patch.object(semantic, "stop_process"), \
                patch.object(semantic.LocalEncoder, "_api", return_value={"status": "ok"}):
            with semantic.LocalEncoder(self.config):
                pass
        self.assertEqual(self.model.read_bytes(), original_model)
        self.assertFalse(captured["directory"].exists())

    def test_startup_diagnostics_are_bounded_and_never_echo_private_text(self):
        cases = (
            (1, b"error: failed to open file 'private secret path'", "runtime_file_unreadable"),
            (1, b"invalid argument: private secret", "runtime_argument_invalid"),
            (0xC0000135, b"", "missing_runtime_dependency"),
            (0xC000001D, b"", "unsupported_cpu_instruction"),
            (1, b"private secret " * 100000, "native_runtime_startup_failed"),
        )
        for code, raw, reason in cases:
            with self.subTest(code=code, reason=reason):
                diagnostics = semantic._StartupDiagnostics(io.BytesIO(raw))
                try:
                    message = diagnostics.failure(code)
                    self.assertEqual(message,
                        f"semantic_runtime_failed_to_start: exit_code={code}; reason={reason}")
                    self.assertLessEqual(len(diagnostics.capture), 16 * 1024)
                    diagnostics.ready()
                    self.assertEqual(len(diagnostics.capture), 0)
                finally:
                    diagnostics.close()

    def test_failed_native_start_reports_only_exit_code_and_safe_reason(self):
        self.process.poll.return_value = 1
        self.process.stderr = io.BytesIO(b"error: failed to open file 'private path'")
        with patch.object(semantic, "MODEL_SHA", semantic._sha(self.model)), \
                patch.object(semantic, "is_windows", return_value=True), \
                patch.object(semantic, "start_process", return_value=self.process), \
                patch.object(semantic, "stop_process") as stopped:
            encoder = semantic.LocalEncoder(self.config)
            with self.assertRaisesRegex(RuntimeError,
                    "^semantic_runtime_failed_to_start: exit_code=1; reason=runtime_file_unreadable$"):
                encoder.__enter__()
        stopped.assert_called_once()
        self.assertIsNone(encoder.directory)
        self.assertTrue(self.process.stderr.closed)

    def test_windows_http_client_connects_directly_and_authenticates_every_request(self):
        encoder = semantic.LocalEncoder(self.config)
        encoder.loopback_port = 49152
        encoder.token = "synthetic-per-request-token"
        connection = Mock()
        response = connection.getresponse.return_value
        response.status = 200
        response.read.return_value = b'{"tokens":[1,2]}'
        with patch.object(semantic.http.client, "HTTPConnection", return_value=connection) as created:
            self.assertEqual(encoder._api("tokenize", {"content": "synthetic text"}), {"tokens": [1, 2]})
        created.assert_called_once_with("127.0.0.1", 49152, timeout=60)
        request = connection.request.call_args
        self.assertEqual(request.args, ("POST", "/tokenize"))
        self.assertEqual(request.kwargs["headers"]["Authorization"], "Bearer " + encoder.token)
        connection.close.assert_called_once()

    def test_http_error_body_never_leaks_private_input(self):
        encoder = semantic.LocalEncoder(self.config)
        encoder.loopback_port = 49152
        encoder.token = "synthetic-token"
        connection = Mock()
        connection.getresponse.return_value.status = 500
        connection.getresponse.return_value.read.return_value = b"secret transcription content"
        with patch.object(semantic.http.client, "HTTPConnection", return_value=connection):
            with self.assertRaisesRegex(ValueError, "^semantic_inference_failed$"):
                encoder._api("embedding", {"input": [1]})
        connection.close.assert_called_once()


@unittest.skipUnless(os.environ.get("AUDAY_SEMANTIC_MODEL") and os.environ.get("AUDAY_SEMANTIC_SERVER"),
                     "Optional local model paths were not supplied; never downloads models")
class RealModelTests(unittest.TestCase):
    def test_multilingual_retrieval_and_token_budget(self):
        fixture = json.loads((Path(__file__).parent / "semantic-fixture.json").read_text(encoding="utf-8"))
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
