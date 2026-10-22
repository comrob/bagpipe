import cv2
import numpy as np
from typing import Any, List, Tuple, Optional
from bagpipe.convert.plugin_manager import BasePlugin

class DebayerPlugin(BasePlugin):
    def process(self, topic: str, msg: Any, msg_type: str, timestamp: int) -> Tuple[List[Tuple[str, Any, str, Optional[str]]], bool]:
        
        default_emission = [(topic, msg, msg_type, None)]

        # 1. Configurable Filter
        target_sub = self.config.get('target_topic', 'camera_front')
        if target_sub not in topic or "CompressedImage" not in msg_type:
            return default_emission, False
        
        fmt = getattr(msg, 'format', '').lower()
        if "bayer" not in fmt:
            return default_emission, False

        try:
            np_arr = np.frombuffer(msg.data, np.uint8)
            bayer_img = cv2.imdecode(np_arr, cv2.IMREAD_GRAYSCALE)

            if bayer_img is None:
                return default_emission, False

            # Debayer
            color_img = cv2.cvtColor(bayer_img, cv2.COLOR_BayerBG2BGR)

            # Re-encode
            quality = self.config.get('jpeg_quality', 95)
            encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
            success, enc_buf = cv2.imencode(".jpg", color_img, encode_param)

            if success:
                # Modify In-Place
                msg.data = enc_buf.flatten()
                msg.format = "bgr8; jpeg compressed bgr8 " 
                
                return default_emission, True

        except Exception as e:
            print(f"[PLUGIN ERR] Debayer failed on {topic}: {e}")

        return default_emission, False