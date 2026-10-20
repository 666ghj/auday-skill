import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import multiprocessing
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import auday
from auday import Auday, profile_operation_lock


class SnapshotPeer:
    """Immutable synthetic generations; publication may happen during a transfer."""

    def __init__(self, leases=True):
        self.snapshot = "a" * 64
        self.leases_enabled = leases
        self.leases = {}
        self.calls = []
        self.status_calls = 0
        self.after_status = None
        self.after_snapshot_prepare = None
        self.before_list = None
        self.before_prepare = None
        self.fail_audio_once = False
        self.ready = True
        self.reopen_as = None
        self.prepare_polls = 0
        self.pending = {}
        self.fail_preparation = False

    @staticmethod
    def body(snapshot):
        return (snapshot[0].encode() + b"-synthetic-audio") * 20000

    def __call__(self, request):
        self.calls.append(dict(request))
        op = request["op"]
        snapshot = request.get("snapshot", self.snapshot)
        base = {"ok": True, "schemaVersion": 1, "sourceDeviceID": "synthetic-phone",
                "snapshot": snapshot}
        if op in ("status", "prepareSnapshot"):
            token = request.get("lease")
            if op == "prepareSnapshot":
                self.ready = True
                if self.reopen_as:
                    self.snapshot = self.reopen_as
                snapshot = self.snapshot
                if request.get("useSnapshotLease"):
                    token = str(uuid.uuid4())
                    self.leases[token] = snapshot
                    self.pending[token] = self.prepare_polls
            else:
                self.status_calls += 1
                if token:
                    snapshot = self.leases[token]
                    self.pending[token] = max(0, self.pending.get(token, 0) - 1)
            preparing = self.pending.get(token, 0) > 0
            result = {
                **base, "snapshot": snapshot if self.ready and not preparing else None,
                "access": "text-and-audio-read-only",
                "recordCount": 1 if self.ready and not preparing else None,
                "assetCount": 1 if self.ready and not preparing else None,
                "snapshotState": "preparing" if preparing else "ready" if self.ready else "idle",
                "snapshotCapturedAt": "2026-10-20T00:00:00Z" if snapshot[0] == "a"
                else "2026-10-20T00:01:00Z",
                "snapshotCurrent": snapshot == self.snapshot,
                "capabilities": {"onDemandAudio": True, "snapshotLeases": self.leases_enabled,
                                 "onDemandSnapshot": True},
            }
            if token:
                result["lease"] = token
            if self.fail_preparation and token and not preparing:
                result["snapshotState"] = "failed"
            if op == "prepareSnapshot" and self.after_snapshot_prepare:
                self.after_snapshot_prepare()
            return self.after_status(result, self.status_calls) if self.after_status and op == "status" else result
        if op == "acquireSnapshot":
            if not self.ready:
                return {"ok": False, "error": "snapshot_not_ready"}
            token = str(uuid.uuid4())
            self.leases[token] = snapshot
            return {**base, "lease": token}
        if op == "releaseSnapshot":
            snapshot = self.leases.pop(request["lease"], None)
            self.pending.pop(request["lease"], None)
            if snapshot is None:
                raise AssertionError("wrong lease release")
            return {**base, "snapshot": snapshot}
        if self.leases_enabled and self.leases.get(request.get("lease")) != snapshot:
            raise AssertionError("missing lease for immutable snapshot")
        if op == "list":
            if self.before_list:
                self.before_list()
            return {**base, "records": [
                {"id": "sample-record", "kind": "transcript", "text": snapshot[0]},
            ], "nextOffset": None}
        if op == "assets":
            return {**base, "assets": [
                {"id": "sample-audio", "preparation": "on-demand",
                 "contentRevision": snapshot},
            ], "nextOffset": None}
        if op == "prepareAudio":
            if self.before_prepare:
                self.before_prepare()
            body = self.body(snapshot)
            return {**base, "asset": {
                "id": "sample-audio", "sha256": hashlib.sha256(body).hexdigest(),
                "size": len(body), "mime": "audio/wav",
                "timeline": [{"fileStart": 0, "fileEnd": 3,
                              "recordedStart": "2026-10-20T00:00:00Z"}],
            }}
        if op == "audio":
            if self.fail_audio_once:
                self.fail_audio_once = False
                return {"ok": False, "error": "audio_not_prepared"}
            body = self.body(snapshot)
            offset = request["offset"]
            chunk = body[offset:offset + request["length"]]
            return {**base, "data": base64.b64encode(chunk).decode(),
                    "offset": offset, "size": len(body),
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "chunkSHA256": hashlib.sha256(chunk).hexdigest()}
        raise AssertionError(op)


def wait_for(event):
    if not event.wait(5):
        raise AssertionError("synthetic synchronization barrier timed out")


def lock_process(root, connection):
    # Called in a fresh/spawned process or a fork that must discard inherited
    # thread-lock state. Never reuse the parent's SQLite connection.
    def progress(message, *_, **__):
        if message.startswith("Waiting"):
            connection.send("waiting")
    auday.report = progress
    connection.send("ready")
    with profile_operation_lock(Path(root)):
        connection.send("entered")
    connection.close()


def inherited_lock_process(lock, connection):
    try:
        with lock:
            connection.send("incorrectly entered inherited lock")
    except RuntimeError as error:
        connection.send(str(error))
    connection.close()


class SnapshotConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="auday-snapshot-synthetic-")
        self.peer = SnapshotPeer()
        self.app = Auday(self.temp.name, transport=self.peer)

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def test_new_arrivals_wait_for_the_next_explicit_sync(self):
        self.peer.before_list = lambda: setattr(self.peer, "snapshot", "b" * 64)
        result = self.app.sync()
        self.assertEqual(result["snapshot"], "a" * 64)
        self.assertFalse(result["sourceSnapshotCurrent"])
        self.assertEqual(result["sourceSnapshotAt"], "2026-10-20T00:00:00Z")
        self.assertEqual(self.app.read("sample-record")["record"]["text"], "a")
        self.assertEqual([p["op"] for p in self.peer.calls],
                         ["status", "prepareSnapshot", "list", "assets", "status", "releaseSnapshot"])
        self.assertFalse(self.peer.leases)
        result = self.app.sync()
        self.assertEqual(result["snapshot"], "b" * 64)
        self.assertTrue(result["sourceSnapshotCurrent"])
        self.assertEqual(self.app.read("sample-record")["record"]["text"], "b")

    def test_final_status_failure_keeps_verified_data_without_claiming_freshness(self):
        failures = [ConnectionError("synthetic offline"), ValueError("invalid_response_object"),
                    subprocess.TimeoutExpired("synthetic", 1)]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                self.peer.status_calls = 0
                def final_status(result, calls):
                    if calls == 2:
                        raise failure
                    return result
                self.peer.after_status = final_status
                result = self.app.sync()
                self.assertFalse(result["sourceSnapshotCurrent"])
                self.assertEqual(self.app.read("sample-record")["record"]["text"], "a")
                self.assertEqual(self.peer.status_calls, 2)
                self.assertFalse(self.peer.leases)

    def test_unknown_or_mismatched_final_status_never_claims_current(self):
        mutations = [
            lambda value: value.pop("snapshotCurrent"),
            lambda value: value.update(snapshotCurrent="true"),
            lambda value: value.update(sourceDeviceID="different-synthetic-phone"),
            lambda value: value.update(access="text-only-read-only"),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.peer.status_calls = 0
                def final_status(value, calls):
                    if calls == 2:
                        mutate(value)
                    return value
                self.peer.after_status = final_status
                self.assertFalse(self.app.sync()["sourceSnapshotCurrent"])
                self.assertEqual(self.app.metadata()["sourceDeviceID"], "synthetic-phone")

    def test_unchanged_sync_checks_final_freshness_without_redownloading(self):
        self.app.sync()
        self.peer.after_status = lambda value, calls: (
            {**value, "snapshotCurrent": False} if calls == 4 else value
        )
        result = self.app.sync()
        self.assertEqual(result["downloadedRecords"], 0)
        self.assertFalse(result["sourceSnapshotCurrent"])
        self.assertEqual(sum(p["op"] == "list" for p in self.peer.calls), 1)

    def test_failed_or_interrupted_transfer_releases_lease_and_writer_lock(self):
        for failure in [ConnectionError("synthetic failure"), KeyboardInterrupt()]:
            with self.subTest(failure=type(failure).__name__):
                def fail():
                    raise failure
                self.peer.before_list = fail
                with self.assertRaises(type(failure)):
                    self.app.sync()
                self.assertFalse(self.peer.leases)
                self.assertIsNone(self.app.snapshot_lease)
                self.peer.before_list = None
                # A different thread/instance must be able to acquire the lock.
                with ThreadPoolExecutor(max_workers=1) as pool:
                    result = pool.submit(self.run_sync).result(timeout=5)
                self.assertEqual(result["snapshot"], self.peer.snapshot)
                self.peer.snapshot = "b" * 64 if self.peer.snapshot[0] == "a" else "a" * 64

    def run_sync(self, peer=None, profile="default"):
        client = Auday(self.temp.name, profile=profile, transport=peer or self.peer)
        try:
            return client.sync()
        finally:
            client.close()

    def test_audio_reuses_one_lease_for_nested_preparation_retry_and_all_chunks(self):
        self.app.sync()
        self.peer.calls.clear()
        self.peer.fail_audio_once = True
        result = self.app.audio("sample-audio")
        self.assertEqual(Path(result["path"]).read_bytes(), self.peer.body("a" * 64))
        operations = [p["op"] for p in self.peer.calls]
        self.assertEqual(operations.count("acquireSnapshot"), 1)
        self.assertEqual(operations.count("releaseSnapshot"), 1)
        self.assertEqual(operations.count("prepareAudio"), 2)
        requests = [p for p in self.peer.calls if p["op"] in ("prepareAudio", "audio")]
        self.assertEqual(len({p["lease"] for p in requests}), 1)
        self.assertEqual({p["snapshot"] for p in requests}, {"a" * 64})
        self.assertFalse(self.peer.leases)
        self.app.transport = lambda _: self.fail("verified cached audio must stay offline")
        self.assertTrue(self.app.audio("sample-audio")["cacheHit"])

    def test_audio_reopening_prepares_once_before_acquiring_cached_version(self):
        self.app.sync()
        self.peer.ready = False
        self.peer.calls.clear()
        self.assertFalse(self.app.audio("sample-audio")["cacheHit"])
        operations = [p["op"] for p in self.peer.calls]
        self.assertEqual(operations.count("prepareSnapshot"), 1)
        preparation = next(p for p in self.peer.calls if p["op"] == "prepareSnapshot")
        self.assertTrue(preparation["useSnapshotLease"])
        self.assertEqual(operations.count("acquireSnapshot"), 1)
        self.assertEqual(operations.count("releaseSnapshot"), 1)

    def test_audio_reopening_does_not_silently_switch_to_new_version(self):
        self.app.sync()
        self.peer.ready = False
        self.peer.reopen_as = "b" * 64
        self.peer.calls.clear()
        with self.assertRaisesRegex(ValueError, "snapshot_changed"):
            self.app.audio("sample-audio")
        self.assertNotIn("prepareAudio", [p["op"] for p in self.peer.calls])
        self.assertFalse(self.peer.leases)

    def test_upgraded_phone_leases_cover_audio_even_with_older_cached_capabilities(self):
        self.peer.leases_enabled = False
        self.app.sync()
        self.assertFalse(self.app.metadata()["capabilities"]["snapshotLeases"])
        self.peer.leases_enabled = True
        self.peer.ready = False
        self.peer.calls.clear()
        self.peer.before_prepare = lambda: setattr(self.peer, "snapshot", "b" * 64)
        result = self.app.audio("sample-audio")
        self.assertEqual(Path(result["path"]).read_bytes(), self.peer.body("a" * 64))
        data = [p for p in self.peer.calls if p["op"] in ("prepareAudio", "audio")]
        self.assertEqual({p["snapshot"] for p in data}, {"a" * 64})
        self.assertEqual(len({p["lease"] for p in data}), 1)
        self.assertEqual(self.peer.calls[-1]["op"], "releaseSnapshot")
        self.assertFalse(self.peer.leases)

    def test_direct_preparation_rejects_old_catalogue_asset(self):
        self.app.sync()
        asset = self.app.assets()["assets"][0]
        self.app.prepare_audio(asset)
        self.assertFalse(self.peer.leases)
        self.peer.snapshot = "b" * 64
        self.app.sync()
        self.peer.calls.clear()
        with self.assertRaisesRegex(ValueError, "audio_changed"):
            self.app.prepare_audio(asset)
        self.assertEqual(self.peer.calls, [])
        self.assertEqual(self.app.assets()["assets"][0]["contentRevision"], "b" * 64)

    def test_older_companion_without_leases_remains_supported(self):
        self.peer.leases_enabled = False
        self.app.sync()
        self.app.audio("sample-audio")
        self.assertNotIn("acquireSnapshot", [p["op"] for p in self.peer.calls])
        self.assertTrue(all("lease" not in p for p in self.peer.calls))
        self.assertTrue(all("useSnapshotLease" not in p for p in self.peer.calls))

    def test_preparation_lease_pins_the_result_before_first_page_and_through_polling(self):
        self.peer.prepare_polls = 2
        self.peer.after_snapshot_prepare = lambda: setattr(self.peer, "snapshot", "b" * 64)
        with patch("auday.time.sleep", return_value=None):
            result = self.app.sync()
        self.assertEqual(result["snapshot"], "a" * 64)
        self.assertFalse(result["sourceSnapshotCurrent"])
        self.assertEqual(self.app.read("sample-record")["record"]["text"], "a")
        self.assertNotIn("acquireSnapshot", [p["op"] for p in self.peer.calls])
        bound = [p for p in self.peer.calls if "lease" in p]
        self.assertEqual(len({p["lease"] for p in bound}), 1)
        self.assertEqual(sum(p["op"] == "status" for p in bound), 2)
        self.assertFalse(self.peer.leases)

    def test_failed_or_timed_out_preparation_releases_pending_lease(self):
        self.peer.fail_preparation = True
        with self.assertRaisesRegex(ValueError, "snapshot_preparation_failed"):
            self.app.sync()
        self.assertFalse(self.peer.leases)
        self.assertEqual(self.app.metadata(), {})
        self.peer.fail_preparation = False
        self.peer.prepare_polls = 2
        with patch("auday.time.monotonic", side_effect=[0, 121]):
            with self.assertRaisesRegex(ValueError, "snapshot_preparation_timed_out"):
                self.app.sync()
        self.assertFalse(self.peer.leases)
        self.assertEqual(self.app.metadata(), {})

    def test_invalid_prepared_metadata_releases_lease_without_replacing_cache(self):
        def invalid_metadata(request):
            result = self.peer(request)
            if request["op"] == "prepareSnapshot":
                result["recordCount"] = -1
            return result
        self.app.transport = invalid_metadata
        with self.assertRaisesRegex(ValueError, "invalid_snapshot_counts"):
            self.app.sync()
        self.assertFalse(self.peer.leases)
        self.assertIsNone(self.app.snapshot_lease)
        self.assertEqual(self.app.metadata(), {})

    def test_release_failure_does_not_discard_a_verified_snapshot(self):
        def offline_at_release(request):
            if request["op"] == "releaseSnapshot":
                raise ConnectionError("synthetic offline at release")
            return self.peer(request)
        self.app.transport = offline_at_release
        result = self.app.sync()
        self.assertEqual(result["snapshot"], "a" * 64)
        self.assertIsNone(self.app.snapshot_lease)
        self.assertEqual(self.app.read("sample-record")["record"]["text"], "a")

    def test_profile_directory_aliases_share_the_operation_lock(self):
        alias = Path(self.temp.name) / "profile-alias"
        alias.symlink_to(self.app.root, target_is_directory=True)
        same_profile = profile_operation_lock(alias)
        self.assertIs(same_profile, self.app.operation_lock)
        with self.app.operation_lock:
            with same_profile:
                self.assertEqual(same_profile.depth, 2)

    def test_same_profile_syncs_cannot_publish_out_of_order(self):
        paused, release, waiting = threading.Event(), threading.Event(), threading.Event()
        def block_first_page():
            paused.set()
            wait_for(release)
        def progress(message, *_, **__):
            if message.startswith("Waiting"):
                waiting.set()
        self.peer.before_list = block_first_page
        with patch("auday.report", side_effect=progress), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.run_sync)
            try:
                wait_for(paused)
                self.peer.snapshot = "b" * 64
                second = pool.submit(self.run_sync)
                wait_for(waiting)
                self.assertEqual(self.peer.status_calls, 1)
                # The previous cache remains queryable while network work waits.
                self.assertEqual(self.app.search()["records"], [])
            finally:
                release.set()
            self.assertEqual(first.result(timeout=5)["snapshot"], "a" * 64)
            self.assertEqual(second.result(timeout=5)["snapshot"], "b" * 64)
        self.assertEqual(self.app.metadata()["snapshot"], "b" * 64)
        self.assertEqual(self.app.read("sample-record")["record"]["text"], "b")

    def test_audio_and_sync_cannot_mix_old_asset_with_new_metadata(self):
        self.app.sync()
        paused, release, waiting = threading.Event(), threading.Event(), threading.Event()
        def block_preparation():
            paused.set()
            wait_for(release)
        def progress(message, *_, **__):
            if message.startswith("Waiting"):
                waiting.set()
        def download():
            client = Auday(self.temp.name, transport=self.peer)
            try:
                return client.audio("sample-audio")
            finally:
                client.close()
        self.peer.before_prepare = block_preparation
        self.peer.calls.clear()
        with patch("auday.report", side_effect=progress), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(download)
            try:
                wait_for(paused)
                self.peer.snapshot = "b" * 64
                second = pool.submit(self.run_sync)
                wait_for(waiting)
                self.assertNotIn("status", [p["op"] for p in self.peer.calls])
            finally:
                release.set()
            audio = first.result(timeout=5)
            second.result(timeout=5)
        self.assertEqual(Path(audio["path"]).read_bytes(), self.peer.body("a" * 64))
        self.assertEqual(self.app.metadata()["snapshot"], "b" * 64)
        asset = self.app.assets()["assets"][0]
        self.assertEqual(asset["contentRevision"], "b" * 64)
        self.assertNotIn("sha256", asset)
        self.assertEqual({p["snapshot"] for p in self.peer.calls if p["op"] == "audio"}, {"a" * 64})

    def test_different_profiles_do_not_wait_for_each_other(self):
        paused, release = threading.Event(), threading.Event()
        def block():
            paused.set()
            wait_for(release)
        self.peer.before_list = block
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.run_sync)
            try:
                wait_for(paused)
                second = pool.submit(self.run_sync, SnapshotPeer(), "other")
                self.assertEqual(second.result(timeout=5)["snapshot"], "a" * 64)
                self.assertFalse(first.done())
            finally:
                release.set()
            first.result(timeout=5)

    def test_nested_lock_serializes_independent_and_forked_processes(self):
        for method in ("spawn", "fork"):
            if method not in multiprocessing.get_all_start_methods():
                continue
            with self.subTest(method=method):
                context = multiprocessing.get_context(method)
                parent, child = context.Pipe(duplex=False)
                process = context.Process(target=lock_process, args=(str(self.app.root), child))
                try:
                    with self.app.operation_lock:
                        with self.app.operation_lock:
                            pass
                        process.start()
                        self.assertTrue(parent.poll(5))
                        self.assertEqual(parent.recv(), "ready")
                        self.assertTrue(parent.poll(5))
                        self.assertEqual(parent.recv(), "waiting")
                        self.assertFalse(parent.poll(0.05))
                    self.assertTrue(parent.poll(5))
                    self.assertEqual(parent.recv(), "entered")
                    process.join(timeout=5)
                    self.assertEqual(process.exitcode, 0)
                finally:
                    if process.is_alive():
                        process.terminate()
                        process.join(timeout=5)
                    parent.close()
                    child.close()

    @unittest.skipUnless("fork" in multiprocessing.get_all_start_methods(), "fork unavailable")
    def test_forked_worker_must_not_reuse_an_inherited_client(self):
        context = multiprocessing.get_context("fork")
        parent, child = context.Pipe(duplex=False)
        process = context.Process(target=inherited_lock_process,
                                  args=(self.app.operation_lock, child))
        try:
            with self.app.operation_lock:
                process.start()
                self.assertTrue(parent.poll(5))
                self.assertTrue(parent.recv().startswith("inherited_client:"))
                process.join(timeout=5)
                self.assertEqual(process.exitcode, 0)
        finally:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            parent.close()
            child.close()


if __name__ == "__main__":
    unittest.main()
