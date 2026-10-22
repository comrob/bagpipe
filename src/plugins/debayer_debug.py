import sys
import cv2
import numpy as np
from typing import Any, List, Tuple, Optional
from bagpipe.convert.plugin_manager import BasePlugin

class DebayerDebugPlugin(BasePlugin):
    def process(self, topic: str, msg: Any, msg_type: str, timestamp: int) -> Tuple[List[Tuple[str, Any, str, Optional[str]]], bool]:
        
        default_emission = [(topic, msg, msg_type, None)]

        target_sub = self.config.get('target_topic', 'camera_front')
        if target_sub not in topic or "CompressedImage" not in msg_type:
            return default_emission, False
        
        fmt = getattr(msg, 'format', '').lower()
        if "bayer" not in fmt:
            return default_emission, False

        try:
            np_arr = np.frombuffer(msg.data, np.uint8)
            bayer_img = cv2.imdecode(np_arr, cv2.IMREAD_GRAYSCALE)

            if bayer_img is None: return default_emission, False

            color_img = cv2.cvtColor(bayer_img, cv2.COLOR_BayerBG2BGR)
            
            quality = self.config.get('jpeg_quality', 95)
            encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
            success, enc_buf = cv2.imencode(".jpg", color_img, encode_param)

            if success:
                msg.data = enc_buf.flatten()
                msg.format = "jpeg; rgb8" 

                print(f"\n[DEBUG] ROS2 Message updated successfully.")
                print(f"        Topic: {topic}")
                print(f"        New Data Size: {len(msg.data)} bytes")
                print(f"        Timestamp: {timestamp} ns")

                cv2.imwrite("debug_final_result.jpg", color_img)
                print("  -> Saved 'debug_final_result.jpg' to disk.")
                
                print("[DEBUG] Exiting script now.")
                sys.exit(0)

        except SystemExit:
            raise
        except Exception as e:
            print(f"[PLUGIN ERR] {e}")

        return default_emission, False