"""On-demand, isolated Mac audio setup. No model download occurs on import."""

import json
import os
from pathlib import Path
import platform
import re
import signal
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone

from progress import report

SCRIPT_ROOT = Path(__file__).resolve().parent
MANIFEST = json.loads((SCRIPT_ROOT / "audio-assets.json").read_text())


def _paths(root):
    root = Path(root).expanduser().resolve()
    runtime = root / "runtime" / "audio"
    return root, runtime, root / "models" / "audio"


def _read_config(root):
    _, runtime, _ = _paths(root)
    try:
        result = json.loads((runtime / "config.json").read_text())
        if not isinstance(result, dict) or result.get("schemaVersion") != 1:
            return {}
        path_fields = (
            "python", "ffmpeg", "whisperCLI", "model", "segmentationModel",
            "embeddingModel", "modelPreset", "sourceRevision",
        )
        if any(key in result and not isinstance(result[key], str) for key in path_fields):
            return {}
        return result
    except (OSError, ValueError):
        return {}


def audio_status(root):
    """Inspect the managed installation without downloading or loading models."""
    _, runtime, _ = _paths(root)
    config = _read_config(root)
    fields = ("python", "ffmpeg", "whisperCLI", "model")
    missing = [name for name in fields if not Path(config.get(name, "")).is_file()]
    ready = bool(config.get("verifiedAt")) and not missing
    diarization = ready and bool(config.get("diarizationVerifiedAt")) and all(
        Path(config.get(name, "")).is_file()
        for name in ("segmentationModel", "embeddingModel")
    )
    return {
        "installed": ready,
        "diarizationReady": bool(diarization),
        "model": config.get("modelPreset"),
        "runtimeDirectory": str(runtime),
        "missing": missing,
        "verifiedAt": config.get("verifiedAt"),
        "modelChoices": {
            name: {"name": value["name"], "downloadBytes": value["size"]}
            for name, value in MANIFEST["models"].items()
        },
        "speakerModelDownloadBytes": sum(
            value["size"] for value in MANIFEST["diarization"].values()
        ),
    }


def _native_environment(env):
    """Build only against Apple system tools, never a user's Homebrew prefix."""
    env = dict(env)
    env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    for name in list(env):
        if name.startswith("CMAKE_") or name in (
            "CPATH", "LIBRARY_PATH", "DYLD_LIBRARY_PATH", "CFLAGS", "CXXFLAGS",
            "CPPFLAGS", "ARCHFLAGS", "LDFLAGS", "SDKROOT", "CC", "CXX",
            "DEVELOPER_DIR", "MACOSX_DEPLOYMENT_TARGET", "MAKEFLAGS", "MFLAGS",
            "MAKEOVERRIDES",
        ):
            env.pop(name, None)
    env.update({"CC": "/usr/bin/clang", "CXX": "/usr/bin/clang++"})
    clt = Path("/Library/Developer/CommandLineTools")
    if (clt / "usr/bin/clang").is_file():
        env["DEVELOPER_DIR"] = str(clt)
    return env


def _command(command, log, env, stage, timeout=1800):
    report(stage, force=True)
    with Path(log).open("a") as output:
        started = time.monotonic()
        process = subprocess.Popen(
            [str(value) for value in command], env=env, stdout=output,
            stderr=subprocess.STDOUT, start_new_session=True,
        )
        try:
            while True:
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise TimeoutError(f"audio_setup_timed_out; stage={stage}; log={log}")
                try:
                    code = process.wait(timeout=min(20, remaining))
                    break
                except subprocess.TimeoutExpired:
                    # Native builds and uv cannot expose an honest fixed total.
                    report(stage + " (still running)", force=True)
        except BaseException:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
            raise
        if code:
            raise RuntimeError(
                f"audio_setup_command_failed; stage={stage}; log={log}"
            )
    report(stage + " complete", force=True)


def _probe_python(python, program, env):
    return subprocess.check_output(
        [str(python), "-I", "-c", program], text=True, env=env,
        stderr=subprocess.PIPE, timeout=120,
    ).strip()


def _check_build_tools(env):
    try:
        clang = subprocess.check_output(
            ["/usr/bin/xcrun", "--find", "clang"], text=True, env=env,
            stderr=subprocess.PIPE, timeout=20,
        ).strip()
        subprocess.run(
            ["/usr/bin/make", "--version"], env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=20, check=True,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError(
            "apple_command_line_tools_required; Agent should start xcode-select "
            "--install and explain Apple's installer before retrying; no Homebrew needed"
        ) from error
    return clang


def _download_model(asset, directory, label):
    from setup_runtime import download_asset
    return download_asset(
        asset["url"], directory / asset["filename"],
        asset["sha256"], size=asset["size"], label=label,
    )


def setup_audio(root, model="small", diarize=True, *, uv_path=None):
    """Install one selected ASR model and optional default speaker models.

    Caller obtains the user's model choice/authorization. This never installs
    Homebrew, writes system Python, or silently selects a different model.
    """
    if platform.system() != "Darwin" or platform.machine() not in ("arm64", "x86_64"):
        raise RuntimeError("managed_audio_requires_macos_arm64_or_x86_64")
    if model not in MANIFEST["models"]:
        raise ValueError("audio_model_must_be_small_or_turbo")
    from setup_runtime import download_asset, ensure_uv, extract_tar, managed_env

    os.umask(0o077)
    root, runtime, models = _paths(root)
    runtime.mkdir(parents=True, exist_ok=True)
    models.mkdir(parents=True, exist_ok=True)
    log = runtime / "setup.log"
    env = managed_env(root)
    native_env = _native_environment(env)
    # Fail before downloading large assets if the Apple compiler is absent.
    _check_build_tools(native_env)
    uv = Path(uv_path) if uv_path else ensure_uv(root)
    venv = runtime / "venv"
    env["UV_PROJECT_ENVIRONMENT"] = str(venv)
    old = _read_config(root)
    # Opting out of a new job never deletes an already installed speaker setup.
    install_speakers = bool(diarize or old.get("diarizationVerifiedAt"))
    sync = [
        uv, "sync", "--project", SCRIPT_ROOT, "--frozen", "--no-config",
        "--python", "3.12.15", "--managed-python",
    ]
    if install_speakers:
        sync += ["--extra", "diarization"]
    _command(sync, log, env, "Installing isolated audio runtime")
    python = venv / "bin/python"
    # imageio-ffmpeg has a fixed bundled binary; ignore its environment override.
    ffmpeg = Path(_probe_python(
        python,
        "import imageio_ffmpeg,pathlib; "
        "p=pathlib.Path(imageio_ffmpeg.__file__).parent/'binaries'; "
        "b=list(p.glob('ffmpeg-macos*')); "
        "assert len(b)==1,'bundled_ffmpeg_missing'; print(b[0])",
        env,
    ))
    ffmpeg.chmod(ffmpeg.stat().st_mode | 0o100)
    cmake = Path(_probe_python(
        python, "import cmake; print(cmake.CMAKE_BIN_DIR + '/cmake')", env,
    ))
    source_asset = MANIFEST["whisperSource"]
    source = runtime / source_asset["directory"]
    build = runtime / ("build-" + source_asset["revision"])
    whisper = build / "bin" / "whisper-cli"
    if not (source / "CMakeLists.txt").is_file():
        archive = download_asset(
            source_asset["url"], runtime / "whisper-source.tar.gz",
            source_asset["sha256"], size=source_asset["size"],
            label="Downloading pinned Whisper.cpp source",
        )
        # Use a fresh destination; source trees are never overlaid.
        with tempfile.TemporaryDirectory(prefix="extract-", dir=runtime) as tmp:
            unpacked = Path(tmp) / "contents"
            extract_tar(archive, unpacked)
            shutil.move(str(unpacked / source_asset["directory"]), str(source))
    if not whisper.is_file():
        _command(
            [cmake, "-S", source, "-B", build, "-G", "Unix Makefiles",
             "-DCMAKE_MAKE_PROGRAM=/usr/bin/make",
             "-DCMAKE_BUILD_TYPE=Release", "-DBUILD_SHARED_LIBS=OFF",
             "-DWHISPER_BUILD_TESTS=OFF", "-DWHISPER_BUILD_EXAMPLES=ON",
             "-DGGML_METAL=ON", "-DGGML_METAL_EMBED_LIBRARY=ON",
             "-DGGML_OPENMP=OFF",
             "-DCMAKE_IGNORE_PREFIX_PATH=/opt/homebrew;/usr/local"],
            log, native_env, "Configuring Whisper.cpp with Apple tools",
        )
        _command(
            [cmake, "--build", build, "--config", "Release",
             "--target", "whisper-cli", "-j", str(min(4, os.cpu_count() or 2))],
            log, native_env, "Building local transcription engine",
        )
    model_path = _download_model(
        MANIFEST["models"][model], models, "Downloading selected transcription model",
    )
    config = {
        "schemaVersion": 1, "modelPreset": model,
        "python": str(python), "ffmpeg": str(ffmpeg), "whisperCLI": str(whisper),
        "model": str(model_path), "sourceRevision": source_asset["revision"],
    }
    if diarize:
        for name, asset in MANIFEST["diarization"].items():
            path = _download_model(asset, models, f"Downloading speaker {name} model")
            config[name + "Model"] = str(path)
    elif old.get("diarizationVerifiedAt"):
        for key in ("segmentationModel", "embeddingModel", "diarizationVerifiedAt"):
            config[key] = old[key]

    report("Verifying transcription with public sample audio", force=True)
    # This upstream JFK sample is part of the pinned source archive.
    # No user recordings are read during setup.
    checks = runtime / "verification"
    checks.mkdir(exist_ok=True)
    output = checks / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    verification = _run_transcription(
        config, source / "samples/jfk.wav", output, language="en",
        diarize=diarize, threads=2, env=env,
    )
    if not verification.get("segments"):
        raise RuntimeError("audio_verification_produced_no_transcript")
    sample_result = json.loads((output / "result.json").read_text())
    words = set(re.findall(
        r"[a-z]+",
        " ".join(row["text"] for row in sample_result["segments"]).lower(),
    ))
    if not {"ask", "country"}.issubset(words):
        raise RuntimeError("audio_verification_expected_sample_words_missing")
    if diarize and not verification.get("speakerCount"):
        raise RuntimeError("audio_verification_produced_no_speaker_turns")
    now = datetime.now(timezone.utc).isoformat()
    config["verifiedAt"] = now
    if diarize:
        config["diarizationVerifiedAt"] = now
    config["verification"] = {
        "sample": "whisper.cpp/samples/jfk.wav",
        "segments": verification["segments"],
        "speakerCount": verification.get("speakerCount"),
        "expectedSampleWordsPresent": ["ask", "country"],
        "output": str(output),
        "scope": "local public-sample smoke test; not transcription-quality benchmark",
    }
    temporary = runtime / "config.json.tmp"
    temporary.write_text(json.dumps(config, indent=2) + "\n")
    temporary.replace(runtime / "config.json")
    report("Local audio setup verified", force=True)
    return {"ok": True, **audio_status(root), "verification": config["verification"]}


def _run_transcription(config, audio, output, *, language="auto", diarize=True,
                       threads=4, speakers=None, asset_metadata=None, env=None):
    command = [
        config["python"], str(SCRIPT_ROOT / "refine_audio.py"),
        "--audio", str(Path(audio).expanduser().resolve()),
        "--model", config["model"],
        "--output", str(Path(output).expanduser().resolve()),
        "--language", language, "--threads", str(threads),
        "--ffmpeg", config["ffmpeg"], "--whisper-cli", config["whisperCLI"],
    ]
    if diarize:
        command += [
            "--segmentation-model", config["segmentationModel"],
            "--embedding-model", config["embeddingModel"],
        ]
    else:
        command += ["--no-diarize"]
    if speakers is not None:
        command += ["--speakers", str(speakers)]
    if asset_metadata is not None:
        command += ["--asset-metadata", str(Path(asset_metadata).expanduser().resolve())]
    # Progress remains visible on stderr; only structured output is captured.
    result = subprocess.run(
        command, env=env, stdout=subprocess.PIPE, text=True, timeout=21600,
    )
    try:
        payload = json.loads(result.stdout)
    except ValueError as error:
        raise RuntimeError("audio_runner_returned_invalid_json") from error
    if result.returncode or not payload.get("ok"):
        raise RuntimeError(payload.get("error", "local_transcription_failed"))
    return payload


def transcribe(root, audio, output, language="auto", diarize=True, threads=4,
               speakers=None, asset_metadata=None):
    """Use an already prepared runtime; this function never downloads models."""
    state = audio_status(root)
    if not state["installed"]:
        raise RuntimeError("audio_setup_required; run setup audio with selected model")
    if diarize and not state["diarizationReady"]:
        raise RuntimeError(
            "speaker_setup_required; run setup audio or use --no-diarize "
            "only when the user declines speaker labels"
        )
    from setup_runtime import managed_env
    return _run_transcription(
        _read_config(root), audio, output, language=language, diarize=diarize,
        threads=threads, speakers=speakers, asset_metadata=asset_metadata,
        env=managed_env(Path(root).expanduser().resolve()),
    )
