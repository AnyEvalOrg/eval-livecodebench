"""Offline original LiveCodeBench code-generation data and Inspect task."""
# Base data package is usable without the optional Inspect dependency.
try:
    from .task import livecodebench
except ModuleNotFoundError as exc:
    if exc.name != "inspect_ai":
        raise
    __all__ = []
else:
    __all__ = ["livecodebench"]
