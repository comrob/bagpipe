"""
Reading MCAP indexes: which messages of which channels sit where, without
touching message data. Works over slow network shares because it reads only
the small message-index records that follow each chunk.
"""
import os
import struct
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

PLAN_DTYPE = np.dtype([("log_time", "<u8"), ("offset", "<u8"), ("length", "<u4"), ("channel", "<u2")])
MSG_HEADER = struct.Struct("<BQHIQQ")  # opcode, record length, channel, sequence, log_time, publish_time


def records_start(ci) -> int:
    """File offset of the first record inside an uncompressed chunk."""
    return ci.chunk_start_offset + 9 + 8 + 8 + 8 + 4 + 4 + len(ci.compression) + 8


def build_plan(fd: int, chunk_indexes, wanted: set, start_ns: int, end_ns: int, threads: int) -> np.ndarray:
    chunks = [ci for ci in chunk_indexes
              if wanted & ci.message_index_offsets.keys()
              and ci.message_end_time >= start_ns and ci.message_start_time <= end_ns]
    if any(ci.compression for ci in chunks):
        sys.exit("[ERR] source chunks are compressed; this tool relies on uncompressed chunks")

    def plan_chunk(ci):
        buf = os.pread(fd, ci.message_index_length, ci.chunk_start_offset + ci.chunk_length)
        pos, entries = 0, []
        while pos + 9 <= len(buf):
            _, ln = struct.unpack_from("<BQ", buf, pos)
            cid, alen = struct.unpack_from("<HI", buf, pos + 9)
            for i in range(alen // 16):
                t, off = struct.unpack_from("<QQ", buf, pos + 15 + 16 * i)
                entries.append((off, t, cid))
            pos += 9 + ln
        entries.sort()
        base = records_start(ci)
        ends = [e[0] for e in entries[1:]] + [ci.uncompressed_size]
        return [(t, base + off, end - off, cid) for (off, t, cid), end in zip(entries, ends)
                if cid in wanted and start_ns <= t <= end_ns]

    rows, t0 = [], time.time()
    with ThreadPoolExecutor(threads) as pool:
        for i, part in enumerate(pool.map(plan_chunk, chunks), 1):
            rows.extend(part)
            if i % 5000 == 0 or i == len(chunks):
                print(f"\r[PLAN] {i}/{len(chunks)} chunk indexes read ({time.time() - t0:.0f}s)", end="", flush=True)
    print()
    plan = np.array(rows, dtype=PLAN_DTYPE)
    plan.sort(order=["log_time", "offset"])
    return plan


def index_times(path: Path, summary) -> dict:
    """channel id -> sorted log times, read from the message indexes (no decompression)."""
    times = defaultdict(list)
    with open(path, "rb") as f:
        for ci in summary.chunk_indexes:
            f.seek(ci.chunk_start_offset + ci.chunk_length)
            buf = f.read(ci.message_index_length)
            pos = 0
            while pos + 9 <= len(buf):
                _, ln = struct.unpack_from("<BQ", buf, pos)
                cid, alen = struct.unpack_from("<HI", buf, pos + 9)
                times[cid].extend(t for t, _ in struct.iter_unpack("<QQ", buf[pos + 15:pos + 15 + alen]))
                pos += 9 + ln
    return {cid: np.sort(np.array(t, dtype=np.uint64)) for cid, t in times.items()}
