"""
Entry point for `python -m bagpipe <command> [args...]`.

Commands are imported only when used, so `extract` (mcap, numpy, PyYAML) runs
without the conversion stack (rosbags, tqdm) installed.
"""
import importlib
import sys

from . import __version__

COMMANDS = {
    "convert": ("bagpipe.convert.converter", "Convert ROS1 .bag files to ROS2 .mcap"),
    "extract": ("bagpipe.extract.run", "Copy selected topics out of a large .mcap, resumably"),
}


def usage() -> str:
    lines = [f"bagpipe {__version__}", "", "usage: python -m bagpipe <command> [args...]", "", "commands:"]
    lines += [f"  {name:<9} {help_}" for name, (_, help_) in COMMANDS.items()]
    return "\n".join(lines)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(usage())
        return 0 if argv else 1
    if argv[0] == "--version":
        print(f"bagpipe {__version__}")
        return 0
    command, rest = argv[0], argv[1:]
    if command not in COMMANDS:
        print(f"unknown command: {command}\n\n{usage()}", file=sys.stderr)
        return 1
    module = importlib.import_module(COMMANDS[command][0])
    return module.main(rest) or 0
