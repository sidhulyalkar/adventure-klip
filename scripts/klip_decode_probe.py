"""Measure where frame sampling time goes for one recording. Read-only.

Usage:  python scripts/klip_decode_probe.py /Volumes/.../DJI_xxx.MP4 [--seconds 60]

Pick a recording that has not been read recently, so the disk number is not served
from memory. Each test covers only the first --seconds of the file.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

STEP_S = 2.0
WIDTH = 448


def _timed(cmd: list[str], timeout: float = 900) -> float:
    start = time.perf_counter()
    subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)
    return time.perf_counter() - start


def _probe(path: Path, seconds: float) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=codec_name,width,height,avg_frame_rate,bit_rate,pix_fmt:format=duration,bit_rate",
         "-of", "json", str(path)], check=True, capture_output=True, text=True, timeout=300)
    info = json.loads(out.stdout)
    stream, fmt = info["streams"][0], info["format"]
    num, den = (int(x) for x in stream["avg_frame_rate"].split("/"))
    keys = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey",
         "-read_intervals", f"%+{seconds:g}", "-show_entries", "frame=pts_time",
         "-of", "csv=p=0", str(path)], check=True, capture_output=True, text=True, timeout=600)
    times = [float(t.split(",")[0]) for t in keys.stdout.split() if t.split(",")[0].strip()]
    gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
    return {"codec": stream["codec_name"], "size": f"{stream['width']}x{stream['height']}",
            "pixFmt": stream.get("pix_fmt"), "fps": round(num / den, 2) if den else None,
            "durationS": round(float(fmt["duration"]), 1),
            "mbitPerS": round(int(fmt.get("bit_rate", 0)) / 1e6, 1),
            "keyframesInWindow": len(times),
            "keyframeIntervalS": round(sum(gaps) / len(gaps), 2) if gaps else None}


def _read_speed(path: Path, limit: int = 1 << 30) -> float:
    start, done = time.perf_counter(), 0
    with path.open("rb") as f:
        while done < limit and (chunk := f.read(8 << 20)):
            done += len(chunk)
    return done / (1 << 20) / max(time.perf_counter() - start, 1e-6)


def _keyframe_pass(path: Path, seconds: float, hwaccel: str | None) -> list[str]:
    keep = f"isnan(prev_selected_t)+gte(t-prev_selected_t\\,{STEP_S - 1e-3:.3f})"
    cmd = ["ffmpeg", "-nostdin", "-v", "error"]
    if hwaccel:
        cmd += ["-hwaccel", hwaccel]
    return cmd + ["-skip_frame", "nokey", "-t", f"{seconds:g}", "-i", str(path),
                  "-map", "0:v:0", "-an", "-sn", "-vf", f"select='{keep}',scale={WIDTH}:-2",
                  "-fps_mode", "vfr", "-f", "null", "-"]


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("recording", type=Path)
    ap.add_argument("--seconds", type=float, default=60.0)
    args = ap.parse_args(argv)
    path, secs = args.recording, args.seconds

    info = _probe(path, secs)
    secs = min(secs, info["durationS"])
    rows = {"diskReadMBPerS": round(_read_speed(path), 1),
            "diskNeededForRealTimeMBPerS": round(info["mbitPerS"] / 8, 1)}
    tests = {
        "keyframesOnly": _keyframe_pass(path, secs, None),
        "keyframesOnlyVideoToolbox": _keyframe_pass(path, secs, "videotoolbox"),
        "everyFrame": ["ffmpeg", "-nostdin", "-v", "error", "-t", f"{min(secs, 20):g}",
                       "-i", str(path), "-map", "0:v:0", "-an", "-f", "null", "-"],
        # What the motion timeline needs: every frame decoded, kept at 2 fps.
        "everyFrameVideoToolbox": ["ffmpeg", "-nostdin", "-v", "error", "-hwaccel", "videotoolbox",
                                   "-t", f"{min(secs, 20):g}", "-i", str(path), "-map", "0:v:0",
                                   "-an", "-vf", "fps=2,scale=64:36,format=gray", "-f", "null", "-"],
    }
    speed = {}
    for name, cmd in tests.items():
        window = min(secs, 20) if name.startswith("everyFrame") else secs
        try:
            speed[name] = round(window / _timed(cmd), 1)
        except (subprocess.SubprocessError, OSError) as err:
            speed[name] = f"failed: {type(err).__name__}"
    seeks = [i * STEP_S for i in range(int(secs / STEP_S))][:30]
    start = time.perf_counter()
    for t in seeks:
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-noaccurate_seek", "-ss", f"{t:.3f}",
                        "-i", str(path), "-map", "0:v:0", "-frames:v", "1",
                        "-vf", f"scale={WIDTH}:-2", "-f", "null", "-"],
                       check=True, capture_output=True, timeout=300)
    speed["seekPerSample"] = round(len(seeks) * STEP_S / (time.perf_counter() - start), 1)
    print(json.dumps({"recording": path.name, "file": info, "disk": rows,
                      "timesFasterThanRealTime": speed}, indent=2))


if __name__ == "__main__":
    main()
