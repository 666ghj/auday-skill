#!/usr/bin/env python3
"""Check Git index objects without reading or changing working-tree files."""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

MAX_BLOB_BYTES = 50 * 1024 * 1024
BLOCKED_DIRS = {
    ".private-work",
    ".private",
    "node_modules",
    ".venv",
    "venv",
    "DerivedData",
    ".swiftpm-cache",
    ".build",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
}
CREDENTIAL_SUFFIXES = (
    ".p12",
    ".pfx",
    ".p8",
    ".mobileprovision",
    ".provisionprofile",
    ".keystore",
    ".jks",
)
CREDENTIAL_NAMES = {
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "credentials.json",
}
TOKEN_RULES = {
    "private-key": (
        rb"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"
        rb"|-----BEGIN PGP "
        rb"PRIVATE KEY BLOCK-----"
    ),
    "openai-token": rb"(?<![\w-])sk-(?:proj-|svcacct-)[A-Za-z0-9_-]{40,}(?![\w-])",
    "provider-token": rb"(?<![\w-])sk-[A-Za-z0-9]{32,}(?![\w-])",
    "anthropic-token": rb"(?<![\w-])sk-ant-api\d{2}-[A-Za-z0-9_-]{40,}(?![\w-])",
    "github-token": rb"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})\b",
    "aws-access-key": rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
    "google-api-key": rb"\bAIza[0-9A-Za-z_-]{35}(?![\w-])",
    "slack-token": rb"\bxox[baprs]-[A-Za-z0-9-]{20,}(?![\w-])",
    "stripe-live-secret": rb"\bsk_live_[A-Za-z0-9]{16,}\b",
}
TOKEN_RULES = {name: re.compile(pattern) for name, pattern in TOKEN_RULES.items()}


def git(*args, input=None, allowed_codes=(0,)):
    if input is None:
        result = subprocess.run(
            ["git", *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
    else:
        # Avoid simultaneous pipe writes while Git emits batch responses.
        with tempfile.TemporaryFile() as source:
            source.write(input)
            source.seek(0)
            result = subprocess.run(
                ["git", *args],
                stdin=source,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
    if result.returncode not in allowed_codes:
        # Do not echo arbitrary Git output, which might include sensitive values.
        raise RuntimeError("Git read failed: " + args[0])
    return result.stdout


def path_rules(path):
    parts = path.split("/")
    name = parts[-1]
    if set(parts[:-1]) & BLOCKED_DIRS:
        yield "private-or-generated-directory"
    if name == ".env" or (
        name.startswith(".env.")
        and name != ".env.sample"
        and not name.endswith(".example")
    ):
        yield "environment-credentials-file"
    if name.lower().endswith(CREDENTIAL_SUFFIXES) or name in CREDENTIAL_NAMES:
        yield "credential-file"


def inspect(all_files=False):
    repo_root = git("rev-parse", "--show-toplevel").rstrip(b"\n")
    os.chdir(os.fsdecode(repo_root))
    selected = (
        None
        if all_files
        else set(
            git("diff", "--cached", "--name-only", "-z", "--diff-filter=ACMRT").split(
                b"\0"
            )
        )
    )
    allowed_submodules = set(
        git(
            "config",
            "--local",
            "--get-all",
            "audayGitGuard.allowedSubmodule",
            allowed_codes=(0, 1),
        )
        .decode("utf-8", "surrogateescape")
        .splitlines()
    )
    entries = []
    problems = set()
    for record in git("ls-files", "--stage", "-z").split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, oid, stage = metadata.split()
        path = os.fsdecode(raw_path)
        if stage != b"0":
            problems.add((path, "unresolved-index-entry"))
            continue
        if selected is not None and raw_path not in selected:
            continue
        for rule in path_rules(path):
            problems.add((path, rule))
        if mode == b"160000":
            if path not in allowed_submodules:
                problems.add((path, "unapproved-submodule"))
            continue
        entries.append((path, oid))

    unique_oids = list(dict.fromkeys(oid for _, oid in entries))
    if not unique_oids:
        return problems, len(entries)
    metadata = git(
        "cat-file",
        "--batch-check=%(objectname) %(objecttype) %(objectsize)",
        input=b"\n".join(unique_oids) + b"\n",
    )
    sizes = {}
    for line in metadata.splitlines():
        fields = line.split()
        if len(fields) != 3 or fields[1] != b"blob":
            raise RuntimeError("Unexpected or missing index object")
        sizes[fields[0]] = int(fields[2])

    content_rules = {}
    with subprocess.Popen(
        ["git", "cat-file", "--batch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ) as reader:
        try:
            for oid in unique_oids:
                size = sizes[oid]
                if size > MAX_BLOB_BYTES:
                    content_rules[oid] = ["blob-over-50MiB-use-git-lfs"]
                    continue
                reader.stdin.write(oid + b"\n")
                reader.stdin.flush()
                header = reader.stdout.readline().split()
                if header != [oid, b"blob", str(size).encode()]:
                    raise RuntimeError("Unexpected Git object response")
                data = reader.stdout.read(size)
                if len(data) != size or reader.stdout.read(1) != b"\n":
                    raise RuntimeError("Incomplete Git object response")
                # LFS pointer size is the pointer blob size, not its target size.
                # Remove NUL bytes for UTF-16/32 text; do not exempt binary blobs,
                # since a credential can be embedded in otherwise binary data.
                # Also scan pointer extension lines; pointers are not exempt
                # from credential checks.
                scan_data = data.replace(b"\0", b"")
                content_rules[oid] = [
                    rule
                    for rule, pattern in TOKEN_RULES.items()
                    if pattern.search(scan_data)
                ]
        finally:
            reader.stdin.close()
        if reader.wait() != 0:
            raise RuntimeError("Git object reader failed")

    for path, oid in entries:
        for rule in content_rules[oid]:
            problems.add((path, rule))
    return problems, len(entries)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--all", action="store_true", help="check every indexed file")
    mode.add_argument(
        "--staged", action="store_true", help="check staged changes (default)"
    )
    args = parser.parse_args()
    try:
        problems, count = inspect(args.all)
    except (OSError, RuntimeError, ValueError) as error:
        print("Git guard failed: " + str(error), file=sys.stderr)
        return 2
    for path, rule in sorted(problems):
        # Paths and rule names only; never print matching content or token values.
        print(json.dumps(path, ensure_ascii=True) + ": " + rule, file=sys.stderr)
    if problems:
        print(
            "Git guard rejected the index; resolve the listed rules before committing.",
            file=sys.stderr,
        )
        return 1
    print("Git guard passed: {} indexed files checked.".format(count))
    return 0


if __name__ == "__main__":
    sys.exit(main())
