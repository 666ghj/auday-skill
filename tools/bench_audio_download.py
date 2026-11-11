#!/usr/bin/env python3
"""Time audio downloads through the request path users run, on loopback.

Synthetic bytes are served by the test peer over real TLS-PSK and fetched by an
unmodified client: its own link program, link session and verification. Tests
that inject a transport skip exactly these costs.

The result covers the computer's side only. It excludes phone preparation,
Wi-Fi, and the saved-secret lookup, so it is not a physical-device measurement.
"""

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import auday
from test_workflow import PYTHON_PEER, client_command, fixture_peer, make_library
from native_peer import peer_binary


def synthetic_assets(source, count, size):
    library = make_library(source)
    template = library["assets"][0]
    library["assets"] = []
    for index in range(count):
        name = f"bench-{index}.wav"
        digest, written, block = hashlib.sha256(), 0, hashlib.sha256(name.encode()).digest()
        with (source / name).open("wb") as out:
            while written < size:
                block = hashlib.sha256(block).digest()
                piece = (block * 2048)[: size - written]
                out.write(piece)
                digest.update(piece)
                written += len(piece)
        library["assets"].append({
            **template, "id": "audio-demo" if index == 0 else f"audio-bench-{index}",
            "path": name, "size": size, "sha256": digest.hexdigest(),
            "provenance": "synthetic bytes; not speech",
        })
    (source / "library.json").write_text(json.dumps(library, ensure_ascii=False), encoding="utf-8")


def measure(port, password, count):
    with ExitStack() as stack:
        home = stack.enter_context(tempfile.TemporaryDirectory(prefix="auday-bench-home-"))
        if PYTHON_PEER and sys.platform != "win32":
            # Opt-in: the Windows client on a PSK-capable Python elsewhere.
            stack.enter_context(mock.patch.object(auday, "bridge_command", return_value=client_command()))
        # Locate the link as a new command would, not from this process's memory.
        stack.enter_context(mock.patch.object(auday, "_link_binary", None))
        run = stack.enter_context(mock.patch.object(subprocess, "run", wraps=subprocess.run))
        started = stack.enter_context(mock.patch.object(subprocess, "Popen", wraps=subprocess.Popen))
        app = auday.Auday(home)
        try:
            # The synthetic secret reaches the link through stdin only.
            (app.root / "connection.json").write_text(
                json.dumps({"host": "127.0.0.1", "port": port, "password": password}), encoding="utf-8",
            )
            began = time.perf_counter()
            app.sync()
            synced = time.perf_counter()
            result = app.audio("audio-demo") if count == 1 else app.audio_batch()
            finished = time.perf_counter()
        finally:
            app.close()
        report = {
            "syncSeconds": round(synced - began, 3),
            "audioSeconds": round(finished - synced, 3),
            "processesStarted": started.call_count,
            "swiftPackageQueries": sum(call.args[0][0] == "swift" for call in run.call_args_list),
        }
        if count == 1:
            report.update(requests=result["transferRequests"], timings=result["timings"])
        else:
            report.update(downloaded=result["downloaded"], complete=result["complete"])
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--megabytes", type=float, default=10.6, help="Size of each synthetic asset")
    parser.add_argument("--assets", type=int, default=1, help="More than one uses the batch download")
    parser.add_argument("--large-chunks", action="store_true",
                        help="The synthetic phone advertises largeAudioChunks and accepts 1 MiB")
    parser.add_argument("--runs", type=int, default=2)
    args = parser.parse_args()
    if args.megabytes <= 0 or args.assets < 1 or args.runs < 1:
        parser.error("sizes and counts must be positive")
    os.environ["AUDAY_PROGRESS"] = "0"
    size = int(args.megabytes * 1024 * 1024)
    password = "".join(secrets.choice("23456789ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(16))
    client_command()  # Build the link before timing anything.
    with tempfile.TemporaryDirectory(prefix="auday-bench-source-") as source:
        source = Path(source)
        synthetic_assets(source, args.assets, size)
        with fixture_peer(source, password, native_binary=None if PYTHON_PEER else peer_binary(),
                          large_audio_chunks=args.large_chunks) as port:
            for _ in range(args.runs):
                print(json.dumps({
                    "assets": args.assets, "bytesEach": size, "largeAudioChunks": args.large_chunks,
                    "client": "python" if PYTHON_PEER else "swift",
                    **measure(port, password, args.assets),
                }), flush=True)


if __name__ == "__main__":
    main()
