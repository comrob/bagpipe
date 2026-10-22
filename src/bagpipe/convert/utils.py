import sys
import re
import signal
import yaml
import os
import numpy as np
from pathlib import Path
from typing import Any, Optional, List, Dict

# Constants
TF_STATIC_TOPIC = '/tf_static'
TF_STATIC_QOS_POLICY = """
- history: 3
  depth: 1
  reliability: 1
  durability: 1
  deadline: {sec: 2147483647, nsec: 4294967295}
  lifespan: {sec: 2147483647, nsec: 4294967295}
  liveliness: 1
  liveliness_lease_duration: {sec: 2147483647, nsec: 4294967295}
  avoid_ros_namespace_conventions: false
""".strip()

class NoOpContextManager:
    """
    A dummy context manager that mimics tqdm but does nothing.
    Used when the bag file is empty or has invalid duration to prevent crashes.
    """
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        pass

    def update(self, n=1):
        pass

# --- Reporting / Metadata Utils ---

def write_metadata_yaml(folder: Path, stats_list: List[Dict], distro: str):
    """Generates the metadata.yaml required for ROS2 playback."""
    try:
        if not stats_list: return

        print(f"[INFO] Generating metadata.yaml for {len(stats_list)} files...")
        total_messages = 0
        min_start = None
        max_end = None
        merged_topics = {}
        files_data = []

        for s in stats_list:
            if min_start is None or s['start_time'] < min_start: min_start = s['start_time']
            if max_end is None or s['end_time'] > max_end: max_end = s['end_time']
            total_messages += s['message_count']

            for t_name, t_info in s['topics'].items():
                if t_name not in merged_topics:
                    merged_topics[t_name] = t_info.copy()
                else:
                    merged_topics[t_name]['count'] += t_info['count']

            files_data.append({
                "path": s['filename'],
                "starting_time": {"nanoseconds_since_epoch": s['start_time']},
                "duration": {"nanoseconds": s['duration']},
                "message_count": s['message_count']
            })

        duration_ns = (max_end - min_start) if (max_end and min_start) else 0

        topics_list = []
        for t_name, t_data in merged_topics.items():
            topics_list.append({
                "topic_metadata": {
                    "name": t_name,
                    "type": t_data['type'],
                    "serialization_format": "cdr",
                    "offered_qos_profiles": ""
                },
                "message_count": t_data['count']
            })

        metadata = {
            "rosbag2_bagfile_information": {
                "version": 5,
                "storage_identifier": "mcap",
                "ros_distro": distro,
                "relative_file_paths": [f['path'] for f in files_data],
                "duration": {"nanoseconds": duration_ns},
                "starting_time": {"nanoseconds_since_epoch": min_start if min_start else 0},
                "message_count": total_messages,
                "topics_with_message_count": topics_list,
                "compression_format": "",
                "compression_mode": "",
                "files": files_data
            }
        }

        yaml_path = folder / "metadata.yaml"
        with open(yaml_path, "w") as f:
            yaml.dump(metadata, f, default_flow_style=False)
        print(f"[SUCCESS] Metadata written to {yaml_path}")

    except Exception as e:
        print(f"[ERR] Failed to generate metadata.yaml: {e}")
def print_and_save_summary(stats_list: List[Dict], output_folder: Optional[Path], 
                           interrupted: bool = False, 
                           topic_errors: Dict[str, List[str]] = None):
    """
    Prints a table summary and saves detailed stats to YAML.
    Includes explicit error logs and interruption status.
    """
    if not stats_list and not topic_errors:
        return

    host_mount = os.environ.get("HOST_DATA_DIR")
    status_str = "INTERRUPTED" if interrupted else "SUCCESS"
    
    # --- Prepare Data for YAML ---
    summary_data_for_yaml = {
        "status": status_str,
        "total_files": len(stats_list),
        "conversions": [],
        "errors": topic_errors if topic_errors else {}
    }

    # --- Prepare Text Report ---
    lines = []
    lines.append("="*120)
    lines.append(f"{'CONVERSION SUMMARY':^120}")
    lines.append(f"{'Status: ' + status_str:^120}")
    lines.append("="*120)
    header = f"{'Source Input':<25} | {'Output File':<25} | {'Msgs':<10} | {'Full Output Path (Host)':<50}"
    lines.append(header)
    lines.append("-" * 120)

    for s in stats_list:
        src_name = s.get('source_bag', 'Unknown')
        out_name = s.get('filename', 'Unknown')
        msg_count = s.get('message_count', 0)
        
        # Resolve Host Path
        output_path_str = str(s.get('output_path', ''))
        display_path = output_path_str

        if host_mount and output_path_str:
            p = Path(output_path_str)
            try:
                if p.is_absolute():
                     rel = p.relative_to(Path.cwd())
                     final_host_path = Path(host_mount) / rel
                else:
                     final_host_path = Path(host_mount) / p
                display_path = str(final_host_path)
            except ValueError:
                pass 

        lines.append(f"{src_name:<25} | {out_name:<25} | {msg_count:<10} | {display_path}")

        summary_data_for_yaml["conversions"].append({
            "source_input": src_name,
            "output_file": out_name,
            "message_count": msg_count,
            "host_path": display_path,
            "duration_sec": float(s.get("duration", 0)) / 1e9
        })

    lines.append("="*120)

    # --- Append Error Section to Text Report ---
    if topic_errors:
        lines.append("\n" + "!"*120)
        lines.append(f"{'ERROR REPORT':^120}")
        lines.append("!"*120)
        for topic, errs in topic_errors.items():
            lines.append(f"TOPIC: {topic}")
            unique_errors = sorted(list(set(errs)))
            for i, err in enumerate(unique_errors):
                lines.append(f"  - {err}")
                if i >= 4: 
                    lines.append(f"    ... and {len(unique_errors) - 5} other unique errors.")
                    break
        lines.append("!"*120 + "\n")

    # 1. Print to Console
    print("\n".join(lines))

    # 2. Save Files
    if output_folder and output_folder.exists():
        # Save TXT
        txt_path = output_folder / "conversion_summary.txt"
        try:
            with open(txt_path, "w") as f:
                f.write("\n".join(lines))
        except Exception as e:
            print(f"[WARN] Could not save summary TXT: {e}")

        # Save YAML
        yaml_path = output_folder / "conversion_summary.yaml"
        try:
            with open(yaml_path, "w") as f:
                yaml.dump(summary_data_for_yaml, f, sort_keys=False)
            print(f"[INFO] Summaries saved to:\n       - {txt_path}\n       - {yaml_path}")
        except Exception as e:
            print(f"[WARN] Could not save summary YAML: {e}")

# --- Core Utilities ---

class GracefulExiter:
    def __init__(self):
        self.stop_requested = False
        signal.signal(signal.SIGINT, self.request_stop)
        signal.signal(signal.SIGTERM, self.request_stop)

    def request_stop(self, *args):
        if not self.stop_requested:
            print("\n[WARN] Stop requested! Finishing current message and closing file safely...")
            print("[TIP]  Please wait a moment for the footer to write.")
            self.stop_requested = True
        else:
            print("\n[ALERT] Force kill detected. File may be corrupted.")
            sys.exit(1)

def to_ros2_type(ros1_type: str) -> str:
    """
    Converts a ROS1 type string to a ROS2 Python/Internal type string.
    NOTE: This returns the format 'package/msg/Name', which is used for 
    looking up Python classes in 'rosbags'. It is NOT for schema definitions.
    """
    if ros1_type == 'Header': return 'std_msgs/msg/Header'
    if "/msg/" not in ros1_type and "/" in ros1_type:
        parts = ros1_type.split('/')
        if len(parts) == 2:
            return f"{parts[0]}/msg/{parts[1]}"
    return ros1_type

def fix_ros1_def(text: str, type_name: str) -> str:
    """
    Cleans up ROS1 message definitions to be valid ROS2 .msg schemas.
    CRITICAL FIX: This generates the textual definition. It must use
    'package/Name' format (e.g., 'std_msgs/Header'), NOT 'package/msg/Name'.
    """
    # 1. CameraInfo specific field name fixes (ROS1 uses capitalized, ROS2 lower)
    if "CameraInfo" in type_name:
        text = text.replace("float64[] D", "float64[] d")
        text = text.replace("float64[9] K", "float64[9] k")
        text = text.replace("float64[9] R", "float64[9] r")
        text = text.replace("float64[12] P", "float64[12] p")

    lines = text.splitlines()
    fixed_lines = []
    
    for line in lines:
        stripped = line.strip()
        
        # Preserve empty lines
        if not stripped:
            fixed_lines.append(line)
            continue
            
        # Remove sequence IDs (deprecated in ROS2)
        if stripped.startswith("uint32 seq"): 
            continue
            
        # Handle Nested Definitions (MSG: separator)
        if stripped.startswith("MSG:"):
            # FIX: Do NOT inject '/msg/' here. Keep it as Package/Name.
            # We just return the line as is, unless it needs whitespace trimming.
            fixed_lines.append(line)
            continue

        # Parse field definitions: "type name"
        # Regex breakdown: (indent)(type)(whitespace)(name)(comment/rest)
        match = re.match(r'^(\s*)([a-zA-Z0-9_/\[\]]+)(\s+)([^#\s]+)(.*)$', line)
        if match:
            indent, type_str, ws, name_str, rest = match.groups()
            
            # --- Type Mapping Fixes ---
            
            # 1. Handle Built-ins
            # ROS2 .msg files use 'builtin_interfaces/Time', NOT '.../msg/Time'
            if type_str == 'time': 
                type_str = 'builtin_interfaces/Time'
            elif type_str == 'duration': 
                type_str = 'builtin_interfaces/Duration'
            elif type_str == 'Header': 
                type_str = 'std_msgs/Header'
            
            # 2. Handle Arrays (strip [] temporarily to check type)
            base_type = type_str.split('[')[0] 
            suffix = type_str[len(base_type):] # e.g., "[]" or "[9]"

            # 3. Standardize Package/Name format
            # If it looks like 'geometry_msgs/Point', keep it.
            # If the user's previous code injected '/msg/', we would have to remove it,
            # but here we just ensure we don't add it.
            
            # (Self-Correction logic if input was already malformed or weird)
            if '/msg/' in base_type:
                # OPTIONAL: Aggressively fix if input is already bad
                base_type = base_type.replace('/msg/', '/')
            
            # Reconstruct type string
            type_str = f"{base_type}{suffix}"

            # --- Name Mapping Fixes ---
            if "CameraInfo" in type_name:
                if name_str in ['D', 'K', 'R', 'P']: name_str = name_str.lower()

            new_line = f"{indent}{type_str}{ws}{name_str}{rest}"
            fixed_lines.append(new_line)
        else:
            # Pass through comments or unparseable lines
            fixed_lines.append(line)

    return "\n".join(fixed_lines)

def convert_ros1_to_ros2(ros1_obj, ros2_type, ros2_store):
    try:
        MsgType = ros2_store.types[ros2_type]
    except KeyError:
        return ros1_obj

    kwargs = {}
    for field_name, field_desc in MsgType.__dataclass_fields__.items():
        val = getattr(ros1_obj, field_name, None)
        if val is None:
            val = getattr(ros1_obj, field_name.upper(), None)

        if val is None:
            if field_name in ['d', 'k', 'r', 'p', 'binning_x', 'binning_y', 'covariance']:
                size = 0
                if field_name in ['k', 'r', 'covariance']: size = 9
                elif field_name == 'p': size = 12
                val = np.zeros(size, dtype=np.float64)
            else:
                continue

        if isinstance(val, (list, tuple)):
            if field_name in ['d', 'k', 'r', 'p', 'covariance'] or \
               (len(val) > 0 and isinstance(val[0], (int, float))):
                np_val = np.array(val, dtype=np.float64)
                expected_size = 0
                if field_name in ['k', 'r', 'covariance']: expected_size = 9
                elif field_name == 'p': expected_size = 12
                if expected_size > 0 and np_val.size != expected_size:
                    new_arr = np.zeros(expected_size, dtype=np.float64)
                    lim = min(np_val.size, expected_size)
                    new_arr[:lim] = np_val[:lim]
                    val = new_arr
                else:
                    val = np_val

        if hasattr(val, '__msgtype__'):
            sub_type_ros1 = val.__msgtype__
            sub_type_ros2 = to_ros2_type(sub_type_ros1)
            val = convert_ros1_to_ros2(val, sub_type_ros2, ros2_store)
        elif isinstance(val, list):
             if len(val) > 0 and hasattr(val[0], '__msgtype__'):
                 sub_type_ros1 = val[0].__msgtype__
                 sub_type_ros2 = to_ros2_type(sub_type_ros1)
                 val = [convert_ros1_to_ros2(x, sub_type_ros2, ros2_store) for x in val]
        
        if hasattr(val, 'secs') and hasattr(val, 'nsecs'):
             if 'builtin_interfaces/msg/Time' in str(field_desc.type):
                 from rosbags.typesys.stores.ros2_humble import builtin_interfaces__msg__Time as Time
                 val = Time(sec=val.secs, nanosec=val.nsecs)
             elif 'builtin_interfaces/msg/Duration' in str(field_desc.type):
                 from rosbags.typesys.stores.ros2_humble import builtin_interfaces__msg__Duration as Duration
                 val = Duration(sec=val.secs, nanosec=val.nsecs)

        kwargs[field_name] = val
    return MsgType(**kwargs)