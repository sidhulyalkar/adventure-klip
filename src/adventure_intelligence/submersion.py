"""Propose continuous fully-underwater filming intervals from sparse scene evidence.

This module describes camera submersion and filming continuity, never the
camera operator's breathing, physiology, safety, or emotional state. All
zero-shot scene boundaries require validation with DJI source recordings.
"""
from __future__ import annotations

from statistics import median


def _timestamp(frame: dict) -> int | None:
    t = frame.get("decodedPtsMs")
    if not isinstance(t, int) or isinstance(t, bool) or t < 0:
        t = frame.get("tMs")
    return t if isinstance(t, int) and not isinstance(t, bool) and t >= 0 else None


def submersion_samples(frames: list[dict], *, margin: float = .02) -> list[dict]:
    if not 0 < margin <= 1:
        raise ValueError("margin must be in (0,1]")
    out = []
    for f in frames:
        t = _timestamp(f)
        scene = f.get("sceneScores")
        if t is None or not isinstance(scene, dict) or \
                scene.get("method") != "siglip2_image_text_cosine":
            continue
        scores = scene.get("similarities", {})
        pos, neg = scores.get("submerged"), scores.get("at_surface")
        if not isinstance(pos, (int, float)) or not isinstance(neg, (int, float)):
            continue
        if abs(pos) > 1.01 or abs(neg) > 1.01:
            continue
        difference = float(pos - neg)
        state = "submerged" if difference >= margin else \
                "surface" if difference <= -margin else "uncertain"
        out.append({"tMs": t, "state": state, "margin": round(difference, 5)})
    # Distinct actual source timestamps, deterministic even with repeated frames.
    return list({s["tMs"]: s for s in sorted(out, key=lambda v: v["tMs"])}.values())


def continuous_underwater_intervals(samples: list[dict], *, duration_ms: int,
                                    min_ms: int = 8000,
                                    max_gap_ms: int = 5500,
                                    edge_pad_ms: int = 500) -> list[dict]:
    """Suggest intervals with positive underwater observations, no surface evidence.

    A single uncertain sample between two submerged samples may be bridged.
    Gaps larger than max_gap_ms force a boundary even without a surface label.
    """
    if duration_ms < 0 or min_ms <= 0 or max_gap_ms <= 0 or edge_pad_ms < 0:
        raise ValueError("Invalid continuity parameters")
    data = sorted((x for x in samples if 0 <= x["tMs"] < duration_ms),
                  key=lambda x: x["tMs"])
    if not data:
        return []
    deltas = [b["tMs"] - a["tMs"] for a, b in zip(data, data[1:])
              if 0 < b["tMs"] - a["tMs"] <= max_gap_ms]
    half_step = int(median(deltas)/2) if deltas else 0
    runs = []
    run = []
    for i, x in enumerate(data):
        if x["state"] == "surface":
            if run:
                runs.append(run)
                run = []
            continue
        if run and x["tMs"] - run[-1]["tMs"] > max_gap_ms:
            runs.append(run)
            run = []
        if x["state"] == "submerged":
            run.append(x)
        elif x["state"] == "uncertain" and run:
            # Allow one uncertain observation only when followed by a positive
            # one within the gap. Never bridge known surface evidence.
            next_sample = data[i+1] if i+1 < len(data) else None
            if (next_sample and next_sample["state"] == "submerged"
                    and next_sample["tMs"]-x["tMs"] <= max_gap_ms
                    and x["tMs"]-run[-1]["tMs"] <= max_gap_ms):
                run.append(x)
            else:
                runs.append(run)
                run = []
    if run:
        runs.append(run)
    result = []
    for run in runs:
        positives = [x for x in run if x["state"] == "submerged"]
        if len(positives) < 3:
            continue
        start = max(0, run[0]["tMs"] - half_step + edge_pad_ms)
        end = min(duration_ms, run[-1]["tMs"] + half_step - edge_pad_ms)
        if end-start < min_ms:
            continue
        result.append({
            "startMs": start, "endMs": end, "positiveSamples": len(positives),
            "uncertainSamples": len(run)-len(positives),
            "status": "proposed_unverified",
            "evidence": "sampled_scene_similarity",
            "warning": "Not verified submersion and not a breathing assessment",
        })
    return result


def continuity_report(workspace) -> dict:
    """Read existing cached audio/context evidence; never decode or alter sources."""
    from .catalog import connect
    from .artifacts import read_artifact
    from .acoustics import AUDIO_VERSION, acoustic_changes
    from .editorial_quality import _scene_frames
    results = []
    with connect(workspace) as db:
        for asset in db.execute("SELECT id,duration FROM assets WHERE activity='snorkeling'"):
            aid = asset["id"]
            frames = _scene_frames(db, workspace, aid)
            samples = submersion_samples(frames)
            intervals = continuous_underwater_intervals(
                samples, duration_ms=int((asset["duration"] or 0)*1000))
            audio = read_artifact(db, workspace, aid, AUDIO_VERSION)
            transients = acoustic_changes(audio["series"]) if audio else []
            for interval in intervals:
                interval["acousticTransients"] = [
                    e for e in transients
                    if e["startMs"] < interval["endMs"] and
                       interval["startMs"] < e["endMs"]]
            results.append({
                "assetId": aid, "sampledSceneFrames": len(samples),
                "audioAvailable": bool(audio and audio["series"]),
                "intervals": intervals,
                "status": "candidate_only",
            })
    return {"algorithm": "visual_submersion_v1", "assets": results,
            "interpretation": "Filming continuity, not a breathing or health assessment"}
