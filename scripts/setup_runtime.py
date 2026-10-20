"""Verified private runtime installation, independent of global developer tools."""

import csv
import hashlib
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request

from progress import report

HERE = Path(__file__).resolve().parent
PYTHON_VERSION = "3.12.15"


def file_hash(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def host_architecture():
    if sys.platform == "darwin":
        result = subprocess.run(
            ["/usr/sbin/sysctl", "-n", "hw.optional.arm64"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip() == "1":
            return "arm64"
    value = platform.machine()
    if value in ("arm64", "aarch64"):
        return "arm64"
    if value in ("x86_64", "AMD64"):
        return "x86_64"
    raise ValueError("unsupported_mac_architecture")


def require_macos(minimum="13.0"):
    if sys.platform != "darwin":
        raise ValueError("setup_requires_macos")
    current = tuple(int(part) for part in platform.mac_ver()[0].split(".")[:2])
    required = tuple(int(part) for part in minimum.split("."))
    if current < required:
        raise ValueError("requires_macos_" + minimum + "_or_later")


def runtime_asset(component, architecture=None):
    architecture = architecture or host_architecture()
    with (HERE / "runtime-assets.tsv").open() as source:
        rows = list(csv.DictReader(source, delimiter="\t"))
    matches = [row for row in rows if row["component"] == component
               and row["architecture"] == architecture]
    if len(matches) != 1:
        raise ValueError("unsupported_runtime_asset")
    row = matches[0]
    for key in ("install_dir", "archive_root", "executable"):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", row[key]) or row[key] in (".", ".."):
            raise ValueError("invalid_runtime_manifest")
    row["size"] = int(row["size"])
    return row


def download_asset(url, destination, sha256, size=None, label="Downloading local component"):
    """Resume bounded HTTPS downloads; never expose an unverified final file."""
    if not url.startswith("https://") or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError("verified_https_asset_required")
    if size is not None and (type(size) is not int or not 0 < size <= 20 * 1024**3):
        raise ValueError("invalid_download_size")
    destination = Path(destination).expanduser().absolute()
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if destination.is_symlink():
        raise ValueError("download_destination_is_symlink")
    if destination.exists():
        if destination.is_file() and file_hash(destination) == sha256:
            report("Using verified local component", force=True)
            return destination
        raise ValueError("existing_component_hash_mismatch: preserve it and use a new destination")
    partial = destination.with_name(destination.name + ".partial")
    if partial.is_symlink():
        raise ValueError("download_partial_is_symlink")
    if size is not None:
        remaining = max(0, size - (partial.stat().st_size if partial.exists() else 0))
        if shutil.disk_usage(destination.parent).free < remaining + 64 * 1024**2:
            raise ValueError("insufficient_disk_space_for_download")
    for attempt in range(3):
        offset = partial.stat().st_size if partial.exists() else 0
        if size is not None and offset > size:
            raise ValueError("download_partial_exceeds_expected_size")
        if size is not None and offset == size:
            break
        headers = {"User-Agent": "Auday-Skill-Setup", "Accept-Encoding": "identity"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=60, context=ssl.create_default_context()) as response:
                if not response.url.startswith("https://"):
                    raise ValueError("insecure_download_redirect")
                status = response.status
                if status == 206:
                    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
                    if not match or int(match[1]) != offset or (size is not None and int(match[3]) != size):
                        raise ValueError("invalid_download_range")
                elif status == 200:
                    offset = 0  # A server may ignore Range; restart instead of appending.
                else:
                    raise ValueError("unexpected_download_response")
                total = size
                if total is None and response.headers.get("Content-Length"):
                    total = offset + int(response.headers["Content-Length"])
                report(label, offset, total, "bytes", force=True)
                with partial.open("ab" if offset else "wb") as output:
                    os.chmod(partial, 0o600)
                    while True:
                        block = response.read(1024 * 1024)
                        if not block:
                            break
                        offset += len(block)
                        if (size is not None and offset > size) or offset > 20 * 1024**3:
                            raise ValueError("download_exceeds_expected_size")
                        output.write(block)
                        report(label, offset, total, "bytes")
                    output.flush()
                    os.fsync(output.fileno())
            if size is not None and offset != size:
                raise OSError("incomplete_download")
            break
        except (OSError, urllib.error.URLError, http.client.IncompleteRead):
            if attempt == 2:
                raise ValueError("download_interrupted: partial retained; check network and retry") from None
            report("Connection interrupted; resuming download", force=True)
            time.sleep(1)
    report("Verifying downloaded component", force=True)
    if file_hash(partial) != sha256:
        partial.replace(partial.with_name(partial.name + ".corrupt-" + str(time.time_ns())))
        raise ValueError("download_hash_mismatch: invalid bytes retained separately")
    os.replace(partial, destination)
    return destination


def extract_tar(archive, destination):
    """Extract only ordinary files, directories and contained relative links."""
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError("extraction_destination_exists")
    destination.mkdir(parents=True, mode=0o700)
    try:
        with tarfile.open(archive, "r:*") as source:
            members = source.getmembers()
            targets = {}
            for member in members:
                name = PurePosixPath(member.name)
                if name.is_absolute() or ".." in name.parts:
                    raise ValueError("unsafe_archive_path")
                if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                    raise ValueError("unsupported_archive_member")
                target = destination.joinpath(*name.parts)
                if target in targets and not (member.isdir() and targets[target].isdir()):
                    raise ValueError("duplicate_archive_path")
                targets[target] = member
                if member.issym() or member.islnk():
                    link = PurePosixPath(member.linkname)
                    if link.is_absolute():
                        raise ValueError("unsafe_archive_link")
                    resolved = (target.parent if member.issym() else destination).joinpath(*link.parts).resolve()
                    if not resolved.is_relative_to(destination.resolve()):
                        raise ValueError("unsafe_archive_link")
            # No file may be written through a link, regardless of archive order.
            link_paths = {path for path, member in targets.items() if member.issym() or member.islnk()}
            for path in targets:
                if any(parent in link_paths for parent in path.parents):
                    raise ValueError("archive_link_parent")
            for path, member in targets.items():
                if member.isdir():
                    path.mkdir(parents=True, exist_ok=True, mode=0o700)
                elif member.isfile():
                    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    with source.extractfile(member) as content, path.open("xb") as output:
                        shutil.copyfileobj(content, output)
                    os.chmod(path, member.mode & 0o777)
            for path, member in targets.items():
                if member.issym():
                    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    path.symlink_to(member.linkname)
                elif member.islnk():
                    original = destination / member.linkname
                    if not original.is_file() or original.is_symlink():
                        raise ValueError("invalid_archive_hardlink")
                    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    os.link(original, path)
            # Resolve complete symlink chains after all links exist.
            for path in link_paths:
                try:
                    resolved = path.resolve(strict=True)
                except (OSError, RuntimeError):
                    raise ValueError("invalid_archive_link_target") from None
                if not resolved.is_relative_to(destination.resolve()):
                    raise ValueError("unsafe_archive_link")
        return destination
    except BaseException:
        shutil.rmtree(destination)
        raise


def install_runtime(root, component):
    root = Path(root).expanduser().resolve()
    asset = runtime_asset(component)
    require_macos(asset["min_macos"])
    destination = root / "runtime" / asset["install_dir"]
    executable = destination / asset["archive_root"] / asset["executable"]
    marker = destination / ".auday-archive-sha256"
    if destination.exists():
        if marker.is_file() and marker.read_text().strip() == asset["sha256"] and executable.is_file():
            return executable
        raise ValueError("existing_runtime_incomplete: preserve it and choose a new setup root")
    archive = download_asset(
        asset["url"], root / "cache/downloads" / (asset["sha256"] + ".tar.gz"),
        asset["sha256"], asset["size"], label="Downloading " + component + " runtime",
    )
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix=".runtime-", dir=destination.parent) as temporary:
        stage = extract_tar(archive, Path(temporary) / "unpacked")
        if not (stage / asset["archive_root"] / asset["executable"]).is_file():
            raise ValueError("runtime_executable_missing_from_archive")
        (stage / ".auday-archive-sha256").write_text(asset["sha256"] + "\n")
        destination.mkdir(mode=0o700)
        try:
            os.replace(stage, destination)
        except BaseException:
            destination.rmdir()
            raise
    return executable


def ensure_uv(root):
    return install_runtime(root, "uv")


def managed_env(root):
    root = Path(root).expanduser().resolve()
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("UV_", "PIP_")) and key not in
        ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "PYTHONSTARTUP", "CONDA_PREFIX")
    }
    environment.update({
        "UV_NO_CONFIG": "1", "UV_CACHE_DIR": str(root / "cache/uv"),
        "UV_PYTHON_INSTALL_DIR": str(root / "runtime/python"),
        "UV_PYTHON": PYTHON_VERSION, "UV_MANAGED_PYTHON": "1",
        "PYTHONNOUSERSITE": "1",
        "HF_HOME": str(root / "cache/huggingface"), "TORCH_HOME": str(root / "cache/torch"),
    })
    return environment


def ensure_python(root, uv_path=None):
    uv = Path(uv_path) if uv_path else ensure_uv(root)
    environment = managed_env(root)
    report("Preparing private Python runtime", force=True)
    subprocess.run(
        [str(uv), "--no-config", "python", "install", "cpython@" + PYTHON_VERSION, "--no-bin"],
        env=environment, check=True, timeout=600,
    )
    result = subprocess.run(
        [str(uv), "--no-config", "python", "find", "--managed-python", "cpython@" + PYTHON_VERSION],
        env=environment, capture_output=True, text=True, check=True, timeout=60,
    )
    executable = Path(result.stdout.strip()).resolve()
    if not executable.is_file() or not executable.is_relative_to((Path(root) / "runtime/python").resolve()):
        raise ValueError("python_runtime_outside_private_setup")
    subprocess.run([str(executable), "-I", "-c", "import ssl,sqlite3; assert __import__('sys').version_info[:3] == (3,12,15)"],
                   env=environment, check=True, timeout=30)
    return executable


def command_line_tools_status():
    if sys.platform != "darwin":
        return {"ready": False, "reason": "macOS_required"}
    selected = subprocess.run(["/usr/bin/xcode-select", "-p"], capture_output=True, text=True, timeout=10)
    if selected.returncode:
        return {"ready": False, "reason": "apple_command_line_tools_missing",
                "nextStep": "agent_start_xcode_select_install"}
    found = subprocess.run(["/usr/bin/xcrun", "--find", "swift"], capture_output=True, text=True, timeout=10)
    if found.returncode or not Path(found.stdout.strip()).is_file():
        return {"ready": False, "reason": "swift_toolchain_unavailable"}
    version = subprocess.run(
        ["/usr/bin/xcrun", "swift", "--version"], capture_output=True, text=True, timeout=20,
    )
    match = re.search(r"Swift version (\d+)\.(\d+)", version.stdout)
    ready = version.returncode == 0 and bool(match) and tuple(map(int, match.groups())) >= (5, 9)
    return {"ready": ready, "reason": None if ready else "swift_5_9_or_later_required",
            "version": ".".join(match.groups()) if match else None}
