"""bagpipe: pipelines over ROS bags. Run as `python -m bagpipe <command>`."""
from pathlib import Path

_VERSION_FILE = Path(__file__).resolve().parents[2] / "VERSION"
__version__ = _VERSION_FILE.read_text().strip() if _VERSION_FILE.exists() else "unknown"
