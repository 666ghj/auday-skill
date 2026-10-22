#!/usr/bin/env python3
"""Install the self-contained skill without overwriting an existing destination."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FILES = ("SKILL.md", "LICENSE", "VERSION", "THIRD_PARTY_NOTICES.md")
DIRECTORIES = ("agents", "references", "scripts")
EXCLUDED = {
    ".build",
    ".swiftpm",
    ".venv",
    "__pycache__",
    "node_modules",
    ".private",
    ".private-work",
}
SUFFIXES = {".py", ".swift", ".toml", ".lock", ".md", ".yaml", ".yml", ".json", ".sh", ".ps1", ".tsv", ".txt"}


def package_files(root=ROOT):
    root = Path(root)
    result = []
    for name in FILES:
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing regular package file: {name}")
        result.append(path)
    for name in DIRECTORIES:
        for path in sorted((root / name).rglob("*")):
            relative = path.relative_to(root)
            if any(part in EXCLUDED or part.startswith(".") for part in relative.parts):
                continue
            if path.is_symlink():
                raise ValueError(f"Symlinks are not distributable: {relative}")
            if path.is_file():
                if path.suffix not in SUFFIXES:
                    raise ValueError(f"Unexpected runtime file type: {relative}")
                result.append(path)
    return result


def install(destination, root=ROOT):
    root = Path(root).resolve()
    # Do not resolve away an existing destination symlink before checking it.
    destination = Path(destination).expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError(
            "Destination already exists; preserve it and choose a new location"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".auday-install-", dir=destination.parent))
    try:
        manifest = {}
        for source in package_files(root):
            relative = source.relative_to(root)
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            manifest[str(relative)] = hashlib.sha256(target.read_bytes()).hexdigest()
        (temporary / "install-receipt.json").write_text(
            json.dumps(
                {
                    "version": (root / "VERSION").read_text().strip(),
                    "files": manifest,
                },
                indent=2,
            )
            + "\n"
        )
        if sys.platform == "win32":
            # Windows rename fails if a competing install claimed this path.
            os.rename(temporary, destination)
        else:
            # An empty directory claim makes a racing second install fail safely.
            destination.mkdir()
            try:
                os.replace(temporary, destination)
            except BaseException:
                destination.rmdir()
                raise
        return {
            "installed": str(destination),
            "version": (root / "VERSION").read_text().strip(),
            "files": len(manifest),
        }
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--destination",
        type=Path,
        required=True,
        help="Full destination skill directory",
    )
    args = parser.parse_args()
    try:
        print(json.dumps(install(args.destination)))
    except (OSError, ValueError) as error:
        parser.exit(1, f"Installation stopped: {error}\n")


if __name__ == "__main__":
    main()
