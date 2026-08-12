#!/bin/bash
# Update notice + the `bagpipe update` command.
#
# The invocation that pays the network cost is never the one that prints the
# warning. Foreground work is a single file read plus one `git rev-parse`;
# the comparison against the remote happens in a detached background job whose
# result is consumed by a *later* invocation.

BP_NOTICE="$BP_STATE/update_notice"   # line 1: HEAD it was computed against; rest: message
BP_CHECKED="$BP_STATE/last_check"     # mtime only
BP_CHECK_INTERVAL="${BAGPIPE_CHECK_INTERVAL:-86400}"

bp_upstream_remote() {
    if [[ -n "${BAGPIPE_UPSTREAM:-}" ]]; then
        printf '%s\n' "${BAGPIPE_UPSTREAM:-}"
        return
    fi
    local tracked
    tracked="$(git -C "$BP_REPO" rev-parse --abbrev-ref '@{u}' 2>/dev/null)"
    if [[ -n "$tracked" && "$tracked" == */* ]]; then
        printf '%s\n' "${tracked%%/*}"
        return
    fi
    git -C "$BP_REPO" remote 2>/dev/null | head -n1
}

# Print a pending notice, if one is still valid. Cheap: one read, one rev-parse.
bp_show_update_notice() {
    [[ -n "${BAGPIPE_NO_UPDATE_CHECK:-}" ]] && return 0
    [[ -s "$BP_NOTICE" ]] || return 0

    local notice_head
    IFS= read -r notice_head < "$BP_NOTICE"
    if [[ "$notice_head" == "$(git -C "$BP_REPO" rev-parse HEAD 2>/dev/null)" ]]; then
        tail -n +2 "$BP_NOTICE" >&2
    else
        # HEAD moved since the check -- the notice no longer describes reality.
        rm -f "$BP_NOTICE"
    fi
}

# Fire the background refresh if the cache is stale. Never blocks.
bp_maybe_refresh_notice() {
    [[ -n "${BAGPIPE_NO_UPDATE_CHECK:-}" ]] && return 0
    git -C "$BP_REPO" rev-parse --git-dir >/dev/null 2>&1 || return 0

    local last now
    last="$(stat -c %Y "$BP_CHECKED" 2>/dev/null || echo 0)"
    now="$(date +%s)"
    (( now - last < BP_CHECK_INTERVAL )) && return 0

    ( bp_refresh_notice ) >/dev/null 2>&1 &
    disown 2>/dev/null || true
    return 0
}

# Runs detached. Compares local HEAD against the upstream main branch.
bp_refresh_notice() {
    mkdir -p "$BP_STATE"
    touch "$BP_CHECKED"

    local remote
    remote="$(bp_upstream_remote)"
    [[ -z "$remote" ]] && return 0

    local sha
    sha="$(GIT_TERMINAL_PROMPT=0 \
           GIT_SSH_COMMAND='ssh -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new' \
           timeout 10 git -C "$BP_REPO" ls-remote --heads "$remote" main 2>/dev/null | cut -f1)"

    # Offline, or auth needs a human: keep whatever notice we already had.
    [[ -z "$sha" ]] && return 0

    local head
    head="$(git -C "$BP_REPO" rev-parse HEAD 2>/dev/null)" || return 0

    # Up to date if the remote commit is already contained in our history.
    # Checking containment rather than SHA equality avoids nagging when the
    # user sits on a local branch that already includes main.
    if git -C "$BP_REPO" cat-file -e "${sha}^{commit}" 2>/dev/null \
       && git -C "$BP_REPO" merge-base --is-ancestor "$sha" "$head" 2>/dev/null; then
        rm -f "$BP_NOTICE"
        return 0
    fi

    local behind=""
    behind="$(git -C "$BP_REPO" rev-list --count "$head..$sha" 2>/dev/null)"
    local count_text="new commits are"
    [[ "$behind" == "1" ]] && count_text="1 new commit is"
    [[ -n "$behind" && "$behind" != "1" ]] && count_text="$behind new commits are"

    {
        printf '%s\n' "$head"
        printf '\033[33m!\033[0m bagpipe is out of date (%s available on %s/main).\n' \
            "$count_text" "$remote"
        printf '  update with: \033[1mbagpipe update\033[0m\n'
    } > "$BP_NOTICE.tmp" && mv "$BP_NOTICE.tmp" "$BP_NOTICE"
}

bp_cmd_update() {
    local remote
    remote="$(bp_upstream_remote)"

    if ! git -C "$BP_REPO" rev-parse --git-dir >/dev/null 2>&1; then
        bp_err "$BP_REPO is not a git checkout; cannot self-update."
        return 1
    fi

    if [[ -n "$(git -C "$BP_REPO" status --porcelain 2>/dev/null)" ]]; then
        bp_warn "the checkout has local changes; pulling anyway (fast-forward only)."
    fi

    printf '%s\n' "${BP_BOLD}==>${BP_OFF} Pulling code from ${remote:-origin}/main"
    if ! git -C "$BP_REPO" pull --ff-only "${remote:-origin}" main; then
        bp_err "git pull failed. Resolve the checkout state in $BP_REPO and retry."
        return 1
    fi

    printf '%s\n' "${BP_BOLD}==>${BP_OFF} Pulling container image $(bp_image_ref)"
    if ! BAGPIPE_IMAGE_TAG="${BAGPIPE_IMAGE_TAG:-latest}" \
         docker compose -f "$BP_COMPOSE" pull converter; then
        bp_warn "image pull failed; the previously pulled image is still in place."
    fi

    printf '%s\n' "${BP_BOLD}==>${BP_OFF} Refreshing links and completion"
    "$BP_REPO/scripts/install.sh" --no-pull

    rm -f "$BP_NOTICE" "$BP_CHECKED"
    printf '%s\n' "${BP_BOLD}==>${BP_OFF} Now at $(cat "$BP_REPO/VERSION" 2>/dev/null) ($(git -C "$BP_REPO" rev-parse --short HEAD))"
}
