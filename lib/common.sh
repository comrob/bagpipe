#!/bin/bash
# Shared helpers for the bagpipe wrapper.
# Sourced by bagpipe.sh; not meant to be executed directly.

BP_REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
BP_COMPOSE="$BP_REPO/docker-compose.yml"
BP_STATE="${XDG_CACHE_HOME:-$HOME/.cache}/bagpipe"
BP_VERSION="$(cat "$BP_REPO/VERSION" 2>/dev/null || echo "unknown")"

# A bind mount is only dangerous when it shadows something the container
# actually needs, so the two lists differ in how far down they reach.
#
# Trees: these and everything under them are load-bearing in the image.
# Mounting /usr/lib over the container's own /usr/lib breaks it outright.
BP_FORBIDDEN_TREES=(
    /bin /boot /dev /etc /lib /lib32 /lib64 /libx32 /proc /sbin /sys /usr
    /home/dev
)
# Exact only: the directory itself matters, but its children do not exist in
# the image, so real data locations like /media/<user>/disk, /run/media/...,
# /opt/datasets and /home/<user> stay usable.
BP_FORBIDDEN_EXACT=(
    / /home /media /mnt /opt /root /run /srv /tmp /var
)

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
# Mount discovery
# ------------------------------------------------------------------------------
# Host paths are bind-mounted into the container at their own path, so container
# paths and host paths are identical and user arguments need no rewriting at all.

bp_is_forbidden() {
    local candidate="$1" bad
    for bad in "${BP_FORBIDDEN_TREES[@]}"; do
        [[ "$candidate" == "$bad" || "$candidate" == "$bad"/* ]] && return 0
    done
    for bad in "${BP_FORBIDDEN_EXACT[@]}"; do
        [[ "$candidate" == "$bad" ]] && return 0
    done
    return 1
}

# Resolve one argument to the directory that should be mounted for it.
# Prints nothing when the argument is not a filesystem path.
bp_mount_for_arg() {
    local raw="$1"

    # Ignore flags, but look inside --flag=value for a path.
    if [[ "$raw" == -* ]]; then
        [[ "$raw" != *=* ]] && return 0
        raw="${raw#*=}"
        [[ -z "$raw" ]] && return 0
    fi

    local abs
    abs="$(realpath -m -- "$raw" 2>/dev/null)" || return 0
    [[ -z "$abs" ]] && return 0

    # A file is mounted through its parent so siblings/outputs are writable.
    if [[ -f "$abs" ]]; then
        abs="$(dirname -- "$abs")"
    fi

    # Walk up to the nearest directory that actually exists. Reaching / means
    # nothing in the argument exists on disk, so it was never a path --
    # this is what keeps topic names like /tf_static from being mounted.
    local anc="$abs"
    while [[ ! -d "$anc" ]]; do
        local parent
        parent="$(dirname -- "$anc")"
        [[ "$parent" == "$anc" ]] && break
        anc="$parent"
    done
    [[ "$anc" == "/" ]] && return 0

    printf '%s\n' "$anc"
}

# Populates the BP_MOUNTS array from the given arguments plus $PWD.
# Nested paths collapse into their ancestor; forbidden targets abort.
bp_compute_mounts() {
    local -a raw=()
    local arg m

    for arg in "$@"; do
        m="$(bp_mount_for_arg "$arg")"
        [[ -n "$m" ]] && raw+=("$m")
    done

    # $PWD is mounted so relative arguments and the working directory resolve.
    BP_PWD_MOUNTED=false
    if ! bp_is_forbidden "$PWD"; then
        raw+=("$PWD")
        BP_PWD_MOUNTED=true
    fi

    if ((${#raw[@]} == 0)); then
        bp_err "no usable host path found among the arguments."
        return 1
    fi

    # Shortest paths first, so ancestors are seen before their children.
    local -a sorted=()
    mapfile -t sorted < <(printf '%s\n' "${raw[@]}" | awk '{print length"\t"$0}' | sort -n | cut -f2-)

    BP_MOUNTS=()
    local cand keep existing
    for cand in "${sorted[@]}"; do
        keep=true
        for existing in "${BP_MOUNTS[@]}"; do
            if [[ "$cand" == "$existing" || "$cand" == "$existing"/* ]]; then
                keep=false
                break
            fi
        done
        $keep && BP_MOUNTS+=("$cand")
    done

    for cand in "${BP_MOUNTS[@]}"; do
        if bp_is_forbidden "$cand"; then
            bp_err "refusing to mount ${BP_BOLD}$cand${BP_OFF} -- it would shadow the container's own filesystem."
            bp_err "move the data into a subdirectory, or pass a more specific path."
            return 1
        fi
    done
    return 0
}

# ------------------------------------------------------------------------------
# Container invocation
# ------------------------------------------------------------------------------

# bp_run_in_container <arg>...   -- mounts are taken from BP_MOUNTS.
bp_run_in_container() {
    local -a mount_flags=()
    local m
    for m in "${BP_MOUNTS[@]}"; do
        mount_flags+=(-v "$m:$m")
    done

    local workdir="/"
    [[ "$BP_PWD_MOUNTED" == true ]] && workdir="$PWD"

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
        converter \
        "$@"
}

bp_image_ref() {
    printf 'ghcr.io/comrob/ros_bag_converter:%s\n' "${BAGPIPE_IMAGE_TAG:-latest}"
}
