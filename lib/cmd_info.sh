#!/bin/bash
# bagpipe info -- what this install is, where it lives, and whether it is current.

_bp_count_plugins() {
    local dir="$1" n=0 file
    [[ -d "$dir" ]] || { printf '0\n'; return; }
    for file in "$dir"/*.py; do
        [[ -e "$file" ]] || continue
        [[ "$(basename "$file")" == __* ]] && continue
        n=$((n + $(grep -cE '^class\s+\w+\(\s*BasePlugin' "$file" 2>/dev/null || echo 0)))
    done
    printf '%s\n' "$n"
}

_bp_update_status() {
    if [[ -n "${BAGPIPE_NO_UPDATE_CHECK:-}" ]]; then
        printf 'checking disabled (BAGPIPE_NO_UPDATE_CHECK)\n'
        return
    fi

    local when="never checked"
    if [[ -f "$BP_CHECKED" ]]; then
        local age=$(( $(date +%s) - $(stat -c %Y "$BP_CHECKED" 2>/dev/null || echo 0) ))
        if   (( age < 3600 ));  then when="checked $((age / 60))m ago"
        elif (( age < 86400 )); then when="checked $((age / 3600))h ago"
        else                         when="checked $((age / 86400))d ago"
        fi
    fi

    if [[ -s "$BP_NOTICE" ]]; then
        local notice_head
        IFS= read -r notice_head < "$BP_NOTICE"
        if [[ "$notice_head" == "$(git -C "$BP_REPO" rev-parse HEAD 2>/dev/null)" ]]; then
            printf '%supdate available%s -- run: %sbagpipe update%s (%s)\n' \
                "$BP_YELLOW" "$BP_OFF" "$BP_BOLD" "$BP_OFF" "$when"
            return
        fi
    fi
    printf 'up to date (%s)\n' "$when"
}

bp_cmd_info() {
    local self_link commit dirty image_ref image_id image_created

    self_link="$(command -v bagpipe 2>/dev/null || echo '(not on PATH)')"
    if [[ -L "$self_link" ]]; then
        self_link="$self_link -> $(readlink -f "$self_link")"
    fi

    if git -C "$BP_REPO" rev-parse --git-dir >/dev/null 2>&1; then
        commit="$(git -C "$BP_REPO" rev-parse --abbrev-ref HEAD) @ $(git -C "$BP_REPO" log -1 --format='%h %cs')"
        [[ -n "$(git -C "$BP_REPO" status --porcelain 2>/dev/null)" ]] && dirty=", dirty" || dirty=", clean"
    else
        commit="(not a git checkout)"
        dirty=""
    fi

    image_ref="$(bp_image_ref)"
    image_id="$(docker image inspect --format '{{index .RepoDigests 0}}' "$image_ref" 2>/dev/null)"
    [[ -z "$image_id" ]] && image_id="$(docker image inspect --format '{{.Id}}' "$image_ref" 2>/dev/null)"
    [[ -z "$image_id" ]] && image_id="(not pulled yet -- run: bagpipe update)"
    image_created="$(docker image inspect --format '{{.Created}}' "$image_ref" 2>/dev/null | cut -dT -f1)"
    [[ -n "$image_created" ]] && image_created=" created $image_created"

    printf '%sbagpipe %s%s\n' "$BP_BOLD" "$BP_VERSION" "$BP_OFF"
    printf '  wrapper    %s\n' "$self_link"
    printf '  checkout   %s  (%s%s)\n' "$BP_REPO" "$commit" "$dirty"
    printf '  image      %s\n' "$image_ref"
    printf '             %s%s\n' "$image_id" "$image_created"
    printf '  plugins    %s user, %s system\n' \
        "$(_bp_count_plugins "$BP_REPO/src/plugins")" \
        "$(_bp_count_plugins "$BP_REPO/src/system_plugins")"
    printf '  state      %s\n' "$BP_STATE"
    printf '  updates    %s\n' "$(_bp_update_status)"
}
