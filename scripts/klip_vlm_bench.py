#!/usr/bin/env python3
"""Time local vision judges on one review clip: prompt tokens, prefill and generation.

Answers "why is this model slow, and what would make it faster" before changing the panel:
if prefill dominates and prompt tokens grow with frame count or image width, fewer or
smaller frames help; if not, a smaller model is the lever. Writes nothing; asks the same
questions as the judge panel. The first call of each model includes loading it (reported
apart as loadSeconds).

    klip_vlm_bench.py --clip "$WS/reports/discovery-011/<sha>/proxies/0-27200.mp4" \\
        --sport snorkeling --models qwen3-vl:32b gemma3:27b --frames 4 8 16 --widths 448 224
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

from adventure_intelligence import judge_panel as panel
from adventure_intelligence import run_lock
from adventure_intelligence.media import require_tool
from adventure_intelligence.panel_frames import sample_frames


def _duration_ms(clip: Path) -> int:
    out = subprocess.run([require_tool("ffprobe"), "-v", "error", "-show_entries",
                          "format=duration", "-of", "default=nw=1:nk=1", str(clip)],
                         capture_output=True, text=True, timeout=30, check=True).stdout
    return int(float(out.strip()) * 1000)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--clip", type=Path, required=True, help="a review clip (proxy)")
    parser.add_argument("--sport", required=True, choices=sorted(panel.RUBRICS))
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--frames", nargs="+", type=int, default=[4, 8, 16])
    parser.add_argument("--widths", nargs="+", type=int, default=[448])
    parser.add_argument("--timeout", type=int, default=1200)
    args = parser.parse_args(argv)
    try:
        length = _duration_ms(args.clip)
        lock = run_lock.exclusive(wait=False)   # timings under contention are meaningless
        lock.__enter__()
        for model in args.models:
            for width in args.widths:
                for n in args.frames:
                    times = panel.frame_plan(0, length, [], max_frames=n)
                    images = sample_frames(args.clip, times, width=width)
                    began = time.monotonic()
                    out = panel.ask(model, args.sport, images, times, timeout=args.timeout)
                    print(json.dumps({"model": model, "frames": len(times), "width": width,
                                      "status": out["status"],
                                      "wallSeconds": round(time.monotonic() - began, 1),
                                      **out.get("usage", {}),
                                      **({"failure": out.get("failureDetail")}
                                         if out["status"] != "answered" else {})}), flush=True)
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
