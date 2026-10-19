"""Build the synthetic loopback peer. This directory is excluded from installs."""

from pathlib import Path
import subprocess
import sys

PACKAGE = Path(__file__).resolve().parent / "native-peer"


def peer_binary():
    result = subprocess.run(
        ["swift", "build", "--package-path", str(PACKAGE), "--show-bin-path"],
        check=True,
        capture_output=True,
        text=True,
    )
    binary = Path(result.stdout.strip()) / "auday-test-peer"
    sources = [PACKAGE / "Package.swift", *PACKAGE.glob("Sources/**/*.swift")]
    sources += list((PACKAGE.parents[1] / "scripts/link").glob("Sources/**/*.swift"))
    if (
        not binary.exists()
        or max(path.stat().st_mtime for path in sources) > binary.stat().st_mtime
    ):
        subprocess.run(
            ["swift", "build", "--package-path", str(PACKAGE)],
            check=True,
            stdout=sys.stderr,
        )
    return binary
