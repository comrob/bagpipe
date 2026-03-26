import sys
import os
import argparse
import traceback
import time
from pathlib import Path
from typing import List, Dict, Optional, Any

from tqdm import tqdm

# ROS / MCAP
from rosbags.rosbag1 import Reader
from rosbags.typesys import get_types_from_msg, get_typestore, Stores
from mcap.well_known import SchemaEncoding, MessageEncoding, Profile

# Local Modules
import convert_utils
import writer as mcap_writer_utils
from plugin_manager import PluginManager
from std_defs import get_std_def

# --- Business Logic ---
class BagSeriesConverter:
    # [MODIFIED] Added skip_topics argument
    def __init__(self, ros_distro="humble", enable_plugins=False, skip_topics: List[str] = None):
        self.ros_distro = ros_distro
        self.skip_topics = set(skip_topics) if skip_topics else set()  # Store as set for speed
        self.static_tf_cache: List[Any] = []
        self.tf_static_def: Optional[str] = None 
        
        self.store_ros1 = None
        self.store_ros2 = None
        self.exiter = convert_utils.GracefulExiter()
        
        self.current_topic_errors = {}
        
        self.plugin_dir = Path(__file__).parent / "plugins"
        self.plugin_config_path = None
        if enable_plugins:
            self.plugin_config_path = Path(__file__).parent / "plugins.yaml"
        
        self.plugin_manager = PluginManager(self.plugin_dir, self.plugin_config_path)

    def init_stores(self):
        print(f"[INFO] Initializing Stores...")
        self.store_ros1 = get_typestore(Stores.ROS1_NOETIC)
        self.store_ros2 = get_typestore(Stores.ROS2_HUMBLE)

    def convert_file(self, src: Path, base_dst: Path, split_size: int = 0, is_part_of_series: bool = False) -> List[Dict]:
        if not self.store_ros1: self.init_stores()

        print(f"[INFO] Processing: {src.name}")
        self.current_topic_errors = {} # Reset errors for this file

        tf_type_name = "tf2_msgs/msg/TFMessage"
        all_file_stats = []

        with Reader(src) as reader:
            # NOTE: Writer now handles the .part temporary naming internally
            writer = mcap_writer_utils.RollingMcapWriter(base_dst, split_size, Profile.ROS2)
            
            current_stats = {
                "source_bag": src.name, 
                "filename": writer.current_file_path.name, # This will be .part initially
                "output_path": writer.current_file_path, 
                "start_time": None, "end_time": None,
                "message_count": 0, "topics": {}
            }
            
            # ... [Initialize maps... Same as before] ...
            topic_to_chan_id = {}
            conn_map = {}
            type_map = {}
            registered_schemas = {}

            try:
                # --- PASS 1 --- 
                for conn in reader.connections:
                    if self.exiter.stop_requested: break
                    
                    # [MODIFIED] Check blacklist immediately
                    if conn.topic in self.skip_topics:
                        continue

                    ros1_type = conn.msgtype
                    ros2_type = convert_utils.to_ros2_type(ros1_type)
                    
                    is_standard_ros1 = False
                    try:
                        if self.store_ros1.types.get(ros1_type): is_standard_ros1 = True
                    except: pass

                    if not is_standard_ros1:
                        msg_def = None
                        if hasattr(conn, 'msgdef'): msg_def = conn.msgdef
                        elif hasattr(conn, 'ext'):
                             if isinstance(conn.ext, dict): msg_def = conn.ext.get('message_definition', None)
                             elif hasattr(conn.ext, 'message_definition'): msg_def = conn.ext.message_definition
                        if not msg_def and conn.msgtype in reader.msgtypes: msg_def = reader.msgtypes[conn.msgtype]
                        
                        if msg_def:
                            try: self.store_ros1.register(get_types_from_msg(msg_def, ros1_type))
                            except Exception: pass

                            fixed_def = convert_utils.fix_ros1_def(msg_def, ros2_type)
                            try:
                                types_v2 = get_types_from_msg(fixed_def, ros2_type)
                                self.store_ros2.register(types_v2)
                            except Exception as e:
                                if "std_msgs" not in ros1_type and "geometry_msgs" not in ros1_type:
                                    print(f"  [WARN] Type register fail {ros2_type}: {e}")

                    msg_def_for_schema = ""
                    try:
                         if hasattr(conn, 'msgdef'): msg_def_for_schema = conn.msgdef
                    except: pass
                    
                    if not msg_def_for_schema:
                         msg_def_for_schema = f"# {ros2_type}"

                    fixed_def_for_schema = convert_utils.fix_ros1_def(msg_def_for_schema, ros2_type)

                    if ros2_type not in registered_schemas:
                        sid = writer.register_schema(name=ros2_type, encoding=SchemaEncoding.ROS2, data=fixed_def_for_schema.encode('utf-8'))
                        registered_schemas[ros2_type] = sid
                        if conn.topic == convert_utils.TF_STATIC_TOPIC:
                            self.tf_static_def = fixed_def_for_schema
                    
                    if conn.topic not in topic_to_chan_id:
                        meta = {}
                        if conn.topic == convert_utils.TF_STATIC_TOPIC:
                            meta["offered_qos_profiles"] = convert_utils.TF_STATIC_QOS_POLICY
                        
                        cid = writer.register_channel(
                            topic=conn.topic,
                            message_encoding=MessageEncoding.CDR,
                            schema_id=registered_schemas[ros2_type],
                            metadata=meta
                        )
                        topic_to_chan_id[conn.topic] = cid
                        current_stats["topics"][conn.topic] = {"count": 0, "type": ros2_type}

                    conn_map[conn.id] = topic_to_chan_id[conn.topic]
                    type_map[conn.id] = ros2_type

                # --- INJECTION LOGIC ---
                if is_part_of_series and self.static_tf_cache and not self.exiter.stop_requested:
                    # [MODIFIED] Ensure static TF injection respects skip list too
                    if convert_utils.TF_STATIC_TOPIC not in self.skip_topics:
                        chan_id = 0
                        schema_id_to_use = 0
                        if tf_type_name in registered_schemas:
                            schema_id_to_use = registered_schemas[tf_type_name]
                        elif self.tf_static_def:
                            schema_id_to_use = writer.register_schema(name=tf_type_name, encoding=SchemaEncoding.ROS2, data=self.tf_static_def.encode('utf-8'))
                            registered_schemas[tf_type_name] = schema_id_to_use

                        if schema_id_to_use != 0:
                            if convert_utils.TF_STATIC_TOPIC in topic_to_chan_id:
                                chan_id = topic_to_chan_id[convert_utils.TF_STATIC_TOPIC]
                            else:
                                meta = {"offered_qos_profiles": convert_utils.TF_STATIC_QOS_POLICY}
                                chan_id = writer.register_channel(topic=convert_utils.TF_STATIC_TOPIC, message_encoding=MessageEncoding.CDR, schema_id=schema_id_to_use, metadata=meta)
                                topic_to_chan_id[convert_utils.TF_STATIC_TOPIC] = chan_id
                                current_stats["topics"][convert_utils.TF_STATIC_TOPIC] = {"count": 0, "type": tf_type_name}
                        
                        if chan_id != 0:
                            start_time = reader.start_time if reader.start_time else 0
                            from rosbags.typesys.stores.ros2_humble import builtin_interfaces__msg__Time as Time
                            for ros2_msg in self.static_tf_cache:
                                if hasattr(ros2_msg, 'transforms'):
                                    for t in ros2_msg.transforms:
                                        t.header.stamp = Time(sec=int(start_time/1e9), nanosec=int(start_time%1e9))
                                cdr_bytes = self.store_ros2.serialize_cdr(ros2_msg, tf_type_name)
                                writer.add_message(channel_id=chan_id, log_time=int(start_time), publish_time=int(start_time), data=cdr_bytes)
                                current_stats["topics"][convert_utils.TF_STATIC_TOPIC]["count"] += 1
                                current_stats["message_count"] += 1

                # --- PASS 2: CONVERSION ---
                start_ns = reader.start_time or 0
                end_ns = reader.end_time or 0
                total_duration_sec = (end_ns - start_ns) / 1e9
                last_ts = start_ns
                
                # Context manager logic (same as original)
                if total_duration_sec <= 0.001:
                     context_manager = convert_utils.NoOpContextManager()
                else:
                     context_manager = tqdm(total=total_duration_sec, unit="s", desc="Converting", leave=False, bar_format="{l_bar}{bar}| {n:.2f}/{total:.2f}s [{elapsed}<{remaining}, {rate_fmt}]")

                with context_manager as pbar:
                    for conn, timestamp, raw_data in reader.messages():
                        if self.exiter.stop_requested: break
                        if conn.id not in conn_map: continue
                        
                        # Safe update
                        if hasattr(pbar, 'update'):
                            step = (timestamp - last_ts) / 1e9
                            if step > 0:
                                pbar.update(step)
                                last_ts = timestamp
                        
                        if writer.just_rotated:
                            if current_stats["start_time"] is None: current_stats["start_time"] = 0
                            if current_stats["end_time"] is None: current_stats["end_time"] = 0
                            current_stats["duration"] = current_stats["end_time"] - current_stats["start_time"]
                            all_file_stats.append(current_stats)
                            current_stats = { 
                                "source_bag": src.name, 
                                "filename": writer.current_file_path.name,
                                "output_path": writer.current_file_path, 
                                "start_time": timestamp, "end_time": timestamp, 
                                "message_count": 0, "topics": {} 
                            }
                            writer.just_rotated = False

                        ros2_t = type_map[conn.id]
                        try:
                            ros1_msg = self.store_ros1.deserialize_ros1(raw_data, conn.msgtype)
                            ros2_msg = convert_utils.convert_ros1_to_ros2(ros1_msg, ros2_t, self.store_ros2)
                            
                            emissions = self.plugin_manager.run_plugins(conn.topic, ros2_msg, ros2_t, timestamp)
                            
                            for (out_topic, out_msg, out_type, out_def) in emissions:
                                if out_msg is None: continue
                                
                                # [MODIFIED] Check blacklist for plugin emissions
                                if out_topic in self.skip_topics: continue

                                if out_topic not in topic_to_chan_id:
                                    if out_type not in registered_schemas:
                                        schema_text = out_def
                                        if schema_text is None: schema_text = get_std_def(out_type)
                                        if schema_text is None: schema_text = f"# Definition for {out_type} missing"

                                        sid = writer.register_schema(name=out_type, encoding=SchemaEncoding.ROS2, data=schema_text.encode('utf-8'))
                                        registered_schemas[out_type] = sid
                                    
                                    cid = writer.register_channel(topic=out_topic, message_encoding=MessageEncoding.CDR, schema_id=registered_schemas[out_type], metadata={"offered_qos_profiles": ""})
                                    topic_to_chan_id[out_topic] = cid
                                    current_stats["topics"][out_topic] = {"count": 0, "type": out_type}

                                cdr_bytes = self.store_ros2.serialize_cdr(out_msg, out_type)

                                writer.add_message(channel_id=topic_to_chan_id[out_topic], log_time=int(timestamp), publish_time=int(timestamp), data=cdr_bytes)

                                if out_topic == convert_utils.TF_STATIC_TOPIC: self.static_tf_cache.append(out_msg)

                                if out_topic in current_stats["topics"]:
                                    current_stats["topics"][out_topic]["count"] += 1
                                else:
                                    current_stats["topics"][out_topic] = {"count": 1, "type": out_type}
                                
                                if out_topic == conn.topic: current_stats["message_count"] += 1
                                    
                                if current_stats["start_time"] is None or timestamp < current_stats["start_time"]: current_stats["start_time"] = timestamp
                                if current_stats["end_time"] is None or timestamp > current_stats["end_time"]: current_stats["end_time"] = timestamp
                        
                        except Exception as e:
                            print(f"[ERR] Conversion failed on topic {conn.topic}: {e}", file=sys.stderr)
                            err_msg = str(e)
                            if conn.topic not in self.current_topic_errors:
                                self.current_topic_errors[conn.topic] = []
                            self.current_topic_errors[conn.topic].append(err_msg)

                print(f"  [SUCCESS] {current_stats.get('message_count', 0)} messages converted.")

            finally:
                # [NEW RENAMING LOGIC]
                # Determine success based on whether stop was requested
                was_successful = not self.exiter.stop_requested
                
                # This renames .part -> .mcap OR .part -> .mcap.incomplete
                final_paths = writer.finalize_filenames(success=was_successful)
                
                # Update stats with the REAL final filenames
                if current_stats["message_count"] > 0:
                     if current_stats["start_time"] is None: current_stats["start_time"] = 0
                     if current_stats["end_time"] is None: current_stats["end_time"] = 0
                     current_stats["duration"] = current_stats["end_time"] - current_stats["start_time"]
                     all_file_stats.append(current_stats)

                # Fix paths in stats objects
                for i, stat in enumerate(all_file_stats):
                    if i < len(final_paths):
                        stat["output_path"] = final_paths[i]
                        stat["filename"] = final_paths[i].name

        return all_file_stats

        return all_file_stats

def main():
    parser = argparse.ArgumentParser(description="Convert ROS1 bags to MCAP.")
    parser.add_argument("inputs", nargs='+', help="Input .bag files or a folder")
    parser.add_argument("--series", action="store_true", help="Treat inputs as a split sequence")
    parser.add_argument("--distro", default="humble", help="ROS distro for metadata")
    parser.add_argument("-o", "--out-dir", type=Path, default=None, help="Force a specific output directory")
    parser.add_argument("--split-size", default=None, help="Split output files by size (e.g., 3G, 500M)")
    parser.add_argument("--with-plugins", action="store_true", help="Enable custom processing plugins from plugins.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Validate paths and config without running conversion")
    # [MODIFIED] Added argument for skipping topics
    parser.add_argument("--skip-topics", nargs='+', default=[], help="List of topics to skip (blacklist)")

    args = parser.parse_args()
    
    
    # print arguments for verification
    print(f"Input Paths: {args.inputs}")
    print(f"Series Mode: {args.series}")
    print(f"ROS Distro: {args.distro}")
    print(f"Output Directory: {args.out_dir}")
    print(f"Split Size: {args.split_size}")
    print(f"Enable Plugins: {args.with_plugins}")
    print(f"Dry Run: {args.dry_run}")
    print(f"Topics to Skip: {args.skip_topics}")
    
    try:
        split_bytes = mcap_writer_utils.parse_size(args.split_size)
    except ValueError as e:
        print(f"[ERR] {e}")
        sys.exit(1)

    input_paths = [Path(p) for p in args.inputs]
    bag_files = []
    
    if len(input_paths) == 1 and input_paths[0].is_dir():
        root_folder = input_paths[0]
        bag_files = sorted(list(root_folder.glob("*.bag")))
        print(f"[INFO] Detected folder input: {root_folder} ({len(bag_files)} bags)")
    else:
        bag_files = sorted([p for p in input_paths if p.suffix == ".bag"])

    if not bag_files:
        print("[ERR] No .bag files found.")
        sys.exit(1)

    output_dir = args.out_dir
    if output_dir is None:
        first_file = bag_files[0]
        if args.series:
            stem = first_file.stem
            if len(input_paths) == 1 and input_paths[0].is_dir():
                parent_name = first_file.parent.name
                output_dir = first_file.parent.parent / f"{parent_name}_mcap"
            else:
                output_dir = first_file.parent / f"{stem}_series_mcap"
        else:
            output_dir = None 
    
    if args.dry_run:
        print("Dry run finished.")
        sys.exit(0)

    print(f"[INFO] Processing {len(bag_files)} files. Series Mode: {args.series}")
    
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        print(f"[INFO] Output Directory: {output_dir}")
    
    # [MODIFIED] Passed skip_topics to converter
    converter = BagSeriesConverter(ros_distro=args.distro, enable_plugins=args.with_plugins, skip_topics=args.skip_topics)
    valid_stats = []

    all_topic_errors = {}

    for f in bag_files:
        if converter.exiter.stop_requested: break

        if output_dir:
            dst = output_dir / f.with_suffix(".mcap").name
        else:
            dst = f.with_suffix(".mcap")
        
        stats_list = converter.convert_file(
            f, dst, 
            split_size=split_bytes, 
            is_part_of_series=args.series
        )
        
        # [NEW] Collect errors
        if converter.current_topic_errors:
             for topic, errs in converter.current_topic_errors.items():
                 if topic not in all_topic_errors: all_topic_errors[topic] = []
                 all_topic_errors[topic].extend(errs)

        if stats_list:
            valid_stats.extend(stats_list)
        
        if not args.series:
            converter.static_tf_cache.clear()
            converter.tf_static_def = None

    # [NEW EXIT LOGIC]
    if converter.exiter.stop_requested:
        print(f"\n[WARN] Process interrupted. Marked files as .incomplete.")
        # Write the summary (which includes the INTERRUPTED status)
        convert_utils.print_and_save_summary(
            valid_stats, output_dir, 
            interrupted=True, 
            topic_errors=all_topic_errors
        )
        # Skip metadata.yaml
        sys.exit(130)

    # Success path
    if output_dir and valid_stats:
        convert_utils.write_metadata_yaml(output_dir, valid_stats, args.distro)
    
    convert_utils.print_and_save_summary(
        valid_stats, output_dir, 
        interrupted=False, 
        topic_errors=all_topic_errors
    )

if __name__ == "__main__":
    main()