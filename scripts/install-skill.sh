#!/usr/bin/env bash
# Install the tg-cli Telegram skill into ~/.claude/skills/telegram/ as a
# symlink pointing at the repo's skill/ directory.
#
# Idempotent: re-running is a no-op if the symlink already points at the
# correct source. Errors out (non-zero) if the target exists but is not
# a symlink or points somewhere else, so we never clobber user data.
#
# HOME can be overridden via the HOME env var for testability.

set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "${script_dir}/.." && pwd)
source_dir="${repo_root}/skill"

if [ ! -d "${source_dir}" ]; then
    echo "error: skill source directory not found: ${source_dir}" >&2
    exit 1
fi

target_parent="${HOME}/.claude/skills"
target="${target_parent}/telegram"

mkdir -p "${target_parent}"

if [ -L "${target}" ]; then
    current=$(readlink "${target}")
    if [ "${current}" = "${source_dir}" ]; then
        echo "skill already installed: ${target} -> ${source_dir}"
        exit 0
    fi
    echo "error: ${target} is a symlink to ${current}; refusing to replace" >&2
    echo "remove it manually if you want to reinstall" >&2
    exit 1
fi

if [ -e "${target}" ]; then
    echo "error: ${target} exists and is not a symlink; refusing to overwrite" >&2
    exit 1
fi

ln -s "${source_dir}" "${target}"
echo "installed skill: ${target} -> ${source_dir}"
