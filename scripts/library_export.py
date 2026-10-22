"""Export a read-only SQLite snapshot as private, human-readable Markdown."""

from collections import Counter
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile

from platform_utils import is_windows, secure_directory


LABELS = {
    "en": {
        "title": "Auday text library", "synced": "Last synced", "exported": "Exported",
        "coverage": "Readable record text, timestamps, states and source IDs. Complete structured payloads remain in the SQLite database. Audio files are not included. This is a saved snapshot, not a live folder.",
        "date": "Date", "count": "Records", "undated": "Undated", "record": "Record",
        "id": "Source ID", "start": "Start", "end": "End", "zone": "Time zone",
        "state": "State", "name": "Title", "sources": "Related source IDs",
        "search": "Open this folder in a text editor for folder-wide search, or open a daily file and use Find. Files use the preserved record date; a memory's date may be its creation date, not the date of the event it describes.",
        "transcript": "Transcript", "highlight": "Highlight", "summary": "Summary",
        "reflection": "Reflection", "memory": "Memory",
    },
    "zh-CN": {
        "title": "Auday 文字资料库", "synced": "上次同步", "exported": "导出时间",
        "coverage": "这里保存可读正文、时间、状态和来源 ID；完整结构化内容仍保存在 SQLite 数据库中。本目录不包含音频文件，是一次导出的静态快照，不会自动更新。",
        "date": "日期", "count": "记录数", "undated": "日期不明", "record": "记录",
        "id": "来源 ID", "start": "开始时间", "end": "结束时间", "zone": "时区",
        "state": "状态", "name": "标题", "sources": "关联来源 ID",
        "search": "可以用文本编辑器打开整个目录进行全文查找，也可以打开某一天的文件后查找。本目录按记录保存的日期分组；记忆的日期可能是创建时间，不一定是所描述事件的发生时间。",
        "transcript": "转写", "highlight": "重点", "summary": "摘要",
        "reflection": "日反思", "memory": "记忆",
    },
}


def record_day(value):
    match = re.match(r"^(\d{4}-\d{2}-\d{2})(?:T|$)", str(value))
    if match:
        try:
            return date.fromisoformat(match[1]).isoformat()
        except ValueError:
            pass
    return "undated"


def literal_block(value):
    """Keep source text inert, even if it contains Markdown or HTML."""
    text = str(value)
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}text\n{text}\n{fence}\n\n"


def export_library(database, destination, language="en", progress=None):
    if language not in LABELS:
        raise ValueError("unsupported_export_language")
    labels = LABELS[language]
    database = Path(database).expanduser().resolve()
    destination = Path(destination).expanduser().absolute()
    if not database.is_file():
        raise ValueError("no_synced_library")
    if destination.exists() or destination.is_symlink():
        raise ValueError("export_destination_exists: choose a new directory")
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    staging = None
    try:
        connection.execute("BEGIN")
        metadata = {key: json.loads(value) for key, value in
                    connection.execute("SELECT key,value FROM metadata")}
        if not metadata.get("snapshot"):
            raise ValueError("no_synced_snapshot")
        total = connection.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = secure_directory(Path(tempfile.mkdtemp(prefix=".auday-export-", dir=destination.parent)))
        (staging / "days").mkdir(mode=0o700)
        counts = Counter()
        kinds = Counter()
        exported_at = datetime.now(timezone.utc).isoformat()
        if progress:
            progress("Exporting readable text", completed=0, total=total, unit="records", force=True)
        for number, (body,) in enumerate(
            connection.execute("SELECT body FROM records ORDER BY date,id"), start=1
        ):
            row = json.loads(body)
            day = record_day(row.get("start", ""))
            path = staging / "days" / (day + ".md")
            if day not in counts:
                path.write_text(
                    f"# {day if day != 'undated' else labels['undated']}\n\n"
                    + literal_block(f"{labels['synced']}: {metadata.get('syncedAt', '')}")
                    + labels["coverage"] + "\n\n", encoding="utf-8"
                )
                os.chmod(path, 0o600)
            counts[day] += 1
            kinds[row.get("kind", "unknown")] += 1
            fields = [
                ("id", row.get("id", "")), ("start", row.get("start", "")),
                ("end", row.get("end", "")), ("zone", row.get("timeZone", "")),
                ("state", row.get("state", row.get("status", ""))),
                ("name", row.get("title", "")),
            ]
            if row.get("sourceIDs"):
                fields.append(("sources", json.dumps(row["sourceIDs"], ensure_ascii=False)))
            with path.open("a", encoding="utf-8") as output:
                kind = labels.get(row.get("kind"), labels["record"])
                output.write(f"## {counts[day]} · {kind}\n\n")
                output.write(literal_block("\n".join(
                    f"{labels[key]}: {value}" for key, value in fields if value != ""
                )))
                output.write(literal_block(row.get("text", "")))
            if progress:
                progress("Exporting readable text", completed=number, total=total, unit="records")
        if total != metadata.get("recordCount"):
            raise ValueError("inconsistent_cached_record_count")
        manifest = {
            "schemaVersion": 1, "exportedAt": exported_at,
            "syncedAt": metadata.get("syncedAt"), "snapshot": metadata["snapshot"],
            "sourceSnapshotCurrent": metadata.get("sourceSnapshotCurrent"),
            "recordCount": total, "recordKinds": dict(kinds), "dayCount": len(counts),
            "structuredPayloadIncluded": False, "audioFilesIncluded": False,
            "files": [
                {"date": day, "path": f"days/{day}.md", "records": counts[day]}
                for day in sorted(counts)
            ],
        }
        index = [
            f"# {labels['title']}", "", labels["coverage"], "", labels["search"], "",
            literal_block(
                f"{labels['synced']}: {metadata.get('syncedAt', '')}\n"
                f"{labels['exported']}: {exported_at}\n{labels['count']}: {total}"
            ),
            f"| {labels['date']} | {labels['count']} |", "| --- | ---: |",
        ]
        index.extend(
            f"| [{day if day != 'undated' else labels['undated']}](days/{day}.md) | {counts[day]} |"
            for day in sorted(counts)
        )
        (staging / "index.md").write_text("\n".join(index) + "\n", encoding="utf-8")
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        for path in (staging / "index.md", staging / "manifest.json"):
            os.chmod(path, 0o600)
        # Claim the new destination; never replace an existing export or edits.
        if is_windows():
            # Windows rename fails atomically if the destination already exists;
            # MoveFileEx cannot replace an existing directory, even when empty.
            os.rename(staging, destination)
        else:
            destination.mkdir(mode=0o700)
            try:
                os.replace(staging, destination)
            except BaseException:
                destination.rmdir()
                raise
        if progress:
            progress("Readable export complete", completed=total, total=total, unit="records", force=True)
        return {
            "ok": True, "path": str(destination), "index": str(destination / "index.md"),
            "recordCount": total, "dayCount": len(counts),
            "syncedAt": metadata.get("syncedAt"),
        }
    finally:
        connection.close()
        if staging and staging.exists():
            shutil.rmtree(staging)
