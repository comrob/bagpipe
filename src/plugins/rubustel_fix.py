import sys
import re
import json
from typing import Any, List, Tuple, Optional

# Import ROS2 String type
from rosbags.typesys.stores.ros2_humble import std_msgs__msg__String as StringMsg
from bagpipe.convert.plugin_manager import BasePlugin

class RobustelFixPlugin(BasePlugin):
    def process(self, topic: str, msg: Any, msg_type: str, timestamp: int) -> Tuple[List[Tuple[str, Any, str, Optional[str]]], bool]:
        
        # Default: Keep original message
        emissions = [(topic, msg, msg_type, None)]

        # 1. Filter using config (default to "robustel")
        filter_str = self.config.get('input_filter', 'robustel')
        if filter_str not in topic:
            return emissions, False

        try:
            # 2. Extract Raw Text (Handle Bad Bytes)
            raw_text = ""
            if hasattr(msg, 'data'):
                if isinstance(msg.data, str):
                    raw_text = msg.data
                elif isinstance(msg.data, bytes):
                    raw_text = msg.data.decode('utf-8', errors='ignore')
                else:
                    raw_text = str(msg)
            else:
                raw_text = str(msg)

            # 3. Parse fields using Regex
            parsed_data = {}
            patterns = {
                "rsrp_value": r"rsrp_value\s*=\s*([-+]?\d+(?:\.\d+)?)",
                "network_type": r"network_type\s*=\s*(\w+)",
                "cell_id": r"cell_id\s*=\s*(\d+)",
                "band": r"band\s*=\s*(\d+)",
                "sinr_value": r"sinr_value\s*=\s*([-+]?\d+(?:\.\d+)?)",
                "rsrq_value": r"rsrq_value\s*=\s*([-+]?\d+(?:\.\d+)?)"
            }

            found_any = False
            for key, pattern in patterns.items():
                match = re.search(pattern, raw_text)
                if match:
                    val = match.group(1)
                    if "." in val: parsed_data[key] = float(val)
                    elif val.isdigit() or (val.startswith("-") and val[1:].isdigit()): parsed_data[key] = int(val)
                    else: parsed_data[key] = val
                    found_any = True

            if found_any:
                # 4. Inject Metadata
                parsed_data['timestamp_ns'] = timestamp
                parsed_data['timestamp'] = timestamp / 1e9

                # 5. Create New Message
                json_str = json.dumps(parsed_data)
                new_msg = StringMsg(data=json_str)
                
                # 6. Add to emissions
                out_topic = self.config.get('output_topic', '/robustel/parsed')
                
                # Append new tuple: (Topic, Msg, Type, DefinitionOverride=None)
                emissions.append((out_topic, new_msg, "std_msgs/msg/String", None))
                
                return emissions, True

        except Exception as e:
            print(f"[PLUGIN ERR] Robustel parse failed: {e}")

        return emissions, False