#!/bin/bash
# Shared helpers for the bagpipe wrapper.
# Sourced by bagpipe.sh; not meant to be executed directly.

BP_REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
BP_COMPOSE="$BP_REPO/docker-compose.yml"
BP_STATE="${XDG_CACHE_HOME:-$HOME/.cache}/bagpipe"
BP_VERSION="$(cat "$BP_REPO/VERSION" 2>/dev/null || echo "unknown")"

# Top-level host directories that belong to the operating system. Everything
# else at the top of / -- home directories, data disks, network shares -- is
# mounted into the container at its own path (see bp_host_mounts).
BP_SYSTEM_DIRS=(bin boot dev etc lib lib32 lib64 libx32 lost+found proc root run sbin snap sys usr var)

if [[ -t 2 ]]; then
    BP_RED=$'\033[31m'; BP_YELLOW=$'\033[33m'; BP_DIM=$'\033[2m'
    BP_BOLD=$'\033[1m'; BP_OFF=$'\033[0m'
else
    BP_RED=""; BP_YELLOW=""; BP_DIM=""; BP_BOLD=""; BP_OFF=""
fi

bp_err()  { printf '%s\n' "${BP_RED}error:${BP_OFF} $*" >&2; }
bp_warn() { printf '%s\n' "${BP_YELLOW}warning:${BP_OFF} $*" >&2; }
bp_dim()  { printf '%s\n' "${BP_DIM}$*${BP_OFF}" >&2; }

# ------------------------------------------------------------------------------
# Running the Python commands
# ------------------------------------------------------------------------------
# `python -m bagpipe <command>` runs natively through uv when it is installed:
# dependencies come from uv.lock into .venv of this checkout, and the process
# sees the host filesystem as it is, so every path just works. Machines without
# uv fall back to a container that holds the same locked dependencies.

bp_runtime() {
    case "${BAGPIPE_RUNTIME:-auto}" in
        native|docker) printf '%s\n' "$BAGPIPE_RUNTIME" ;;
        auto) if command -v uv >/dev/null 2>&1; then echo native; else echo docker; fi ;;
        *)
            bp_err "BAGPIPE_RUNTIME must be auto, native or docker (got: $BAGPIPE_RUNTIME)"
            return 1
            ;;
    esac
}

bp_venv() {
    local venv="${UV_PROJECT_ENVIRONMENT:-.venv}"
    [[ "$venv" == /* ]] || venv="$BP_REPO/$venv"
    printf '%s\n' "$venv"
}

# Creates or updates .venv from uv.lock when needed; a no-op otherwise.
bp_sync_venv() {
    if ! command -v uv >/dev/null 2>&1; then
        bp_err "the native runtime needs uv: https://docs.astral.sh/uv/getting-started/installation/"
        return 1
    fi
    uv sync --project "$BP_REPO" --locked --quiet || {
        bp_err "could not set up the Python environment (uv sync in $BP_REPO)"
        return 1
    }
}

# bp_run_bagpipe <command> [args...]
bp_run_bagpipe() {
    local runtime
    runtime="$(bp_runtime)" || return 1
    if [[ "$runtime" == native ]]; then
        bp_sync_venv || return 1
        [[ -n "${BAGPIPE_DEBUG:-}" ]] && bp_dim "[debug] native: $(bp_venv)/bin/python -m bagpipe $*"
        "$(bp_venv)/bin/python" -m bagpipe "$@"
    else
        bp_run_in_container python -m bagpipe "$@"
    fi
}

# ------------------------------------------------------------------------------
# Container fallback
# ------------------------------------------------------------------------------
# Every non-system directory at the top of / is mounted at its own path, so the
# container sees the same paths as the host: arguments, paths inside configs,
# outputs the program decides on, and symlinks between disks all resolve. The
# image keeps its own files under /usr/local, which these mounts never cover.

bp_host_mounts() {
    local entry name
    BP_MOUNTS=()
    for entry in /*; do
        name="${entry#/}"
        [[ -d "$entry" && ! -L "$entry" ]] || continue
        [[ " ${BP_SYSTEM_DIRS[*]} " == *" $name "* || "$name" == *.usr-is-merged ]] && continue
        BP_MOUNTS+=("$entry")
    done
    # Removable drives live under /run on some distributions.
    [[ -d /run/media ]] && BP_MOUNTS+=(/run/media)
    return 0
}

# bp_run_in_container <arg>...
bp_run_in_container() {
    bp_host_mounts
    local -a mount_flags=()
    local m workdir=/
    for m in "${BP_MOUNTS[@]}"; do
        mount_flags+=(-v "$m:$m")
        [[ "$PWD" == "$m" || "$PWD" == "$m"/* ]] && workdir="$PWD"
    done

    if [[ -n "${BAGPIPE_DEBUG:-}" ]]; then
        bp_dim "[debug] mounts: ${BP_MOUNTS[*]}"
        bp_dim "[debug] workdir: $workdir"
        bp_dim "[debug] command: $*"
    fi

    CURRENT_UID="$(id -u)" CURRENT_GID="$(id -g)" \
    BAGPIPE_IMAGE_TAG="${BAGPIPE_IMAGE_TAG:-latest}" \
    docker compose -f "$BP_COMPOSE" run --rm \
        "${mount_flags[@]}" \
        -w "$workdir" \
        -e HOME="$HOME" \
        -e PYTHONPATH="$BP_REPO/src" \
        bagpipe \
        "$@"
}

bp_image_ref() {
    printf 'ghcr.io/comrob/bagpipe:%s\n' "${BAGPIPE_IMAGE_TAG:-latest}"
}
