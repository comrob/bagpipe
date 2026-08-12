#!/bin/bash
# bagpipe -- containerized pipelines over ROS bags.
#
# Usage: bagpipe <command> [args...]
# Run `bagpipe help` for the command list.

set -uo pipefail

BP_LIB="$(cd -- "$(dirname -- "$(realpath "${BASH_SOURCE[0]}")")/lib" && pwd)"

# shellcheck source=lib/common.sh
source "$BP_LIB/common.sh"
# shellcheck source=lib/update.sh
source "$BP_LIB/update.sh"
# shellcheck source=lib/cmd_convert.sh
source "$BP_LIB/cmd_convert.sh"
# shellcheck source=lib/cmd_info.sh
source "$BP_LIB/cmd_info.sh"
# shellcheck source=lib/cmd_plugins.sh
source "$BP_LIB/cmd_plugins.sh"

bp_usage() {
    cat <<EOF
${BP_BOLD}bagpipe${BP_OFF} $BP_VERSION -- pipelines over ROS bags

${BP_BOLD}Usage:${BP_OFF}
  bagpipe <command> [args...]

${BP_BOLD}Commands:${BP_OFF}
  convert <input> [opts]   Convert ROS1 .bag files to ROS2 .mcap
  plugins                  List available plugins and their configuration
  shell                    Open a shell in the container, in the current directory
  info                     Show version, paths, image and update status
  update                   Pull the latest code and container image
  help                     Show this message

${BP_BOLD}Planned:${BP_OFF}
  run <pipeline> <input>   Run a named bag-to-bag pipeline
  inspect <bag>            Summarize topics, types and duration

${BP_BOLD}Environment:${BP_OFF}
  BAGPIPE_NO_UPDATE_CHECK=1   Disable the update notice
  BAGPIPE_IMAGE_TAG=<tag>     Use a specific container image tag
  BAGPIPE_DEBUG=1             Print the computed mounts and command
EOF
}

bp_planned() {
    bp_err "'bagpipe $1' is not implemented yet."
    bp_dim "The name is reserved; see the roadmap in README.md."
    return 1
}

main() {
    # Invoked through the legacy `convert_bag` symlink, the verb is implied.
    # Handling it here rather than in a wrapper script means the compatibility
    # name is just a symlink to this file.
    if [[ "$(basename -- "$0")" == "convert_bag" ]]; then
        if [[ -t 2 && -z "${BAGPIPE_NO_DEPRECATION:-}" ]]; then
            bp_dim "note: convert_bag is now \`bagpipe convert\`; this alias still works."
        fi
        set -- convert "$@"
    fi

    if (($# == 0)); then
        bp_usage
        return 1
    fi

    local cmd="$1"
    shift

    # A pending notice is printed before the command runs; the check that
    # produced it ran in the background during an earlier invocation.
    case "$cmd" in
        update|help|-h|--help) : ;;
        *) bp_show_update_notice ;;
    esac

    local status=0
    case "$cmd" in
        convert)        bp_cmd_convert "$@" || status=$? ;;
        plugins)        bp_cmd_plugins "$@" || status=$? ;;
        shell)          bp_cmd_shell "$@" || status=$? ;;
        info)           bp_cmd_info "$@" || status=$? ;;
        update)         bp_cmd_update "$@" || status=$? ;;
        run|inspect)    bp_planned "$cmd" || status=$? ;;
        help|-h|--help) bp_usage ;;
        --version)      printf 'bagpipe %s\n' "$BP_VERSION" ;;
        *)
            bp_err "unknown command: $cmd"
            bp_dim "Run 'bagpipe help' for the command list."
            status=1
            ;;
    esac

    # Schedules the next remote comparison. Detached: never blocks this run.
    case "$cmd" in
        update) : ;;
        *) bp_maybe_refresh_notice ;;
    esac

    return $status
}

main "$@"
