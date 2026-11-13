"""Where this computer keeps the Auday archive: one answer for every entrypoint.

A command uses, in order:

1. an explicit --home;
2. the AUDAY_HOME environment variable;
3. the saved data-root setting (`data-root --save`);
4. the earlier default location, while it holds a paired profile and the visible
   default does not, so an upgrade never strands an existing archive;
5. the visible default, `~/Auday` (`%USERPROFILE%\\Auday` on Windows).

bootstrap.sh and bootstrap.ps1 apply the same order before Python exists; keep
all three in step. The setting is one line, an absolute path, stored in the
hidden application directory as `data-root`. Runtimes, caches and models of the
visible default stay in that hidden directory (see platform_utils.support_root).
"""

import os
from pathlib import Path
import sqlite3

from platform_utils import default_home, saved_data_root_text, secure_directory, support_root, visible_home

ENVIRONMENT_VARIABLE = "AUDAY_HOME"
SETTING_NAME = "data-root"


class DecisionRequired(ValueError):
    """Stop and let the user choose; never pick one of these paths for them."""

    def __init__(self, code, message, **details):
        super().__init__(code + ": " + message)
        self.code = code
        self.message = message
        self.details = details

    def payload(self):
        return {"ok": False, "error": self.code, "message": self.message,
                "needsUserDecision": True, **self.details}


def setting_path():
    return default_home() / SETTING_NAME


def _absolute(value, error):
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(error)
    return path


def _canonical(path):
    return Path(path).expanduser().resolve()


def environment_home():
    value = os.environ.get(ENVIRONMENT_VARIABLE)
    return _absolute(value, "auday_home_must_be_an_absolute_directory_path") if value else None


def saved_home():
    value = saved_data_root_text()
    return _absolute(value, "saved_data_root_must_be_absolute_run_data_root_clear") if value else None


def has_pairing(root):
    """Any profile directory under root that completed pairing."""
    if root is None:
        return False
    try:
        return any((child / "connection.json").is_file() for child in Path(root).iterdir() if child.is_dir())
    except OSError:
        return False


def legacy_home():
    """The earlier default archive location, when it is still the one in use."""
    legacy = default_home()
    if has_pairing(legacy) and not has_pairing(visible_home()):
        return legacy
    return None


def unavailable_saved_root(saved):
    return DecisionRequired(
        "saved_data_root_unavailable",
        "the saved archive location cannot be found; its drive may be disconnected",
        dataRoot=str(saved),
        choices=[
            {"choice": "reconnect_and_retry",
             "detail": "Reconnect the drive or folder, then repeat the same command."},
            {"choice": "use_another_location",
             "detail": "Start or select a library elsewhere: --home <absolute-path> data-root --save. "
                       "A new location needs pairing and a first full sync; the old archive is left untouched."},
            {"choice": "return_to_default_location",
             "detail": "data-root --clear, then use " + str(visible_home())
                       + ". It needs pairing and a first full sync unless a library is already there."},
        ],
    )


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
            raise unavailable_saved_root(saved)
        return saved, "saved"
    legacy = legacy_home()
    if legacy is not None:
        return legacy, "legacy"
    return visible_home(), "default"


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
    if home == _canonical(visible_home()) and legacy_home() is None:
        clear_home()
        return
    # The saved location exists from now on; a later absence means a disconnected
    # drive. It may be a folder the user already shares, so only create it: its
    # permissions are theirs. Each profile folder inside is protected separately.
    home.mkdir(parents=True, exist_ok=True)
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


def known_roots():
    roots = []
    for find in (environment_home, saved_home, visible_home, default_home):
        try:
            value = find()
        except ValueError:
            continue
        if value is not None and _canonical(value) not in roots:
            roots.append(_canonical(value))
    return roots


def other_libraries(home, profile):
    """Paired or synchronized copies of this profile under the other roots this
    computer knows about: AUDAY_HOME, the saved setting, the visible default and
    the earlier default location."""
    current = _canonical(home)
    found = []
    for root in known_roots():
        if root == current:
            continue
        state = profile_state(root / profile)
        if state["paired"] or state["synchronized"]:
            found.append({"dataRoot": str(root), "profile": profile, **state})
    return found


def ensure_single_library(home, profile, allow_new=False):
    """Ask before starting an empty library while this profile already lives elsewhere."""
    if allow_new:
        return
    state = profile_state(_canonical(home) / profile)
    if state["paired"] or state["synchronized"]:
        return
    others = other_libraries(home, profile)
    if others:
        raise DecisionRequired(
            "another_library_exists",
            "this phone profile already has a library at another location",
            dataRoot=str(_canonical(home)), otherLibraries=others,
            choices=[
                {"choice": "use_existing_library",
                 "detail": "--home <that dataRoot> data-root --save, then continue there; "
                           "its pairing and synchronized records are kept."},
                {"choice": "start_separate_library",
                 "detail": "Repeat the command with --new-library. It needs pairing and a first full sync; "
                           "the existing library is not changed."},
            ],
        )


def ensure_paired_here(home, profile):
    """Ask before `sync` reports a plain not_paired while this profile is paired elsewhere."""
    if (_canonical(home) / profile / "connection.json").is_file():
        return
    others = other_libraries(home, profile)
    if others:
        raise DecisionRequired(
            "not_paired_here", "this location is not paired, but this phone profile has a library elsewhere",
            dataRoot=str(_canonical(home)), otherLibraries=others,
            choices=[
                {"choice": "use_existing_library",
                 "detail": "--home <that dataRoot> data-root --save, then sync there."},
                {"choice": "start_separate_library",
                 "detail": "pair-window ... --new-library here, then sync; the first sync is full."},
            ],
        )


def describe(home, source, profile):
    try:
        later, later_source = configured_home()
        without_home = {"dataRoot": str(_canonical(later)), "source": later_source}
    except DecisionRequired as error:
        without_home = {"dataRoot": error.details["dataRoot"], "source": "saved",
                        "error": error.code}
    except ValueError as error:
        without_home = {"dataRoot": None, "source": None, "error": str(error).split(":", 1)[0]}
    try:
        saved = saved_home()
    except ValueError:
        saved = None
    try:
        default = str(_canonical(visible_home()))
    except ValueError:
        default = None
    return {
        "dataRoot": str(_canonical(home)),
        "source": source,
        "profile": profile,
        "profileState": profile_state(_canonical(home) / profile),
        "runtimeRoot": str(support_root(home)),
        "withoutHomeOption": without_home,
        "savedDataRoot": str(saved) if saved else None,
        "environmentDataRoot": os.environ.get(ENVIRONMENT_VARIABLE) or None,
        "defaultDataRoot": default,
        "otherLibraries": other_libraries(home, profile),
    }
