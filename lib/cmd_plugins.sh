#!/bin/bash
# bagpipe plugins -- list discovered plugins and whether they are configured.

_bp_list_plugin_dir() {
    local dir="$1" yaml="$2" label="$3"
    local file class configured

    printf '%s\n' "${BP_BOLD}$label${BP_OFF}"
    if [[ ! -d "$dir" ]]; then
        printf '  (directory not found: %s)\n' "$dir"
        return
    fi

    local found=false
    for file in "$dir"/*.py; do
        [[ -e "$file" ]] || continue
        [[ "$(basename "$file")" == __* ]] && continue
        while read -r class; do
            [[ -z "$class" ]] && continue
            found=true
            configured="not in $(basename "$yaml")"
            if grep -qE "^[[:space:]]+${class}:" "$yaml" 2>/dev/null; then
                configured="configured"
            fi
            printf '  %-28s %-22s %s\n' "$class" "$(basename "$file")" "${BP_DIM}$configured${BP_OFF}"
        done < <(grep -oP '^class\s+\K\w+(?=\s*\(\s*BasePlugin)' "$file" 2>/dev/null)
    done
    $found || printf '  (none)\n'
}

bp_cmd_plugins() {
    _bp_list_plugin_dir "$BP_REPO/src/system_plugins" \
                        "$BP_REPO/src/system_plugins.yaml" \
                        "System plugins (always on)"
    printf '\n'
    _bp_list_plugin_dir "$BP_REPO/src/plugins" \
                        "$BP_REPO/src/plugins.yaml" \
                        "User plugins (need --with-plugins)"
    printf '\n%s\n' "${BP_DIM}Configure in src/plugins.yaml. Edits apply immediately -- src/ is mounted.${BP_OFF}"
}
