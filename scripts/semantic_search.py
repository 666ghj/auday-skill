#!/usr/bin/env python3
"""Local, persistent semantic retrieval. Python standard library only."""

import argparse
import contextlib
import datetime
import hashlib
import heapq
import http.client
import json
import math
import operator
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time

from platform_utils import (
    is_windows, lock_file, unlock_file, secure_directory, start_process, stop_process,
)

try:
    from progress import report
except ImportError:
    def report(stage, completed=None, total=None, unit=None, force=False):
        if force or completed is None or completed == total or completed % 50 == 0:
            print(json.dumps({"stage": stage, "completed": completed, "total": total,
                              "unit": unit}), file=sys.stderr, flush=True)


MODEL_SHA = "b5ce9d77a3fc4b3b39ccb5643c36777911cc4eb46a66962eadfa3f5f60490d63"
MODEL_REVISION = "0f741b5a6585bd53aeb15cd1372c56f2a0f65e12"
DIMENSIONS = 768
MAX_TOKENS = 512
CHUNK_CHARS = 900
CHUNK_OVERLAP = 100
SCHEMA = "auday.semantic.v1"
POLICY = {
    "schema": SCHEMA, "model": "EmbeddingGemma 300M ordinary Q8_0",
    "modelSHA256": MODEL_SHA, "modelRevision": MODEL_REVISION,
    "dimensions": DIMENSIONS, "maxTokens": MAX_TOKENS,
    "queryPrefix": "task: search result | query: ",
    "documentFormat": "title: {title} | text: {text}",
    "pooling": "mean", "projection": "dense_2+dense_3", "normalization": "L2",
    "storage": "little-endian-float16", "noRepack": True,
    "chunkChars": CHUNK_CHARS, "overlapChars": CHUNK_OVERLAP,
}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _atomic_json(path, value):
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix=".semantic-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            target.write(_json(value))
            target.flush()
            os.fsync(target.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _runtime_identity(server):
    """Include linked libraries: recent llama-server binaries are thin launchers."""
    server = Path(server)
    libraries = sorted(
        (path for path in server.parent.rglob("*")
         if path.is_file() and path.suffix.lower() in (".dylib", ".dll")),
        key=lambda path: path.relative_to(server.parent).as_posix(),
    )
    files = [server, *libraries]
    return _digest({p.relative_to(server.parent).as_posix(): _sha(p) for p in files})


def configure(root, model_path, server_path):
    root = Path(root).expanduser().resolve()
    model = Path(model_path).expanduser().resolve()
    server = Path(server_path).expanduser().resolve()
    if not model.is_file() or _sha(model) != MODEL_SHA:
        raise ValueError("semantic_model_mismatch: require the complete pinned ordinary Q8_0 model")
    if not server.is_file() or not os.access(server, os.X_OK):
        raise ValueError("semantic_runtime_missing")
    result = subprocess.run([str(server), "--version"], capture_output=True, text=True, timeout=60)
    version = result.stdout + result.stderr
    if result.returncode or not re.search(r"build 10865, commit d4389a4dd", version):
        raise ValueError("semantic_runtime_mismatch: require llama.cpp b10865 (d4389a4dd)")
    config = {"modelPath": str(model), "serverPath": str(server),
              "runtimeSHA256": _runtime_identity(server), "policy": POLICY}
    secure_directory(root)
    _atomic_json(root / "semantic-config.json", config)
    return {"configured": True, "model": POLICY["model"], "dimensions": DIMENSIONS,
            "configPath": str(root / "semantic-config.json"), "modelBytes": model.stat().st_size,
            "inference": "local", "runtime": "llama.cpp b10865"}


def _configuration(root):
    path = Path(root) / "semantic-config.json"
    if not path.is_file():
        raise ValueError("semantic_not_configured: run configure-semantic with local model and runtime")
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("policy") != POLICY:
        raise ValueError("semantic_configuration_changed: configure and rebuild the index")
    return config


class _UnixHTTP(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=60)
        self.path = str(path)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


def _loopback_port():
    # The runtime cannot inherit a listening socket. Reserve an OS-selected port
    # until immediately before launch; startup detects a runtime bind failure.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


class LocalEncoder:
    """Private Unix socket or authenticated Windows loopback; local inference only."""
    def __init__(self, config):
        self.config = config
        self.identity = _digest(config["policy"] | {"runtimeSHA256": config["runtimeSHA256"]})
        self.process = None
        self.directory = None
        self.loopback_port = None

    def __enter__(self):
        model = Path(self.config["modelPath"])
        server = Path(self.config["serverPath"])
        report("Verifying local semantic model", force=True)
        if _sha(model) != MODEL_SHA or _runtime_identity(server) != self.config["runtimeSHA256"]:
            raise ValueError("semantic_model_or_runtime_changed: configure and rebuild")
        # macOS Unix sockets have a short path limit. Windows uses the user's
        # system temp directory and never assumes a POSIX filesystem layout.
        self.directory = tempfile.TemporaryDirectory(
            prefix="auday-emb-", dir=None if is_windows() else "/tmp",
        )
        try:
            directory = secure_directory(Path(self.directory.name))
            self.token = secrets.token_urlsafe(32)
            token_file = directory / "auth"
            token_file.write_text(self.token, encoding="utf-8")
            if not is_windows():
                token_file.chmod(0o600)
            if is_windows():
                self.loopback_port = _loopback_port()
                endpoint_arguments = ["--host", "127.0.0.1", "--port", str(self.loopback_port)]
            else:
                self.endpoint = directory / "s.sock"
                endpoint_arguments = ["--host", str(self.endpoint)]
            cmd = [
                str(server), "-m", str(model), "--embeddings", "--pooling", "mean",
                "--embd-normalize", "2", "-ngl", "0", "--device", "none",
                "-t", "2", "-tb", "2", "-c", str(MAX_TOKENS), "-b", str(MAX_TOKENS),
                "-ub", str(MAX_TOKENS), "-np", "1", *endpoint_arguments,
                "--api-key-file", str(token_file), "--offline", "--no-warmup",
                "--no-cache-prompt", "--cache-ram", "0", "--no-repack",
                "--no-webui", "--log-disable",
            ]
            report("Loading local semantic model", force=True)
            self.process = start_process(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, cwd=server.parent,
            )
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError("semantic_runtime_failed_to_start")
                try:
                    healthy = self._api("health", timeout=1).get("status") == "ok"
                    if healthy and self.process.poll() is None:
                        return self
                except (OSError, http.client.HTTPException, ValueError):
                    pass
                time.sleep(0.1)
            raise TimeoutError("semantic_runtime_load_timeout")
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        try:
            if self.process is not None:
                stop_process(self.process, timeout=5)
        finally:
            self.process = None
            if self.directory is not None:
                self.directory.cleanup()
                self.directory = None

    def _api(self, endpoint, payload=None, *, timeout=60):
        # http.client connects directly; proxy environment variables cannot send
        # private text off-machine. Neither endpoint comes from user config.
        connection = (http.client.HTTPConnection("127.0.0.1", self.loopback_port, timeout=timeout)
                      if self.loopback_port is not None else _UnixHTTP(self.endpoint))
        connection.timeout = timeout
        try:
            connection.request(
                "POST" if payload is not None else "GET", "/" + endpoint,
                body=_json(payload).encode() if payload is not None else None,
                headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.token},
            )
            response = connection.getresponse()
            # Do not propagate server bodies: an error may echo private input.
            raw = response.read(1024 * 1024)
            if response.status != 200:
                raise ValueError("semantic_inference_failed")
            return json.loads(raw)
        finally:
            connection.close()

    def tokens(self, text):
        return self._api("tokenize", {"content": text, "add_special": True})["tokens"]

    def encode(self, text):
        tokens = self.tokens(text)
        if len(tokens) > MAX_TOKENS:
            raise ValueError("semantic_input_too_long")
        result = self._api("embedding", {"input": tokens, "embd_normalize": 2})
        return _normalize(result[0]["embedding"][0])

    def document_chunks(self, text, title):
        prefix = POLICY["documentFormat"].format(title=title[:120] or "none", text="")
        for start, end in _character_chunks(text):
            yield from self._fit_chunk(text, start, end, prefix)

    def _fit_chunk(self, text, start, end, prefix):
        # Split until every character has been encoded; never silently truncate.
        value = prefix + text[start:end]
        if len(self.tokens(value)) <= MAX_TOKENS:
            yield start, end, value
        elif end - start > 1:
            mid = start + (end - start) // 2
            yield from self._fit_chunk(text, start, mid, prefix)
            yield from self._fit_chunk(text, mid, end, prefix)
        else:
            raise ValueError("semantic_title_exceeds_token_budget")


def _normalize(vector):
    if len(vector) != DIMENSIONS or not all(math.isfinite(x) for x in vector):
        raise ValueError("semantic_invalid_vector")
    norm = math.sqrt(sum(x * x for x in vector))
    if norm <= 1e-12:
        raise ValueError("semantic_invalid_vector")
    return [x / norm for x in vector]


def _pack(vector):
    return struct.pack("<" + "e" * DIMENSIONS, *_normalize(vector))


def _unpack(raw):
    if len(raw) != DIMENSIONS * 2:
        raise ValueError("semantic_corrupt_vector")
    return _normalize(struct.unpack("<" + "e" * DIMENSIONS, raw))


def _character_chunks(text):
    start = 0
    while start < len(text):
        end = min(len(text), start + CHUNK_CHARS)
        if end < len(text):
            boundary = max(text.rfind(c, start + CHUNK_CHARS // 2, end) for c in "\n。.!?；;")
            if boundary >= 0:
                end = boundary + 1
        yield start, end
        if end == len(text):
            break
        start = max(start + 1, end - CHUNK_OVERLAP)


def _open_source(root):
    path = Path(root) / "library.sqlite"
    if not path.is_file():
        raise ValueError("semantic_library_missing: synchronize text first")
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    db.execute("BEGIN")
    return db


def _source_state(db):
    metadata = {k: json.loads(v) for k, v in db.execute("SELECT key,value FROM metadata")}
    if not metadata.get("snapshot"):
        raise ValueError("semantic_library_not_synced: synchronize text first")
    digest = hashlib.sha256()
    count = 0
    for row in db.execute("SELECT id,kind,date,body FROM records ORDER BY id"):
        digest.update(_json(row).encode())
        count += 1
    return {
        "snapshot": metadata.get("snapshot"), "sourceDeviceID": metadata.get("sourceDeviceID"),
        "syncedAt": metadata.get("syncedAt"), "recordCount": count,
        "recordsSHA256": digest.hexdigest(),
    }


def _read_source_state(root):
    with contextlib.closing(_open_source(root)) as source:
        return _source_state(source)


def _same_source(left, right):
    return all(left.get(key) == right.get(key)
               for key in ("snapshot", "sourceDeviceID", "recordCount", "recordsSHA256"))


def _source_records(root):
    # Extract only the fields needed for retrieval, then release the source read
    # transaction before expensive encoding. Large record payloads stay on disk.
    with contextlib.closing(_open_source(root)) as source:
        state = _source_state(source)
        records = []
        for record_id, kind, date, raw_body in source.execute(
                "SELECT id,kind,date,body FROM records ORDER BY id"):
            body = json.loads(raw_body)
            text = str(body.get("text") or "")
            title = str(body.get("title") or "")
            records.append((record_id, kind, date, body.get("state", body.get("status")), title,
                            text if text.strip() else title))
        return state, records


def _manifest(db):
    return json.loads(db.execute("SELECT value FROM metadata WHERE key='manifest'").fetchone()[0])


def _open_index(root):
    path = Path(root) / "semantic.sqlite"
    if not path.is_file():
        raise ValueError("semantic_index_missing: run index after synchronization")
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        if _manifest(db).get("schema") != SCHEMA:
            raise ValueError("semantic_index_version_mismatch: rebuild the index")
        return db
    except BaseException:
        db.close()
        raise


@contextlib.contextmanager
def _build_lock(root):
    path = Path(root) / ".semantic-index.lock"
    with path.open("a+b") as lock:
        os.chmod(path, 0o600)
        try:
            lock_file(lock, blocking=False)
        except BlockingIOError:
            raise ValueError("semantic_index_busy") from None
        try:
            yield
        finally:
            unlock_file(lock)


def build_index(root, *, encoder=None):
    """Atomically publish a consistent generation; reuse unchanged record vectors."""
    root = Path(root).expanduser().resolve()
    config = _configuration(root) if encoder is None else None
    with _build_lock(root):
        return _build_index(root, encoder or LocalEncoder(config))


def _build_index(root, encoder):
    started = time.monotonic()
    staging = None
    with contextlib.ExitStack() as stack:
        source_state, source_records = _source_records(root)
        databases = stack.enter_context(contextlib.ExitStack())
        old = None
        try:
            old = databases.enter_context(contextlib.closing(_open_index(root)))
            old_meta = _manifest(old)
            if old_meta["encoderIdentity"] != encoder.identity:
                old = None
            elif _same_source(old_meta["source"], source_state):
                return {**old_meta, "source": source_state, "changed": False,
                        "indexPath": str(root / "semantic.sqlite")}
        except (ValueError, sqlite3.Error, KeyError, TypeError):
            old = None
        fd, staging = tempfile.mkstemp(prefix=".semantic-build-", suffix=".sqlite", dir=root)
        os.close(fd)
        try:
            target = databases.enter_context(contextlib.closing(sqlite3.connect(staging)))
            target.executescript("""
                PRAGMA journal_mode=DELETE;
                PRAGMA synchronous=FULL;
                CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
                CREATE TABLE records(id TEXT PRIMARY KEY,revision TEXT,kind TEXT,date TEXT,state TEXT,title TEXT);
                CREATE TABLE chunks(record_id TEXT,ordinal INTEGER,start INTEGER,end INTEGER,text TEXT,vector BLOB,
                                    PRIMARY KEY(record_id,ordinal));
                CREATE INDEX records_filters ON records(kind,date);
            """)
            count = reused = encoded = chunk_count = 0
            active_encoder = None
            report("Indexing text", 0, source_state["recordCount"], "records", force=True)
            for record_id, kind, date, state, title, text in source_records:
                revision = _digest({"text": text, "title": title, "policy": POLICY})
                target.execute("INSERT INTO records VALUES(?,?,?,?,?,?)",
                               (record_id, revision, kind, date, _json(state), title))
                previous = old.execute("SELECT revision FROM records WHERE id=?", (record_id,)).fetchone() if old else None
                if previous and previous[0] == revision:
                    rows = list(old.execute("SELECT record_id,ordinal,start,end,text,vector FROM chunks WHERE record_id=? ORDER BY ordinal", (record_id,)))
                    target.executemany("INSERT INTO chunks VALUES(?,?,?,?,?,?)", rows)
                    chunk_count += len(rows)
                    reused += 1
                elif text.strip():
                    if active_encoder is None:
                        active_encoder = stack.enter_context(encoder)
                    for ordinal, (start, end, content) in enumerate(active_encoder.document_chunks(text, title)):
                        vector = _pack(active_encoder.encode(content))
                        target.execute("INSERT INTO chunks VALUES(?,?,?,?,?,?)",
                                       (record_id, ordinal, start, end, text[start:end], vector))
                        chunk_count += 1
                        encoded += 1
                count += 1
                report("Indexing text", count, source_state["recordCount"], "records")
            manifest = {
                "schema": SCHEMA, "encoderIdentity": encoder.identity, "policy": POLICY,
                "source": source_state, "recordCount": count, "chunkCount": chunk_count,
                "reusedRecords": reused, "encodedChunks": encoded,
                "builtAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "buildSeconds": round(time.monotonic() - started, 3),
            }
            target.execute("INSERT INTO metadata VALUES('manifest',?)", (_json(manifest),))
            target.commit()
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("semantic_index_integrity_failed")
            # A sync can finish while encoding. Never label that old snapshot current.
            if not _same_source(_read_source_state(root), source_state):
                raise ValueError("semantic_source_changed_during_build: rerun index")
            # Windows cannot replace a file while SQLite still has it open.
            # This closes both the old index and staged database before publish.
            databases.close()
            os.replace(staging, root / "semantic.sqlite")
            staging = None
            report("Semantic index ready", count, count, "records", force=True)
            return {**manifest, "changed": True, "indexPath": str(root / "semantic.sqlite"),
                    "indexBytes": (root / "semantic.sqlite").stat().st_size}
        finally:
            databases.close()
            if staging:
                for suffix in ("", "-journal"):
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(staging + suffix)


def status(root):
    root = Path(root).expanduser().resolve()
    if not root.exists():
        return {"configured": False, "indexPath": str(root / "semantic.sqlite"), "indexed": False}
    with _build_lock(root):
        return _status(root)


def _status(root):
    result = {"configured": (root / "semantic-config.json").is_file(),
              "indexPath": str(root / "semantic.sqlite"), "indexed": False}
    if not (root / "semantic.sqlite").exists():
        return result
    with contextlib.closing(_open_index(root)) as index:
        manifest = _manifest(index)
    config = _configuration(root)
    identity = LocalEncoder(config).identity
    source = _read_source_state(root)
    return {**result, **manifest, "indexed": True,
            "stale": not _same_source(manifest["source"], source) or manifest["encoderIdentity"] != identity,
            "syncedAt": source["syncedAt"],
            "indexBytes": (root / "semantic.sqlite").stat().st_size}


def search(root, query, kind=None, date=None, limit=20, *, encoder=None):
    root = Path(root).expanduser().resolve()
    with _build_lock(root):
        return _search(root, query, kind, date, limit, encoder=encoder)


def _search(root, query, kind, date, limit, *, encoder):
    if not isinstance(query, str) or not query.strip():
        raise ValueError("semantic_query_required")
    if not 1 <= limit <= 100:
        raise ValueError("limit_must_be_1_to_100")
    if date is not None and not re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", date):
        raise ValueError("invalid_date_filter")
    encoder = encoder or LocalEncoder(_configuration(root))
    with contextlib.closing(_open_index(root)) as index:
        manifest = _manifest(index)
        if manifest["encoderIdentity"] != encoder.identity:
            raise ValueError("semantic_model_index_mismatch: rebuild the index")
        current_source = _read_source_state(root)
        if not _same_source(manifest["source"], current_source):
            raise ValueError("semantic_index_stale: run index after synchronization")
        with encoder as active:
            report("Understanding your search", force=True)
            vector = _normalize(active.encode(POLICY["queryPrefix"] + query.strip()))
        clauses, values = [], []
        if kind:
            clauses.append("r.kind=?")
            values.append(kind)
        if date:
            clauses.append("r.date LIKE ?")
            values.append(date + "%")
        sql = """SELECT r.id,r.kind,r.date,r.state,r.title,c.ordinal,c.start,c.end,c.text,c.vector
                 FROM chunks c JOIN records r ON r.id=c.record_id"""
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        best = {}
        report("Searching by meaning", force=True)
        for row in index.execute(sql, values):
            score = sum(map(operator.mul, vector, _unpack(row[9])))
            if row[0] not in best or score > best[row[0]][0]:
                best[row[0]] = (score, row[:9])
        hits = heapq.nsmallest(limit, best.values(), key=lambda item: (-item[0], item[1][0]))
        records = [{
            "id": row[0], "recordID": row[0], "kind": row[1], "start": row[2],
            "state": json.loads(row[3]), "title": row[4], "text": row[8],
            "score": round(score, 6), "scoreType": "cosine_similarity",
            "chunk": {"ordinal": row[5], "startCharacter": row[6], "endCharacter": row[7]},
        } for score, row in hits]
        report("Semantic search complete", len(records), len(records), "results", force=True)
        return {"mode": "semantic", "freshness": "last_synced_snapshot",
                "syncedAt": current_source["syncedAt"], "snapshot": manifest["source"]["snapshot"],
                "model": POLICY["model"], "records": records, "limit": limit,
                "candidateRecordCount": len(best), "scoreMeaning": "similarity, not factual confidence"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Profile directory containing library.sqlite")
    sub = parser.add_subparsers(dest="command", required=True)
    setup = sub.add_parser("configure")
    setup.add_argument("--model", required=True, type=Path)
    setup.add_argument("--server", required=True, type=Path)
    sub.add_parser("index")
    sub.add_parser("status")
    query = sub.add_parser("search")
    query.add_argument("query")
    query.add_argument("--kind")
    query.add_argument("--date")
    query.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    try:
        if args.command == "configure":
            result = configure(args.root, args.model, args.server)
        elif args.command == "index":
            result = build_index(args.root)
        elif args.command == "status":
            result = status(args.root)
        else:
            result = search(args.root, args.query, args.kind, args.date, args.limit)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, sqlite3.Error, subprocess.SubprocessError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
