"""Capture the actual decoded frame PTS alongside the requested seek time.

FFmpeg's input-side -ss is efficient, while -copyts preserves source timestamps.
The showinfo filter observes the exact frame sent onward to the PNG encoder.
No inferred frame rate or synthetic timestamp is substituted for missing PTS.
"""
from __future__ import annotations

from fractions import Fraction
from pathlib import Path
import re
import subprocess

from .media import require_tool

_BASE = re.compile(r"config in time_base:\s*(\d+)/(\d+)")
_FRAME = re.compile(r"\bn:\s*0\s+pts:\s*(-?\d+)\b")


def extract_frame_png(path: Path, requested_ms: int, width: int = 384) -> tuple[bytes, dict]:
    """Return (PNG, source-PTS evidence) for one decoded video frame.

    decodedPtsMs is in the source stream's PTS timeline. It is *not*
    automatically normalized to zero, wall clock, or the video's start_time.
    """
    if requested_ms < 0 or not 32 <= width <= 4096:
        raise ValueError("Invalid seek position or image width")
    cmd = [
        require_tool("ffmpeg"), "-hide_banner", "-loglevel", "info",
        "-nostdin", "-copyts", "-ss", f"{requested_ms / 1000:.6f}",
        "-i", str(path), "-map", "0:v:0", "-an", "-sn",
        "-frames:v", "1", "-vf", f"showinfo,scale={width}:-2",
        "-f", "image2pipe", "-vcodec", "png", "pipe:1",
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=120, check=False)
    if result.returncode != 0 or not result.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("FFmpeg failed to decode the requested video frame")
    log = result.stderr.decode("utf-8", errors="replace")
    base = _BASE.search(log)
    pts = _FRAME.search(log)
    evidence = {
        "requestedMs": requested_ms,
        "decodedPts": None,
        "ptsTimeBase": None,
        "decodedPtsMs": None,
        "timestampBasis": "requested_seek_pts_unavailable",
    }
    if base and pts and int(base.group(2)):
        timebase = Fraction(int(base.group(1)), int(base.group(2)))
        value = int(pts.group(1))
        evidence.update({
            "decodedPts": value,
            "ptsTimeBase": f"{timebase.numerator}/{timebase.denominator}",
            "decodedPtsMs": int(round(1000 * value * timebase)),
            "timestampBasis": "decoded_frame_pts",
        })
    return result.stdout, evidence
