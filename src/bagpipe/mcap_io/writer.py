import re
from collections import Counter
from pathlib import Path
from typing import Optional, List, Dict, Tuple

from mcap.writer import ChunkBuilder, Writer as McapWriter

from .partial import PartialMcap, scan_partial_mcap, restore_index

def parse_size(size_str: Optional[str]) -> int:
    """Converts human readable size (3G, 500M) to bytes. Returns 0 if None."""
    if not size_str:
        return 0
    units = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
    match = re.match(r'^(\d+)([KMGT]?)$', size_str.upper())
    if match:
        val, unit = match.groups()
        return int(val) * units.get(unit, 1)
    raise ValueError(f"Invalid size format: {size_str}")

class TopicChunkedWriter(McapWriter):
    """
    An MCAP writer that keeps one open chunk per channel instead of one shared
    chunk, so every chunk holds a single topic. A reader after a few topics
    then decompresses only their chunks, and data of one kind is compressed
    together. The file is standard MCAP: indexed readers merge the overlapping
    chunks by log time. Schema and channel records go into the data section
    ahead of the chunks, so a file cut short still describes every channel.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._builders: Dict[int, ChunkBuilder] = {}
        # Without a current chunk, the base class writes schema and channel
        # records straight into the data section
        self._Writer__chunk_builder = None

    def add_message(self, channel_id, log_time, data, publish_time, sequence=0):
        builder = self._builders.get(channel_id)
        if builder is None:
            builder = self._builders[channel_id] = ChunkBuilder()
        # The base class writes into whichever builder is current
        self._Writer__chunk_builder = builder
        super().add_message(channel_id=channel_id, log_time=log_time, data=data,
                            publish_time=publish_time, sequence=sequence)
        self._Writer__chunk_builder = None

    def finish(self):
        for cid in sorted(self._builders):
            self._Writer__chunk_builder = self._builders[cid]
            self._Writer__finalize_chunk()
        self._Writer__chunk_builder = None
        super().finish()


class RollingMcapWriter:
    def __init__(self, base_path: Path, split_size: int, profile: str, resume: bool = False,
                 topic_chunks: bool = False):
        self.base_path = base_path
        self.split_size = split_size
        self.profile = profile
        # One chunk stream per topic (TopicChunkedWriter) instead of mixed chunks
        self.writer_class = TopicChunkedWriter if topic_chunks else McapWriter
        self.part_num = 0

        # Track all generated file segments for final renaming
        self.generated_segments: List[Path] = []

        # State preservation for rotation, keyed by id so every segment
        # (and a resumed file) re-registers them under the same ids
        self.schemas: Dict[int, Tuple[str, str, bytes]] = {}
        self.channels: Dict[int, Tuple[str, str, int, Dict[str, str]]] = {}

        # Current active writer state
        self.writer = None
        self._f = None
        self.current_file_path = None # This will be the .part path

        # Signal that a rotation happened
        self.just_rotated = False

        # One entry per segment when continuing an interrupted conversion
        self.resumed: List[PartialMcap] = []
        if resume:
            try:
                self._resume()
            except Exception as e:
                print(f"[WARN] Cannot continue the partial output ({e}); converting from scratch.")
                if self._f:
                    self._f.close()
                self.writer, self._f = None, None
                self.schemas.clear()
                self.channels.clear()

        if not self.resumed:
            self._open_new_segment()

        # Reset flag after initial open
        self.just_rotated = False

    def _final_path(self, part_num: int) -> Path:
        """The target name of a segment (e.g. "data.mcap" or "data_1.mcap")."""
        if part_num == 0:
            return self.base_path
        return self.base_path.parent / f"{self.base_path.stem}_{part_num}.mcap"

    def _part_path(self, part_num: int) -> Path:
        # e.g., data.mcap -> data.mcap.part
        final_name = self._final_path(part_num)
        return final_name.with_suffix(final_name.suffix + ".part")

    def _open_new_segment(self):
        """Closes current and opens next file segment."""
        if self.writer:
            self.writer.finish()
            self._f.close()

        # ALWAYS write to a .part file initially
        self.current_file_path = self._part_path(self.part_num)
        self.generated_segments.append(self.current_file_path)

        self._f = open(self.current_file_path, "wb")
        self.writer = self.writer_class(self._f)
        self.writer.start(profile=self.profile)

        self._register_known()

        self.part_num += 1
        self.just_rotated = True

    def _register_known(self):
        """Re-registers every known schema and channel in the current writer, under the same ids."""
        for sid, (name, enc, data) in self.schemas.items():
            if self.writer.register_schema(name=name, encoding=enc, data=data) != sid:
                raise RuntimeError(f"schema ids are not contiguous at {sid}")
        for cid, (topic, enc, schema_id, meta) in self.channels.items():
            if self.writer.register_channel(topic=topic, message_encoding=enc, schema_id=schema_id, metadata=meta) != cid:
                raise RuntimeError(f"channel ids are not contiguous at {cid}")

    def _find_leftover_segments(self) -> List[Path]:
        """
        Segments left by an earlier run of this conversion: .part after a crash
        or kill, .incomplete after Ctrl+C. The latter are renamed back to .part.
        """
        parts = []
        while self._part_path(len(parts)).exists():
            parts.append(self._part_path(len(parts)))
        if parts:
            return parts

        def incomplete_path(i: int) -> Path:
            # finalize_filenames() names the first of several segments "_0"
            final_name = self.base_path.parent / f"{self.base_path.stem}_0.mcap" if i == 0 else self._final_path(i)
            return final_name.with_suffix(final_name.suffix + ".incomplete")

        single = self.base_path.with_suffix(self.base_path.suffix + ".incomplete")
        if single.exists():
            incomplete = [single]
        else:
            incomplete = []
            while incomplete_path(len(incomplete)).exists():
                incomplete.append(incomplete_path(len(incomplete)))

        for i, path in enumerate(incomplete):
            print(f"[INFO] Renaming {path.name} -> {self._part_path(i).name} to continue it.")
            path.rename(self._part_path(i))
        return [self._part_path(i) for i in range(len(incomplete))]

    def _resume(self):
        """Opens the last leftover segment for appending, after its last intact chunk."""
        paths = self._find_leftover_segments()
        if not paths:
            return

        segments = []
        for path in paths:
            print(f"[INFO] Found partial output {path.name} ({path.stat().st_size / 1024**3:.1f} GiB), checking which data is intact...")
            segments.append(scan_partial_mcap(path))

        if not segments[0].chunk_indexes:
            print(f"[INFO] {paths[0].name} holds no complete data yet.")
            return
        if not all(s.complete for s in segments[:-1]):
            raise RuntimeError("an earlier segment is damaged")

        last = segments[-1]
        for s in segments:
            for sid, schema in sorted(s.schemas.items()):
                self.schemas.setdefault(sid, (schema.name, schema.encoding, schema.data))
            for cid, channel in sorted(s.channels.items()):
                self.channels.setdefault(cid, (channel.topic, channel.message_encoding, channel.schema_id, channel.metadata))

        self._f = open(last.path, "r+b")
        self.writer = self.writer_class(self._f)
        # Buffered into the next chunk; repeating identical records under the same ids is valid MCAP
        self._register_known()
        restore_index(self.writer, last)

        dropped = last.path.stat().st_size - last.keep_bytes
        self._f.truncate(last.keep_bytes)
        self._f.seek(last.keep_bytes)
        print(f"[INFO] Keeping {sum(s.message_count for s in segments)} messages, "
              f"discarding {dropped / 1024**2:.1f} MiB of unfinished data at the end of {last.path.name}.")

        self.generated_segments = list(paths)
        self.current_file_path = paths[-1]
        self.part_num = len(paths)
        self.resumed = segments

    def resume_point(self) -> Tuple[int, Counter]:
        """
        The bag time to continue from, and per topic how many messages logged at
        exactly that time are already written (a chunk can end partway through them).
        """
        end_time = max(s.end_time for s in self.resumed if s.end_time is not None)
        written = Counter()
        for s in self.resumed:
            if s.end_time == end_time:
                for cid, n in s.end_time_counts.items():
                    written[self.channels[cid][0]] += n
        return end_time, written

    def resumed_counts(self) -> Dict[str, Tuple[int, int]]:
        """Per topic: messages already written, and the log time of the last one."""
        counts = {}
        for s in self.resumed:
            for cid, n in s.channel_message_counts.items():
                topic = self.channels[cid][0]
                prev_n, prev_t = counts.get(topic, (0, 0))
                counts[topic] = (prev_n + n, max(prev_t, s.channel_end_times[cid]))
        return counts

    def resumed_stats(self) -> List[Dict]:
        """Per-segment stats of the data kept from the interrupted run."""
        stats = []
        for path, s in zip(self.generated_segments, self.resumed):
            topics = {}
            for cid, channel in s.channels.items():
                topics[channel.topic] = {"count": s.channel_message_counts[cid], "type": self.schemas[channel.schema_id][0]}
            stats.append({
                "filename": path.name,
                "output_path": path,
                "start_time": s.start_time, "end_time": s.end_time,
                "duration": (s.end_time or 0) - (s.start_time or 0),
                "message_count": s.message_count, "topics": topics,
            })
        return stats

    def register_schema(self, name, encoding, data):
        # Identical definitions share an id: a resumed run registers everything again
        definition = (name, encoding, data)
        for sid, known in self.schemas.items():
            if known == definition:
                return sid
        sid = self.writer.register_schema(name=name, encoding=encoding, data=data)
        self.schemas[sid] = definition
        return sid

    def register_channel(self, topic, message_encoding, schema_id, metadata):
        definition = (topic, message_encoding, schema_id, metadata)
        for cid, known in self.channels.items():
            if known == definition:
                return cid
        cid = self.writer.register_channel(topic=topic, message_encoding=message_encoding, schema_id=schema_id, metadata=metadata)
        self.channels[cid] = definition
        return cid

    def add_message(self, channel_id, log_time, publish_time, data, sequence=0):
        if self.split_size > 0 and self._f.tell() > self.split_size:
            self._open_new_segment()
            
        self.writer.add_message(
            channel_id=channel_id, 
            log_time=log_time, 
            data=data, 
            publish_time=publish_time,
            sequence=sequence
        )

    def finish(self):
        if self.writer:
            self.writer.finish()
            self._f.close()

    def finalize_filenames(self, success: bool = True) -> List[Path]:
        """
        Renames all generated .part files to their final state.
        If success=True:  .mcap.part -> .mcap
        If success=False: .mcap.part -> .mcap.incomplete
        
        [NEW] If more than one file exists, the first file is renamed to _0.mcap
        """
        # Ensure everything is closed first
        self.finish()

        final_paths = []
        
        # Detect if we have a sequence (more than 1 file generated)
        is_sequence = len(self.generated_segments) > 1
        
        for i, temp_path in enumerate(self.generated_segments):
            if not temp_path.exists():
                continue
                
            # temp_path is like "data.mcap.part"
            # Remove ".part" to get the base "data.mcap"
            base_name = temp_path.with_suffix("") 
            
            # --- LOGIC CHANGE START ---
            # If this is the first file (index 0) AND it is part of a sequence,
            # rename "data.mcap" to "data_0.mcap"
            if i == 0 and is_sequence:
                stem = base_name.stem
                parent = base_name.parent
                base_name = parent / f"{stem}_0.mcap"
            # --- LOGIC CHANGE END ---

            if success:
                new_path = base_name # "data.mcap" or "data_0.mcap"
            else:
                # Add .incomplete suffix to the base name -> "data.mcap.incomplete"
                new_path = base_name.with_suffix(base_name.suffix + ".incomplete")

            try:
                temp_path.rename(new_path)
                final_paths.append(new_path)
            except OSError as e:
                print(f"[ERR] Failed to rename {temp_path.name}: {e}")
        
        return final_paths