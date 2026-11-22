"""Frame sampling for the judge panel from a local proxy clip."""
from __future__ import annotations

import base64
from pathlib import Path
import subprocess
import tempfile

from .media import require_tool


def sample_frames(proxy: Path, times_ms: list[int], *, width: int = 448) -> list[str]:
    """Base64 JPEGs at the given proxy-relative times, decoded to a private temp dir."""
    ffmpeg = require_tool("ffmpeg")
    out = []
    with tempfile.TemporaryDirectory(prefix="klip-panel-private-") as folder:
        for i, t in enumerate(times_ms):
            target = Path(folder) / f"{i}.jpg"
            job = subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
                 "-ss", f"{t / 1000:.3f}", "-i", str(proxy), "-frames:v", "1",
                 "-vf", f"scale='min({width},iw)':-2", "-q:v", "7", "-y", str(target)],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30, check=False)
            if job.returncode != 0 or not target.is_file() or target.stat().st_size < 100:
                raise ValueError(f"Could not decode a frame at {t} ms")
            out.append(base64.b64encode(target.read_bytes()).decode("ascii"))
    return out


def video_span(proxy: Path) -> int | None:
    """Milliseconds of decodable video in a review clip: 0 when it has no video frames
    (an audio-only tail of an interrupted recording), None when it cannot be probed."""
    try:
        job = subprocess.run(
            [require_tool("ffprobe"), "-v", "error", "-select_streams", "v:0", "-count_packets",
             "-show_entries", "stream=nb_read_packets,duration:format=duration",
             "-of", "json", str(proxy)], capture_output=True, text=True, timeout=60, check=False)
        import json
        doc = json.loads(job.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    streams = doc.get("streams") or []
    if job.returncode != 0:
        return None
    if not streams or int(streams[0].get("nb_read_packets") or 0) == 0:
        return 0
    for value in (streams[0].get("duration"), (doc.get("format") or {}).get("duration")):
        try:
            return int(float(value) * 1000)
        except (TypeError, ValueError):
            continue
    return None
