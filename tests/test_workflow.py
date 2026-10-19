import base64, hashlib, importlib.util, json, os, select, secrets, shutil, socket, subprocess, sys, tempfile, time, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from auday import Auday, bridge, sha

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_fixture import make


@unittest.skipUnless(sys.platform == "darwin", "Native TLS bridge requires macOS")
class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.binary = bridge()
        cls.temp = tempfile.TemporaryDirectory(prefix="auday-synthetic-")
        cls.root = Path(cls.temp.name)
        cls.library = make(cls.root / "source")
        cls.password = "".join(
            secrets.choice("23456789ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(16)
        )
        cls.server = subprocess.Popen(
            [str(cls.binary), "serve"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        cls.server.stdin.write(
            json.dumps({"password": cls.password, "root": str(cls.root / "source")})
            + "\n"
        )
        cls.server.stdin.flush()
        if not select.select([cls.server.stdout], [], [], 15)[0]:
            raise RuntimeError("listener did not start")
        cls.ready = json.loads(cls.server.stdout.readline())
        cls.port = cls.ready["port"]

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        cls.server.communicate(timeout=5)
        cls.temp.cleanup()

    def setUp(self):
        self.cache = tempfile.TemporaryDirectory(prefix="auday-cache-")
        self.app = Auday(self.cache.name, transport=self.request)

    def tearDown(self):
        self.app.close()
        self.cache.cleanup()

    def raw_request(self, payload, password=None):
        data = {
            "host": "127.0.0.1",
            "port": self.port,
            "password": password or self.password,
            "request": payload,
            "timeout": 2,
        }
        result = subprocess.run(
            [str(self.binary), "request"],
            input=json.dumps(data) + "\n",
            capture_output=True,
            text=True,
            timeout=5,
        )
        return json.loads(result.stdout)

    def request(self, payload):
        return self.raw_request(payload)

    def test_authenticated_all_categories(self):
        status = self.request({"op": "status"})
        self.assertTrue(status["ok"])
        self.assertEqual(status["access"], "text-and-audio-read-only")
        self.assertEqual(len(status["categories"]), 6)
        self.assertEqual(
            status["transport"], {"tlsVersion": "0x0303", "cipherSuite": "0xccac"}
        )

    def test_wrong_password_releases_no_metadata(self):
        password = "".join(
            secrets.choice("23456789ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(16)
        )
        result = self.raw_request({"op": "status"}, password)
        self.assertFalse(result["ok"])
        self.assertNotIn("recordCount", result)

    def test_short_password_rejected(self):
        result = self.raw_request({"op": "status"}, "123456")
        self.assertFalse(result["ok"])

    def test_plaintext_cannot_read(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as client:
            data = b'{"op":"status"}'
            client.sendall(len(data).to_bytes(4, "big") + data)
            client.settimeout(2)
            try:
                received = client.recv(4096)
            except (TimeoutError, ConnectionError):
                received = b""
        self.assertNotIn(b"recordCount", received)

    def test_mutation_denied(self):
        for op in ["delete", "write", "regenerate", "record"]:
            self.assertEqual(
                self.request({"op": op, "id": "memory-1"})["error"],
                "read_only_operation_not_supported",
            )

    def test_all_pages_atomic_and_idempotent(self):
        first = self.app.sync()
        self.assertEqual(first["recordCount"], 221)
        self.assertEqual(first["changedRecords"], 221)
        self.assertEqual(self.app.sync()["changedRecords"], 0)
        self.assertFalse((self.app.root / "audio").exists())
        self.assertEqual(self.app.search(limit=100)["nextOffset"], 100)
        self.assertEqual(len(self.app.search(limit=100, offset=200)["records"]), 21)
        self.assertIsNone(self.app.search(limit=100, offset=200)["nextOffset"])
        self.assertEqual(self.app.assets()["assets"][0]["id"], "audio-demo")

    def test_search_citations_status_and_untrusted_text(self):
        self.app.sync()
        rows = self.app.search("发布检查", limit=4)["records"]
        self.assertEqual(len(rows), 4)
        self.assertEqual(self.app.read("memory-1")["record"]["status"], "confirmed")
        self.assertEqual(self.app.read("memory-2")["record"]["status"], "candidate")
        self.assertEqual(
            self.app.read("highlight-1")["record"]["sourceIDs"], ["transcript-000"]
        )
        self.assertIn("example.invalid", self.app.read("injection-1")["record"]["text"])
        self.assertEqual(self.app.search("' OR 1=1 --")["records"], [])

    def test_audio_integrity_and_cached_offline_read(self):
        self.app.sync()
        result = self.app.audio("audio-demo")
        self.assertEqual(sha(result["path"]), self.library["assets"][0]["sha256"])
        self.app.transport = lambda _: (_ for _ in ()).throw(RuntimeError("offline"))
        self.assertTrue(self.app.audio("audio-demo")["cacheHit"])
        self.assertTrue(self.app.search("发布")["records"])

    def test_resume_after_connection_failure(self):
        self.app.sync()
        original = self.app.transport
        calls = 0

        def interrupted(payload):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("synthetic_disconnect")
            return original(payload)

        self.app.transport = interrupted
        with self.assertRaises(OSError):
            self.app.audio("audio-demo")
        partial = list((self.app.root / "audio").glob("*.partial"))[0]
        self.assertEqual(partial.stat().st_size, 262144)
        self.app.transport = original
        self.assertEqual(
            sha(self.app.audio("audio-demo")["path"]),
            self.library["assets"][0]["sha256"],
        )

    def test_corrupt_chunk_not_published(self):
        self.app.sync()
        original = self.app.transport

        def corrupt(payload):
            value = original(payload)
            if payload["op"] == "audio":
                value["data"] = base64.b64encode(b"x").decode()
            return value

        self.app.transport = corrupt
        with self.assertRaisesRegex(ValueError, "integrity"):
            self.app.audio("audio-demo")
        self.assertFalse(list((self.app.root / "audio").glob("*.wav")))

    def test_snapshot_change_rolls_back(self):
        self.app.sync()
        before = self.app.metadata()
        original = self.app.transport

        def changed(payload):
            value = original(payload)
            if payload["op"] == "status":
                value["snapshot"] = "f" * 64
            elif payload["op"] == "list":
                value = original({**payload, "snapshot": before["snapshot"]})
                value["snapshot"] = "f" * 64
            if payload["op"] == "list" and payload["offset"] > 0:
                value["snapshot"] = "changed"
            return value

        self.app.transport = changed
        with self.assertRaisesRegex(ValueError, "snapshot_changed"):
            self.app.sync()
        self.assertEqual(self.app.metadata(), before)

    def test_device_identity_cannot_silently_change(self):
        self.app.sync()
        original = self.app.transport
        self.app.transport = lambda payload: {
            **original(payload),
            "sourceDeviceID": "different-phone",
        }
        with self.assertRaisesRegex(ValueError, "source_device_changed"):
            self.app.sync()

    def test_request_path_is_not_a_file_selector(self):
        self.assertEqual(
            self.request({"op": "audio", "id": "../../etc/passwd"})["error"],
            "asset_not_found",
        )
        self.assertEqual(
            self.request({"op": "audio", "id": "audio-demo", "offset": -1})["error"],
            "invalid_range",
        )
        self.assertEqual(
            self.request({"op": "audio", "id": "audio-demo", "length": 9999999})[
                "error"
            ],
            "invalid_range",
        )

    def test_password_not_persisted_in_cache(self):
        self.app.sync()
        for path in self.app.root.rglob("*"):
            if path.is_file():
                self.assertNotIn(self.password.encode(), path.read_bytes())
        self.assertEqual(self.app.root.stat().st_mode & 0o777, 0o700)

    def test_wrong_snapshot_and_no_path_leak(self):
        self.assertEqual(
            self.request({"op": "read", "id": "memory-1", "snapshot": "wrong"})[
                "error"
            ],
            "snapshot_changed",
        )
        assets = self.request({"op": "assets"})["assets"]
        self.assertNotIn("path", assets[0])

    def test_scope_downgrade_is_enforced_by_phone_and_cache(self):
        self.app.sync()
        downloaded = Path(self.app.audio("audio-demo")["path"])
        self.assertTrue(downloaded.exists())
        server = subprocess.Popen(
            [str(self.binary), "serve"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        server.stdin.write(
            json.dumps(
                {
                    "root": str(self.root / "source"),
                    "password": self.password,
                    "access": "text-only-read-only",
                }
            )
            + "\n"
        )
        server.stdin.flush()
        port = json.loads(server.stdout.readline())["port"]

        def text_transport(payload):
            args = {
                "host": "127.0.0.1",
                "port": port,
                "password": self.password,
                "request": payload,
                "timeout": 2,
            }
            r = subprocess.run(
                [str(self.binary), "request"],
                input=json.dumps(args) + "\n",
                text=True,
                capture_output=True,
                timeout=5,
            )
            return json.loads(r.stdout)

        try:
            self.assertEqual(
                text_transport(
                    {
                        "op": "audio",
                        "id": "audio-demo",
                        "access": "text-and-audio-read-only",
                    }
                )["error"],
                "audio_access_denied",
            )
            self.app.transport = text_transport
            status = self.app.sync()
            self.assertEqual(status["access"], "text-only-read-only")
            self.assertEqual(status["assetCount"], 0)
            self.assertEqual(self.app.assets()["assets"], [])
            self.assertNotIn("audioAssetID", self.app.read("transcript-000")["record"])
            with self.assertRaisesRegex(ValueError, "audio_access_denied"):
                self.app.audio("audio-demo")
            self.assertTrue(
                downloaded.exists(),
                "Scope change must not silently delete earlier copies",
            )
            self.assertEqual(
                text_transport({"op": "delete", "id": "memory-1"})["error"],
                "read_only_operation_not_supported",
            )
        finally:
            server.terminate()
            server.communicate(timeout=5)

    def test_malformed_status_does_not_replace_cache(self):
        self.app.sync()
        before = self.app.metadata()
        original = self.app.transport
        for patch in [
            {"access": "write-everything"},
            {"recordCount": -1},
            {"schemaVersion": 999},
            {"snapshot": "invalid"},
        ]:

            def malformed(payload, patch=patch):
                value = original(payload)
                return {**value, **patch} if payload["op"] == "status" else value

            self.app.transport = malformed
            with self.assertRaises(ValueError):
                self.app.sync()
            self.assertEqual(self.app.metadata(), before)

    def test_installed_runtime_builds_and_connects_independently(self):
        sys.path.insert(0, str(ROOT / "tools"))
        from install import install

        destination = Path(self.cache.name) / "installed agent" / "auday"
        install(destination)
        code = """
import json,subprocess,sys
from pathlib import Path
sys.path.insert(0,str(Path(sys.argv[1])/'scripts'))
from auday import Auday,bridge
config=json.loads(sys.stdin.readline());binary=str(bridge())
def request(payload):
    result=subprocess.run([binary,'request'],input=json.dumps({**config,'request':payload})+'\\n',capture_output=True,text=True,timeout=15)
    return json.loads(result.stdout)
app=Auday(sys.argv[2],transport=request)
try:
    status=app.sync();record=app.read('highlight-1');audio=app.audio('audio-demo')
    print(json.dumps({'records':status['recordCount'],'sourceIDs':record['record']['sourceIDs'],'audioBytes':Path(audio['path']).stat().st_size}))
finally:app.close()
"""
        config = {
            "host": "127.0.0.1",
            "port": self.port,
            "password": self.password,
            "timeout": 5,
        }
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                code,
                str(destination),
                str(Path(self.cache.name) / "isolated-cache"),
            ],
            input=json.dumps(config) + "\n",
            cwd=self.cache.name,
            capture_output=True,
            text=True,
            timeout=180,
        )
        self.assertEqual(result.returncode, 0, result.stderr[-1000:])
        summary = json.loads(result.stdout)
        self.assertEqual(summary["records"], 221)
        self.assertEqual(summary["sourceIDs"], ["transcript-000"])
        self.assertEqual(summary["audioBytes"], self.library["assets"][0]["size"])
        self.assertNotIn(self.password, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
