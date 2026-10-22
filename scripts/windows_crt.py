"""Install pinned Microsoft release runtime DLLs beside a private native executable."""

import json
import os
from pathlib import Path
import shutil
import tempfile

from platform_utils import exclusive_file_lock, secure_directory

CRT_ASSET = json.loads(
    (Path(__file__).resolve().parent / "audio-assets.json").read_text(encoding="utf-8")
).get("windowsRuntime", {})
CORE_FILES = ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll")


def ensure_windows_crt(root, destination, *, openmp=False):
    """Use only the official pinned release files; no global installer or audio setup."""
    from setup_runtime import download_asset, extract_zip, file_hash

    root = Path(root).expanduser().resolve()
    destination = Path(destination).expanduser().absolute()
    if destination.is_symlink() or not destination.resolve().is_relative_to(root):
        raise ValueError("windows_runtime_destination_outside_private_setup")
    names = (*CORE_FILES, "vcomp140.dll") if openmp else CORE_FILES
    assets = {item["filename"]: item for item in CRT_ASSET.get("files", [])}
    if not all(name in assets for name in names):
        raise ValueError("windows_runtime_manifest_incomplete")
    selected = [assets[name] for name in names]

    def matches(path, item):
        return (path.is_file() and not path.is_symlink()
                and path.stat().st_size == item["size"] and file_hash(path) == item["sha256"])

    with exclusive_file_lock(root / "runtime" / ".windows-crt.lock"):
        missing = []
        for item in selected:
            target = destination / item["filename"]
            if target.exists() or target.is_symlink():
                if not matches(target, item):
                    raise ValueError("existing_windows_runtime_library_mismatch: preserve it and use a new setup root")
            else:
                missing.append(item)
        if not missing:
            return [destination / name for name in names]
        archive = download_asset(
            CRT_ASSET["url"], root / "cache/downloads" / (CRT_ASSET["sha256"] + ".vsix"),
            CRT_ASSET["sha256"], size=CRT_ASSET["size"],
            label="Downloading private Windows runtime libraries",
        )
        secure_directory(destination)
        with tempfile.TemporaryDirectory(prefix=".windows-crt-", dir=destination.parent) as directory:
            unpacked = extract_zip(archive, Path(directory) / "unpacked")
            # Validate every requested release file before changing the executable
            # directory. Debug and unrelated DLLs stay in the temporary archive tree.
            for item in missing:
                if not matches(unpacked / item["member"], item):
                    raise ValueError("windows_runtime_library_verification_failed")
            for item in missing:
                target = destination / item["filename"]
                fd, temporary = tempfile.mkstemp(prefix=".crt-", dir=destination)
                try:
                    with os.fdopen(fd, "wb") as stream, (unpacked / item["member"]).open("rb") as source:
                        shutil.copyfileobj(source, stream)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, target)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
            for item in selected:
                if not matches(destination / item["filename"], item):
                    raise ValueError("windows_runtime_library_verification_failed")
    return [destination / name for name in names]
