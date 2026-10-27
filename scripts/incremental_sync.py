"""Snapshot-bound record manifests and verified, batched body transfers."""

import base64
import hashlib
import json
import re

from progress import report


def body_json(record):
    return json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False)


def body_hash(body):
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def descriptor(row):
    if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
        raise ValueError("invalid_record_manifest")
    transfer = row.get("recordTransfer")
    if (
        not isinstance(transfer, dict)
        or transfer.get("encoding") != "json-utf8"
        or type(transfer.get("size")) is not int
        or not 0 < transfer["size"] <= 128 * 1024 * 1024
        or not isinstance(transfer.get("sha256"), str)
        or not re.fullmatch("[0-9a-f]{64}", transfer["sha256"])
    ):
        raise ValueError("invalid_record_manifest")
    return transfer


def check_reply(reply, status):
    if reply.get("snapshot") != status["snapshot"]:
        raise ValueError("snapshot_changed")
    if reply.get("sourceDeviceID") != status["sourceDeviceID"]:
        raise ValueError("source_device_changed: use a new profile")
    if reply.get("schemaVersion") != 1:
        raise ValueError("unsupported_protocol_or_access_scope")


def read_manifest(app, status):
    result, seen, offset = [], set(), 0
    total = status["recordCount"]
    report("Checking record manifest", 0, total, "records", force=True)
    while True:
        reply = app.request({"op": "recordManifest", "snapshot": status["snapshot"], "offset": offset})
        check_reply(reply, status)
        rows = reply.get("records")
        if not isinstance(rows, list) or len(rows) > 1000:
            raise ValueError("invalid_record_manifest_page")
        for row in rows:
            descriptor(row)
            if "data" in row or row["id"] in seen:
                raise ValueError("duplicate_or_invalid_manifest_id")
            seen.add(row["id"])
            result.append(row)
        if len(result) > total:
            raise ValueError("incomplete_snapshot")
        report("Checking record manifest", len(result), total, "records")
        next_offset = reply.get("nextOffset")
        if next_offset is None:
            break
        if type(next_offset) is not int or next_offset != offset + len(rows) or next_offset <= offset:
            raise ValueError("invalid_cursor")
        offset = next_offset
    if len(result) != total:
        raise ValueError("incomplete_snapshot")
    return result


def reject_constant(_):
    raise ValueError("invalid_record_json")


def record_batches(pending):
    # The phone budgets about 1 MiB per batch reply. Leave envelope/escaping
    # headroom and account for Base64, so ordinary records do not spill into
    # dozens of separate chunk connections merely because there are 100 IDs.
    batch, size = [], 0
    for item in pending:
        estimate = len(json.dumps(item, ensure_ascii=True).encode("utf-8")) + 16
        estimate += 4 * ((item["recordTransfer"]["size"] + 2) // 3)
        if batch and (len(batch) == 100 or size + estimate > 900 * 1024):
            yield batch
            batch, size = [], 0
        batch.append(item)
        size += estimate
    if batch:
        yield batch


def fetch_records(app, status, mode, staged_records):
    """Return a complete verified projection without mutating the saved snapshot.

    Wire hashes cover the phone's exact JSON bytes. Local hashes cover the saved
    Python JSON separately: the two serializers need not emit identical bytes.
    """
    manifest = read_manifest(app, status)
    saved = {identity: body for identity, body in app.db.execute("SELECT id,body FROM records")}
    fingerprints = {row[0]: row[1:] for row in app.db.execute(
        "SELECT id,wire_sha256,wire_size,body_sha256 FROM record_fingerprints"
    )}
    rows, pending, verified = {}, [], {}
    previous = app.metadata()
    reuse_allowed = (
        mode == "incremental"
        and previous.get("sourceDeviceID") == status["sourceDeviceID"]
        and previous.get("access") == status["access"]
    )
    for item in manifest:
        identity, transfer = item["id"], item["recordTransfer"]
        fingerprint = fingerprints.get(identity)
        body = saved.get(identity)
        if (
            reuse_allowed and isinstance(body, str) and fingerprint is not None
            and fingerprint == (transfer["sha256"], transfer["size"], body_hash(body))
        ):
            try:
                record = json.loads(body, parse_constant=reject_constant)
            except (TypeError, ValueError):
                record = None
            if isinstance(record, dict) and record.get("id") == identity and "recordTransfer" not in record:
                rows[identity] = record
                verified[identity] = fingerprint
                continue
        pending.append(item)
    reused = len(rows)
    report("Receiving changed text records", 0, len(pending), "records", force=True)
    completed = 0
    for batch in record_batches(pending):
        expected = {item["id"]: item["recordTransfer"] for item in batch}
        reply = app.request({
            "op": "readRecords", "snapshot": status["snapshot"], "ids": list(expected),
        })
        check_reply(reply, status)
        received = reply.get("records")
        if not isinstance(received, list) or len(received) != len(batch):
            raise ValueError("incomplete_record_batch")
        seen = set()
        for item in received:
            transfer = descriptor(item)
            identity = item["id"]
            if identity in seen or identity not in expected or transfer != expected[identity]:
                raise ValueError("record_batch_metadata_mismatch")
            seen.add(identity)
            if "data" in item:
                try:
                    raw = base64.b64decode(item["data"], validate=True)
                except (TypeError, ValueError):
                    raise ValueError("invalid_record_data") from None
                if len(raw) != transfer["size"] or hashlib.sha256(raw).hexdigest() != transfer["sha256"]:
                    raise ValueError("record_hash_mismatch")
                try:
                    record = json.loads(raw.decode("utf-8"), parse_constant=reject_constant)
                except (UnicodeError, ValueError):
                    raise ValueError("invalid_record_json") from None
            else:
                record = app.complete_record(item, status["snapshot"], status["capabilities"], staged_records)
            if not isinstance(record, dict) or record.get("id") != identity or "recordTransfer" in record:
                raise ValueError("record_identity_mismatch")
            rows[identity] = record
            verified[identity] = (transfer["sha256"], transfer["size"], body_hash(body_json(record)))
        completed += len(batch)
        report("Receiving changed text records", completed, len(pending), "records")
    return [rows[item["id"]] for item in manifest], verified, len(pending), reused
