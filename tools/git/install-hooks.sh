#!/bin/sh
# Auday Git guard v1
set -eu

fail() { printf '%s\n' "$*" >&2; exit 1; }
script_dir=$(CDPATH= cd "$(dirname "$0")" && pwd -P)
repo_root=$(CDPATH= cd "$script_dir/../.." && pwd -P)
cd "$repo_root"
[ "$(git rev-parse --show-toplevel)" = "$repo_root" ] || fail "Install this tool at the repository root's tools/git directory."
command -v python3 >/dev/null 2>&1 || fail "python3 is required. No packages were installed."
git lfs version >/dev/null 2>&1 || fail "Git LFS is required. Install it before running this script."

for hook in pre-commit pre-push; do
    [ -x ".githooks/$hook" ] || fail ".githooks/$hook is missing or not executable."
    grep -q '^# Auday Git guard v1$' ".githooks/$hook" || fail ".githooks/$hook does not belong to this tool; refusing to configure it."
done
[ -f tools/git/check_staged.py ] || fail "tools/git/check_staged.py is missing."

if existing=$(git config --get core.hooksPath); then
    case "$existing" in
        .githooks|"$repo_root/.githooks") ;;
        *) fail "An existing core.hooksPath is configured. Preserve and integrate those hooks before installing Auday Git guard." ;;
    esac
else
    default_hooks=$(git rev-parse --git-path hooks)
    for hook in "$default_hooks"/*; do
        case "$hook" in *.sample) continue ;; esac
        if [ -f "$hook" ] && [ -x "$hook" ]; then
            fail "An executable default Git hook already exists. Preserve and integrate it before installing Auday Git guard."
        fi
    done
fi

# Our tracked pre-push hook already invokes git lfs pre-push.
# --skip-repo prevents Git LFS from replacing or rejecting that hook.
git lfs install --local --skip-repo
git config --local core.hooksPath .githooks
printf '%s\n' 'Auday Git guard enabled for this repository only. No commit or push was performed.'
