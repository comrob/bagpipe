import json
from typing import Any, Dict, List, Optional, Tuple

from plugin_manager import BasePlugin


class _StringMsgProxy:
    def __init__(self, data: str):
        self.data = data


class CompatibilityTranslatorSystemPlugin(BasePlugin):
    """Translates ROS1-only or schema-incompatible streams into portable text messages."""

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        self.translate_types = set(
            self.config.get(
                "translate_types",
                [
                    "dynamic_reconfigure/Config",
                    "dynamic_reconfigure/ConfigDescription",
                    "dynamic_reconfigure/msg/Config",
                    "dynamic_reconfigure/msg/ConfigDescription",
                    "visualization_msgs/Marker",
                    "visualization_msgs/MarkerArray",
                    "visualization_msgs/msg/Marker",
                    "visualization_msgs/msg/MarkerArray",
                    "rosgraph_msgs/Log",
                    "rosgraph_msgs/msg/Log",
                ],
            )
        )
        self.target_type = self.config.get("target_type", "std_msgs/msg/String")
        self.topic_suffix = self.config.get("topic_suffix", "_translated")

    def _to_safe_obj(self, value: Any) -> Any:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, (list, tuple)):
            return [self._to_safe_obj(v) for v in value]
        if isinstance(value, dict):
            return {str(k): self._to_safe_obj(v) for k, v in value.items()}
        if hasattr(value, "dtype") and hasattr(value, "tolist"):
            return value.tolist()
        if hasattr(value, "__dict__"):
            return {
                k: self._to_safe_obj(v)
                for k, v in vars(value).items()
                if not k.startswith("_")
            }
        return str(value)

    def _render_payload(self, topic: str, msg: Any, msg_type: str, timestamp: int) -> str:
        # Keep message portable and debuggable when native ROS2 type does not exist or mismatches.
        safe_payload = {
            "source_topic": topic,
            "source_type": msg_type,
            "timestamp_ns": int(timestamp),
            "payload": self._to_safe_obj(msg),
        }
        return json.dumps(safe_payload, ensure_ascii=True, separators=(",", ":"), default=str)

    def process(
        self,
        topic: str,
        msg: Any,
        msg_type: str,
        timestamp: int,
    ) -> Tuple[List[Tuple[str, Any, str, Optional[str]]], bool]:
        if msg_type not in self.translate_types:
            return [(topic, msg, msg_type, None)], False

        out_topic = f"{topic}{self.topic_suffix}"
        out_msg = _StringMsgProxy(self._render_payload(topic, msg, msg_type, timestamp))
        return [(out_topic, out_msg, self.target_type, None)], True