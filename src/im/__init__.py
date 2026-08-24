"""Platform-agnostic IM adapter layer (sync).

The package is introduced incrementally. Existing WeChat/iLink code keeps
its original import paths until the corresponding migration stage is enabled.
"""

__all__ = [
    "base",
    "message",
    "registry",
    "push_channel",
    "config_schema",
    "progress_router",
    "delivery",
]
