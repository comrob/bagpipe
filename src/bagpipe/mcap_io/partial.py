"""
Reading MCAP files whose writer never finished (killed, crashed, or stopped
with Ctrl+C), so that they can be cut back to their intact part and appended to.
"""
import struct
from collections import Counter
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Optional, List, Dict

from mcap.data_stream import ReadDataStream
from mcap.opcode import Opcode
from mcap.records import Channel, Chunk, ChunkIndex, Message, MessageIndex, Schema
from mcap.stream_reader import breakup_chunk
from mcap.writer import Writer as McapWriter, MCAP0_MAGIC

@dataclass
class PartialMcap:
    """The intact prefix of an MCAP file whose writer did not finish."""
    path: Path
    keep_bytes: int = 0       # everything before this offset is intact
    complete: bool = False    # the writer reached DataEnd (finished file)
    schemas: Dict[int, Schema] = field(default_factory=dict)
    channels: Dict[int, Channel] = field(default_factory=dict)
    chunk_indexes: List[ChunkIndex] = field(default_factory=list)
    channel_message_counts: Counter = field(default_factory=Counter)
    start_time: Optional[int] = None
    end_time: Optional[int] = None
    # channel_id -> messages logged exactly at end_time
    end_time_counts: Counter = field(default_factory=Counter)
    # channel_id -> log time of its last message
    channel_end_times: Dict[int, int] = field(default_factory=dict)

    @property
    def message_count(self) -> int:
        return sum(self.channel_message_counts.values())

def scan_partial_mcap(path: Path) -> PartialMcap:
    """
    Walks the data section of an MCAP that may be cut off at any byte.

    Our writers emit the data section as groups of [Chunk, MessageIndex...],
    possibly with Schema and Channel records between them. A group is kept only
    when the chunk passes its CRC and all of its message indexes were read, so
    the file can be truncated at keep_bytes and appended to.
    """
    partial = PartialMcap(path)
    with open(path, "rb") as f:
        if f.read(len(MCAP0_MAGIC)) != MCAP0_MAGIC:
            return partial
        partial.keep_bytes = f.tell()

        group = None  # (chunk, chunk_index, records, channel ids, indexes) awaiting its message indexes
        while True:
            offset = f.tell()
            head = f.read(9)
            if len(head) < 9:
                break
            opcode, length = struct.unpack("<BQ", head)
            body = f.read(length)
            if len(body) < length:
                break
            stream = ReadDataStream(BytesIO(body))

            if opcode == Opcode.MESSAGE_INDEX and group is not None:
                chunk, chunk_index, records, channel_ids, indexes = group
                index = MessageIndex.read(stream)
                chunk_index.message_index_offsets[index.channel_id] = offset
                indexes.append(index)
                if len(indexes) == len(channel_ids):
                    chunk_index.message_index_length = f.tell() - (chunk_index.chunk_start_offset + chunk_index.chunk_length)
                    _commit_group(partial, chunk, chunk_index, records, indexes)
                    partial.keep_bytes = f.tell()
                    group = None
                continue
            if group is not None:
                break  # a chunk whose message indexes were never written
            if opcode == Opcode.SCHEMA:  # outside chunks, as TopicChunkedWriter writes them
                schema = Schema.read(stream)
                partial.schemas[schema.id] = schema
                partial.keep_bytes = f.tell()
                continue
            if opcode == Opcode.CHANNEL:
                channel = Channel.read(stream)
                partial.channels[channel.id] = channel
                partial.keep_bytes = f.tell()
                continue
            if opcode == Opcode.HEADER:
                partial.keep_bytes = f.tell()
                continue
            if opcode == Opcode.DATA_END:
                partial.complete = True
                break
            if opcode != Opcode.CHUNK:
                break

            chunk = Chunk.read(stream)
            try:
                records = breakup_chunk(chunk, validate_crc=True)
            except Exception:
                break
            channel_ids = {r.channel_id for r in records if isinstance(r, Message)}
            if not channel_ids:
                break
            chunk_index = ChunkIndex(
                message_start_time=chunk.message_start_time,
                message_end_time=chunk.message_end_time,
                chunk_start_offset=offset,
                chunk_length=f.tell() - offset,
                message_index_offsets={},
                message_index_length=0,
                compression=chunk.compression,
                compressed_size=len(chunk.data),
                uncompressed_size=chunk.uncompressed_size,
            )
            group = (chunk, chunk_index, records, channel_ids, [])

    return partial

def _commit_group(partial: PartialMcap, chunk: Chunk, chunk_index: ChunkIndex, records: list, indexes: List[MessageIndex]):
    for r in records:
        if isinstance(r, Schema):
            partial.schemas[r.id] = r
        elif isinstance(r, Channel):
            partial.channels[r.id] = r
    partial.chunk_indexes.append(chunk_index)

    if partial.start_time is None or chunk.message_start_time < partial.start_time:
        partial.start_time = chunk.message_start_time
    if partial.end_time is None or chunk.message_end_time > partial.end_time:
        partial.end_time = chunk.message_end_time
        partial.end_time_counts.clear()

    for index in indexes:
        partial.channel_message_counts[index.channel_id] += len(index.records)
        if index.records:
            last = max(t for t, _ in index.records)
            partial.channel_end_times[index.channel_id] = max(partial.channel_end_times.get(index.channel_id, last), last)
        if chunk.message_end_time == partial.end_time:
            partial.end_time_counts[index.channel_id] += sum(1 for t, _ in index.records if t == partial.end_time)

def restore_index(writer: McapWriter, partial: PartialMcap):
    """Teaches a fresh writer about the chunks already in the file, so its summary covers them."""
    try:
        chunk_indexes = writer._Writer__chunk_indices
        stats = writer._Writer__statistics
    except AttributeError:
        raise RuntimeError("this version of the mcap library cannot be resumed into")

    chunk_indexes.extend(partial.chunk_indexes)
    stats.chunk_count += len(partial.chunk_indexes)
    if partial.chunk_indexes:
        stats.message_count += partial.message_count
        for cid, n in partial.channel_message_counts.items():
            stats.channel_message_counts[cid] += n
        stats.message_start_time = partial.start_time
        stats.message_end_time = partial.end_time
