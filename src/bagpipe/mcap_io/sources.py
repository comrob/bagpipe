"""
Message sources for copying: an indexed MCAP read with parallel, coalesced
reads (for network shares), and an earlier extract read locally.

Messages in uncompressed chunks are read on their own. A compressed chunk can
only be decompressed whole, so it is read in full the first time a plan entry
needs it and kept until the last entry that needs it has been copied.
"""
import os
import queue
import threading
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import lz4.frame
import numpy as np
import zstandard
from mcap.reader import make_reader

from .index import MSG_HEADER, records_start


def read_sizes(plan: np.ndarray, chunks: list, field: str = "compressed_size") -> np.ndarray:
    """
    Bytes each plan entry costs to fetch: its own length in an uncompressed
    chunk; for a compressed chunk, the whole chunk (`field` of its index),
    counted at the first entry that needs it.
    """
    size = plan["length"].astype(np.int64)
    if not len(plan):
        return size
    idx = np.flatnonzero(compressed_flags(chunks)[plan["chunk"]])
    size[idx] = 0
    if len(idx):
        _, first = np.unique(plan["chunk"][idx], return_index=True)
        first = idx[first]
        size[first] = [getattr(chunks[c], field) for c in plan["chunk"][first]]
    return size


def compressed_flags(chunks: list) -> np.ndarray:
    return np.array([bool(ci.compression) for ci in chunks] or [False])


def batches(cost: np.ndarray, batch_bytes: int):
    """(start, end) ranges of the plan, each costing about `batch_bytes` to fetch."""
    start, acc = 0, 0
    for i, n in enumerate(cost):
        acc += int(n)
        if acc >= batch_bytes:
            yield start, i + 1
            start, acc = i + 1, 0
    if start < len(cost):
        yield start, len(cost)


def read_ranges(fd: int, ranges: list, pool: ThreadPoolExecutor, max_gap: int = 256 * 1024) -> list:
    """Reads (offset, length) ranges, merging nearby ones into one read; a memoryview per range."""
    order = sorted(range(len(ranges)), key=lambda i: ranges[i][0])
    spans = []  # [start, end, [range indexes]]
    for i in order:
        off, n = ranges[i]
        if spans and off - spans[-1][1] <= max_gap:
            spans[-1][1] = max(spans[-1][1], off + n)
            spans[-1][2].append(i)
        else:
            spans.append([off, off + n, [i]])
    datas = pool.map(lambda s: os.pread(fd, s[1] - s[0], s[0]), spans)
    out = [None] * len(ranges)
    for (s0, _, idx), data in zip(spans, datas):
        mv = memoryview(data)
        for i in idx:
            off, n = ranges[i]
            out[i] = mv[off - s0:off - s0 + n]
    return out


def decompress(ci, data) -> bytes:
    if ci.compression == "zstd":
        out = zstandard.ZstdDecompressor().decompress(data, max_output_size=ci.uncompressed_size)
    else:
        out = lz4.frame.decompress(data)
    if len(out) != ci.uncompressed_size:
        raise RuntimeError(f"chunk at offset {ci.chunk_start_offset} decompressed to {len(out)} bytes, "
                           f"expected {ci.uncompressed_size}")
    return out


def fetch(fd: int, batch: np.ndarray, chunks: list, cache: dict, pool: ThreadPoolExecutor):
    """Every message of a batch, as memoryviews; decompressed chunks are added to `cache`."""
    comp = compressed_flags(chunks)[batch["chunk"]]
    direct = np.flatnonzero(~comp)
    # Membership tests only: the consumer evicts from `cache` while this runs
    need = [c for c in np.unique(batch["chunk"][comp]).tolist() if c not in cache]
    ranges = [(int(batch["offset"][i]), int(batch["length"][i])) for i in direct]
    ranges += [(records_start(chunks[c]), chunks[c].compressed_size) for c in need]
    views = read_ranges(fd, ranges, pool)
    out = [None] * len(batch)
    for i, v in zip(direct, views):
        out[i] = v
    for c, data in zip(need, pool.map(lambda cv: decompress(chunks[cv[0]], cv[1]), zip(need, views[len(direct):]))):
        cache[c] = data
    for i in np.flatnonzero(comp):
        off = int(batch["offset"][i])
        out[i] = memoryview(cache[int(batch["chunk"][i])])[off:off + int(batch["length"][i])]
    return out


def remote_messages(fd: int, plan: np.ndarray, chunks: list, batch_bytes: int, pool: ThreadPoolExecutor):
    """Yields (log_time, sequence, publish_time, data) from SRC in plan order, one batch prefetched ahead."""
    comp = np.flatnonzero(compressed_flags(chunks)[plan["chunk"]]) if len(plan) else np.array([], dtype=int)
    last_use = np.full(len(chunks), -1)
    np.maximum.at(last_use, plan["chunk"][comp], comp)
    cache = {}  # chunk -> decompressed records
    with ThreadPoolExecutor(1) as prefetch:
        it = batches(read_sizes(plan, chunks, "uncompressed_size"), batch_bytes)
        nxt = next(it, None)
        pending = prefetch.submit(fetch, fd, plan[slice(*nxt)], chunks, cache, pool) if nxt else None
        while pending is not None:
            (start, end), datas = nxt, pending.result()
            nxt = next(it, None)
            pending = prefetch.submit(fetch, fd, plan[slice(*nxt)], chunks, cache, pool) if nxt else None
            batch = plan[start:end]
            for rec, data in zip(batch, datas):
                op, ln, cid, seq, log_time, pub_time = MSG_HEADER.unpack_from(data)
                if op != 0x05 or cid != rec["channel"] or log_time != rec["log_time"]:
                    raise RuntimeError(f"unexpected record at offset {rec['offset']} of chunk {rec['chunk']}")
                yield log_time, seq, pub_time, bytes(data[MSG_HEADER.size:])
            del datas
            for c in np.unique(batch["chunk"]).tolist():
                if c in cache and last_use[c] < end:
                    del cache[c]


class LocalSource:
    """Messages of some topics of an earlier extract, decoded in a background thread."""

    def __init__(self, path: Path, topics: list, start_time: int, end_time: int):
        self.path = path
        self.buffered = defaultdict(deque)  # topic -> (log_time, sequence, publish_time, data)
        self._q: queue.Queue = queue.Queue(maxsize=64)
        self._done = object()

        def produce():
            try:
                with open(path, "rb") as f:
                    for _, ch, m in make_reader(f).iter_messages(topics=topics, start_time=start_time,
                                                                 end_time=end_time + 1, log_time_order=True):
                        self._q.put((ch.topic, (m.log_time, m.sequence, m.publish_time, m.data)))
                self._q.put(self._done)
            except BaseException as e:  # surfaced in take()
                self._q.put(e)

        threading.Thread(target=produce, daemon=True).start()

    def take(self, topic: str, log_time: int):
        """The next message of `topic`, which must be logged at `log_time`."""
        buf = self.buffered[topic]
        while True:
            # Messages before the requested one are outside the reused range
            while buf and buf[0][0] < log_time:
                buf.popleft()
            if buf:
                break
            item = self._q.get()
            if item is self._done:
                raise RuntimeError(f"{self.path.name} ended before {topic} at {log_time}")
            if isinstance(item, BaseException):
                raise item
            self.buffered[item[0]].append(item[1])
        if buf[0][0] != log_time:
            raise RuntimeError(f"{self.path.name}: {topic} has no message at {log_time}")
        return buf.popleft()
