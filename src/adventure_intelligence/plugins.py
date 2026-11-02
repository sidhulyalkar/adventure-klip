"""Extensibility contract for independently versioned temporal event detectors."""
from __future__ import annotations
from importlib.metadata import entry_points
from typing import Protocol, Any

class EventDetector(Protocol):
    """Plugins produce proposals; they do not silently publish world bindings."""
    name: str
    version: str
    def detect(self, asset_id: str, series: list[dict], metadata: dict[str, Any]) -> list[dict]: ...

class FlowEvidence:
    name = "flow"
    version = "1"

    def extract(self, path):
        from .vision import flow_series
        return flow_series(path)

    def detect(self, asset_id: str, series: list[dict], metadata: dict) -> list[dict]:
        from .vision import detect_flow_events
        return detect_flow_events(asset_id, series)


class MotionBaseline:
    name = "baseline"
    version = "1"

    def detect(self, asset_id: str, series: list[dict], metadata: dict) -> list[dict]:
        from .analyze import detect_motion_events
        return detect_motion_events(asset_id, series)


def detector_named(name: str) -> EventDetector:
    if name == "baseline":
        return MotionBaseline()
    if name == "flow":
        return FlowEvidence()
    for ep in entry_points(group="adventure_intelligence.detectors"):
        if ep.name == name:
            detector = ep.load()()
            if not all(hasattr(detector, attr) for attr in ("name", "version", "detect")):
                raise TypeError(f"Detector {name} does not implement detector protocol")
            return detector
    raise ValueError(f"No detector named {name!r} is installed")
