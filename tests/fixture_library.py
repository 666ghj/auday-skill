"""Synthetic fixture callback for the test-only loopback Python TLS peer.

This mirrors tests/native-peer's FixtureLibrary. It is never installed with the
desktop skill and has no access to phone APIs or personal data stores.
"""

import base64
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath


KINDS = ("transcript", "highlight", "summary", "reflection", "memory")
SCOPES = ("text-only-read-only", "text-and-audio-read-only")
MAXIMUM_FRAME = 2 * 1024 * 1024
MAXIMUM_LIBRARY = 32 * 1024 * 1024
PAGE_BYTES = 1024 * 1024
CHUNK_BYTES = 262144


def _json_bytes(value):
    # Foundation JSONSerialization uses compact UTF-8 and escapes forward
    # slashes. Match that representation for the native peer's byte budget.
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).replace("/", "\\/").encode("utf-8")


def _file_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reject_constant(_):
    raise ValueError("invalid_library")


def _integer(request, key, default):
    value = request.get(key)
    return value if type(value) is int else default


class FixtureLibrary:
    def __init__(self, root, access="text-and-audio-read-only"):
        if access not in SCOPES:
            raise ValueError("invalid_access")
        self.root = Path(root).resolve()
        self.access = access
        self.allows_audio = access == "text-and-audio-read-only"
        try:
            with (self.root / "library.json").open("rb") as stream:
                raw = stream.read(MAXIMUM_LIBRARY + 1)
            if len(raw) > MAXIMUM_LIBRARY:
                raise ValueError("invalid_library")
            value = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
        except (OSError, ValueError) as error:
            raise ValueError("invalid_library") from error
        if (
            not isinstance(value, dict)
            or type(value.get("schemaVersion")) is not int
            or value["schemaVersion"] != 1
            or not isinstance(value.get("sourceDeviceID"), str)
            or not value["sourceDeviceID"]
            or not isinstance(value.get("records"), list)
            or not isinstance(value.get("assets"), list)
            or not all(isinstance(row, dict) for row in value["records"] + value["assets"])
        ):
            raise ValueError("invalid_library")

        self.snapshot = hashlib.sha256(raw + access.encode("utf-8")).hexdigest()
        self.source_device_id = value["sourceDeviceID"]
        for rows in (value["records"], value["assets"]):
            identities = [row.get("id") for row in rows]
            if (
                any(not isinstance(identity, str) or not identity for identity in identities)
                or len(set(identities)) != len(identities)
            ):
                raise ValueError("duplicate_or_missing_id")
        for row in value["records"]:
            try:
                valid = row.get("kind") in KINDS and len(_json_bytes(row)) < MAXIMUM_FRAME - 4096
            except (UnicodeError, ValueError):
                valid = False
            if not valid:
                raise ValueError("invalid_record")

        self.records = value["records"]
        self.assets = value["assets"] if self.allows_audio else []
        if not self.allows_audio:
            for row in self.records:
                row.pop("audioAssetID", None)
        for row in self.assets:
            path = self.asset_url(row)
            if (
                type(row.get("size")) is not int
                or row["size"] != path.stat().st_size
                or row.get("sha256") != _file_digest(path)
            ):
                raise ValueError("asset_integrity_failed")

    def asset_url(self, row):
        path = row.get("path")
        if not isinstance(path, str) or not path or "\0" in path:
            raise ValueError("invalid_asset_path")
        posix = PurePosixPath(path)
        windows = PureWindowsPath(path)
        if (
            posix.is_absolute()
            or windows.drive
            or windows.root
            or ".." in posix.parts
            or ".." in windows.parts
            or ":" in path
        ):
            raise ValueError("invalid_asset_path")
        try:
            resolved = (self.root / path).resolve()
            relative = resolved.relative_to(self.root)
            if not relative.parts or not resolved.is_file():
                raise ValueError("asset_outside_library")
        except (OSError, RuntimeError, ValueError) as error:
            raise ValueError("asset_outside_library") from error
        return resolved

    def respond(self, request):
        """Return the same success/error envelope as the native test server."""
        try:
            return self._respond(request)
        except ValueError as error:
            return {"ok": False, "error": str(error)}
        except OSError:
            # Do not expose local filesystem paths in a protocol error.
            return {"ok": False, "error": "asset_unavailable"}

    def _respond(self, request):
        if not isinstance(request, dict):
            raise ValueError("invalid_request")
        expected = request.get("snapshot")
        if isinstance(expected, str) and expected != self.snapshot:
            raise ValueError("snapshot_changed")
        reply = {
            "schemaVersion": 1,
            "snapshot": self.snapshot,
            "sourceDeviceID": self.source_device_id,
            "ok": True,
        }
        operation = request.get("op")
        if operation == "status":
            reply.update(
                access=self.access,
                recordCount=len(self.records),
                assetCount=len(self.assets),
                categories=list(KINDS) + (["audio"] if self.allows_audio else []),
            )
        elif operation in ("list", "assets"):
            rows = self.records if operation == "list" else self.assets
            offset = _integer(request, "offset", 0)
            if not 0 <= offset <= len(rows):
                raise ValueError("invalid_offset")
            page = []
            size = 0
            for row in rows[offset:offset + 100]:
                if operation == "list":
                    row_size = len(_json_bytes(row))
                    if page and size + row_size > PAGE_BYTES:
                        break
                    size += row_size
                safe = copy.deepcopy(row)
                if operation == "assets":
                    safe.pop("path", None)
                page.append(safe)
            reply["records" if operation == "list" else "assets"] = page
            next_offset = offset + len(page)
            reply["nextOffset"] = next_offset if next_offset < len(rows) else None
        elif operation == "read":
            record = next((row for row in self.records if row["id"] == request.get("id")), None)
            if record is None:
                raise ValueError("record_not_found")
            reply["record"] = copy.deepcopy(record)
        elif operation == "audio":
            if not self.allows_audio:
                raise ValueError("audio_access_denied")
            asset = next((row for row in self.assets if row["id"] == request.get("id")), None)
            if asset is None:
                raise ValueError("asset_not_found")
            size = asset["size"]
            offset = _integer(request, "offset", 0)
            length = _integer(request, "length", CHUNK_BYTES)
            if not 0 <= offset <= size or not 0 < length <= CHUNK_BYTES:
                raise ValueError("invalid_range")
            count = min(length, size - offset)
            with self.asset_url(asset).open("rb") as stream:
                stream.seek(offset)
                data = stream.read(count)
            if len(data) != count:
                raise ValueError("asset_changed")
            reply.update(
                data=base64.b64encode(data).decode("ascii"),
                offset=offset,
                sha256=asset["sha256"],
                chunkSHA256=hashlib.sha256(data).hexdigest(),
                size=size,
            )
        else:
            raise ValueError("read_only_operation_not_supported")
        return reply
