#!/bin/bash
# bagpipe convert -- ROS1 .bag -> ROS2 .mcap
#
# Arguments are forwarded to `python -m bagpipe convert` verbatim. Because host paths are
# mounted at their own paths inside the container, no rewriting is needed and
# this wrapper does not need to know the converter's flag list.

bp_cmd_convert() {
    if (($# == 0)); then
        printf 'Usage: bagpipe convert <input> [options]\n' >&2
        printf "Run 'bagpipe convert --help' for the full option list.\n" >&2
        return 1
    fi

    bp_compute_mounts "$@" || return 1
    bp_run_in_container python3 -m bagpipe convert "$@"
}

# bagpipe extract -- copy selected topics out of a large .mcap.
# Usually `bagpipe extract configs/extract/NAME.yaml`; paths inside the config
# are mounted like paths on the command line (see bp_compute_mounts).
bp_cmd_extract() {
    if (($# == 0)); then
        printf 'Usage: bagpipe extract <config.yaml> | <src.mcap> <out.mcap> --topics ...\n' >&2
        printf "Run 'bagpipe extract --help' for the full option list.\n" >&2
        return 1
    fi

    bp_compute_mounts "$@" || return 1
    bp_run_in_container python3 -m bagpipe extract "$@"
}

bp_cmd_shell() {
    bp_compute_mounts "$@" || return 1
    bp_run_in_container bash "$@"
}
