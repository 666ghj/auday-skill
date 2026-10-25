"""On-demand, isolated macOS and Windows audio setup; no import-time downloads."""

import ctypes
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
from datetime import datetime, timezone

from progress import report
from platform_utils import start_process, stop_process, venv_python

SCRIPT_ROOT = Path(__file__).resolve().parent
MANIFEST = json.loads((SCRIPT_ROOT / "audio-assets.json").read_text(encoding="utf-8"))


def _paths(root):
    root = Path(root).expanduser().resolve()
    runtime = root / "runtime" / "audio"
    return root, runtime, root / "models" / "audio"


def _read_config(root):
    _, runtime, _ = _paths(root)
    try:
        result = json.loads((runtime / "config.json").read_text(encoding="utf-8"))
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
    with Path(log).open("a", encoding="utf-8") as output:
        started = time.monotonic()
        process = start_process(
            [str(value) for value in command], env=env, stdout=output,
            stderr=subprocess.STDOUT,
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
            stop_process(process)
            raise
        if code:
            raise RuntimeError(
                f"audio_setup_command_failed; stage={stage}; log={log}"
            )
    report(stage + " complete", force=True)


def _probe_python(python, program, env):
    return subprocess.check_output(
        [str(python), "-I", "-X", "utf8", "-c", program], text=True, encoding="utf-8", env=env,
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


def _check_windows_audio_cpu():
    """Check the OS-enabled AVX2 feature before downloading the pinned CPU engine."""
    # Official feature ID 40 is PF_AVX2_INSTRUCTIONS_AVAILABLE:
    # https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-isprocessorfeaturepresent
    # The upstream x64 build also requires FMA/F16C (recorded in the manifest).
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    supports = kernel.IsProcessorFeaturePresent
    supports.argtypes = [ctypes.c_uint]
    supports.restype = ctypes.c_int
    if not supports(40):
        raise RuntimeError(
            "windows_audio_requires_avx2_cpu; this local audio engine also requires "
            "FMA and F16C; connection, text synchronization and export remain available"
        )


def _check_windows_audio_python(python, architecture, env):
    """Refuse an old emulated interpreter before loading native audio wheels."""
    expected = "win-arm64" if architecture == "arm64" else "win-amd64"
    _probe_python(
        python,
        "import sysconfig; "
        f"assert sysconfig.get_platform().lower() == {expected!r}, 'audio_python_architecture_mismatch'",
        env,
    )


def _check_windows_audio_engines(ffmpeg, whisper, log, env):
    """Detect broken native archives before downloading the selected models."""
    _command([ffmpeg, "-version"], log, env,
             "Checking native audio decoder", timeout=60)
    _command([whisper, "--help"], log, env,
             "Checking native transcription engine", timeout=60)


def _download_model(asset, directory, label):
    from setup_runtime import download_asset
    return download_asset(
        asset["url"], directory / asset["filename"],
        asset["sha256"], size=asset["size"], label=label,
    )


def _ensure_source(runtime, *, windows=False):
    """The pinned archive also supplies the public verification sample and license."""
    from setup_runtime import download_asset, extract_tar
    source_asset = MANIFEST["whisperSource"]
    source = runtime / source_asset["directory"]
    required = ("LICENSE", "samples/jfk.wav") if windows else (
        "CMakeLists.txt", "LICENSE", "samples/jfk.wav"
    )
    if source.exists() or source.is_symlink():
        if source.is_symlink() or not all((source / name).is_file() for name in required):
            raise ValueError("existing_audio_source_incomplete; preserve it and choose a new setup root")
    else:
        archive = download_asset(
            source_asset["url"], runtime / "whisper-source.tar.gz",
            source_asset["sha256"], size=source_asset["size"],
            label="Downloading pinned Whisper.cpp source",
        )
        # Use a fresh destination; source trees are never overlaid.
        with tempfile.TemporaryDirectory(prefix="extract-", dir=runtime) as tmp:
            unpacked = Path(tmp) / "contents"
            if windows:
                # Only the license and sample are needed with the prebuilt CLI.
                # Unrelated source paths can exceed legacy Windows path limits.
                selected = unpacked / source_asset["directory"]
                with tarfile.open(archive, "r:gz") as tar:
                    for name in required:
                        archive_name = source_asset["directory"] + "/" + name
                        matches = [member for member in tar.getmembers()
                                   if member.name == archive_name]
                        if (len(matches) != 1 or not matches[0].isfile() or
                                not 0 < matches[0].size <= 32 * 1024**2):
                            raise ValueError("invalid_whisper_verification_asset")
                        destination = selected / name
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with tar.extractfile(matches[0]) as content, destination.open("xb") as output:
                            shutil.copyfileobj(content, output)
            else:
                extract_tar(archive, unpacked)
            shutil.move(str(unpacked / source_asset["directory"]), str(source))
    return source


def _prepare_mac_whisper(runtime, source, python, env, log):
    source_asset = MANIFEST["whisperSource"]
    native_env = _native_environment(env)
    cmake = Path(_probe_python(
        python, "import cmake; print(cmake.CMAKE_BIN_DIR + '/cmake')", env,
    ))
    build = runtime / ("build-" + source_asset["revision"])
    whisper = build / "bin" / "whisper-cli"
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
    return whisper


def _prepare_windows_whisper(runtime, source, *, architecture="x86_64"):
    """Install official CPU binaries and app-local release DLLs into a fresh tree."""
    from setup_runtime import download_asset, extract_zip, file_hash
    from windows_crt import ensure_windows_crt
    arm64 = architecture == "arm64"
    engine = MANIFEST["whisperWindowsArm64" if arm64 else "whisperWindows"]
    if arm64:
        from windows_crt import crt_asset
        redist = crt_asset("arm64")
    else:
        redist = MANIFEST["windowsRuntime"]
    openmp = MANIFEST["whisperOpenMPArm64"] if arm64 else None
    destination = runtime / engine["directory"]
    executable = destination / engine["executable"]
    files = [*engine["files"], *redist["files"], *(openmp["files"] if openmp else [])]

    def verified(directory):
        for item in files:
            path = directory / "Release" / item["filename"]
            if (not path.is_file() or path.is_symlink() or
                    path.stat().st_size != item["size"] or
                    file_hash(path) != item["sha256"]):
                return False
        if arm64:
            license_file = directory / "WHISPER-LICENSE"
            if (not license_file.is_file() or license_file.is_symlink() or
                    license_file.stat().st_size != engine["licenseSize"] or
                    file_hash(license_file) != engine["licenseSHA256"]):
                return False
        return True

    if destination.exists() or destination.is_symlink():
        if not destination.is_symlink() and verified(destination):
            return executable
        raise ValueError("existing_audio_engine_incomplete; preserve it and choose a new setup root")
    engine_archive = download_asset(
        engine["url"], runtime / (engine["sha256"] + ".zip"),
        engine["sha256"], size=engine["size"],
        label="Downloading verified Windows transcription engine",
    )
    with tempfile.TemporaryDirectory(prefix="engine-", dir=runtime) as temporary:
        temporary = Path(temporary)
        stage = temporary / "install"
        release = stage / "Release"
        release.mkdir(parents=True)
        unpacked = extract_zip(engine_archive, temporary / "engine")
        for item in engine["files"]:
            shutil.copyfile(unpacked / item["member"], release / item["filename"])
        # The same verified app-local CRT also supports semantic search without
        # installing audio; OpenMP is needed only by this Whisper CPU build.
        if arm64:
            # The upstream Whisper archive contains a Visual Studio
            # debug_nonredist OpenMP DLL. Never copy it. LLVM's redistributable
            # runtime exports the same required ABI; retain its license too.
            openmp_archive = download_asset(
                openmp["url"], runtime.parent.parent / "cache/downloads" / (openmp["sha256"] + ".zip"),
                openmp["sha256"], size=openmp["size"],
                label="Downloading redistributable ARM64 OpenMP runtime",
            )
            openmp_tree = extract_zip(openmp_archive, temporary / "openmp")
            for item in openmp["files"]:
                shutil.copyfile(openmp_tree / item["member"], release / item["filename"])
            ensure_windows_crt(runtime.parent.parent, release, architecture="arm64")
        else:
            ensure_windows_crt(runtime.parent.parent, release, openmp=True)
        license_source = source / "LICENSE"
        if arm64:
            license_source = download_asset(
                engine["licenseDownloadURL"], runtime / (engine["licenseSHA256"] + "-LICENSE"),
                engine["licenseSHA256"], size=engine["licenseSize"],
                label="Preparing matching Whisper license",
            )
        shutil.copyfile(license_source, stage / "WHISPER-LICENSE")
        if not verified(stage):
            raise ValueError("windows_audio_engine_file_verification_failed")
        (stage / "provenance.json").write_text(json.dumps({
            "whisperArchiveSHA256": engine["sha256"],
            "runtimeArchiveSHA256": redist["sha256"],
            "runtimeLicenseURL": redist["licenseURL"],
            "architecture": architecture,
            "openmpArchiveSHA256": openmp["sha256"] if openmp else None,
        }, indent=2) + "\n", encoding="utf-8")
        stage.replace(destination)
    return executable


def _prepare_windows_arm64_ffmpeg(runtime):
    """Install the pinned native LGPL FFmpeg without global tools or emulation."""
    from setup_runtime import download_asset, extract_zip, file_hash
    asset = MANIFEST["ffmpegWindowsArm64"]
    destination = runtime / asset["directory"]

    def verified(directory):
        return all(
            (directory / item["filename"]).is_file()
            and not (directory / item["filename"]).is_symlink()
            and (directory / item["filename"]).stat().st_size == item["size"]
            and file_hash(directory / item["filename"]) == item["sha256"]
            for item in asset["files"]
        )

    if destination.exists() or destination.is_symlink():
        if not destination.is_symlink() and verified(destination):
            return destination / asset["executable"]
        raise ValueError("existing_audio_decoder_incomplete; preserve it and choose a new setup root")
    archive = download_asset(
        asset["url"], runtime / (asset["sha256"] + ".zip"),
        asset["sha256"], size=asset["size"], label="Downloading native ARM64 audio decoder",
    )
    with tempfile.TemporaryDirectory(prefix="decoder-", dir=runtime) as temporary:
        temporary = Path(temporary)
        unpacked = extract_zip(archive, temporary / "archive")
        stage = temporary / "install"
        stage.mkdir()
        for item in asset["files"]:
            shutil.copyfile(unpacked / item["member"], stage / item["filename"])
        if not verified(stage):
            raise ValueError("windows_audio_decoder_file_verification_failed")
        (stage / "provenance.json").write_text(json.dumps({
            "archiveSHA256": asset["sha256"], "sourceURL": asset["url"],
            "licenseURL": asset["licenseURL"], "architecture": "arm64",
        }, indent=2) + "\n", encoding="utf-8")
        stage.replace(destination)
    return destination / asset["executable"]


def setup_audio(root, model="small", diarize=True, *, uv_path=None):
    """Install one selected ASR model and optional default speaker models.

    Caller obtains the user's model choice/authorization. This never installs
    Homebrew, writes system Python, or silently selects a different model.
    """
    windows = platform.system() == "Windows"
    from setup_runtime import ensure_uv, managed_env, managed_python_version, host_architecture
    architecture = host_architecture() if windows else platform.machine()
    supported = (windows and architecture in ("arm64", "x86_64")) or (
        platform.system() == "Darwin" and platform.machine() in ("arm64", "x86_64")
    )
    if not supported:
        raise RuntimeError("managed_audio_requires_macos_or_windows_x64_or_arm64")
    if model not in MANIFEST["models"]:
        raise ValueError("audio_model_must_be_small_or_turbo")
    if windows and architecture == "x86_64":
        _check_windows_audio_cpu()

    os.umask(0o077)
    root, runtime, models = _paths(root)
    runtime.mkdir(parents=True, exist_ok=True)
    models.mkdir(parents=True, exist_ok=True)
    log = runtime / "setup.log"
    env = managed_env(root)
    # Windows uses a prebuilt engine; only macOS needs Apple compiler tools.
    if not windows:
        _check_build_tools(_native_environment(env))
    uv = Path(uv_path) if uv_path else ensure_uv(root)
    # Keep the native ARM64 environment separate from any older manually
    # prepared x64 audio environment; do not replace its Python or packages.
    venv = runtime / ("venv-arm64" if windows and architecture == "arm64" else "venv")
    env["UV_PROJECT_ENVIRONMENT"] = str(venv)
    old = _read_config(root)
    # Opting out of a new job never deletes an already installed speaker setup.
    install_speakers = bool(diarize or old.get("diarizationVerifiedAt"))
    python_request = managed_python_version()
    if windows:
        from setup_runtime import managed_python_request
        python_request = managed_python_request()
    sync = [
        uv, "sync", "--project", SCRIPT_ROOT, "--frozen", "--no-config",
        "--python", python_request, "--managed-python", "--no-build",
    ]
    if install_speakers:
        sync += ["--extra", "diarization"]
    _command(sync, log, env, "Installing isolated audio runtime")
    python = venv_python(venv)
    if windows:
        _check_windows_audio_python(python, architecture, env)
    # imageio-ffmpeg has a fixed bundled binary; ignore its environment override.
    if windows and architecture == "arm64":
        ffmpeg = _prepare_windows_arm64_ffmpeg(runtime)
    else:
        ffmpeg_pattern = "ffmpeg-win*.exe" if windows else "ffmpeg-macos*"
        ffmpeg = Path(_probe_python(
            python,
            "import imageio_ffmpeg,pathlib; "
            "p=pathlib.Path(imageio_ffmpeg.__file__).parent/'binaries'; "
            f"b=list(p.glob({ffmpeg_pattern!r})); "
            "assert len(b)==1,'bundled_ffmpeg_missing'; print(b[0])",
            env,
        ))
    if not windows:
        ffmpeg.chmod(ffmpeg.stat().st_mode | 0o100)
    source_asset = MANIFEST["whisperSource"]
    source = _ensure_source(runtime, windows=windows)
    if windows:
        whisper = _prepare_windows_whisper(runtime, source, architecture=architecture)
        _check_windows_audio_engines(ffmpeg, whisper, log, env)
    else:
        whisper = _prepare_mac_whisper(runtime, source, python, env, log)
    model_path = _download_model(
        MANIFEST["models"][model], models, "Downloading selected transcription model",
    )
    config = {
        "schemaVersion": 1, "modelPreset": model,
        "python": str(python), "ffmpeg": str(ffmpeg), "whisperCLI": str(whisper),
        "model": str(model_path), "sourceRevision": source_asset["revision"],
        "platform": ("windows-" if windows else "macos-") + architecture,
        "architecture": architecture,
    }
    if windows:
        engine = MANIFEST["whisperWindowsArm64" if architecture == "arm64" else "whisperWindows"]
        config["engineArchiveSHA256"] = engine["sha256"]
        config["sourceRevision"] = engine["revision"]
    if diarize:
        for name, asset in MANIFEST["diarization"].items():
            path = _download_model(asset, models, f"Downloading speaker {name} model")
            config[name + "Model"] = str(path)
    elif old.get("diarizationVerifiedAt"):
        for key in ("segmentationModel", "embeddingModel"):
            config[key] = old[key]
        # Retain reusable weights, but a new native interpreter has not passed
        # the previous environment's speaker inference check.
        if old.get("platform") == config["platform"] and old.get("python") == config["python"]:
            config["diarizationVerifiedAt"] = old["diarizationVerifiedAt"]

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
    sample_result = json.loads((output / "result.json").read_text(encoding="utf-8"))
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
    temporary.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    temporary.replace(runtime / "config.json")
    report("Local audio setup verified", force=True)
    return {"ok": True, **audio_status(root), "verification": config["verification"]}


def _run_transcription(config, audio, output, *, language="auto", diarize=True,
                       threads=4, speakers=None, asset_metadata=None, env=None):
    command = [
        config["python"], "-X", "utf8", str(SCRIPT_ROOT / "refine_audio.py"),
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
    process = start_process(
        command, env=env, stdout=subprocess.PIPE, text=True, encoding="utf-8",
    )
    try:
        stdout, _ = process.communicate(timeout=21600)
    except BaseException:
        stop_process(process)
        raise
    try:
        payload = json.loads(stdout)
    except ValueError as error:
        raise RuntimeError("audio_runner_returned_invalid_json") from error
    if process.returncode or not payload.get("ok"):
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
