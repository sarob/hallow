"""Security analysis — secret detection and taint tracking."""

from hallow.security.secrets import detect_hardcoded_secrets
from hallow.security.taint import detect_taint_sinks

__all__ = ["detect_hardcoded_secrets", "detect_taint_sinks"]
