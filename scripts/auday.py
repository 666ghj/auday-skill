#!/usr/bin/env python3
"""Local Auday workflow CLI. Standard library only; macOS Network.framework bridge."""

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
import fcntl
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from progress import report

HERE = Path(__file__).resolve().parent
DEFAULT_HOME = Path.home() / "Library/Application Support/AudayWorkflow"


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
    with temporary.open("w") as out:
        json.dump(value, out, ensure_ascii=False, allow_nan=False)
    os.chmod(temporary, 0o600)
    temporary.replace(path)


class Auday:
    def __init__(self, home=DEFAULT_HOME, profile="default", transport=None):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", profile):
            raise ValueError("invalid_profile_name")
        self.root = Path(home).expanduser().resolve() / profile
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.profile = profile
        self.transport = transport
        self.keychain_account = (
            "profile-" + hashlib.sha256(str(self.root).encode()).hexdigest()[:32]
        )
        self.db = sqlite3.connect(self.root / "library.sqlite")
        os.chmod(self.root / "library.sqlite", 0o600)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY, kind TEXT, text TEXT, date TEXT, body TEXT);
            CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY, body TEXT);
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT);
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
        return json.loads(path.read_text())

    def request(self, payload, timeout=15):
        if self.transport:
            result = self.transport(payload)
        else:
            config = self.config()
            config.update(profile=self.keychain_account, request=payload, timeout=max(1, timeout - 5))
            process = subprocess.run(
                [str(bridge()), "request"],
                input=json.dumps(config) + "\n",
                text=True,
                capture_output=True,
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

    def sync(self):
        report("Checking phone status", force=True)
        status = self.prepared_status()
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
        previous = self.metadata()
        capabilities = status.get("capabilities", {})
        if not isinstance(capabilities, dict):
            raise ValueError("invalid_capabilities")
        source_freshness = {
            "sourceSnapshotAt": status.get("snapshotCapturedAt"),
            "sourceSnapshotCurrent": status.get("snapshotCurrent", True),
        }
        if type(source_freshness["sourceSnapshotCurrent"]) is not bool:
            raise ValueError("invalid_snapshot_freshness")
        if (
            previous.get("snapshot") == snapshot
            and previous.get("recordCount") == status["recordCount"]
            and previous.get("assetCount") == status["assetCount"]
            and previous.get("access") == status["access"]
        ):
            with self.db:
                self.db.executemany(
                    "INSERT OR REPLACE INTO metadata VALUES(?,?)",
                    [(key, json.dumps(value)) for key, value in {
                        "syncedAt": now(), "capabilities": capabilities, **source_freshness
                    }.items()],
                )
            report("Snapshot unchanged; local library is current")
            return {
                **self.metadata(),
                "changedRecords": 0,
                "downloadedRecords": 0,
                "audioDownloaded": False,
            }
        record_rows, asset_rows, staged_records = [], [], []
        for op, field, destination in [
            ("list", "records", record_rows),
            ("assets", "assets", asset_rows),
        ]:
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
        # Full verified snapshot replaces the query projection only; downloaded files/results remain.
        before = {r[0]: r[1] for r in self.db.execute("SELECT id,body FROM records")}
        changed = sum(
            before.get(row["id"]) != json.dumps(row, ensure_ascii=False, sort_keys=True)
            for row in record_rows
        )
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
            self.db.execute("DELETE FROM records")
            self.db.execute("DELETE FROM assets")
            for row in record_rows:
                body = json.dumps(row, ensure_ascii=False, sort_keys=True)
                self.db.execute(
                    "INSERT INTO records VALUES(?,?,?,?,?)",
                    (
                        row["id"],
                        row["kind"],
                        str(row.get("text", "")) + " " + str(row.get("title", "")),
                        row.get("start", ""),
                        body,
                    ),
                )
            self.db.executemany(
                "INSERT INTO assets VALUES(?,?)",
                [(r["id"], json.dumps(r, ensure_ascii=False)) for r in asset_rows],
            )
            self.db.executemany(
                "INSERT OR REPLACE INTO metadata VALUES(?,?)",
                [(k, json.dumps(v)) for k, v in meta.items()],
            )
        for path in staged_records:
            path.unlink(missing_ok=True)
        report("Synchronization complete")
        return {**meta, "changedRecords": changed, "audioDownloaded": False}

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
        status = self.request({"op": "prepareSnapshot", "retry": True})
        while status.get("snapshotState") == "preparing":
            if time.monotonic() >= deadline:
                raise ValueError("snapshot_preparation_timed_out")
            time.sleep(0.25)
            status = self.request({"op": "status"})
        if status.get("snapshotState") == "failed":
            raise ValueError("snapshot_preparation_failed")
        if status.get("snapshotState") != "ready":
            raise ValueError("snapshot_not_ready")
        report("Text snapshot ready")
        return status

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

    def prepare_audio(self, asset):
        if self.metadata().get("access") != "text-and-audio-read-only":
            raise ValueError("audio_access_denied")
        if self.metadata().get("capabilities", {}).get("onDemandAudio") is not True:
            raise ValueError("on_demand_audio_not_supported")
        snapshot = self.metadata()["snapshot"]
        request = {"op": "prepareAudio", "id": asset["id"], "snapshot": snapshot}
        report("Preparing requested audio on phone", force=True)
        try:
            reply = self.request(request, timeout=90)
        except ValueError as error:
            if str(error) != "snapshot_not_ready":
                raise
            # Access may have been reopened since the cache was synchronized.
            # Prepare once, then require the same source version before reading.
            status = self.prepared_status()
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
        if self.metadata().get("access") == "text-only-read-only":
            raise ValueError(
                "audio_access_denied: enable text and audio on the phone, then sync"
            )
        row = self.db.execute(
            "SELECT body FROM assets WHERE id=?", (identity,)
        ).fetchone()
        if not row:
            raise ValueError("asset_not_in_synced_snapshot")
        asset = json.loads(row[0])
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
        if asset.get("preparation") == "on-demand":
            saved_hash = str(asset.get("sha256", ""))
            if re.fullmatch("[0-9a-f]{64}", saved_hash):
                cached = self.root / "audio" / (saved_hash + asset_suffix + suffixes.get(asset.get("mime"), ".bin"))
                if cached.is_file() and sha(cached) == saved_hash:
                    report("Using verified cached audio", force=True)
                    return {"path": str(cached), "asset": asset, "cacheHit": True}
            asset = self.prepare_audio(asset)
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
                    "length": min(262144, size - offset), "snapshot": self.metadata()["snapshot"],
                }
                try:
                    reply = self.request(request)
                except ValueError as error:
                    if str(error) != "audio_not_prepared" or preparation_retried or asset.get("preparation") != "on-demand":
                        raise
                    preparation_retried = True
                    refreshed = self.prepare_audio(asset)
                    if refreshed["sha256"] != expected or refreshed["size"] != size:
                        raise ValueError("audio_changed: sync again before retrying") from None
                    reply = self.request(request)
                data = base64.b64decode(reply["data"], validate=True)
                if (
                    reply["offset"] != offset
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
    with (build / "auday-build.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
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


def main():
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
    commands.add_parser("sync")
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
    args = parser.parse_args()
    os.umask(0o077)
    if args.command == "doctor":
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
                "liveConnectionSupported": sys.platform == "darwin",
            }
        )
        return
    if args.command == "discover":
        subprocess.run(
            [str(bridge()), "discover"]
            + (["--peer-to-peer"] if args.peer_to_peer else []),
            check=True,
        )
        return
    if args.command == "export":
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", args.profile):
            raise ValueError("invalid_profile_name")
        from library_export import export_library
        database = args.home.expanduser().resolve() / args.profile / "library.sqlite"
        dump(export_library(database, args.output, args.language))
        return
    app = Auday(args.home, args.profile)
    try:
        if args.command in ["pair", "pair-window"]:
            if args.command == "pair-window":
                bridge()
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
                launcher = app.root / "pair.command"
                launcher.write_text("#!/bin/zsh\n" + shlex.join(command) + "\n")
                os.chmod(launcher, 0o700)
                subprocess.run(["open", str(launcher)], check=True)
                dump(
                    {
                        "status": "awaiting_local_password_input",
                        "launcher": str(launcher),
                    }
                )
                return
            command = [str(bridge()), "pair", "--profile", app.keychain_account]
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
            dump(app.sync())
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
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        dump({"ok": False, "error": str(error)})
        sys.exit(1)
