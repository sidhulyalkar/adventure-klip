"""FFmpeg probing and low-resolution evidence extraction; not semantic recognition."""
from __future__ import annotations
import json
import shutil
import subprocess
from pathlib import Path


def require_tool(name: str) -> str:
    binary = shutil.which(name)
    if not binary:
        raise RuntimeError(f"{name} is missing; install FFmpeg (macOS: brew install ffmpeg)")
    return binary


def probe(path: Path) -> dict:
    cmd = [require_tool("ffprobe"), "-v", "error", "-show_format", "-show_streams",
           "-of", "json", str(path)]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=120)
    raw = json.loads(result.stdout)
    video = next((s for s in raw.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video:
        raise ValueError(f"No video stream in {path}")
    fmt = raw.get("format", {})
    duration = float(fmt.get("duration") or video.get("duration") or 0)
    raw_date = video.get("tags", {}).get("creation_time") or fmt.get("tags", {}).get("creation_time")
    # Preserve raw source dates; do not convert unknown local clock to UTC.
    return {"duration":duration,"width":video.get("width"),"height":video.get("height"),
            "codec":video.get("codec_name"),"avg_frame_rate":video.get("avg_frame_rate"),
            "capture_time":raw_date,"audio":any(s.get("codec_type")=="audio" for s in raw.get("streams",[]))}


def motion_series(path: Path, fps: int = 2, width: int = 64, height: int = 36,
                  max_seconds: float | None = None) -> list[dict]:
    """Mean absolute inter-frame pixel change; camera motion and cuts confound this signal.

    Bounded RAM, streaming decoder. Time approximated by uniform fps; this is a proposal
    generator only and must never be interpreted as a confirmed jump or turn.
    """
    cmd = [require_tool("ffmpeg"), "-v", "error", "-nostdin", "-i", str(path)]
    if max_seconds is not None:
        cmd.extend(["-t", str(max_seconds)])
    cmd.extend(["-vf", f"fps={fps},scale={width}:{height},format=gray", "-an", "-sn",
                "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"])
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert proc.stdout is not None
    frame_bytes = width*height
    previous: bytes | None = None
    result = []
    index = 0
    try:
        while True:
            frame = proc.stdout.read(frame_bytes)
            if not frame:
                break
            if len(frame) != frame_bytes:
                raise RuntimeError("Partial decoded frame")
            if previous is not None:
                delta = sum(abs(a-b) for a,b in zip(frame,previous))/(frame_bytes*255)
                result.append({"t_ms":int(round(index*1000/fps)),"change":round(delta,6)})
            previous = frame
            index += 1
    finally:
        proc.stdout.close()
        code = proc.wait(timeout=60)
        if code != 0:
            raise RuntimeError(f"ffmpeg decode failed (exit {code}) for {path}")
    return result
