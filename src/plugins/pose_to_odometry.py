from typing import Any, List, Tuple, Optional

import numpy as np

from rosbags.typesys.stores.ros2_humble import (
    geometry_msgs__msg__Twist as TwistMsg,
    geometry_msgs__msg__TwistWithCovariance as TwistWithCovarianceMsg,
    geometry_msgs__msg__Vector3 as Vector3Msg,
    nav_msgs__msg__Odometry as OdometryMsg,
)

from plugin_manager import BasePlugin


class PoseToOdometryPlugin(BasePlugin):
    def process(
        self,
        topic: str,
        msg: Any,
        msg_type: str,
        timestamp: int,
    ) -> Tuple[List[Tuple[str, Any, str, Optional[str]]], bool]:
        emissions = [(topic, msg, msg_type, None)]

        input_topic = self.config.get("input_topic", "/legged_odometry/pose_in_odom")
        if topic != input_topic:
            return emissions, False

        if msg_type != "geometry_msgs/msg/PoseWithCovarianceStamped":
            return emissions, False

        try:
            zero_vec = Vector3Msg(x=0.0, y=0.0, z=0.0)
            zero_twist = TwistMsg(linear=zero_vec, angular=zero_vec)
            zero_twist_cov = TwistWithCovarianceMsg(
                twist=zero_twist,
                covariance=np.zeros(36, dtype=np.float64),
            )

            odom_msg = OdometryMsg(
                header=msg.header,
                child_frame_id=self.config.get("child_frame_id", "base"),
                pose=msg.pose,
                twist=zero_twist_cov,
            )

            output_topic = self.config.get("output_topic", "/legged_odometry/odom")
            emissions.append((output_topic, odom_msg, "nav_msgs/msg/Odometry", None))
            return emissions, True
        except Exception as e:
            print(f"[PLUGIN ERR] PoseToOdometry conversion failed: {e}")
            return emissions, False
