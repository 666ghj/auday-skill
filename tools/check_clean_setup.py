#!/usr/bin/env python3
"""Opt-in network acceptance using a fresh installed skill and public/synthetic data.

The harness may use the runner's Python. Every product subprocess instead starts
through the installed bootstrap with an empty user home and a sanitized PATH.
This proves installation on the stated hosted OS baseline, not a pristine OS.
"""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time

from install import install

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_TOOLS = ("python", "python3", "uv", "ffmpeg", "whisper-cli", "cmake")
WINDOWS_MACHINES = {0x8664: "x86_64", 0xAA64: "arm64", 0xA64E: "arm64x"}


def file_hash(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def clean_environment(output, source=None, windows=None):
    """Keep OS facilities; exclude inherited Python, package and model caches."""
    source = dict(os.environ if source is None else source)
    windows = sys.platform == "win32" if windows is None else windows
    environment = {
        key: value for key, value in source.items()
        if not key.upper().startswith(("UV_", "PIP_", "HF_", "PYTHON", "CONDA_"))
        and key.upper() not in (
            "VIRTUAL_ENV", "IMAGEIO_FFMPEG_EXE", "FFMPEG_BINARY", "TORCH_HOME",
            "CMAKE_PREFIX_PATH", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "PATH",
        )
    }
    home = output / "empty-home 用户"
    temporary = output / "temporary"
    poison = output / "forbidden-tools"
    for directory in (home, temporary, poison, home / "local", home / "roaming"):
        directory.mkdir(parents=True, exist_ok=True)
    sentinel = output / "borrowed-tool"
    for tool in FORBIDDEN_TOOLS:
        if windows:
            (poison / (tool + ".cmd")).write_text(
                '@echo off\necho forbidden > "%AUDAY_FORBIDDEN_TOOL_SENTINEL%"\nexit /b 97\n',
                encoding="ascii",
            )
        else:
            path = poison / tool
            path.write_text(
                '#!/bin/sh\nprintf forbidden > "$AUDAY_FORBIDDEN_TOOL_SENTINEL"\nexit 97\n',
                encoding="ascii",
            )
            path.chmod(0o700)
    if windows:
        system_root = Path(source.get("SystemRoot", source.get("SYSTEMROOT", r"C:\Windows")))
        entries = [poison, system_root / "System32", system_root,
                   system_root / "System32/WindowsPowerShell/v1.0"]
    else:
        entries = [poison, "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    environment.update({
        "PATH": (";" if windows else ":").join(map(str, entries)),
        "HOME": str(home), "USERPROFILE": str(home),
        "LOCALAPPDATA": str(home / "local"), "APPDATA": str(home / "roaming"),
        "XDG_CACHE_HOME": str(home / "cache"), "XDG_CONFIG_HOME": str(home / "config"),
        "XDG_DATA_HOME": str(home / "data"), "TEMP": str(temporary),
        "TMP": str(temporary), "TMPDIR": str(temporary),
        "PYTHONNOUSERSITE": "1", "PYTHONUTF8": "1",
        "AUDAY_FORBIDDEN_TOOL_SENTINEL": str(sentinel),
    })
    return environment, home, sentinel


def install_windows_tool_traps(powershell, environment, *, build_parent=None):
    """Also trap direct CreateProcess calls, which do not use .cmd scripts."""
    poison = Path(environment["AUDAY_FORBIDDEN_TOOL_SENTINEL"]).parent / "forbidden-tools"
    program = r'''public class AudayForbiddenTool {
        public static int Main() {
            System.IO.File.WriteAllText(
                System.Environment.GetEnvironmentVariable("AUDAY_FORBIDDEN_TOOL_SENTINEL"),
                "forbidden");
            return 97;
        }
    }'''
    # Legacy Windows PowerShell CodeDOM compilers may reject Unicode TEMP or
    # output paths. This is test scaffolding: compile in a fresh ASCII runner
    # directory, then copy the executable into the actual Unicode test paths.
    parent = Path(build_parent or os.environ.get("RUNNER_TEMP", tempfile.gettempdir())).absolute()
    if not str(parent).isascii():
        raise ValueError("acceptance_ascii_build_parent_required: set RUNNER_TEMP to an ASCII path")
    with tempfile.TemporaryDirectory(prefix="auday-trap-build-", dir=parent) as temporary:
        build = Path(temporary)
        compiler_env = dict(environment)
        compiler_env.update({"TEMP": str(build), "TMP": str(build), "TMPDIR": str(build)})
        command = "$ErrorActionPreference = 'Stop'; " + \
                  "Add-Type -TypeDefinition @'\n" + program + "\n'@ " + \
                  "-OutputAssembly 'forbidden.exe' -OutputType ConsoleApplication"
        subprocess.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-EncodedCommand",
             base64.b64encode(command.encode("utf-16-le")).decode("ascii")],
            env=compiler_env, cwd=build, check=True, timeout=60,
        )
        binary = (build / "forbidden.exe").read_bytes()
    for tool in FORBIDDEN_TOOLS:
        (poison / (tool + ".exe")).write_bytes(binary)
    # Prove the copied executable can actually execute and write a Unicode path,
    # while keeping the real accidental-tool sentinel untouched.
    proof = poison / "trap-proof"
    proof_env = dict(environment, AUDAY_FORBIDDEN_TOOL_SENTINEL=str(proof))
    result = subprocess.run([str(poison / "python.exe")], env=proof_env, timeout=30)
    try:
        if result.returncode != 97 or not proof.is_file() or proof.read_text() != "forbidden":
            raise AssertionError("native_tool_trap_self_check_failed")
    finally:
        proof.unlink(missing_ok=True)


def pe_architecture(path):
    """Read the executable's machine type; platform.machine can hide emulation."""
    with Path(path).open("rb") as source:
        header = source.read(64)
        if len(header) != 64 or header[:2] != b"MZ":
            raise ValueError("not_a_pe_binary: " + str(path))
        source.seek(struct.unpack_from("<I", header, 0x3C)[0])
        signature = source.read(6)
    if len(signature) != 6 or signature[:4] != b"PE\0\0":
        raise ValueError("invalid_pe_header")
    machine = struct.unpack_from("<H", signature, 4)[0]
    if machine not in WINDOWS_MACHINES:
        raise ValueError("unsupported_pe_machine: " + hex(machine))
    return WINDOWS_MACHINES[machine]


def windows_native_architecture():
    """Get the hardware/OS machine type even inside an emulated test harness."""
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.IsWow64Process2.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.USHORT),
                                      ctypes.POINTER(wintypes.USHORT)]
    kernel.IsWow64Process2.restype = wintypes.BOOL
    process, native = wintypes.USHORT(), wintypes.USHORT()
    if not kernel.IsWow64Process2(kernel.GetCurrentProcess(), ctypes.byref(process), ctypes.byref(native)):
        raise OSError(ctypes.get_last_error(), "IsWow64Process2 failed")
    if native.value not in (0x8664, 0xAA64):
        raise ValueError("unsupported_windows_machine: " + hex(native.value))
    return WINDOWS_MACHINES[native.value]


def verify_windows_host(expected):
    native = windows_native_architecture()
    python = pe_architecture(sys.executable)
    assert native == expected, f"Expected {expected} Windows host, found {native}"
    assert python == expected, f"Expected native {expected} Python, found {python}"
    return {"host": native, "python": python}


def audit_windows_architecture(executables, data_root, expected):
    """Reject compatible-but-emulated binaries in installed-package acceptance."""
    result = []
    for executable in executables:
        path = Path(executable).resolve()
        assert path.is_relative_to(data_root.resolve()), "Native executable outside managed data"
        actual = pe_architecture(path)
        assert actual == expected, f"Expected native {expected} executable, found {actual}: {path.name}"
        result.append({"binary": str(path.relative_to(data_root.resolve())), "architecture": actual})
    return sorted(result, key=lambda row: row["binary"])


def pe_imports(path):
    """Read regular and delay imports without a global dumpbin/LLVM dependency."""
    data = Path(path).read_bytes()
    if data[:2] != b"MZ":
        raise ValueError("not_a_pe_binary: " + str(path))
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise ValueError("invalid_pe_header")
    sections = struct.unpack_from("<H", data, pe + 6)[0]
    optional_size = struct.unpack_from("<H", data, pe + 20)[0]
    optional = pe + 24
    magic = struct.unpack_from("<H", data, optional)[0]
    if magic not in (0x10B, 0x20B):
        raise ValueError("unsupported_pe_optional_header")
    directories = optional + (112 if magic == 0x20B else 96)
    image_base = struct.unpack_from("<Q" if magic == 0x20B else "<I", data,
                                    optional + (24 if magic == 0x20B else 28))[0]
    section_table = optional + optional_size
    regions = []
    for index in range(sections):
        entry = section_table + index * 40
        virtual_size, virtual, size, raw = struct.unpack_from("<IIII", data, entry + 8)
        regions.append((virtual, max(virtual_size, size), raw))

    def offset(rva):
        for virtual, size, raw in regions:
            if virtual <= rva < virtual + size:
                position = raw + rva - virtual
                if position < len(data):
                    return position
        raise ValueError("pe_rva_outside_sections")

    def name(rva):
        position = offset(rva)
        end = data.find(b"\0", position, position + 512)
        if end < 0:
            raise ValueError("unterminated_pe_import")
        return data[position:end].decode("ascii").lower()

    imports = set()
    for directory, stride, field in ((1, 20, 12), (13, 32, 4)):
        address, size = struct.unpack_from("<II", data, directories + directory * 8)
        if not address:
            continue
        position = offset(address)
        # A malformed table must never become an unbounded scan.
        for cursor in range(position, min(position + size, len(data)), stride):
            descriptor = data[cursor:cursor + stride]
            if descriptor == bytes(stride):
                break
            imported = struct.unpack_from("<I", data, cursor + field)[0]
            if directory == 13 and not (struct.unpack_from("<I", data, cursor)[0] & 1):
                imported -= image_base
            imports.add(name(imported))
    return sorted(imports)


def audit_windows_dependencies(executables, data_root, system_root, expected_architecture=None):
    """Fail when engines borrow redistributables from the hosted image.

    PATH isolation does not isolate the Windows loader from System32. Therefore
    VC/OpenMP/compiler runtime imports must have app-local copies. Windows API
    DLLs remain part of the explicitly stated OS baseline.
    """
    compiler_prefixes = ("msvcp", "vcruntime", "vcomp", "concrt", "libomp", "libiomp",
                         "libgcc", "libstdc++", "libwinpthread")
    pending = [Path(path).resolve() for path in executables]
    seen, result = set(), []
    while pending:
        binary = pending.pop()
        if binary in seen:
            continue
        seen.add(binary)
        assert binary.is_relative_to(data_root.resolve()), "Native executable outside managed data"
        siblings = {path.name.lower(): path for path in binary.parent.iterdir() if path.is_file()}
        architecture = pe_architecture(binary)
        if expected_architecture:
            allowed = {expected_architecture}
            # ARM64X redistributable DLLs contain native ARM64 and x64-compatible
            # views. They do not justify accepting an emulated executable.
            if binary.suffix.lower() == ".dll" and expected_architecture == "arm64":
                allowed.add("arm64x")
            assert architecture in allowed, f"Wrong native dependency architecture: {binary.name}: {architecture}"
        imports = pe_imports(binary)
        for imported in imports:
            if imported in siblings:
                pending.append(siblings[imported])
            elif imported.startswith(compiler_prefixes):
                raise AssertionError(f"Redistributable must be app-local: {binary.name}: {imported}")
            elif imported.startswith(("api-ms-win-", "ext-ms-win-")):
                continue
            elif not (system_root / "System32" / imported).is_file():
                raise AssertionError(f"Native dependency is not app-local or an OS DLL: {imported}")
        result.append({"binary": str(binary.relative_to(data_root.resolve())),
                       "architecture": architecture, "imports": imports})
    return sorted(result, key=lambda row: row["binary"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New directory outside the repository")
    parser.add_argument("--audio", choices=["skip", "full"], default="full")
    parser.add_argument("--audio-model", choices=["small", "turbo"], default="small")
    parser.add_argument("--expected-architecture", choices=["x86_64", "arm64"],
                        help="Require this native Windows host and every managed executable")
    args = parser.parse_args()
    output = args.output.expanduser().absolute()
    if output.exists() or output.is_symlink() or output.is_relative_to(ROOT):
        raise ValueError("new_external_acceptance_directory_required")
    output.mkdir(parents=True, mode=0o700)
    package = output / "installed-skill 安装"
    install(package)
    data = output / "fresh-data 缓存"
    environment, empty_home, sentinel = clean_environment(output)
    windows = sys.platform == "win32"
    if windows:
        powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        install_windows_tool_traps(powershell, environment)
        launcher = [str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                    "-File", str(package / "scripts/bootstrap.ps1")]
    else:
        launcher = ["/bin/sh", str(package / "scripts/bootstrap.sh")]
    steps = []
    started = time.monotonic()
    report = {
        "status": "running", "version": (package / "VERSION").read_text().strip(),
        "operatingSystem": platform.system(), "osVersion": platform.version(),
        "architecture": platform.machine(), "freshDataRoot": True, "emptyUserHome": True,
        "personalDataRead": False, "phoneConnectionTested": False,
        "systemBaseline": (
            "GitHub standard Windows hosted image; Windows PowerShell 5.1 and OS DLLs; "
            "PATH excludes developer tools; VC/OpenMP engine imports checked app-local; "
            "not a claim of a pristine OS"
            if windows else "macOS system utilities and Apple developer tools; not a pristine OS"
        ),
        "steps": steps,
    }

    def save_report():
        report["elapsedSeconds"] = round(time.monotonic() - started, 3)
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    def run(*arguments):
        begin = time.monotonic()
        step = {"command": arguments[0], "status": "running"}
        steps.append(step)
        save_report()
        result = subprocess.run(
            [*launcher, "--home", str(data), *arguments], cwd=empty_home,
            env=environment, stdout=subprocess.PIPE, text=True, encoding="utf-8", timeout=1800,
        )
        step.update({"seconds": round(time.monotonic() - begin, 3), "exitCode": result.returncode})
        with (output / "command-log.txt").open("a", encoding="utf-8") as log:
            log.write(json.dumps({"command": list(arguments), "stdout": result.stdout,
                                  "exitCode": result.returncode}) + "\n")
        if result.returncode:
            print(result.stdout, file=sys.stderr)
            setup_log = data / "runtime/audio/setup.log"
            if setup_log.is_file():
                print("\n".join(setup_log.read_text(encoding="utf-8", errors="replace").splitlines()[-50:]),
                      file=sys.stderr)
            raise RuntimeError("clean_setup_command_failed: " + arguments[0])
        assert not sentinel.exists(), "A command borrowed a forbidden global tool"
        step["status"] = "passed"
        save_report()
        return json.loads(result.stdout)

    try:
        expected_architecture = None
        if windows:
            expected_architecture = args.expected_architecture or windows_native_architecture()
            report["nativeWindowsArchitecture"] = verify_windows_host(expected_architecture)
        inspection = run("check")
        assert inspection["downloadsStarted"] is False and not data.exists()
        if windows:
            assert inspection["architecture"] == expected_architecture
        doctor = run("doctor")
        assert doctor["capabilities"]["core"]["pythonReady"]
        assert doctor["capabilities"]["core"]["pythonManagedBySetup"]
        if windows:
            assert doctor["liveConnectionSupported"]
            assert doctor["python"].startswith("3.13.")
            base_executables = list((data / "runtime").rglob("python.exe"))
            base_executables += list((data / "runtime").rglob("uv.exe"))
            assert len(base_executables) >= 3, "Missing base Python, link Python, or uv"
            report["baseArchitectureAudit"] = audit_windows_architecture(
                base_executables, data, expected_architecture,
            )
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
        if windows:
            report["semanticArchitectureAudit"] = audit_windows_architecture(
                [semantic["runtimePath"]], data, expected_architecture,
            )

        # These are freshly authored synthetic records, never phone/personal data.
        fixture = json.loads((ROOT / "tests/semantic-fixture.json").read_text(encoding="utf-8"))
        profile = data / "default"
        profile.mkdir(exist_ok=True)
        database_path = profile / "library.sqlite"
        with sqlite3.connect(database_path) as database:
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
        database_hash = file_hash(database_path)
        indexed = run("index")
        assert indexed["recordCount"] == len(fixture["records"])
        for query in fixture["queries"]:
            found = run("search", query["text"], "--mode", "semantic", "--limit", "1")
            assert found["records"][0]["id"] == query["target"], "Synthetic semantic query mismatch"
        assert run("index")["changed"] is False
        exported = run("export", "--output", str(output / "readable 导出"), "--language", "en")
        assert exported["recordCount"] == len(fixture["records"])
        assert file_hash(database_path) == database_hash, "Read/index/export modified source SQLite"
        assert not (profile / "connection.json").exists(), "Acceptance unexpectedly paired a phone"
        if not windows:
            subprocess.run(
                ["/usr/bin/swift", "build", "--package-path", str(package / "scripts/link")],
                cwd=empty_home, env=environment, check=True, timeout=600,
            )
        audio_result, audio_config = None, None
        if args.audio == "full":
            opted_out = run("setup", "--audio", "--model", args.audio_model, "--no-diarize")
            assert opted_out["installed"] and not opted_out["diarizationReady"]
            audio_config = json.loads((data / "runtime/audio/config.json").read_text(encoding="utf-8"))
            subprocess.run([
                audio_config["python"], "-I", "-c",
                "import importlib.util; assert importlib.util.find_spec('sherpa_onnx') is None; "
                "assert importlib.util.find_spec('soundfile') is None",
            ], cwd=empty_home, env=environment, check=True, timeout=30)
            assets = json.loads((package / "scripts/audio-assets.json").read_text(encoding="utf-8"))
            assert all(not (data / "models/audio" / asset["filename"]).exists()
                       for asset in assets["diarization"].values())
            report["audioOptOutWithoutSpeakerDependencies"] = True
            audio_result = run("setup", "--audio", "--model", args.audio_model)
            assert audio_result["installed"] and audio_result["diarizationReady"]
            audio_config = json.loads((data / "runtime/audio/config.json").read_text(encoding="utf-8"))
            if windows:
                assert audio_config["architecture"] == expected_architecture
                report["audioArchitectureAudit"] = audit_windows_architecture(
                    [audio_config[key] for key in ("python", "ffmpeg", "whisperCLI")],
                    data, expected_architecture,
                )
            for field in ("python", "ffmpeg", "whisperCLI", "model", "segmentationModel", "embeddingModel"):
                assert Path(audio_config[field]).resolve().is_relative_to(data.resolve()), field
            sample = data / "runtime/audio" / assets["whisperSource"]["directory"] / "samples/jfk.wav"
            sample_hash = file_hash(sample)
            for diarize, destination in ((False, "transcript 无说话人"), (True, "transcript 说话人")):
                arguments = ["transcribe", "--audio", str(sample), "--output", str(output / destination),
                             "--language", "en"]
                if not diarize:
                    arguments.append("--no-diarize")
                result = run(*arguments)
                assert result["ok"] and result["segments"] > 0
                assert bool(result["speakerCount"]) is diarize
                actual = json.loads((output / destination / "result.json").read_text(encoding="utf-8"))
                assert actual["diarization"]["status"] == ("completed" if diarize else "not_requested")
            assert file_hash(sample) == sample_hash, "Transcription changed its source audio"
            for asset in [assets["models"][args.audio_model], *assets["diarization"].values()]:
                path = data / "models/audio" / asset["filename"]
                assert path.stat().st_size == asset["size"] and file_hash(path) == asset["sha256"]
            report["audioModelHashesVerified"] = True
        if windows:
            engines = [semantic["runtimePath"]]
            if audio_config:
                engines += [audio_config["ffmpeg"], audio_config["whisperCLI"]]
            report["nativeDependencyAudit"] = audit_windows_dependencies(
                engines, data, Path(os.environ["SystemRoot"]), expected_architecture,
            )
        assert not sentinel.exists()
        report.update({
            "status": "passed", "globalToolsBorrowed": False, "unicodePathsTested": True,
            "baseDownloadsWithoutOptionalModels": True, "deferChoicePersisted": True,
            "semanticSetupChecks": 3, "semanticRetrievalChecks": len(fixture["queries"]),
            "sourceDatabaseUnchanged": True, "readableExportVerified": True,
            "nativeBridgeBuiltFromInstalledPackage": not windows,
            "audio": args.audio_model + " with opt-out then default diarization" if audio_result else "not_run",
            "dataBytes": sum(path.stat().st_size for path in data.rglob("*") if path.is_file()),
        })
    except BaseException as error:
        report.update({"status": "failed", "error": str(error), "errorType": type(error).__name__})
        raise
    finally:
        save_report()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
