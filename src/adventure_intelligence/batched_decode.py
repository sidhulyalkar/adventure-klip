"""Conservative opt-in multi-frame FFmpeg decoding for adaptive POV schedules.

Nearby sample requests share one decoder/seek. Widely separated coverage probes
retain the original single-seek path. Batches fail closed to individually
timestamped seeks, never to guessed image/frame positions.
"""
from __future__ import annotations

from fractions import Fraction
from pathlib import Path
import re
import subprocess

from .frame_timing import extract_frame_png
from .media import require_tool

_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_BASE = re.compile(r"config in time_base:\s*(\d+)/(\d+)")
_FRAME = re.compile(r"\bn:\s*(\d+)\s+pts:\s*(-?\d+)\b")
_MAX_PNG_BYTES = 16 * 1024 * 1024
_VERSION = "select-pts-png-v1"


def groups(times: list[int], *, max_gap_ms: int = 3500,
           max_span_ms: int = 30000, max_frames: int = 12):
    """Only cluster nearby sorted samples, never decode through long quiet gaps."""
    if (times != sorted(set(times)) or any(type(t) is not int or t < 0 for t in times)
            or max_gap_ms <= 0 or max_span_ms <= 0 or not 2 <= max_frames <= 64):
        raise ValueError("Invalid batched timestamp schedule or bounds")
    group = []
    for t in times:
        if group and (t - group[-1] > max_gap_ms or t - group[0] > max_span_ms
                      or len(group) >= max_frames):
            yield group
            group = []
        group.append(t)
    if group:
        yield group


def _png_stream(data: bytes) -> list[bytes]:
    """Split concatenated PNGs at exact IEND chunks, not incidental signatures."""
    frames = []
    pos = 0
    while pos < len(data):
        start = pos
        if data[pos:pos + 8] != _SIGNATURE:
            raise ValueError("Invalid PNG pipe signature")
        pos += 8
        while True:
            if pos + 12 > len(data):
                raise ValueError("Truncated PNG pipe")
            length = int.from_bytes(data[pos:pos + 4], "big")
            kind = data[pos + 4:pos + 8]
            if length > _MAX_PNG_BYTES or pos + length + 12 > len(data):
                raise ValueError("Oversized or truncated PNG chunk")
            pos += 12 + length
            if pos - start > _MAX_PNG_BYTES:
                raise ValueError("Oversized decoded frame")
            if kind == b"IEND":
                if length:
                    raise ValueError("Invalid PNG IEND")
                frames.append(data[start:pos])
                break
    return frames


def _decode_batch(path: Path, requested: list[int], width: int):
    if len(requested) < 2:
        raise ValueError("A batch requires at least two timestamps")
    if not 32 <= width <= 4096:
        raise ValueError("Invalid image width")
    # selected_n increments only when a frame passes the filter.
    # Escape function commas for FFmpeg's filtergraph argument parser.
    expr = "0"
    for index in reversed(range(len(requested))):
        expr = (f"if(eq(selected_n\\,{index})\\,gte(t\\,"
                f"{requested[index] / 1000:.6f})\\,{expr})")
    # -copyts keeps source stream PTS so showinfo and select share a clock.
    start = max(0., (requested[0] - 500) / 1000)
    cmd = [require_tool("ffmpeg"), "-hide_banner", "-loglevel", "info",
           "-nostdin", "-copyts", "-ss", f"{start:.6f}",
           "-i", str(path), "-map", "0:v:0", "-an", "-sn",
           "-vf", f"select={expr},showinfo,scale={width}:-2",
           "-fps_mode", "vfr", "-frames:v", str(len(requested)),
           "-f", "image2pipe", "-vcodec", "png", "pipe:1"]
    run = subprocess.run(cmd, capture_output=True, timeout=180, check=False)
    if run.returncode:
        raise RuntimeError("Batched FFmpeg decoding failed")
    images = _png_stream(run.stdout)
    log = run.stderr.decode("utf-8", errors="replace")
    base = _BASE.search(log)
    found = _FRAME.findall(log)
    if not base or int(base.group(2)) == 0 or len(images) != len(requested):
        raise ValueError("Batched frame count/timebase unavailable")
    if len(found) != len(images) or [int(x[0]) for x in found] != list(range(len(images))):
        raise ValueError("Non-sequential batch PTS log")
    fraction = Fraction(int(base.group(1)), int(base.group(2)))
    evidence = []
    for target, (_, pts_text) in zip(requested, found):
        pts = int(pts_text)
        ms = round(1000 * pts * fraction)
        # A source offset, broken -copyts, or delayed seek must not silently
        # pair a sampled image with the wrong point on the video timeline.
        if ms < target - 100 or ms > target + 750:
            raise ValueError("Batched source PTS outside requested window")
        evidence.append({"requestedMs": target, "decodedPts": pts,
                         "ptsTimeBase": f"{fraction.numerator}/{fraction.denominator}",
                         "decodedPtsMs": ms, "timestampBasis": "decoded_frame_pts"})
    return list(zip(images, evidence))


def decode_scheduled(path: Path, times: list[int], *, width: int = 384,
                     mode: str = "single", counters: dict | None = None):
    """Yield requested timestamp, PNG, timing; preserve ordering and one result per request.

    Any failed batch is retried through the original single-frame extractor.
    This generator never writes to source media or maintains decoded video on disk.
    """
    if mode not in {"single", "batched"}:
        raise ValueError("Unknown decoder mode")
    if counters is None:
        counters = {}
    for key in ("batchAttempts", "batchSucceeded", "batchFallbacks",
                "singleSeekFrames", "batchedFrames"):
        counters.setdefault(key, 0)
    chunks = (groups(times) if mode == "batched" else ([t] for t in times))
    for chunk in chunks:
        if len(chunk) > 1:
            counters["batchAttempts"] += 1
            try:
                values = _decode_batch(path, chunk, width)
            except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired):
                counters["batchFallbacks"] += 1
            else:
                counters["batchSucceeded"] += 1
                counters["batchedFrames"] += len(chunk)
                for t, (png, evidence) in zip(chunk, values):
                    yield t, png, evidence
                continue
        for t in chunk:
            counters["singleSeekFrames"] += 1
            png, evidence = extract_frame_png(path, t, width)
            yield t, png, evidence
