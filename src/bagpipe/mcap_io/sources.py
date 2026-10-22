"""
Message sources for copying: an uncompressed indexed MCAP read with parallel,
coalesced reads (for network shares), and an earlier extract read locally.
"""
import os
import queue
import threading
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from mcap.reader import make_reader

from .index import MSG_HEADER

def batches(plan: np.ndarray, batch_bytes: int):
    start, acc = 0, 0
    for i, n in enumerate(plan["length"]):
        acc += int(n)
        if acc >= batch_bytes:
            yield plan[start:i + 1]
            start, acc = i + 1, 0
    if start < len(plan):
        yield plan[start:]


def fetch(fd: int, batch: np.ndarray, pool: ThreadPoolExecutor, max_gap: int = 256 * 1024):
    """Reads every message of a batch, merging nearby ones into one read."""
    order = np.argsort(batch["offset"], kind="stable")
    spans = []  # [start, end, [batch indexes]]
    for i in order:
        off, n = int(batch["offset"][i]), int(batch["length"][i])
        if spans and off - spans[-1][1] <= max_gap:
            spans[-1][1] = max(spans[-1][1], off + n)
            spans[-1][2].append(i)
        else:
            spans.append([off, off + n, [i]])
    datas = pool.map(lambda s: os.pread(fd, s[1] - s[0], s[0]), spans)
    out = [None] * len(batch)
    for (s0, _, idx), data in zip(spans, datas):
        mv = memoryview(data)
        for i in idx:
            off = int(batch["offset"][i]) - s0
            out[i] = mv[off:off + int(batch["length"][i])]
    return out


def remote_messages(fd: int, plan: np.ndarray, batch_bytes: int, pool: ThreadPoolExecutor):
    """Yields (log_time, sequence, publish_time, data) from SRC in plan order, one batch prefetched ahead."""
    with ThreadPoolExecutor(1) as prefetch:
        it = batches(plan, batch_bytes)
        nxt = next(it, None)
        pending = prefetch.submit(fetch, fd, nxt, pool) if nxt is not None else None
        while pending is not None:
            batch, datas = nxt, pending.result()
            nxt = next(it, None)
            pending = prefetch.submit(fetch, fd, nxt, pool) if nxt is not None else None
            for rec, data in zip(batch, datas):
                op, ln, cid, seq, log_time, pub_time = MSG_HEADER.unpack_from(data)
                if op != 0x05 or cid != rec["channel"] or log_time != rec["log_time"]:
                    raise RuntimeError(f"unexpected record at offset {rec['offset']}")
                yield log_time, seq, pub_time, bytes(data[MSG_HEADER.size:])


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
