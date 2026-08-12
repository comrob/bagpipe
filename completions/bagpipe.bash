# bash completion for bagpipe (and the legacy convert_bag alias)

_bagpipe_convert_args() {
    local cur="$1" prev="$2"

    case "$prev" in
        -o|--out-dir)
            COMPREPLY=( $(compgen -d -- "$cur") )
            compopt -o filenames 2>/dev/null
            return
            ;;
        --split-size)
            COMPREPLY=( $(compgen -W "500M 1G 2G 3G 4G" -- "$cur") )
            return
            ;;
        --distro)
            COMPREPLY=( $(compgen -W "humble iron jazzy rolling" -- "$cur") )
            return
            ;;
    esac

    if [[ "$cur" == -* ]]; then
        COMPREPLY=( $(compgen -W "--series --distro --out-dir --split-size \
            --with-plugins --dry-run --skip-topics --help" -- "$cur") )
        return
    fi

    # Bags are given as .bag files or as a folder containing them.
    COMPREPLY=( $(compgen -f -X '!*.bag' -- "$cur") $(compgen -d -- "$cur") )
    compopt -o filenames 2>/dev/null
}

_bagpipe() {
    local cur prev cmd
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"

    if (( COMP_CWORD == 1 )); then
        COMPREPLY=( $(compgen -W "convert plugins shell info update help" -- "$cur") )
        return
    fi

    cmd="${COMP_WORDS[1]}"
    case "$cmd" in
        convert)
            _bagpipe_convert_args "$cur" "$prev"
            ;;
        shell)
            COMPREPLY=( $(compgen -d -- "$cur") )
            compopt -o filenames 2>/dev/null
            ;;
        *)
            COMPREPLY=()
            ;;
    esac
}

# The legacy entry point takes convert's arguments directly, with no verb.
_bagpipe_convert_bag() {
    _bagpipe_convert_args "${COMP_WORDS[COMP_CWORD]}" "${COMP_WORDS[COMP_CWORD-1]}"
}

complete -F _bagpipe bagpipe
complete -F _bagpipe_convert_bag convert_bag
