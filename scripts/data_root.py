"""Where this computer keeps Auday data: one answer for every entrypoint.

A command uses, in order: an explicit --home, the AUDAY_HOME environment
variable, the saved data-root setting, then the platform default. bootstrap.sh
and bootstrap.ps1 apply the same order before Python exists; keep all three in
step. The setting is one line, an absolute path, in `<platform default>/data-root`.
"""

import os
from pathlib import Path
import sqlite3

from platform_utils import default_home, secure_directory

ENVIRONMENT_VARIABLE = "AUDAY_HOME"
SETTING_NAME = "data-root"


def setting_path():
    return default_home() / SETTING_NAME


def _absolute(value, error):
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(error)
    return path


def environment_home():
    value = os.environ.get(ENVIRONMENT_VARIABLE)
    return _absolute(value, "auday_home_must_be_an_absolute_directory_path") if value else None


def saved_home():
    try:
        text = setting_path().read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None
    # The entrypoints read exactly the first line, so this does too.
    value = text.split("\n", 1)[0].rstrip("\r")
    return _absolute(value, "saved_data_root_must_be_absolute_run_data_root_clear") if value else None


def configured_home():
    """The root a command without --home uses, and where that choice came from."""
    environment = environment_home()
    if environment is not None:
        return environment, "environment"
    saved = saved_home()
    if saved is not None:
        # A saved root always existed when it was saved. If it is gone, its drive
        # is probably disconnected: never start a new library at that path.
        if not saved.is_dir():
            raise ValueError("saved_data_root_unavailable: " + str(saved)
                             + "; reconnect its drive, or run with --home and data-root --clear")
        return saved, "saved"
    return default_home(), "default"


def _canonical(path):
    return Path(path).expanduser().resolve()


def resolve_home(explicit=None):
    """Return (home, source). The bootstrap entrypoints pass their resolved root
    on as --home, so an explicit root equal to the configured one keeps that
    configured source; any other explicit root is a one-command override."""
    if explicit is None:
        return configured_home()
    home = Path(explicit).expanduser()
    try:
        configured, source = configured_home()
    except ValueError:
        return home, "option"
    return home, source if _canonical(home) == _canonical(configured) else "option"


def save_home(home):
    home = _canonical(home)
    if any(ord(character) < 32 or ord(character) == 127 for character in str(home)):
        raise ValueError("data_root_must_not_contain_control_characters")
    if home == _canonical(default_home()):
        clear_home()
        return
    secure_directory(default_home())
    target = setting_path()
    temporary = target.with_name(SETTING_NAME + ".tmp")
    temporary.write_bytes((str(home) + "\n").encode("utf-8"))
    os.chmod(temporary, 0o600)
    temporary.replace(target)


def clear_home():
    try:
        setting_path().unlink()
    except FileNotFoundError:
        pass


def profile_state(root):
    """Whether a profile directory is paired or holds a synchronized snapshot.

    Reads without creating or migrating anything, so probing another root never
    starts a library there.
    """
    root = Path(root)
    paired = (root / "connection.json").is_file()
    synchronized = False
    database = root / "library.sqlite"
    if database.is_file():
        try:
            connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
            try:
                synchronized = connection.execute(
                    "SELECT 1 FROM metadata WHERE key='snapshot'").fetchone() is not None
            finally:
                connection.close()
        except sqlite3.Error:
            synchronized = False
    return {"paired": paired, "synchronized": synchronized}


def other_libraries(home, profile):
    """Paired or synchronized copies of this profile under the other roots this
    computer knows about: AUDAY_HOME, the saved setting and the platform default."""
    current = _canonical(home)
    roots = []
    for find in (environment_home, saved_home, default_home):
        try:
            value = find()
        except ValueError:
            continue
        if value is None:
            continue
        root = _canonical(value)
        if root != current and root not in roots:
            roots.append(root)
    found = []
    for root in roots:
        state = profile_state(root / profile)
        if state["paired"] or state["synchronized"]:
            found.append({"dataRoot": str(root), "profile": profile, **state})
    return found


def ensure_single_library(home, profile, allow_new=False):
    """Refuse to start an empty library while this profile already lives elsewhere."""
    if allow_new:
        return
    state = profile_state(_canonical(home) / profile)
    if state["paired"] or state["synchronized"]:
        return
    others = other_libraries(home, profile)
    if others:
        raise ValueError(
            "another_library_exists: " + others[0]["dataRoot"]
            + " already holds this profile; run data-root to choose it, or pass --new-library to start a separate library here"
        )


def describe(home, source, profile):
    later, later_source = configured_home()
    try:
        saved = saved_home()
    except ValueError:
        saved = None
    environment = os.environ.get(ENVIRONMENT_VARIABLE) or None
    return {
        "dataRoot": str(_canonical(home)),
        "source": source,
        "profile": profile,
        "profileState": profile_state(_canonical(home) / profile),
        "withoutHomeOption": {"dataRoot": str(_canonical(later)), "source": later_source},
        "savedDataRoot": str(saved) if saved else None,
        "environmentDataRoot": environment,
        "defaultDataRoot": str(_canonical(default_home())),
        "otherLibraries": other_libraries(home, profile),
    }
