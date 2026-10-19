#!/usr/bin/env python3
"""Check distributable source and tracked history without printing sensitive values."""

import argparse
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/git"))
from check_staged import TOKEN_RULES, path_rules

PRIVATE_PATH = re.compile(rb'/(?:Users|Volumes|home)/[A-Za-z0-9][^\s\x00"<>]{1,}')
MEDIA = {
    ".wav",
    ".m4a",
    ".mp3",
    ".caf",
    ".onnx",
    ".safetensors",
    ".gguf",
    ".sqlite",
    ".db",
    ".p12",
}


def inspect_blob(path, data):
    problems = list(path_rules(path))
    if Path(path).suffix.lower() in MEDIA:
        problems.append("private-media-model-or-database")
    if len(data) > 2 * 1024 * 1024:
        problems.append("unexpected-large-source-blob")
    if PRIVATE_PATH.search(data):
        problems.append("machine-specific-absolute-path")
    for name, pattern in TOKEN_RULES.items():
        if pattern.search(data):
            problems.append(name)
    return problems


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def check(history=False):
    problems = []
    files = set(
        git("ls-files", "--cached", "--others", "--exclude-standard", "-z")
        .decode()
        .split("\0")
    ) - {""}
    for name in sorted(files):
        path = ROOT / name
        if not path.exists():
            continue
        if path.is_symlink():
            problems.append((name, "source-symlink"))
            continue
        problems.extend((name, item) for item in inspect_blob(name, path.read_bytes()))
    history_blobs = 0
    if history:
        seen = set()
        for commit in git("rev-list", "--all").decode().splitlines():
            for entry in git("ls-tree", "-r", "-z", commit).split(b"\0"):
                if not entry:
                    continue
                metadata, raw_name = entry.split(b"\t", 1)
                mode, kind, oid = metadata.split()
                name = raw_name.decode()
                if kind != b"blob":
                    problems.append((name, "non-blob-history-entry"))
                    continue
                if mode == b"120000":
                    problems.append((name, "history-symlink"))
                key = (name, oid)
                if key in seen:
                    continue
                seen.add(key)
                history_blobs += 1
                data = git("cat-file", "blob", oid.decode())
                problems.extend((name, item) for item in inspect_blob(name, data))
    # Entrypoint references must be local and portable.
    for path in [ROOT / "SKILL.md", *(ROOT / "references").glob("*.md")]:
        if not path.exists():
            problems.append((str(path.relative_to(ROOT)), "missing-skill-document"))
            continue
        for target in re.findall(r"\]\(([^)]+)\)", path.read_text()):
            if (
                "://" not in target
                and not target.startswith("#")
                and not (path.parent / target.split("#")[0]).exists()
            ):
                problems.append(
                    (str(path.relative_to(ROOT)), "broken-relative-reference")
                )
    for name, reason in sorted(set(problems)):
        print(f"{name}: {reason}", file=sys.stderr)
    if problems:
        return 1
    print(
        f"Public-source checks passed: {len(files)} working files, {history_blobs} historical blobs. No matching values were printed."
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true")
    sys.exit(check(parser.parse_args().history))
