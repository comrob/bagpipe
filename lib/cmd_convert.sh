#!/bin/bash
# bagpipe convert -- ROS1 .bag -> ROS2 .mcap
#
# Arguments are forwarded to src/convert.py verbatim. Because host paths are
# mounted at their own paths inside the container, no rewriting is needed and
# this wrapper does not need to know the converter's flag list.

bp_cmd_convert() {
    if (($# == 0)); then
        printf 'Usage: bagpipe convert <input> [options]\n' >&2
        printf "Run 'bagpipe convert --help' for the full option list.\n" >&2
        return 1
    fi

    bp_compute_mounts "$@" || return 1
    bp_run_in_container python3 /home/dev/src/convert.py "$@"
}

bp_cmd_shell() {
    bp_compute_mounts "$@" || return 1
    bp_run_in_container bash "$@"
}
