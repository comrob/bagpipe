"""YAML configs for `bagpipe extract` (see configs/extract/)."""
import argparse
from pathlib import Path

import yaml

KEYS = {"source", "output", "topics", "start", "duration", "reuse"}


def apply_config(ap: argparse.ArgumentParser, args: argparse.Namespace):
    """Fills arguments from a YAML config; anything given on the command line wins."""
    cfg = yaml.safe_load(args.config.read_text()) or {}
    unknown = set(cfg) - KEYS
    if unknown:
        ap.error(f"unknown keys in {args.config}: {sorted(unknown)}")
    topics = cfg.get("topics", [])
    if isinstance(topics, dict):  # grouped: {group: [topics]}
        topics = [t for group in topics.values() for t in group]
    args.src = args.src or (Path(cfg["source"]) if "source" in cfg else None)
    args.out = args.out or (Path(cfg["output"]) if "output" in cfg else None)
    args.topics = args.topics or topics
    if args.start == ap.get_default("start"):
        args.start = float(cfg.get("start", args.start))
    if args.duration is None and cfg.get("duration") is not None:
        args.duration = float(cfg["duration"])
    args.reuse = args.reuse or [Path(r) for r in cfg.get("reuse", [])]
