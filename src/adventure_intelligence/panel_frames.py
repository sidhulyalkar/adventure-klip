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
