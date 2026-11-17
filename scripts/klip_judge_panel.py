#!/usr/bin/env python3
"""Ask a panel of local Ollama vision models factual questions about one proxy clip.

Reads one local H.264 proxy (never a camera original) and talks only to Ollama
on 127.0.0.1. Nothing is uploaded.

    klip_judge_panel.py --proxy clip.mp4 --sport ski --events-ms 6288 12402 \\
        --models qwen3-vl:32b gemma3:27b --output panel.json

Times are relative to the start of the proxy. Models must already be pulled
(`ollama pull qwen3-vl:32b`). Use at least two different model families.
"""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from adventure_intelligence import judge_panel as panel
from adventure_intelligence.media import probe, require_tool


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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--proxy", type=Path, required=True)
    parser.add_argument("--sport", choices=sorted(panel.RUBRICS), required=True)
    parser.add_argument("--models", nargs="+", default=["qwen3-vl:32b", "gemma3:27b"])
    parser.add_argument("--events-ms", nargs="*", type=int, default=[])
    parser.add_argument("--max-frames", type=int, default=16)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.proxy.is_symlink() or not args.proxy.is_file():
            raise ValueError("Proxy clip missing or symlinked")
        if len({panel.family(m) for m in args.models}) < 2:
            raise ValueError("Use models from at least two different families")
        duration_ms = int(float(probe(args.proxy)["duration"]) * 1000)
        times = panel.frame_plan(0, duration_ms, args.events_ms, max_frames=args.max_frames)
        images = sample_frames(args.proxy, times)
        judgments = [panel.ask(m, args.sport, images, times) for m in args.models]
        report = {"frameTimesMs": times, "judgments": judgments,
                  "panel": panel.combine(judgments),
                  "backend": "ollama_loopback_only", "evidenceLevel": "model_observation"}
        text = json.dumps(report, indent=2, allow_nan=False) + "\n"
        if args.output:
            if args.output.exists():
                raise ValueError("Refusing to overwrite an existing report")
            args.output.write_text(text, encoding="utf-8")
        else:
            sys.stdout.write(text)
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
