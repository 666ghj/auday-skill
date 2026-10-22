"""Finish the Windows bootstrap using its verified private base interpreter."""

import json
from pathlib import Path
import subprocess
import sys

from setup_runtime import ensure_windows_link, managed_env


def main(arguments=None):
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    if sys.platform != "win32":
        raise ValueError("windows_bootstrap_requires_windows")
    if len(arguments) < 2 or arguments[0] != "--home":
        raise ValueError("bootstrap_home_required")
    root = Path(arguments[1]).resolve()
    python = ensure_windows_link(root, python_path=sys.executable)
    # Use a subprocess instead of Windows exec emulation so the parent reliably
    # receives the client's final exit status, stdout JSON and stderr progress.
    return subprocess.run(
        [str(python), str(Path(__file__).with_name("auday.py")), *arguments],
        env=managed_env(root),
    ).returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        raise SystemExit(1)
