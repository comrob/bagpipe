"""
Copy selected topics out of a huge, uncompressed, indexed MCAP into a new
zstd-compressed MCAP, reading only the bytes of the selected messages.

    bagpipe extract SRC.mcap OUT.mcap --topics /tf /tf_static ...
    bagpipe extract SRC.mcap OUT.mcap --topics ... --start 600 --duration 30   # a sample
    bagpipe extract SRC.mcap OUT.mcap --topics ... --reuse OLD_EXTRACT.mcap
    bagpipe extract configs/extract/NAME.yaml                 # see configs/extract/

How it works:
  1. Plan: read each chunk's message index (a few KB per chunk) and list every
     selected message as (log_time, file offset, length). Cached next to the
     output as OUT.mcap.plan.npz, so a restart does not redo it.
  2. Copy: walk the plan in log-time order, fetch messages in batches with
     parallel coalesced reads, and write them through RollingMcapWriter.

Reuse (--reuse OLD.mcap ...): for each topic, an earlier extract of the same
recording is used over the time range it covers -- after checking that its
timestamps there match the plan message for message -- and the rest is read
from SRC. Messages are written in the same order as a plain extraction
(log time, then position in SRC), so the result is identical either way.

Interruptions: output is written to OUT.mcap.part (Ctrl+C leaves a valid
OUT.mcap.incomplete). Re-running the same command continues after the last
intact chunk, using the same resume logic as `bagpipe convert`.
"""
import argparse
import os
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from mcap.reader import make_reader

from ..mcap_io.index import build_plan
from ..mcap_io.sources import LocalSource, remote_messages
from ..mcap_io.writer import RollingMcapWriter
from .config import apply_config
from .reuse import assign_reuse


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bagpipe extract", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", type=Path, nargs="?", help="source .mcap, or a YAML config")
    ap.add_argument("out", type=Path, nargs="?", help="output .mcap")
    ap.add_argument("--config", type=Path, help="YAML with any of: source, output, topics, start, duration, reuse")
    ap.add_argument("--topics", nargs="+")
    ap.add_argument("--start", type=float, default=0.0, help="seconds from the start of the recording")
    ap.add_argument("--duration", type=float, default=None, help="seconds to copy (default: all)")
    ap.add_argument("--threads", type=int, default=16)
    ap.add_argument("--batch-mb", type=int, default=256)
    ap.add_argument("--reuse", nargs="+", type=Path, default=[],
                    help="earlier extracts of the same recording; topics are read from them where they cover the range")
    args = ap.parse_args(argv)
    if args.src and args.src.suffix in (".yaml", ".yml") and not args.config:
        args.config, args.src = args.src, None  # `bagpipe extract CONFIG.yaml`
    if args.config:
        apply_config(ap, args)
    if not (args.src and args.out and args.topics):
        ap.error("source, output and topics are required (as arguments or in --config)")

    stop = {"requested": False}
    def request_stop(*_):
        if stop["requested"]:
            sys.exit(1)
        print("\n[WARN] Stop requested; closing the output cleanly (Ctrl+C again to kill).")
        stop["requested"] = True
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    fd = os.open(args.src, os.O_RDONLY)
    with open(args.src, "rb") as f:
        summary = make_reader(f).get_summary()

    by_topic = {ch.topic: cid for cid, ch in summary.channels.items()}
    missing = [t for t in args.topics if t not in by_topic]
    if missing:
        sys.exit(f"[ERR] topics not in source: {missing}")
    wanted = {by_topic[t] for t in args.topics}

    st = summary.statistics
    start_ns = st.message_start_time + int(args.start * 1e9)
    end_ns = st.message_end_time if args.duration is None else start_ns + int(args.duration * 1e9)

    plan_path = args.out.with_suffix(args.out.suffix + ".plan.npz")
    key = np.array([str(args.src.resolve()), " ".join(sorted(args.topics)), str(start_ns), str(end_ns)])
    plan = None
    if plan_path.exists():
        cached = np.load(plan_path)
        if np.array_equal(cached["key"], key):
            plan = cached["plan"]
            print(f"[PLAN] using cached plan {plan_path.name}")
    if plan is None:
        plan = build_plan(fd, summary.chunk_indexes, wanted, start_ns, end_ns, args.threads)
        np.savez(plan_path, plan=plan, key=key)
    topic_of = {cid: summary.channels[cid].topic for cid in wanted}
    source = assign_reuse(args.reuse, summary, wanted, plan, start_ns, end_ns)
    remote_total = int(plan["length"][source == 0].sum())
    print(f"[PLAN] {len(plan)} messages, {(source > 0).sum()} reused, {remote_total / 1e9:.1f} GB to read from source")

    writer = RollingMcapWriter(args.out, 0, "ros2", resume=True)

    # Schemas and channels in source id order; identical ones are reused on resume
    out_cid = {}
    for cid in sorted(wanted):
        ch = summary.channels[cid]
        sc = summary.schemas[ch.schema_id]
        sid = writer.register_schema(name=sc.name, encoding=sc.encoding, data=sc.data)
        out_cid[cid] = writer.register_channel(topic=ch.topic, message_encoding=ch.message_encoding,
                                               schema_id=sid, metadata=dict(ch.metadata))

    # Skip what an interrupted run already wrote: everything before its last
    # timestamp, plus per topic the messages it wrote at exactly that timestamp
    resume_time = start_ns
    if writer.resumed:
        resume_time, written = writer.resume_point()
        keep = plan["log_time"] >= resume_time
        for i in np.flatnonzero(plan["log_time"] == resume_time):
            topic = topic_of[int(plan["channel"][i])]
            if written[topic] > 0:
                written[topic] -= 1
                keep[i] = False
        plan, source = plan[keep], source[keep]
        print(f"[INFO] Resuming at {(resume_time - start_ns) / 1e9:.0f}s of {(end_ns - start_ns) / 1e9:.0f}s, "
              f"{plan['length'][source == 0].sum() / 1e9:.1f} GB left to read from source")

    t0, last_print = time.time(), 0.0
    remote_read = reused = 0
    span = max(end_ns - start_ns, 1)
    frac0 = (resume_time - start_ns) / span
    with ThreadPoolExecutor(args.threads) as pool:
        remote = remote_messages(fd, plan[source == 0], args.batch_mb << 20, pool)
        local = {k: LocalSource(path, sorted({topic_of[int(c)] for c in plan["channel"][source == k]}), resume_time, end_ns)
                 for k, path in enumerate(args.reuse, 1) if (source == k).any()}
        # Same order as a plain extraction: the plan is sorted by (log time, position in SRC)
        for rec, k in zip(plan, source):
            if stop["requested"]:
                remote.close()  # finish in-flight reads while the pool is still open
                break
            cid, log_time = int(rec["channel"]), int(rec["log_time"])
            if k == 0:
                t, seq, pub_time, data = next(remote)
                remote_read += int(rec["length"])
            else:
                t, seq, pub_time, data = local[k].take(topic_of[cid], log_time)
                reused += 1
            writer.add_message(channel_id=out_cid[cid], log_time=t, publish_time=pub_time, data=data, sequence=seq)
            now = time.time()
            if now - last_print > 10:
                last_print = now
                frac = (log_time - start_ns) / span
                left = (now - t0) / max(frac - frac0, 1e-9) * (1 - frac)
                print(f"[COPY] {100 * frac:5.1f}%  source {remote_read / 1e9:6.1f}/{remote_total / 1e9:.1f} GB  "
                      f"reused {reused} msgs  ~{left / 60:4.0f} min left  "
                      f"out {writer.current_file_path.stat().st_size / 1e9:.1f} GB", flush=True)

    final = writer.finalize_filenames(success=not stop["requested"])
    print(f"[{'DONE' if not stop['requested'] else 'STOPPED'}] {final[0] if final else '-'}")
    if not stop["requested"]:
        plan_path.unlink(missing_ok=True)
    sys.exit(0 if not stop["requested"] else 130)


if __name__ == "__main__":
    main()
