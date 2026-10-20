#!/usr/bin/env python3
"""Opt-in network acceptance on a fresh data root; never uses personal recordings."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import time

from install import install

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New directory outside the repository")
    parser.add_argument("--audio", choices=["skip", "full"], default="full")
    parser.add_argument("--audio-model", choices=["small", "turbo"], default="small")
    args = parser.parse_args()
    output = args.output.expanduser().absolute()
    if output.exists() or output.is_symlink() or output.is_relative_to(ROOT):
        raise ValueError("new_external_acceptance_directory_required")
    output.mkdir(parents=True, mode=0o700)
    package = output / "installed-skill"
    install(package)
    data = output / "fresh-data"
    poison = output / "forbidden-tools"
    poison.mkdir()
    sentinel = output / "borrowed-tool"
    for tool in ("python", "python3", "uv", "ffmpeg", "whisper-cli", "cmake"):
        path = poison / tool
        path.write_text("#!/bin/sh\nprintf '%s\\n' forbidden > " + "'" +
                        str(sentinel).replace("'", "'\\''") + "'\nexit 97\n")
        path.chmod(0o700)
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("UV_", "PIP_", "HF_")) and key not in
                   ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "CONDA_PREFIX",
                    "IMAGEIO_FFMPEG_EXE", "FFMPEG_BINARY")}
    environment.update({
        "PATH": str(poison) + ":/usr/bin:/bin:/usr/sbin:/sbin",
        "PYTHONNOUSERSITE": "1",
    })
    steps = []
    started = time.monotonic()

    def run(*arguments):
        begin = time.monotonic()
        result = subprocess.run(
            ["/bin/sh", str(package / "scripts/bootstrap.sh"), "--home", str(data), *arguments],
            env=environment, stdout=subprocess.PIPE, text=True, timeout=1800,
        )
        if result.returncode:
            print(result.stdout, file=sys.stderr)
            setup_log = data / "runtime/audio/setup.log"
            if setup_log.is_file():
                print("\n".join(setup_log.read_text(errors="replace").splitlines()[-50:]), file=sys.stderr)
            raise RuntimeError("clean_setup_command_failed: " + arguments[0])
        assert not sentinel.exists(), "A command borrowed a forbidden global tool"
        value = json.loads(result.stdout)
        steps.append({"command": list(arguments), "seconds": round(time.monotonic() - begin, 3)})
        return value

    inspection = run("check")
    assert inspection["downloadsStarted"] is False and not data.exists()
    doctor = run("doctor")
    assert doctor["capabilities"]["core"]["pythonReady"]
    assert doctor["capabilities"]["core"]["pythonManagedBySetup"]
    assert not (data / "models").exists(), "Base preparation downloaded optional models"
    before = run("setup")
    assert before["recommendSemantic"] and not before["audio"]["installed"]
    run("setup", "--defer-semantic")
    assert not run("setup")["recommendSemantic"]
    semantic = run("setup", "--semantic")
    assert semantic["semanticReady"] and semantic["verification"]["checksPassed"] == 3
    for field in ("modelPath", "runtimePath"):
        assert Path(semantic[field]).resolve().is_relative_to(data.resolve())
    assert run("setup")["semantic"]["currentFilesVerified"]

    # The harness creates only synthetic phone-like records, never pairs a phone.
    fixture = json.loads((ROOT / "tests/semantic-fixture.json").read_text())
    profile = data / "default"
    profile.mkdir(exist_ok=True)
    with sqlite3.connect(profile / "library.sqlite") as database:
        database.executescript("""
            CREATE TABLE records(id TEXT PRIMARY KEY,kind TEXT,text TEXT,date TEXT,body TEXT);
            CREATE TABLE assets(id TEXT PRIMARY KEY,body TEXT);
            CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
        """)
        for row in fixture["records"]:
            database.execute("INSERT INTO records VALUES(?,?,?,?,?)", (
                row["id"], row["kind"], row["text"], row["start"], json.dumps(row),
            ))
        snapshot = hashlib.sha256(json.dumps(fixture["records"], sort_keys=True).encode()).hexdigest()
        values = {"snapshot": snapshot, "sourceDeviceID": "synthetic-clean-setup",
                  "recordCount": len(fixture["records"]), "assetCount": 0,
                  "access": "text-only-read-only", "syncedAt": "2026-01-01T00:00:00Z"}
        database.executemany("INSERT INTO metadata VALUES(?,?)",
                             [(key, json.dumps(value)) for key, value in values.items()])
    indexed = run("index")
    assert indexed["recordCount"] == len(fixture["records"])
    for query in fixture["queries"][:3]:
        found = run("search", query["text"], "--mode", "semantic", "--limit", "1")
        assert found["records"][0]["id"] == query["target"], "Synthetic semantic query mismatch"
    assert run("index")["changed"] is False
    exported = run("export", "--output", str(output / "readable"), "--language", "en")
    assert exported["recordCount"] == len(fixture["records"])
    assert not (profile / "connection.json").exists(), "Acceptance unexpectedly paired a phone"

    bridge = package / "scripts/link"
    subprocess.run(
        ["/usr/bin/swift", "build", "--package-path", str(bridge)],
        env=environment, check=True, timeout=600,
    )
    audio_result = None
    if args.audio == "full":
        opted_out = run("setup", "--audio", "--model", args.audio_model, "--no-diarize")
        assert opted_out["installed"] and not opted_out["diarizationReady"]
        audio_config = json.loads((data / "runtime/audio/config.json").read_text())
        subprocess.run([
            audio_config["python"], "-I", "-c",
            "import importlib.util; assert importlib.util.find_spec('sherpa_onnx') is None; "
            "assert importlib.util.find_spec('soundfile') is None",
        ], env=environment, check=True, timeout=30)
        assets = json.loads((package / "scripts/audio-assets.json").read_text())
        assert all(not (data / "models/audio" / asset["filename"]).exists()
                   for asset in assets["diarization"].values())
        audio_result = run("setup", "--audio", "--model", args.audio_model)
        assert audio_result["installed"] and audio_result["diarizationReady"]
        audio_config = json.loads((data / "runtime/audio/config.json").read_text())
        for field in ("python", "ffmpeg", "whisperCLI", "model", "segmentationModel", "embeddingModel"):
            assert Path(audio_config[field]).resolve().is_relative_to(data.resolve()), field
        sample = data / "runtime/audio" / assets["whisperSource"]["directory"] / "samples/jfk.wav"
        result = run("transcribe", "--audio", str(sample), "--output", str(output / "transcript"),
                     "--language", "en", "--no-diarize")
        assert result["ok"] and result["segments"] > 0 and result["speakerCount"] is None
    assert not sentinel.exists()
    report = {
        "status": "passed", "version": (package / "VERSION").read_text().strip(),
        "macOS": platform.mac_ver()[0], "architecture": platform.machine(),
        "freshDataRoot": True, "globalToolsBorrowed": False,
        "baseDownloadsWithoutOptionalModels": True, "deferChoicePersisted": True,
        "semanticSetupChecks": 3, "semanticRetrievalChecks": 3,
        "nativeBridgeBuiltFromInstalledPackage": True,
        "audio": args.audio_model + " with opt-out then default diarization" if audio_result else "not_run",
        "personalDataRead": False, "phoneConnectionTested": False,
        "systemBaseline": "macOS system utilities and Apple developer tools; no claim of an untouched OS",
        "elapsedSeconds": round(time.monotonic() - started, 3),
        "dataBytes": sum(path.stat().st_size for path in data.rglob("*") if path.is_file()),
        "steps": [{"command": item["command"][:1], "seconds": item["seconds"]} for item in steps],
    }
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
