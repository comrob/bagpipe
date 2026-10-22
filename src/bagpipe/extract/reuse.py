"""Deciding which messages can come from earlier extracts instead of the source."""
import numpy as np
from mcap.reader import make_reader

from ..mcap_io.index import index_times

def assign_reuse(paths: list, summary, wanted: set, plan: np.ndarray, start_ns: int, end_ns: int):
    """
    Decides, per plan entry, where it is read from: 0 = SRC, k = paths[k-1].

    A topic is taken from a file over the time range the file covers, if its
    timestamps there equal the plan's exactly. A file cut off mid-timestamp
    (e.g. an .incomplete one) is retried without the timestamps at its two ends.
    """
    source = np.zeros(len(plan), dtype=np.uint8)
    by_topic = {summary.channels[cid].topic: cid for cid in wanted}
    for k, path in enumerate(paths, 1):
        with open(path, "rb") as f:
            rs = make_reader(f).get_summary()
        times = index_times(path, rs)
        for rcid, rch in sorted(rs.channels.items()):
            cid = by_topic.get(rch.topic)
            if cid is None:
                continue
            src_schema, schema = summary.schemas[summary.channels[cid].schema_id], rs.schemas[rch.schema_id]
            if (schema.name, schema.data) != (src_schema.name, src_schema.data):
                print(f"[REUSE] {rch.topic}: schema in {path.name} differs from the source; not reused")
                continue
            rt = times.get(rcid, np.array([], dtype=np.uint64))
            rt = rt[(rt >= start_ns) & (rt <= end_ns)]
            if not len(rt):
                continue
            sel = np.flatnonzero(plan["channel"] == cid)
            pt = plan["log_time"][sel]
            lo, hi = rt[0], rt[-1]
            for inner in (False, True):
                if inner:
                    r, m = rt[(rt > lo) & (rt < hi)], (pt > lo) & (pt < hi)
                else:
                    r, m = rt, (pt >= lo) & (pt <= hi)
                if len(r) and np.array_equal(pt[m], r):
                    break
            else:
                print(f"[REUSE] {rch.topic}: timestamps in {path.name} do not match the source; not reused")
                continue
            idx = sel[m]
            idx = idx[source[idx] == 0]  # earlier --reuse files take precedence
            if not len(idx):
                continue
            source[idx] = k
            print(f"[REUSE] {rch.topic}: {len(idx)}/{len(sel)} messages from {path.name}")
    return source
