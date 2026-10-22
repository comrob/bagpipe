"""
src/std_defs.py
Registry of standard ROS 2 message definitions to support lazy registration.
"""

STD_DEFINITIONS = {
    "std_msgs/msg/String": "string data",
    "std_msgs/msg/Header": "uint32 seq\ntime stamp\nstring frame_id",
    
    # Geometry
    "geometry_msgs/msg/Point": "float64 x\nfloat64 y\nfloat64 z",
    "geometry_msgs/msg/Quaternion": "float64 x\nfloat64 y\nfloat64 z\nfloat64 w",
    "geometry_msgs/msg/Pose": "geometry_msgs/Point position\ngeometry_msgs/Quaternion orientation",
    "geometry_msgs/msg/Vector3": "float64 x\nfloat64 y\nfloat64 z",
    
    # Sensors
    "sensor_msgs/msg/Imu": (
        "std_msgs/Header header\n"
        "geometry_msgs/Quaternion orientation\n"
        "float64[9] orientation_covariance\n"
        "geometry_msgs/Vector3 angular_velocity\n"
        "float64[9] angular_velocity_covariance\n"
        "geometry_msgs/Vector3 linear_acceleration\n"
        "float64[9] linear_acceleration_covariance"
    ),
    # Add other types here as needed (e.g., NavSatFix, Odometry)
}

def get_std_def(type_name: str) -> str:
    """Returns the raw .msg definition text for a standard type, or None."""
    return STD_DEFINITIONS.get(type_name)