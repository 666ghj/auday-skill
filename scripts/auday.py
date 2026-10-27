#!/usr/bin/env python3
"""Local Auday workflow CLI with native macOS and Windows TLS-PSK transports."""

import argparse
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import shlex
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from progress import report
from platform_utils import default_home, is_windows, lock_file, unlock_file, secure_directory
from incremental_sync import body_hash, body_json, fetch_records

HERE = Path(__file__).resolve().parent
DEFAULT_HOME = default_home()
_profile_locks = {}
_profile_locks_guard = threading.Lock()


class ProfileOperationLock:
    """Serialize a profile's writers across processes and allow nested operations."""

    def __init__(self, root):
        self.path = root / "operation.lock"
        self.process_id = os.getpid()
        self.thread_lock = threading.RLock()
        self.depth = 0
        self.stream = None

    def __enter__(self):
        if os.getpid() != self.process_id:
            raise RuntimeError("inherited_client: create a new Auday client after fork")
        if not self.thread_lock.acquire(blocking=False):
            report("Waiting for another operation on this local library", force=True)
            self.thread_lock.acquire()
        try:
            if self.depth == 0:
                self.stream = self.path.open("a+b")
                os.chmod(self.path, 0o600)
                try:
                    lock_file(self.stream, blocking=False)
                except BlockingIOError:
                    report("Waiting for another operation on this local library", force=True)
                    lock_file(self.stream)
            self.depth += 1
            return self
        except BaseException:
            if self.stream is not None:
                self.stream.close()
                self.stream = None
            self.thread_lock.release()
            raise

    def __exit__(self, *_):
        try:
            self.depth -= 1
            if self.depth == 0:
                try:
                    unlock_file(self.stream)
                finally:
                    self.stream.close()
                    self.stream = None
        finally:
            self.thread_lock.release()


def profile_operation_lock(root):
    # Every Auday instance for a canonical profile shares the same thread lock.
    # A lock file alone does not safely express nested operations in one process.
    root = Path(root).resolve()
    with _profile_locks_guard:
        if root not in _profile_locks:
            _profile_locks[root] = ProfileOperationLock(root)
        return _profile_locks[root]


def reset_profile_locks_after_fork():
    global _profile_locks, _profile_locks_guard
    for lock in _profile_locks.values():
        if lock.stream is not None:
            # Close the inherited descriptor without unlocking the parent's
            # shared open-file description. Child workers create their own DB.
            lock.stream.close()
    _profile_locks = {}
    _profile_locks_guard = threading.Lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=reset_profile_locks_after_fork)


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def dump(value):
    print(json.dumps(value, ensure_ascii=False, allow_nan=False))


def atomic_json(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as out:
        json.dump(value, out, ensure_ascii=False, allow_nan=False)
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def valid_lease_token(token):
    try:
        return isinstance(token, str) and str(uuid.UUID(token)) == token.lower()
    except ValueError:
        return False


class Auday:
    def __init__(self, home=DEFAULT_HOME, profile="default", transport=None):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", profile):
            raise ValueError("invalid_profile_name")
        self.root = Path(home).expanduser().resolve() / profile
        secure_directory(self.root)
        self.operation_lock = profile_operation_lock(self.root)
        self.profile = profile
        self.transport = transport
        self.snapshot_lease = None
        self.keychain_account = (
            "profile-" + hashlib.sha256(str(self.root).encode()).hexdigest()[:32]
        )
        self.db = sqlite3.connect(self.root / "library.sqlite")
        os.chmod(self.root / "library.sqlite", 0o600)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY, kind TEXT, text TEXT, date TEXT, body TEXT);
            CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY, body TEXT);
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS record_fingerprints(
                id TEXT PRIMARY KEY, wire_sha256 TEXT, wire_size INTEGER, body_sha256 TEXT NOT NULL
            );
        """)

    def close(self):
        self.db.close()

    def metadata(self):
        return {
            k: json.loads(v)
            for k, v in self.db.execute("SELECT key,value FROM metadata")
        }

    def config(self):
        path = self.root / "connection.json"
        if not path.exists():
            raise ValueError("not_paired: run pair in a local terminal")
        return json.loads(path.read_text(encoding="utf-8"))

    def request(self, payload, timeout=15):
        if self.snapshot_lease and payload.get("op") in (
            "list", "assets", "read", "recordChunk", "prepareAudio", "audio",
            "recordManifest", "readRecords",
        ):
            if payload.get("snapshot") != self.snapshot_lease["snapshot"]:
                raise ValueError("snapshot_changed")
            payload = {**payload, "lease": self.snapshot_lease["lease"]}
        if self.transport:
            result = self.transport(payload)
        else:
            config = self.config()
            config.update(profile=self.keychain_account, request=payload, timeout=max(1, timeout - 5))
            process = subprocess.run(
                bridge_command() + ["request"],
                input=json.dumps(config) + "\n",
                text=True,
                capture_output=True,
                encoding="utf-8",
                timeout=timeout,
            )
            try:
                result = json.loads(process.stdout)
            except ValueError:
                raise ValueError("connection_failed") from None
        if not isinstance(result, dict):
            raise ValueError("invalid_response_object")
        if not result.get("ok"):
            raise ValueError(result.get("error", "request_failed"))
        expected = self.metadata().get("sourceDeviceID")
        if expected and result.get("sourceDeviceID") != expected:
            raise ValueError("source_device_changed: use a new profile")
        return result

    @contextmanager
    def snapshot_access(self, snapshot, capabilities, *, prepare_if_needed=False,
                        prepared_lease=None):
        if self.snapshot_lease is not None:
            if self.snapshot_lease["snapshot"] != snapshot:
                raise ValueError("snapshot_changed")
            yield
            return
        if (
            prepared_lease is None and prepare_if_needed
            and capabilities.get("snapshotLeases") is not True
        ):
            # A phone may have been upgraded since this catalogue was cached.
            # Discover leases before beginning any uncached audio operation so
            # newly supported protection covers preparation and every chunk.
            status = self.request({"op": "status"})
            if status.get("access") != "text-and-audio-read-only":
                raise ValueError("audio_access_denied")
            capabilities = status.get("capabilities", {})
            if not isinstance(capabilities, dict):
                raise ValueError("invalid_capabilities")
        if prepared_lease is None and capabilities.get("snapshotLeases") is not True:
            yield
            return
        if prepared_lease is not None:
            reply = {"snapshot": snapshot, "lease": prepared_lease}
        else:
            request = {"op": "acquireSnapshot", "snapshot": snapshot}
            try:
                reply = self.request(request)
            except ValueError as error:
                if str(error) != "snapshot_not_ready" or not prepare_if_needed:
                    raise
                status = self.prepared_status()
                failure = (
                    "audio_access_denied" if status.get("access") != "text-and-audio-read-only"
                    else "snapshot_changed" if status.get("snapshot") != snapshot else None
                )
                if failure:
                    if status.get("lease") is not None:
                        self.release_snapshot_lease(status["lease"])
                    raise ValueError(failure) from None
                # A lease acquired during preparation already protects this
                # generation, including the interval before its first page.
                reply = status if status.get("lease") is not None else self.request(request)
        token = reply.get("lease")
        if not valid_lease_token(token):
            raise ValueError("invalid_snapshot_lease")
        try:
            if reply.get("snapshot") != snapshot:
                raise ValueError("invalid_snapshot_lease")
            self.snapshot_lease = {"snapshot": snapshot, "lease": token}
            yield
        finally:
            self.snapshot_lease = None
            self.release_snapshot_lease(token)

    def release_snapshot_lease(self, token):
        try:
            # Pending preparation may not have a snapshot identity yet.
            self.request({"op": "releaseSnapshot", "lease": token})
        except (ValueError, OSError, subprocess.TimeoutExpired):
            report("Snapshot access finished; the phone will expire its temporary lease")

    def sync(self, mode="incremental"):
        if mode not in ("incremental", "full"):
            raise ValueError("invalid_sync_mode")
        with self.operation_lock:
            started = time.perf_counter()
            report("Checking phone status", force=True)
            status = self.prepared_status()
            prepared_at = time.perf_counter()
            capabilities = status.get("capabilities", {})
            with self.snapshot_access(
                status.get("snapshot"),
                capabilities if isinstance(capabilities, dict) else {},
                prepared_lease=status.get("lease"),
            ):
                result = self._sync(status, mode)
            transferred_at = time.perf_counter()
            try:
                result["library"] = self.library()
                result["libraryStatus"] = "ready"
            except (OSError, ValueError, sqlite3.Error) as error:
                # The verified source projection already committed. Keep a file
                # maintenance failure separate and recover it offline next time.
                report("Text sync saved; local file library needs an offline refresh", force=True)
                result["libraryStatus"] = "failed"
                result["libraryError"] = type(error).__name__
            result["timings"] = {
                "prepareSeconds": round(prepared_at - started, 3),
                "transferAndCommitSeconds": round(transferred_at - prepared_at, 3),
                "librarySeconds": round(time.perf_counter() - transferred_at, 3),
            }
            last_sync = {key: result[key] for key in (
                "requestedMode", "effectiveMode", "fallbackReason", "downloadedRecords",
                "reusedRecords", "changedRecords", "deletedRecords", "libraryStatus", "timings",
                "syncedAt", "snapshot",
            )}
            try:
                with self.db:
                    self.db.execute("INSERT OR REPLACE INTO metadata VALUES(?,?)", ("lastSync", json.dumps(last_sync)))
                result["syncReportSaved"] = True
            except sqlite3.Error:
                # Disk exhaustion during file publication must not turn the
                # already committed source sync into an apparent transfer failure.
                result["syncReportSaved"] = False
                report("Text sync saved; its final summary could not be recorded", force=True)
            result["lastSync"] = last_sync
            return result

    def library(self, language=None):
        from managed_library import maintain_library
        with self.operation_lock:
            return maintain_library(
                self.root / "library.sqlite", self.root / "library", language=language, progress=report,
            )

    def _sync(self, status, mode="incremental"):
        snapshot = status["snapshot"]
        if (
            not isinstance(status.get("sourceDeviceID"), str)
            or not status["sourceDeviceID"]
        ):
            raise ValueError("invalid_source_identity")
        if status.get("schemaVersion") != 1 or status.get("access") not in (
            "text-only-read-only",
            "text-and-audio-read-only",
        ):
            raise ValueError("unsupported_protocol_or_access_scope")
        if not isinstance(snapshot, str) or not re.fullmatch("[0-9a-f]{64}", snapshot):
            raise ValueError("invalid_snapshot_identity")
        if any(
            type(status.get(key)) is not int or not 0 <= status[key] <= 1000000
            for key in ("recordCount", "assetCount")
        ):
            raise ValueError("invalid_snapshot_counts")
        if status["access"] == "text-only-read-only" and status["assetCount"] != 0:
            raise ValueError("text_scope_contains_audio")
        capabilities = status.get("capabilities", {})
        if not isinstance(capabilities, dict):
            raise ValueError("invalid_capabilities")
        if type(status.get("snapshotCurrent", False)) is not bool:
            raise ValueError("invalid_snapshot_freshness")
        return self._sync_snapshot(status, mode)

    def _sync_snapshot(self, status, mode="incremental"):
        snapshot = status["snapshot"]
        capabilities = status.get("capabilities", {})
        previous = self.metadata()
        incremental = capabilities.get("incrementalRecords") is True
        mode_result = {
            "requestedMode": mode,
            "effectiveMode": mode if incremental else "full",
            "fallbackReason": "phone_does_not_support_incremental_records"
            if mode == "incremental" and not incremental else None,
        }
        if mode_result["fallbackReason"]:
            report("This phone requires full text transfer when its snapshot changes")
        if (
            mode != "full"
            and previous.get("snapshot") == snapshot
            and previous.get("recordCount") == status["recordCount"]
            and previous.get("assetCount") == status["assetCount"]
            and previous.get("access") == status["access"]
            and self.cached_records_intact(status["recordCount"])
            and self.db.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == status["assetCount"]
        ):
            source_freshness = self.final_source_freshness(status)
            with self.db:
                self.db.executemany(
                    "INSERT OR REPLACE INTO metadata VALUES(?,?)",
                    [(key, json.dumps(value)) for key, value in {
                        "syncedAt": now(), "capabilities": capabilities, **source_freshness
                    }.items()],
                )
            report("Snapshot unchanged; reusing cached records")
            return {
                **self.metadata(),
                **mode_result,
                "changedRecords": 0,
                "downloadedRecords": 0,
                "reusedRecords": status["recordCount"],
                "deletedRecords": 0,
                "audioDownloaded": False,
            }
        record_rows, asset_rows, staged_records = [], [], []
        fingerprints = {}
        downloaded, reused = status["recordCount"], 0
        if incremental:
            record_rows, fingerprints, downloaded, reused = fetch_records(self, status, mode, staged_records)
        for op, field, destination in [
            ("list", "records", record_rows),
            ("assets", "assets", asset_rows),
        ]:
            if op == "list" and incremental:
                continue
            offset = 0
            total = status["recordCount" if op == "list" else "assetCount"]
            stage = "Receiving text records" if op == "list" else "Receiving audio catalog"
            report(stage, 0, total, "records" if op == "list" else "assets")
            while True:
                reply = self.request({
                    "op": op, "offset": offset, "snapshot": snapshot,
                    "allowRecordChunks": capabilities.get("chunkedRecords") is True,
                })
                if reply["snapshot"] != snapshot:
                    raise ValueError("snapshot_changed")
                rows = reply[field]
                if not isinstance(rows, list) or len(rows) > 100:
                    raise ValueError("invalid_page")
                if op == "list":
                    rows = [self.complete_record(row, snapshot, capabilities, staged_records) for row in rows]
                destination.extend(rows)
                report(stage, len(destination), total, "records" if op == "list" else "assets")
                if len(destination) > 1000000:
                    raise ValueError("library_too_large")
                next_offset = reply.get("nextOffset")
                if next_offset is None:
                    break
                if next_offset != offset + len(rows) or next_offset <= offset:
                    raise ValueError("invalid_cursor")
                offset = next_offset
        report("Validating synchronized snapshot")
        if (
            len(record_rows) != status["recordCount"]
            or len(asset_rows) != status["assetCount"]
        ):
            raise ValueError("incomplete_snapshot")
        for rows in (record_rows, asset_rows):
            if any(not isinstance(r, dict) for r in rows):
                raise ValueError("invalid_record_type")
            if any(
                not isinstance(r.get("id"), str) or not r["id"] for r in rows
            ) or len({r["id"] for r in rows}) != len(rows):
                raise ValueError("duplicate_or_missing_id")
        for row in record_rows:
            if row.get("kind") not in (
                "transcript",
                "highlight",
                "summary",
                "reflection",
                "memory",
            ):
                raise ValueError("invalid_record_kind")
            if status["access"] == "text-only-read-only" and (
                "audioAssetID" in row or "audioAssetIDs" in row
            ):
                raise ValueError("text_scope_contains_audio_reference")
        for row in asset_rows:
            if row.get("preparation") == "on-demand":
                if capabilities.get("onDemandAudio") is not True or not re.fullmatch(
                    "[0-9a-f]{64}", str(row.get("contentRevision", ""))
                ):
                    raise ValueError("invalid_on_demand_asset")
                continue
            if (
                type(row.get("size")) is not int
                or row["size"] < 0
                or not re.fullmatch("[0-9a-f]{64}", str(row.get("sha256", "")))
            ):
                raise ValueError("invalid_asset_metadata")
        # Commit only after the full manifest/projection and all bodies verify.
        # Remove stale query rows, never downloaded files or user-created work.
        before_rows = {row[0]: row for row in self.db.execute("SELECT id,kind,text,date,body FROM records")}
        before = {identity: row[4] for identity, row in before_rows.items()}
        bodies = {row["id"]: body_json(row) for row in record_rows}
        changed = sum(before.get(identity) != body for identity, body in bodies.items())
        deleted = set(before) - set(bodies)
        if not incremental:
            fingerprints = {identity: (None, None, body_hash(body)) for identity, body in bodies.items()}
        source_freshness = self.final_source_freshness(status)
        meta = {
            "sourceDeviceID": status["sourceDeviceID"],
            "snapshot": snapshot,
            "syncedAt": now(),
            "access": status["access"],
            "recordCount": len(record_rows),
            "assetCount": len(asset_rows),
            "capabilities": capabilities,
            **source_freshness,
        }
        old_assets = {
            row[0]: json.loads(row[1])
            for row in self.db.execute("SELECT id,body FROM assets")
        }
        asset_rows = [
            {**old_assets[row["id"]], **row}
            if row.get("preparation") == "on-demand"
            and row["id"] in old_assets
            and old_assets[row["id"]].get("contentRevision") == row["contentRevision"]
            else row
            for row in asset_rows
        ]
        report("Saving local library")
        with self.db:
            self.db.executemany("DELETE FROM records WHERE id=?", [(identity,) for identity in deleted])
            self.db.execute("DELETE FROM record_fingerprints WHERE id NOT IN (SELECT id FROM records)")
            for row in record_rows:
                body = bodies[row["id"]]
                projection = self.record_projection(row, body)
                if before_rows.get(row["id"]) == projection:
                    continue
                self.db.execute(
                    "INSERT OR REPLACE INTO records VALUES(?,?,?,?,?)",
                    projection,
                )
            self.db.executemany(
                "INSERT INTO record_fingerprints VALUES(?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET wire_sha256=excluded.wire_sha256, "
                "wire_size=excluded.wire_size, body_sha256=excluded.body_sha256 "
                "WHERE wire_sha256 IS NOT excluded.wire_sha256 OR wire_size IS NOT excluded.wire_size "
                "OR body_sha256 IS NOT excluded.body_sha256",
                [(identity, *fingerprint) for identity, fingerprint in fingerprints.items()],
            )
            asset_ids = {row["id"] for row in asset_rows}
            self.db.executemany("DELETE FROM assets WHERE id=?", [(identity,) for identity in set(old_assets) - asset_ids])
            self.db.executemany(
                "INSERT OR REPLACE INTO assets VALUES(?,?)",
                [(r["id"], json.dumps(r, ensure_ascii=False)) for r in asset_rows if old_assets.get(r["id"]) != r],
            )
            self.db.executemany(
                "INSERT OR REPLACE INTO metadata VALUES(?,?)",
                [(k, json.dumps(v)) for k, v in meta.items()],
            )
        for path in staged_records:
            path.unlink(missing_ok=True)
        report("Synchronization complete")
        return {
            **meta, **mode_result, "changedRecords": changed, "downloadedRecords": downloaded,
            "reusedRecords": reused, "deletedRecords": len(deleted), "audioDownloaded": False,
        }

    def cached_records_intact(self, count):
        rows = self.db.execute(
            "SELECT records.id,records.kind,records.text,records.date,records.body,record_fingerprints.body_sha256 FROM records "
            "LEFT JOIN record_fingerprints ON records.id=record_fingerprints.id"
        ).fetchall()
        if len(rows) != count:
            return False
        for row in rows:
            body, digest = row[4], row[5]
            if not isinstance(body, str) or body_hash(body) != digest:
                return False
            try:
                if self.record_projection(json.loads(body), body) != row[:5]:
                    return False
            except (ValueError, TypeError, KeyError, AttributeError):
                return False
        return True

    @staticmethod
    def record_projection(record, body):
        return (
            record["id"], record["kind"],
            str(record.get("text", "")) + " " + str(record.get("title", "")),
            record.get("start", ""), body,
        )

    def final_source_freshness(self, captured):
        """Observe freshness without replacing or restarting this fixed snapshot."""
        result = {
            "sourceSnapshotAt": captured.get("snapshotCapturedAt"),
            "sourceSnapshotCurrent": False,
        }
        try:
            latest = self.request({"op": "status"})
        except (ValueError, OSError, subprocess.TimeoutExpired):
            report("Source freshness unavailable; keeping the verified snapshot")
            return result
        result["sourceSnapshotCurrent"] = (
            latest.get("snapshot") == captured["snapshot"]
            and latest.get("sourceDeviceID") == captured["sourceDeviceID"]
            and latest.get("access") == captured["access"]
            and latest.get("snapshotCurrent") is True
        )
        if not result["sourceSnapshotCurrent"]:
            report("Keeping this snapshot; newer phone data can be included in the next sync")
        return result

    def prepared_status(self):
        status = self.request({"op": "status"})
        capabilities = status.get("capabilities", {})
        if not isinstance(capabilities, dict):
            raise ValueError("invalid_capabilities")
        if capabilities.get("onDemandSnapshot") is not True:
            return status  # Compatible with already-prepared older companions.
        # One explicit attempt per sync. Polling does not restart a failed job
        # or chase each new recording publication indefinitely.
        deadline = time.monotonic() + 120
        report("Preparing text snapshot on phone")
        request = {"op": "prepareSnapshot", "retry": True}
        if capabilities.get("snapshotLeases") is True:
            request["useSnapshotLease"] = True
        token = None
        handed_off = False
        try:
            status = self.request(request)
            if request.get("useSnapshotLease") is True:
                token = status.get("lease")
                if not valid_lease_token(token):
                    token = None
                    raise ValueError("invalid_snapshot_lease")
            while status.get("snapshotState") == "preparing":
                if time.monotonic() >= deadline:
                    raise ValueError("snapshot_preparation_timed_out")
                time.sleep(0.25)
                poll = {"op": "status"}
                if token is not None:
                    poll["lease"] = token
                status = self.request(poll)
            if status.get("snapshotState") == "failed":
                raise ValueError("snapshot_preparation_failed")
            if status.get("snapshotState") != "ready":
                raise ValueError("snapshot_not_ready")
            if token is not None:
                status = {**status, "lease": token}
            report("Text snapshot ready")
            handed_off = True
            return status
        finally:
            if token is not None and not handed_off:
                self.release_snapshot_lease(token)

    def complete_record(self, row, snapshot, capabilities, staged_records):
        if not isinstance(row, dict) or "recordTransfer" not in row:
            return row
        transfer = row["recordTransfer"]
        if (
            capabilities.get("chunkedRecords") is not True
            or not isinstance(row.get("id"), str) or not row["id"]
            or not isinstance(transfer, dict)
            or transfer.get("encoding") != "json-utf8"
            or type(transfer.get("size")) is not int
            # A separate parser/resource bound, not the 2 MiB wire-frame limit.
            or not 0 < transfer["size"] <= 128 * 1024 * 1024
            or not isinstance(transfer.get("sha256"), str)
            or not re.fullmatch("[0-9a-f]{64}", transfer["sha256"])
        ):
            raise ValueError("invalid_record_transfer")
        size, digest = transfer["size"], transfer["sha256"]
        directory = self.root / "record-staging"
        directory.mkdir(mode=0o700, exist_ok=True)
        partial = directory / (digest + ".partial")
        offset = partial.stat().st_size if partial.exists() else 0
        if offset > size:
            raise ValueError("invalid_record_partial_size")
        report("Downloading large text record", offset, size, "bytes", force=True)
        with partial.open("ab") as output:
            os.chmod(partial, 0o600)
            while offset < size:
                length = min(262144, size - offset)
                reply = self.request({
                    "op": "recordChunk", "id": row["id"], "snapshot": snapshot,
                    "offset": offset, "length": length,
                })
                if (
                    reply.get("snapshot") != snapshot or reply.get("id") != row["id"]
                    or type(reply.get("offset")) is not int or reply["offset"] != offset
                    or type(reply.get("size")) is not int or reply["size"] != size
                    or reply.get("sha256") != digest
                ):
                    raise ValueError("record_chunk_metadata_mismatch")
                try:
                    data = base64.b64decode(reply["data"], validate=True)
                except (KeyError, ValueError, TypeError):
                    raise ValueError("invalid_record_chunk") from None
                if len(data) != length or hashlib.sha256(data).hexdigest() != reply.get("chunkSHA256"):
                    raise ValueError("record_chunk_hash_mismatch")
                output.write(data)
                offset += len(data)
                report("Downloading large text record", offset, size, "bytes")
        report("Verifying large text record")
        if sha(partial) != digest:
            partial.replace(directory / (digest + ".corrupt"))
            raise ValueError("record_hash_mismatch")
        with partial.open(encoding="utf-8") as source:
            complete = json.load(source)
        if not isinstance(complete, dict) or complete.get("id") != row["id"] or "recordTransfer" in complete:
            raise ValueError("record_identity_mismatch")
        staged_records.append(partial)
        report("Large text record verified")
        return complete

    def search(self, query="", kind=None, date=None, limit=20, offset=0):
        if not 1 <= limit <= 100:
            raise ValueError("limit_must_be_1_to_100")
        if not 0 <= offset <= 1000000:
            raise ValueError("invalid_offset")
        report("Searching cached text", force=True)
        clauses = []
        values = []
        for token in query.split():
            clauses.append("text LIKE ? ESCAPE '\\'")
            values.append(
                "%"
                + token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                + "%"
            )
        if kind:
            clauses.append("kind=?")
            values.append(kind)
        if date:
            clauses.append("date LIKE ?")
            values.append(date + "%")
        sql = (
            "SELECT body FROM records"
            + (" WHERE " + " AND ".join(clauses) if clauses else "")
            + " ORDER BY date,id LIMIT ? OFFSET ?"
        )
        rows = [
            json.loads(r[0]) for r in self.db.execute(sql, values + [limit + 1, offset])
        ]
        report("Keyword search complete")
        return {
            "syncedAt": self.metadata().get("syncedAt"),
            "freshness": "last_synced_snapshot",
            "records": rows[:limit],
            "limit": limit,
            "nextOffset": offset + limit if len(rows) > limit else None,
        }

    def assets(self, limit=20, offset=0):
        if not 1 <= limit <= 100 or not 0 <= offset <= 1000000:
            raise ValueError("invalid_page")
        rows = [
            json.loads(r[0])
            for r in self.db.execute(
                "SELECT body FROM assets ORDER BY id LIMIT ? OFFSET ?",
                (limit + 1, offset),
            )
        ]
        return {
            "assets": rows[:limit],
            "nextOffset": offset + limit if len(rows) > limit else None,
            "syncedAt": self.metadata().get("syncedAt"),
        }

    def read(self, identity):
        row = self.db.execute(
            "SELECT body FROM records WHERE id=?", (identity,)
        ).fetchone()
        if not row:
            raise ValueError("record_not_found")
        return {
            "record": json.loads(row[0]),
            "syncedAt": self.metadata().get("syncedAt"),
        }

    def prepare_audio(self, asset, *, snapshot=None):
        with self.operation_lock:
            return self._prepare_audio(asset, snapshot=snapshot)

    def _prepare_audio(self, asset, *, snapshot=None):
        metadata = self.metadata()
        if metadata.get("access") != "text-and-audio-read-only":
            raise ValueError("audio_access_denied")
        if metadata.get("capabilities", {}).get("onDemandAudio") is not True:
            raise ValueError("on_demand_audio_not_supported")
        if snapshot is not None and metadata.get("snapshot") != snapshot:
            raise ValueError("snapshot_changed")
        snapshot = metadata["snapshot"]
        row = self.db.execute("SELECT body FROM assets WHERE id=?", (asset["id"],)).fetchone()
        if not row or json.loads(row[0]).get("contentRevision") != asset.get("contentRevision"):
            raise ValueError("audio_changed: sync again before retrying")
        with self.snapshot_access(snapshot, metadata.get("capabilities", {}), prepare_if_needed=True):
            return self._request_prepared_audio(asset, snapshot)

    def _request_prepared_audio(self, asset, snapshot):
        request = {"op": "prepareAudio", "id": asset["id"], "snapshot": snapshot}
        report("Preparing requested audio on phone", force=True)
        try:
            reply = self.request(request, timeout=90)
        except ValueError as error:
            if str(error) != "snapshot_not_ready" or self.snapshot_lease is not None:
                raise
            # Access may have been reopened since the cache was synchronized.
            # Prepare once, then require the same source version before reading.
            status = self.prepared_status()
            if status.get("lease") is not None:
                # Capabilities changed after the outer audio operation started.
                # Do not use a new lease only for preparation and leave the
                # subsequent download unprotected. An explicit sync can retry.
                self.release_snapshot_lease(status["lease"])
                raise ValueError("snapshot_changed") from None
            if status.get("access") != "text-and-audio-read-only":
                raise ValueError("audio_access_denied") from None
            if status.get("snapshot") != snapshot:
                raise ValueError("snapshot_changed") from None
            reply = self.request(request, timeout=90)
        ready = reply.get("asset")
        if (
            reply.get("snapshot") != snapshot
            or not isinstance(ready, dict)
            or ready.get("id") != asset["id"]
            or type(ready.get("size")) is not int
            or ready["size"] <= 0
            or not re.fullmatch("[0-9a-f]{64}", str(ready.get("sha256", "")))
            or ready.get("mime") not in ("audio/mp4", "audio/wav", "audio/x-wav", "audio/x-caf", "audio/aac")
            or not isinstance(ready.get("timeline"), list)
            or not ready["timeline"]
        ):
            raise ValueError("invalid_prepared_audio")
        for span in ready["timeline"]:
            if not isinstance(span, dict):
                raise ValueError("invalid_prepared_audio_timeline")
            start, end = span.get("fileStart"), span.get("fileEnd")
            if (
                type(start) not in (int, float) or type(end) not in (int, float)
                or not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end
            ):
                raise ValueError("invalid_prepared_audio_timeline")
            try:
                recorded = datetime.fromisoformat(span["recordedStart"].replace("Z", "+00:00"))
            except (KeyError, TypeError, ValueError, AttributeError):
                raise ValueError("invalid_prepared_audio_timeline") from None
            if recorded.tzinfo is None:
                raise ValueError("invalid_prepared_audio_timeline")
        asset = {**asset, **ready}
        with self.db:
            self.db.execute("UPDATE assets SET body=? WHERE id=?", (json.dumps(asset), asset["id"]))
        report("Requested audio ready")
        return asset

    def audio(self, identity):
        with self.operation_lock:
            return self._audio(identity)

    def _audio(self, identity):
        metadata = self.metadata()
        if metadata.get("access") == "text-only-read-only":
            raise ValueError(
                "audio_access_denied: enable text and audio on the phone, then sync"
            )
        row = self.db.execute(
            "SELECT body FROM assets WHERE id=?", (identity,)
        ).fetchone()
        if not row:
            raise ValueError("asset_not_in_synced_snapshot")
        asset = json.loads(row[0])
        snapshot = metadata["snapshot"]
        suffixes = {
            "audio/mp4": ".m4a", "audio/wav": ".wav", "audio/x-wav": ".wav",
            "audio/x-caf": ".caf", "audio/aac": ".aac",
        }
        # Identical bytes may belong to distinct recordings/timelines. Keep
        # each on-demand asset's adjacent provenance file independent.
        asset_suffix = (
            "-" + hashlib.sha256(identity.encode()).hexdigest()
            if asset.get("preparation") == "on-demand" else ""
        )
        saved_hash = str(asset.get("sha256", ""))
        if re.fullmatch("[0-9a-f]{64}", saved_hash):
            cached = self.root / "audio" / (saved_hash + asset_suffix + suffixes.get(asset.get("mime"), ".bin"))
            if cached.is_file() and sha(cached) == saved_hash:
                report("Using verified cached audio", force=True)
                return {"path": str(cached), "asset": asset, "cacheHit": True}
        with self.snapshot_access(snapshot, metadata.get("capabilities", {}), prepare_if_needed=True):
            return self._download_audio(identity, asset, snapshot, asset_suffix, suffixes)

    def _download_audio(self, identity, asset, snapshot, asset_suffix, suffixes):
        if asset.get("preparation") == "on-demand":
            asset = self.prepare_audio(asset, snapshot=snapshot)
        expected = asset["sha256"]
        size = asset["size"]
        if (
            not re.fullmatch("[0-9a-f]{64}", expected)
            or type(size) is not int
            or size < 0
        ):
            raise ValueError("invalid_asset_metadata")
        folder = self.root / "audio"
        folder.mkdir(mode=0o700, exist_ok=True)
        suffix = suffixes.get(asset.get("mime"), ".bin")
        target = folder / (expected + asset_suffix + suffix)
        temporary = folder / (expected + asset_suffix + ".partial")
        if target.exists() and sha(target) == expected:
            report("Using verified cached audio", force=True)
            return {"path": str(target), "asset": asset, "cacheHit": True}
        offset = temporary.stat().st_size if temporary.exists() else 0
        if offset > size:
            raise ValueError("partial_size_invalid")
        preparation_retried = False
        report("Downloading audio", offset, size, "bytes", force=True)
        with temporary.open("ab") as out:
            os.chmod(temporary, 0o600)
            while offset < size:
                request = {
                    "op": "audio", "id": identity, "offset": offset,
                    "length": min(262144, size - offset), "snapshot": snapshot,
                }
                try:
                    reply = self.request(request)
                except ValueError as error:
                    if str(error) != "audio_not_prepared" or preparation_retried or asset.get("preparation") != "on-demand":
                        raise
                    preparation_retried = True
                    refreshed = self.prepare_audio(asset, snapshot=snapshot)
                    if refreshed["sha256"] != expected or refreshed["size"] != size:
                        raise ValueError("audio_changed: sync again before retrying") from None
                    reply = self.request(request)
                data = base64.b64decode(reply["data"], validate=True)
                if (
                    reply.get("snapshot") != snapshot
                    or reply["offset"] != offset
                    or reply["sha256"] != expected
                    or reply["size"] != size
                    or len(data) != min(262144, size - offset)
                    or hashlib.sha256(data).hexdigest() != reply["chunkSHA256"]
                ):
                    raise ValueError("audio_chunk_integrity_failed")
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
                offset += len(data)
                report("Downloading audio", offset, size, "bytes")
        report("Verifying downloaded audio")
        if sha(temporary) != expected:
            # Retain evidence without retrying a corrupt partial forever.
            temporary.replace(
                temporary.with_name(
                    expected + ".corrupt-" + datetime.now().strftime("%Y%m%d%H%M%S%f")
                )
            )
            raise ValueError(
                "audio_hash_mismatch: corrupt partial retained; retry starts clean"
            )
        temporary.replace(target)
        atomic_json(target.with_suffix(".asset.json"), asset)
        report("Audio saved and verified")
        return {"path": str(target), "asset": asset, "cacheHit": False}


def bridge():
    if sys.platform != "darwin":
        raise ValueError("macOS_required_for_live_connection")
    if not shutil.which("swift"):
        raise ValueError("swift_missing: install Xcode Command Line Tools")
    package = HERE / "link"
    build = package / ".build"
    build.mkdir(exist_ok=True)
    # Multiple agent processes can request data simultaneously; serialize compilation.
    with (build / "auday-build.lock").open("a+b") as lock:
        lock_file(lock)
        output = subprocess.run(
            ["swift", "build", "--package-path", str(package), "--show-bin-path"],
            text=True,
            capture_output=True,
            check=True,
        )
        binary = Path(output.stdout.strip()) / "auday-link"
        sources = [package / "Package.swift", *package.glob("Sources/**/*.swift")]
        if (
            not binary.exists()
            or max(p.stat().st_mtime for p in sources) > binary.stat().st_mtime
        ):
            subprocess.run(
                ["swift", "build", "--package-path", str(package)],
                stdout=sys.stderr,
                check=True,
            )
        return binary


def bridge_command():
    if is_windows():
        return [sys.executable, str(HERE / "windows_link.py")]
    return [str(bridge())]


def main():
    if is_windows():
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=DEFAULT_HOME)
    parser.add_argument("--profile", default="default")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")
    discover = commands.add_parser("discover")
    discover.add_argument("--peer-to-peer", action="store_true")
    for name in ["pair", "pair-window"]:
        pair = commands.add_parser(name)
        target = pair.add_mutually_exclusive_group(required=True)
        target.add_argument("--service")
        target.add_argument("--host")
        pair.add_argument("--port", type=int)
        pair.add_argument("--peer-to-peer", action="store_true")
    sync = commands.add_parser("sync")
    sync.add_argument("--mode", choices=["incremental", "full"], default="incremental",
                      help="Reuse verified unchanged records by default; full downloads all text again")
    library = commands.add_parser("library", help="Maintain readable files from the saved library, without the phone")
    library.add_argument("--language", choices=["en", "zh-CN"], default=None,
                         help="Choose file labels; otherwise preserve the library's saved language")
    status = commands.add_parser("status")
    status.add_argument("--live", action="store_true")
    search = commands.add_parser("search")
    search.add_argument("query", nargs="?", default="")
    search.add_argument(
        "--kind", choices=["transcript", "highlight", "summary", "reflection", "memory"]
    )
    search.add_argument("--date")
    search.add_argument("--limit", type=int, default=20)
    search.add_argument("--offset", type=int, default=0)
    search.add_argument("--mode", choices=["keyword", "semantic"], default="keyword")
    assets = commands.add_parser("assets")
    assets.add_argument("--limit", type=int, default=20)
    assets.add_argument("--offset", type=int, default=0)
    read = commands.add_parser("read")
    read.add_argument("id")
    audio = commands.add_parser("audio")
    audio.add_argument("id")
    export = commands.add_parser("export", help="Export cached text as readable daily Markdown files")
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--language", choices=["en", "zh-CN"], default="en")
    semantic_setup = commands.add_parser("configure-semantic", help="Configure an existing local embedding model")
    semantic_setup.add_argument("--model", type=Path, required=True)
    semantic_setup.add_argument("--server", type=Path, required=True)
    commands.add_parser("index", help="Build or update the local semantic index")
    commands.add_parser("index-status", help="Check semantic index coverage and freshness")
    setup = commands.add_parser("setup", help="Inspect or prepare first-use capabilities")
    capability = setup.add_mutually_exclusive_group()
    capability.add_argument("--semantic", action="store_true")
    capability.add_argument("--defer-semantic", action="store_true")
    capability.add_argument("--audio", action="store_true")
    setup.add_argument("--model", choices=["small", "turbo"])
    setup.add_argument("--diarize", action=argparse.BooleanOptionalAction, default=True)
    transcribe = commands.add_parser("transcribe", help="Transcribe with the prepared private audio runtime")
    transcribe.add_argument("--audio", type=Path, required=True)
    transcribe.add_argument("--output", type=Path, required=True)
    transcribe.add_argument("--language", default="auto")
    transcribe.add_argument("--diarize", action=argparse.BooleanOptionalAction, default=True)
    transcribe.add_argument("--threads", type=int, default=4)
    transcribe.add_argument("--speakers", type=int)
    transcribe.add_argument("--asset-metadata", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    if args.command == "doctor":
        from first_run import setup_status
        dump(
            {
                "platform": sys.platform,
                "python": sys.version.split()[0],
                "tools": {
                    x: shutil.which(x) for x in ["swift", "ffmpeg", "whisper-cli", "uv"]
                },
                "version": (HERE.parent / "VERSION").read_text().strip(),
                "companionProtocol": "auday-workflow-link-v2",
                "phoneSupport": "compatible-build-required",
                "liveConnectionSupported": sys.platform in ("darwin", "win32"),
                "capabilities": setup_status(args.home, args.profile),
            }
        )
        return
    if args.command == "setup":
        import first_run
        if args.semantic:
            result = first_run.setup_semantic(args.home, args.profile)
        elif args.defer_semantic:
            result = first_run.defer_semantic(args.home, args.profile)
        elif args.audio:
            from audio_setup import setup_audio
            result = setup_audio(
                args.home, model=args.model or first_run.recommended_audio_model(), diarize=args.diarize,
            )
        else:
            result = first_run.setup_status(args.home, args.profile)
            result["recommendedAudioModel"] = first_run.recommended_audio_model()
        dump(result)
        return
    if args.command == "transcribe":
        from audio_setup import transcribe as transcribe_local
        dump(transcribe_local(
            args.home, args.audio, args.output, language=args.language, diarize=args.diarize,
            threads=args.threads, speakers=args.speakers, asset_metadata=args.asset_metadata,
        ))
        return
    if args.command == "discover":
        subprocess.run(
            bridge_command() + ["discover"]
            + (["--peer-to-peer"] if args.peer_to_peer else []),
            check=True,
        )
        return
    if args.command == "export":
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", args.profile):
            raise ValueError("invalid_profile_name")
        from library_export import export_library
        database = args.home.expanduser().resolve() / args.profile / "library.sqlite"
        dump(export_library(database, args.output, args.language, progress=report))
        return
    if args.command in ("configure-semantic", "index", "index-status") or (
        args.command == "search" and args.mode == "semantic"
    ):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", args.profile):
            raise ValueError("invalid_profile_name")
        import semantic_search
        root = args.home.expanduser().resolve() / args.profile
        if args.command == "configure-semantic":
            result = semantic_search.configure(root, args.model, args.server)
        elif args.command == "index":
            result = semantic_search.build_index(root)
        elif args.command == "index-status":
            result = semantic_search.status(root)
        else:
            if args.offset:
                raise ValueError("semantic_offset_not_supported: narrow the query, date or kind")
            result = semantic_search.search(root, args.query, args.kind, args.date, args.limit)
        dump(result)
        return
    app = Auday(args.home, args.profile)
    try:
        if args.command in ["pair", "pair-window"]:
            if args.command == "pair-window":
                bridge_command()
                command = [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--home",
                    str(args.home),
                    "--profile",
                    args.profile,
                    "pair",
                ]
                if args.service:
                    command += ["--service", args.service]
                else:
                    if args.port is None or not 0 < args.port < 65536:
                        raise ValueError("valid_port_required")
                    command += ["--host", args.host, "--port", str(args.port)]
                if args.peer_to_peer:
                    command += ["--peer-to-peer"]
                if is_windows():
                    # The new console owns hidden input; the assistant receives
                    # only this pending status, never the communication secret.
                    environment = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
                    subprocess.Popen(command, creationflags=subprocess.CREATE_NEW_CONSOLE,
                                     close_fds=True, env=environment)
                    dump({"status": "awaiting_local_password_input"})
                    return
                launcher = app.root / "pair.command"
                launcher.write_text("#!/bin/zsh\n" + shlex.join(command) + "\n", encoding="utf-8")
                os.chmod(launcher, 0o700)
                subprocess.run(["open", str(launcher)], check=True)
                dump(
                    {
                        "status": "awaiting_local_password_input",
                        "launcher": str(launcher),
                    }
                )
                return
            command = bridge_command() + ["pair", "--profile", app.keychain_account]
            config = {}
            if args.service:
                command += ["--service", args.service]
                config["service"] = args.service
            else:
                if args.port is None or not 0 < args.port < 65536:
                    raise ValueError("valid_port_required")
                command += ["--host", args.host, "--port", str(args.port)]
                config.update(host=args.host, port=args.port)
            if args.peer_to_peer:
                command += ["--peer-to-peer"]
            config["peerToPeer"] = args.peer_to_peer
            subprocess.run(command, check=True)
            atomic_json(app.root / "connection.json", config)
        elif args.command == "sync":
            dump(app.sync(mode=args.mode))
        elif args.command == "library":
            dump(app.library(language=args.language))
        elif args.command == "status":
            dump(
                {
                    "cache": app.metadata(),
                    "phone": app.request({"op": "status"})
                    if args.live
                    else "not_probed",
                }
            )
        elif args.command == "search":
            dump(app.search(args.query, args.kind, args.date, args.limit, args.offset))
        elif args.command == "assets":
            dump(app.assets(args.limit, args.offset))
        elif args.command == "read":
            dump(app.read(args.id))
        elif args.command == "audio":
            dump(app.audio(args.id))
    finally:
        app.close()


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, RuntimeError, sqlite3.Error, subprocess.SubprocessError) as error:
        dump({"ok": False, "error": str(error)})
        sys.exit(1)
