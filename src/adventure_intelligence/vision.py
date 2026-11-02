"""Optional low-resolution camera-motion evidence, NOT physical action recognition.

This module deliberately uses only sampled grayscale frames. A lateral image sweep
can mean a ski turn, a hand-held pan, or a moving subject. The events emitted here
must therefore never be published as confirmed jumps, carving or wildlife.
"""
from __future__ import annotations

from hashlib import sha256
import math
from pathlib import Path
import subprocess
from statistics import median

FLOW_VERSION = "optical-flow-v1-fps4-160x90"


def _vision_dependencies():
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("Flow analysis needs optional dependencies: pip install 'adventure-intelligence[vision]'") from exc
    return cv2, np


def flow_series(path: Path, fps: int = 4, width: int = 160, height: int = 90,
                max_seconds: float | None = None, progress=None,
                total_seconds: float | None = None) -> list[dict]:
    """Decode a bounded frame at a time; use median flow as a camera-motion proxy.

    Frames are sampled onto an FFmpeg constant-rate timeline. The timing is an
    approximation for cameras with unusual PTS or variable-frame-rate streams.
    """
    from .media import require_tool
    cv2, np = _vision_dependencies()
    if fps <= 0 or width < 32 or height < 24:
        raise ValueError("Invalid optical flow sampling dimensions")
    command = [require_tool("ffmpeg"), "-nostdin", "-v", "error", "-i", str(path)]
    if max_seconds is not None:
        if not math.isfinite(max_seconds) or max_seconds <= 0:
            raise ValueError("max_seconds must be positive")
        command += ["-t", str(max_seconds)]
    command += ["-vf", f"fps={fps},scale={width}:{height},format=gray", "-an", "-sn",
                "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert process.stdout is not None
    frame_size = width * height
    previous = None
    samples: list[dict] = []
    index = 0
    try:
        while True:
            buf = process.stdout.read(frame_size)
            if not buf:
                break
            if len(buf) != frame_size:
                raise RuntimeError("Incomplete video frame from FFmpeg")
            frame = np.frombuffer(buf, dtype=np.uint8).reshape(height, width).copy()
            if previous is not None:
                delta = float(np.mean(cv2.absdiff(frame, previous))) / 255.0
                flow = cv2.calcOpticalFlowFarneback(previous, frame, None,
                    pyr_scale=0.5, levels=2, winsize=15, iterations=2,
                    poly_n=5, poly_sigma=1.1, flags=0)
                dx = float(np.median(flow[..., 0])); dy = float(np.median(flow[..., 1]))
                magnitude = math.hypot(dx, dy)
                residual = np.hypot(flow[..., 0] - dx, flow[..., 1] - dy)
                # A coherent global field often reflects camera translation or panning.
                vec_mag = np.hypot(flow[..., 0], flow[..., 1])
                moving = vec_mag > 0.35
                if moving.any() and magnitude > 0.35:
                    # Fraction of sufficiently moving points aligned with global translation.
                    dot = (flow[..., 0] * dx + flow[..., 1] * dy) / (vec_mag * magnitude + 1e-8)
                    coherence = float(np.mean(dot[moving] > 0.5))
                else:
                    coherence = 0.0
                samples.append({
                    "t_ms": round(index * 1000 / fps),
                    "change": round(delta, 6),
                    "flow_x": round(dx, 4),
                    "flow_y": round(dy, 4),
                    "flow_magnitude": round(magnitude, 4),
                    "flow_coherence": round(coherence, 4),
                    "residual_motion": round(float(np.percentile(residual, 75)), 4),
                })
            previous = frame
            index += 1
            if progress is not None:
                seconds = max_seconds if max_seconds is not None else total_seconds
                expected = max(index, math.ceil(seconds * fps)) if seconds is not None else index
                progress(index, expected)
    finally:
        process.stdout.close()
        try:
            code = process.wait(timeout=90)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise RuntimeError("FFmpeg timed out while closing flow analysis")
        if code != 0:
            raise RuntimeError(f"FFmpeg optical-flow extraction failed (exit {code})")
    return samples


def _event(asset_id: str, kind: str, start_ms: int, end_ms: int,
           score: float, evidence: dict) -> dict:
    score = max(0.0, min(1.0, score))
    uid = sha256(f"{asset_id}|{FLOW_VERSION}|{kind}|{start_ms}|{end_ms}".encode()).hexdigest()[:20]
    return {"id": f"flow-{uid}", "kind": kind, "start_ms": start_ms,
            "end_ms": end_ms, "score": round(score, 4),
            "confidence": round(score * 0.75, 4),
            "evidence": {**evidence, "type": "camera-motion-proposal",
                         "warning": "Not a validated athlete maneuver or animal detection"}}


def _runs(samples: list[dict], predicate, gap_ms: int, min_ms: int) -> list[list[dict]]:
    runs: list[list[dict]] = []
    for s in samples:
        if not predicate(s):
            continue
        if not runs or s["t_ms"] - runs[-1][-1]["t_ms"] > gap_ms:
            runs.append([s])
        else:
            runs[-1].append(s)
    return [run for run in runs if run[-1]["t_ms"] - run[0]["t_ms"] >= min_ms]


def detect_flow_events(asset_id: str, series: list[dict]) -> list[dict]:
    """Propose motion windows and alternating *camera sweeps*, never ski turns.

    Fixed conservative thresholds are intended for a first benchmark, not as a
    trained model. Camera pans, fast-moving water and occlusion can all trigger.
    """
    if len(series) < 5:
        return []
    samples = sorted(series, key=lambda s: s["t_ms"])
    if any(samples[i]["t_ms"] <= samples[i-1]["t_ms"] for i in range(1, len(samples))):
        raise ValueError("Flow sample timestamps must increase strictly")
    step = int(median([samples[i]["t_ms"]-samples[i-1]["t_ms"]
                       for i in range(1,len(samples))]))
    vals = [float(x["change"]) for x in samples]
    base = median(vals)
    # Differentiate a continuous moving environment from a totally static shot.
    threshold = max(0.025, base * 0.55)
    out: list[dict] = []
    for group in _runs(samples, lambda s:s["change"] >= threshold and
                       s["flow_coherence"] >= 0.20, gap_ms=step * 3, min_ms=2000):
        start = max(0, group[0]["t_ms"]-step)
        end = group[-1]["t_ms"]+step
        energy = median([s["change"] for s in group])
        coherence = median([s["flow_coherence"] for s in group])
        score = min(1, 0.25 + 0.4 * min(energy / max(threshold, .001), 1.5)
                    + 0.2 * coherence)
        out.append(_event(asset_id,"sustained_camera_motion", start,end,score,
                          {"sample_count":len(group),"median_change":round(energy,5),
                           "median_coherence":round(coherence,4)}))

    # Direction changes must be supported by several coherent horizontal sweeps.
    # Smooth noisy sign flips using 3-point median, then group sustained sign runs.
    smoothed=[]
    for i,s in enumerate(samples):
        neighboring = samples[max(0,i-1):min(len(samples),i+2)]
        x = median([p["flow_x"] for p in neighboring])
        valid = abs(x) >= 0.65 and s["flow_coherence"] >= 0.35 and s["change"] < 0.42
        smoothed.append({**s,"direction": (1 if x>0 else -1) if valid else 0})
    sweep_runs: list[list[dict]]=[]
    for s in smoothed:
        if s["direction"] == 0:
            continue
        if not sweep_runs or s["direction"] != sweep_runs[-1][-1]["direction"] or \
                s["t_ms"]-sweep_runs[-1][-1]["t_ms"] > step*3:
            sweep_runs.append([s])
        else:
            sweep_runs[-1].append(s)
    sweeps=[r for r in sweep_runs if len(r) >= 2]
    i = 0
    while i < len(sweeps):
        cluster = [sweeps[i]]
        j = i + 1
        while j < len(sweeps):
            run = sweeps[j]
            if run[0]["t_ms"] - cluster[-1][-1]["t_ms"] > 3500:
                break
            if run[0]["direction"] == cluster[-1][0]["direction"]:
                break
            cluster.append(run)
            j += 1
        if len(cluster) < 3:
            i += 1
            continue
        start = max(0, cluster[0][0]["t_ms"] - step)
        end = cluster[-1][-1]["t_ms"] + step
        periods = [cluster[k][0]["t_ms"] - cluster[k-1][0]["t_ms"]
                   for k in range(1,len(cluster))]
        consistency = min(periods)/max(periods) if periods and max(periods) else 0.0
        if consistency >= .30:
            out.append(_event(asset_id, "rhythmic_camera_sweep", start, end,
                min(0.92, .3 + .1 * len(cluster) + .2 * consistency),
                {"alternations":len(cluster)-1,"sweep_count":len(cluster),
                 "period_consistency":round(consistency,4)}))
            i = j  # Do not emit overlapping suffixes of the same observed chain.
        else:
            i += 1
    return sorted(out, key=lambda e:(e["start_ms"],e["kind"],e["id"]))
