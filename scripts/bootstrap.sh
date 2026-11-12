#!/bin/sh
# Start with macOS system tools only; do not require a user's Python or Homebrew.
set -eu
umask 077
auday_script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# Data root order, matching scripts/data_root.py: --home, AUDAY_HOME, the saved
# one-line setting written by `data-root --save`, then the platform default.
auday_default_root="$HOME/Library/Application Support/AudayWorkflow"
auday_data_root=$auday_default_root
auday_root_source=default
if [ "${1-}" = "--home" ]; then
    [ "$#" -ge 2 ] || { printf '%s\n' 'Auday: --home needs a directory.' >&2; exit 2; }
    auday_data_root=$2
    auday_root_source=option
    shift 2
elif [ -n "${AUDAY_HOME-}" ]; then
    auday_data_root=$AUDAY_HOME
    auday_root_source=environment
elif [ -f "$auday_default_root/data-root" ]; then
    auday_saved_root=
    IFS= read -r auday_saved_root < "$auday_default_root/data-root" || true
    auday_saved_root=${auday_saved_root%"$(printf '\r')"}
    if [ -n "$auday_saved_root" ]; then
        case "$auday_saved_root" in /*) ;; *)
            printf '%s\n' 'Auday: the saved data root is not an absolute path. Run with --home and data-root --clear.' >&2
            exit 2 ;;
        esac
        # A saved root existed when it was saved. If it is missing, its drive is
        # probably disconnected; never create a new library at that path.
        [ -d "$auday_saved_root" ] || {
            printf '%s\n' "Auday: the saved data root is unavailable: $auday_saved_root. Reconnect its drive, or run with --home and data-root --clear." >&2
            exit 2
        }
        auday_data_root=$auday_saved_root
        auday_root_source=saved
    fi
fi
case "$auday_data_root" in /*) ;; *)
    printf '%s\n' 'Auday: the data root (--home or AUDAY_HOME) must be an absolute directory path.' >&2; exit 2 ;;
esac
case "$auday_data_root" in *[[:cntrl:]]*)
    printf '%s\n' 'Auday: the data root must not contain control characters.' >&2; exit 2 ;;
esac
if [ "$(/usr/bin/uname -s)" != Darwin ]; then
    printf '%s\n' '{"ok":false,"error":"macOS_required"}'
    exit 1
fi
auday_arch=$(/usr/bin/uname -m)
if [ "$(/usr/sbin/sysctl -n hw.optional.arm64 2>/dev/null || true)" = 1 ]; then
    auday_arch=arm64
fi
case "$auday_arch" in arm64|x86_64) ;; *)
    printf '%s\n' '{"ok":false,"error":"unsupported_mac_architecture"}'; exit 1 ;;
esac
auday_os=$(/usr/bin/sw_vers -productVersion)
auday_major=$(printf '%s' "$auday_os" | /usr/bin/awk -F. '{print $1}')
if [ "$auday_major" -lt 13 ]; then
    printf '%s\n' '{"ok":false,"error":"macOS_13_or_later_required"}'
    exit 1
fi
auday_clt=false
if /usr/bin/xcode-select -p >/dev/null 2>&1; then auday_clt=true; fi
if [ "${1-}" = check ]; then
    auday_probe_dir="$auday_data_root"
    while [ ! -d "$auday_probe_dir" ]; do auday_probe_dir=$(/usr/bin/dirname "$auday_probe_dir"); done
    auday_free_kb=$(/bin/df -Pk "$auday_probe_dir" | /usr/bin/awk 'NR==2 {print $4}')
    auday_memory=$(/usr/sbin/sysctl -n hw.memsize)
    auday_root_json=$(printf '%s' "$auday_data_root" | /usr/bin/sed 's/\\/\\\\/g; s/"/\\"/g')
    printf '{"ok":true,"macOS":"%s","architecture":"%s","memoryBytes":%s,"availableDiskBytes":%s,"appleDeveloperToolsSelected":%s,"dataRoot":"%s","dataRootSource":"%s","downloadsStarted":false,"nextStep":"run bootstrap doctor to prepare the private base runtime"}\n' \
        "$auday_os" "$auday_arch" "$auday_memory" "$((auday_free_kb * 1024))" "$auday_clt" "$auday_root_json" "$auday_root_source"
    exit 0
fi

/bin/mkdir -p "$auday_data_root"
auday_data_root=$(CDPATH= cd -P -- "$auday_data_root" && pwd)
auday_row=$(/usr/bin/awk -F '\t' -v arch="$auday_arch" '$1=="uv" && $2==arch && $10=="macos" {print}' "$auday_script_dir/runtime-assets.tsv")
[ -n "$auday_row" ] || { printf '%s\n' 'Auday: runtime manifest entry missing.' >&2; exit 1; }
auday_tab=$(printf '\t')
IFS="$auday_tab" read -r auday_component auday_manifest_arch auday_url auday_sha auday_bytes auday_install_dir auday_archive_root auday_executable auday_min_os auday_platform auday_archive_format <<EOF
$auday_row
EOF
auday_uv_dir="$auday_data_root/runtime/$auday_install_dir"
auday_uv="$auday_uv_dir/$auday_archive_root/$auday_executable"
auday_marker="$auday_uv_dir/.auday-archive-sha256"
auday_stage=""
trap 'if [ -n "$auday_stage" ] && [ -d "$auday_stage" ]; then /bin/rm -rf "$auday_stage"; fi' EXIT HUP INT TERM
if [ -d "$auday_uv_dir" ]; then
    [ -f "$auday_marker" ] && [ "$(cat "$auday_marker")" = "$auday_sha" ] && [ -x "$auday_uv" ] ||
        { printf '%s\n' 'Auday: existing private runtime is incomplete; it was preserved.' >&2; exit 1; }
else
    /bin/mkdir -p "$auday_data_root/cache/downloads" "$auday_data_root/runtime"
    auday_archive="$auday_data_root/cache/downloads/$auday_sha.tar.gz"
    if [ ! -f "$auday_archive" ]; then
        printf '%s\n' '[Auday] Downloading private setup runtime' >&2
        /usr/bin/curl --fail --location --proto '=https' --proto-redir '=https' \
            --connect-timeout 20 --max-time 600 --retry 2 --continue-at - \
            --progress-bar --output "$auday_archive.partial" "$auday_url"
        auday_actual=$(/usr/bin/shasum -a 256 "$auday_archive.partial" | /usr/bin/awk '{print $1}')
        if [ "$auday_actual" != "$auday_sha" ]; then
            /bin/mv "$auday_archive.partial" "$auday_archive.partial.corrupt.$(/bin/date +%s)"
            printf '%s\n' 'Auday: runtime download failed its integrity check; a retry will start clean.' >&2
            exit 1
        fi
        /bin/mv "$auday_archive.partial" "$auday_archive"
    fi
    auday_actual=$(/usr/bin/shasum -a 256 "$auday_archive" | /usr/bin/awk '{print $1}')
    [ "$auday_actual" = "$auday_sha" ] ||
        { printf '%s\n' 'Auday: cached runtime failed its integrity check.' >&2; exit 1; }
    printf '%s\n' '[Auday] Installing verified private setup runtime' >&2
    auday_stage=$(/usr/bin/mktemp -d "$auday_data_root/runtime/.bootstrap.XXXXXX")
    # The immutable official archive is verified before the system tar sees it.
    /usr/bin/tar -xzf "$auday_archive" -C "$auday_stage"
    [ -x "$auday_stage/$auday_archive_root/$auday_executable" ] ||
        { printf '%s\n' 'Auday: runtime executable is missing.' >&2; exit 1; }
    printf '%s\n' "$auday_sha" > "$auday_stage/.auday-archive-sha256"
    [ ! -e "$auday_uv_dir" ] ||
        { printf '%s\n' 'Auday: another setup created this runtime; retry safely.' >&2; exit 1; }
    /bin/mv "$auday_stage" "$auday_uv_dir"
    auday_stage=""
fi

# Explicit directories prevent uv from borrowing user-managed interpreters/cache.
for auday_env_name in $(/usr/bin/env | /usr/bin/awk -F= '$1 ~ /^(UV_|PIP_)[A-Za-z0-9_]*$/ {print $1}'); do
    unset "$auday_env_name"
done
export UV_NO_CONFIG=1
export UV_CACHE_DIR="$auday_data_root/cache/uv"
export UV_PYTHON_INSTALL_DIR="$auday_data_root/runtime/python"
export UV_PYTHON=3.12.15
export UV_MANAGED_PYTHON=1
export PYTHONNOUSERSITE=1
unset PYTHONPATH PYTHONHOME VIRTUAL_ENV UV_PROJECT_ENVIRONMENT UV_ENV_FILE
auday_python=$("$auday_uv" --no-config python find --managed-python cpython@3.12.15 2>/dev/null) || auday_python=""
if [ -z "$auday_python" ]; then
    printf '%s\n' '[Auday] Preparing private Python runtime' >&2
    "$auday_uv" --no-config python install cpython@3.12.15 --no-bin
    auday_python=$("$auday_uv" --no-config python find --managed-python cpython@3.12.15)
fi
case "$auday_python" in "$auday_data_root/runtime/python/"*) ;; *)
    printf '%s\n' 'Auday: refusing an interpreter outside the private runtime.' >&2; exit 1 ;;
esac
"$auday_python" -I -c 'import ssl, sqlite3, sys; assert sys.version_info[:3] == (3, 12, 15)'
[ "$#" -gt 0 ] || set -- setup
exec "$auday_python" "$auday_script_dir/auday.py" --home "$auday_data_root" "$@"
