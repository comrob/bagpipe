#!/bin/bash
# bagpipe convert / extract / shell
#
# Arguments are forwarded to `python -m bagpipe <command>` verbatim, so this
# wrapper does not need to know any command's flags. Paths need no rewriting:
# the native runtime sees the host filesystem directly, and the container
# fallback mounts host directories at their own paths (see lib/common.sh).

bp_cmd_convert() {
    if (($# == 0)); then
        printf 'Usage: bagpipe convert <input> [options]\n' >&2
        printf "Run 'bagpipe convert --help' for the full option list.\n" >&2
        return 1
    fi
    bp_run_bagpipe convert "$@"
}

# bagpipe extract -- copy selected topics out of a large .mcap.
bp_cmd_extract() {
    if (($# == 0)); then
        printf 'Usage: bagpipe extract <config.yaml> | <src.mcap> <out.mcap> --topics ...\n' >&2
        printf "Run 'bagpipe extract --help' for the full option list.\n" >&2
        return 1
    fi
    bp_run_bagpipe extract "$@"
}

# A shell in the container fallback, with the same mounts as its commands.
bp_cmd_shell() {
    bp_run_in_container bash "$@"
}
