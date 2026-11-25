"""Compile highlight reels from validated outing stories.

recipe   plan_reel: the story's shortlist (already checked by story_harness: grounded, no
         unusable scene) becomes clips. Each scene is trimmed around its moment:
           the observation the moment cites (obs:n, a model-dated frame), else
           a jump/impact candidate from the motion timeline, else
           the motion peak inside the scene, else its middle.
         Windows are sport-shaped (lead-in before the anchor, follow-through after),
         clamped to the scene, and the reel is held to a duration budget by dropping
         the lowest-ranked scenes. Clips play in clock order, so the reel tells the
         day's story, unless order="rank".
preview  render_preview: from the review clips already cut. No camera original is read.
master   render_master: full quality from the camera originals, only for a recipe whose
         SHA-256 the owner approved, only under the trusted source root, and only when
         each original still has its recorded size. Originals are only read.

variety  Near-duplicates are dropped in rank order: a clip overlapping (or within
         DUPLICATE_GAP_S of) a kept clip from the same recording, or whose stored SigLIP2
         embedding (else a colour-thumbnail descriptor from the review clip) is at least
         `similarity_threshold` cosine-similar to a kept clip. Thresholds are v1,
         uncalibrated, and recorded with the recipe.
colour   Underwater clips (judges agreed "underwater", else a measured red deficit) get a
         proposed correction: red compensated from green (Ancuti et al. 2018, linear form)
         then a gentle per-channel levels stretch. Parameters are measured per clip and
         stored, so preview and master look the same. Applied only when the recipe's
         colorMode is "underwater-v1"; an A/B deck (color_ab_*) decides whether it should be.

Every recipe carries the story's model, harness status and packet SHA-256 and is marked
a draft; nothing here publishes or approves anything.
"""
from __future__ import annotations

from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile

from .media import require_tool

VERSION = "klip-highlight-compile-v1"

# Seconds of lead-in before the anchor and follow-through after it.
WINDOWS = {"ski": (3.0, 5.0), "bike": (3.0, 5.0), "snorkeling": (4.0, 6.0)}
DEFAULT_WINDOW = (3.0, 5.0)
MIN_CLIP_S = 2.0
FADE_SPORTS = {"snorkeling"}          # scenic dissolves; action sports hard-cut
PREVIEW_SIZE = (960, 540)
SIMILARITY_THRESHOLD = 0.92          # floor; the outing's own distribution sets the rest
SIMILARITY_QUANTILE = 0.9            # look-alike = more similar than 90% of the outing's pairs
MIN_REEL_CLIPS = 4
SAME_FOOTAGE = 0.985                 # at or above: the same footage, never brought back
DUPLICATE_GAP_S = 2.0
COLOR = {  # v1, uncalibrated; recorded with every proposal
    "redRatioUnderwater": 0.55,      # mean R / mean(G, B) below this reads as underwater
    "alpha": 1.0,                    # Ancuti red compensation strength
    "maxRedFromGreen": 0.6,
    "percentiles": (1.0, 99.0), "strength": 0.8, "minRange": 0.4,
}
COLOR_MODES = ("none", "underwater-v1")


def sample_rgb(proxy: Path, start_s: float, end_s: float, n: int = 4,
               size=(320, 180)) -> list:
    """RGB frames (uint8 HxWx3) evenly inside [start_s, end_s) of a review clip."""
    import numpy as np
    w, h = size
    out = []
    for i in range(n):
        t = start_s + (end_s - start_s) * (i + 0.5) / n
        job = subprocess.run(
            [require_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin",
             "-ss", f"{t:.3f}", "-i", str(proxy), "-frames:v", "1",
             "-vf", f"scale={w}:{h},format=rgb24", "-f", "rawvideo", "-"],
            capture_output=True, timeout=30, check=False)
        if job.returncode == 0 and len(job.stdout) == w * h * 3:
            out.append(np.frombuffer(job.stdout, np.uint8).reshape(h, w, 3))
    if not out:
        raise ValueError(f"Could not sample frames from {proxy.name}")
    return out


def thumbnail_descriptor(frames: list):
    """Unit vector of the mean 32x18 colour thumbnail, centred: a model-free look-alike test."""
    import numpy as np
    acc = []
    for f in frames:
        h, w, _ = f.shape
        bh, bw = h // 18, w // 32
        small = f[:bh * 18, :bw * 32].reshape(18, bh, 32, bw, 3).mean(axis=(1, 3))
        acc.append(small.astype(np.float32).ravel())
    v = np.mean(acc, axis=0)
    v = v - v.mean()
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-6 else None


def color_proposal(frames: list, scene: dict, sport: str) -> dict:
    """Whether a clip reads as underwater, and the measured correction if so."""
    import numpy as np
    rgb = np.concatenate([f.reshape(-1, 3) for f in frames]).astype(np.float32) / 255.0
    r, g, b = rgb[:, 0].mean(), rgb[:, 1].mean(), rgb[:, 2].mean()
    ratio = float(r / max(1e-6, (g + b) / 2))
    base = {"redRatio": round(ratio, 3), "thresholds": COLOR}
    if sport != "snorkeling":
        return {**base, "applies": False, "why": f"not underwater footage ({sport})"}
    if "underwater" in scene.get("factsNo", []):
        return {**base, "applies": False, "why": "judges agreed: not underwater"}
    if "underwater" in scene.get("factsYes", []):
        why = "judges agreed: underwater"
    elif ratio < COLOR["redRatioUnderwater"]:
        why = f"measured red deficit (R/GB {ratio:.2f})"
    else:
        return {**base, "applies": False, "why": f"no red deficit (R/GB {ratio:.2f})"}
    k = float(np.clip(COLOR["alpha"] * (g - r), 0.0, COLOR["maxRedFromGreen"]))
    comp = rgb.copy()
    comp[:, 0] = np.clip(comp[:, 0] + k * comp[:, 1], 0, 1)
    lo_p, hi_p = COLOR["percentiles"]
    lo = np.percentile(comp, lo_p, axis=0)
    hi = np.percentile(comp, hi_p, axis=0)
    st = COLOR["strength"]
    lo, hi = st * lo, 1 - st * (1 - hi)
    hi = np.maximum(hi, lo + COLOR["minRange"])
    hi = np.minimum(hi, 1.0)
    lo = np.minimum(lo, hi - COLOR["minRange"]).clip(0, 1)
    params = {"redFromGreen": round(k, 4), "low": [round(float(x), 4) for x in lo],
              "high": [round(float(x), 4) for x in hi]}
    return {**base, "applies": True, "why": why, "params": params,
            "filter": color_filter(params)}


def color_filter(params: dict) -> str:
    lo, hi = params["low"], params["high"]
    return (f"format=rgb24,colorchannelmixer=rr=1:rg={params['redFromGreen']:.4f},"
            f"colorlevels=rimin={lo[0]:.4f}:gimin={lo[1]:.4f}:bimin={lo[2]:.4f}:"
            f"rimax={hi[0]:.4f}:gimax={hi[1]:.4f}:bimax={hi[2]:.4f}")


def _digest(doc) -> str:
    return sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()


def file_sha(path: Path) -> str:
    h = sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _obs_seconds(observation: str) -> float | None:
    m = re.match(r"\s*([0-9]+(?:\.[0-9]+)?)s:", observation or "")
    return float(m.group(1)) if m else None


def anchor(moment: dict | None, scene: dict, segment: dict | None,
           motion: list[dict]) -> tuple[float, str]:
    """(seconds into the scene, why) for the instant a clip is built around."""
    length = (scene["endMs"] - scene["startMs"]) / 1000
    for ref in (moment or {}).get("evidence", []):
        kind, _, value = ref.partition(":")
        if kind == "obs" and value.isdigit() and 1 <= int(value) <= len(scene["observations"]):
            t = _obs_seconds(scene["observations"][int(value) - 1])
            if t is not None and 0 <= t <= length:
                return t, f"observation {value}"
    events = [(t - scene["startMs"]) / 1000 for t in (segment or {}).get("eventCandidatesMs", [])
              if scene["startMs"] <= t < scene["endMs"]]
    if events:
        return events[0], "event candidate"
    inside = [r for r in motion if scene["startMs"] <= r["t_ms"] < scene["endMs"]]
    if inside:
        peak = max(inside, key=lambda r: r["change"])
        return (peak["t_ms"] - scene["startMs"]) / 1000, "motion peak"
    return length / 2, "middle"


def window(at: float, length: float, sport: str) -> tuple[float, float]:
    before, after = WINDOWS.get(sport, DEFAULT_WINDOW)
    if length <= before + after:
        return 0.0, length
    start = min(max(0.0, at - before), length - (before + after))
    return round(start, 3), round(start + before + after, 3)


def plan_reel(story_doc: dict, locate, *, target_seconds: float = 60.0,
              order: str = "clock", embed=None, sampler=None,
              similarity_threshold: float = SIMILARITY_THRESHOLD,
              color_mode: str = "none") -> dict:
    """Recipe from a story file ({record, packet}).

    locate(sha, start, end) -> {"proxy": Path, "segment": dict | None, "motion": list}
    embed(sha, start_ms, end_ms) -> unit vector or None   (stored SigLIP2 embeddings)
    sampler(proxy, start_s, end_s) -> RGB frames           (colour and the fallback
                                                            look-alike descriptor)
    """
    import numpy as np
    if color_mode not in COLOR_MODES:
        raise ValueError(f"color_mode must be one of {COLOR_MODES}")
    record, packet = story_doc["record"], story_doc["packet"]
    story = record.get("story")
    if not story or not story.get("shortlist"):
        raise ValueError(f"{packet['outing']}: no validated shortlist to compile")
    if order not in ("clock", "rank"):
        raise ValueError("order must be clock or rank")
    scenes = {s["id"]: s for s in packet["scenes"]}
    moment_of = {}
    for m in story.get("moments", []):
        moment_of.setdefault(m["scene"], m)
    # Pass 1: every shortlisted scene becomes a candidate clip (window, look-alike vector).
    candidates, dropped = [], []
    for rank, sid in enumerate(story["shortlist"], 1):
        s = scenes[sid]
        if s["quality"]["verdict"] == "unusable":   # the harness already refuses this
            dropped.append({"scene": sid, "reason": "unusable"})
            continue
        where = locate(s["sourceSha256"], s["startMs"], s["endMs"])
        at, why = anchor(moment_of.get(sid), s, where.get("segment"), where.get("motion", []))
        a, b = window(at, (s["endMs"] - s["startMs"]) / 1000, packet["sport"])
        if b - a < MIN_CLIP_S:
            dropped.append({"scene": sid, "reason": f"under {MIN_CLIP_S} s"})
            continue
        o_in, o_out = s["startMs"] + int(a * 1000), s["startMs"] + int(b * 1000)
        frames = sampler(where["proxy"], a, b) if sampler else None
        vec, method = (embed(s["sourceSha256"], o_in, o_out) if embed else None), "siglip2"
        if vec is None and frames:
            vec, method = thumbnail_descriptor(frames), "thumbnail"
        m = moment_of.get(sid, {})
        candidates.append({
            "vec": None if vec is None else np.asarray(vec, np.float32), "method": method,
            "clip": {
                "scene": sid, "rank": rank, "sourceSha256": s["sourceSha256"], "file": s["file"],
                "clockStart": s["clockStart"], "proxy": str(where["proxy"]),
                "proxyInSeconds": a, "proxyOutSeconds": b,
                "originalInMs": o_in, "originalOutMs": o_out,
                "anchorSeconds": round(at, 3), "anchor": why,
                "what": m.get("what"), "whyItMatters": m.get("whyItMatters"),
                "quality": s["quality"], "lookalike": method if vec is not None else None,
                "color": color_proposal(frames, s, packet["sport"]) if frames else
                {"applies": False, "why": "not measured"}}})

    # The outing's own look-alike cut-off: footage of one place (a reef, a groomed run)
    # is similar overall, so a fixed cosine dropped distinct moments (26 on owner footage).
    sims = [float(x["vec"] @ y["vec"]) for i, x in enumerate(candidates)
            for y in candidates[i + 1:] if x["vec"] is not None and y["vec"] is not None
            and x["method"] == y["method"] and x["vec"].shape == y["vec"].shape]
    effective = similarity_threshold
    if len(sims) >= 3:
        effective = max(similarity_threshold, float(np.quantile(sims, SIMILARITY_QUANTILE)))

    def overlap(c, kept):
        return next((k for k in kept if k["sourceSha256"] == c["sourceSha256"]
                     and c["originalInMs"] < k["originalOutMs"] + DUPLICATE_GAP_S * 1000
                     and k["originalInMs"] < c["originalOutMs"] + DUPLICATE_GAP_S * 1000), None)

    # Pass 2: in rank order; same-recording overlaps always go, look-alikes are deferred.
    clips, vectors, methods, deferred, used = [], [], set(), [], 0.0
    for cand in candidates:
        c = cand["clip"]
        twin = overlap(c, clips)
        if twin:
            dropped.append({"scene": c["scene"], "reason": f"overlaps {twin['scene']} in the "
                                                          "same recording"})
            continue
        if cand["vec"] is not None:
            same = [(float(cand["vec"] @ v), k) for v, m, k in vectors
                    if m == cand["method"] and v.shape == cand["vec"].shape]
            best = max(same, key=lambda x: x[0], default=None)
            if best and best[0] >= effective:
                deferred.append((cand, best))
                continue
        length = c["proxyOutSeconds"] - c["proxyInSeconds"]
        if used + length > target_seconds and clips:
            dropped.append({"scene": c["scene"], "reason": "over the duration budget"})
            continue
        used += length
        clips.append(c)
        if cand["vec"] is not None:
            vectors.append((cand["vec"], cand["method"], c))
            methods.add(cand["method"])
    # A reel keeps at least MIN_REEL_CLIPS when the shortlist has them: look-alikes come
    # back in rank order, marked, before a reel is left with one or two clips.
    minimum = min(MIN_REEL_CLIPS, len(clips) + sum(sim < SAME_FOOTAGE for _, (sim, _o) in deferred))
    for cand, (sim, other) in deferred:
        c = cand["clip"]
        length = c["proxyOutSeconds"] - c["proxyInSeconds"]
        if (len(clips) < minimum and sim < SAME_FOOTAGE and not overlap(c, clips)
                and used + length <= target_seconds):
            used += length
            clips.append({**c, "nearDuplicateOf": other["scene"],
                          "similarity": round(sim, 3)})
            vectors.append((cand["vec"], cand["method"], c))
            methods.add(cand["method"])
        else:
            dropped.append({"scene": c["scene"], "reason": f"near-duplicate of {other['scene']} "
                            f"({cand['method']} cosine {sim:.3f} >= {effective:.3f})"})
    kept_sims = [float(v1 @ v2) for i, (v1, m1, _) in enumerate(vectors)
                 for (v2, m2, _) in vectors[i + 1:] if m1 == m2 and v1.shape == v2.shape]
    if order == "clock":
        position = {s["id"]: i for i, s in enumerate(packet["scenes"])}
        clips.sort(key=lambda c: position[c["scene"]])
    recipe = {
        "version": VERSION, "outing": packet["outing"], "sport": packet["sport"],
        "order": order, "targetSeconds": target_seconds,
        "transition": "crossfade" if packet["sport"] in FADE_SPORTS else "cut",
        "clips": clips, "dropped": dropped,
        "story": {"model": record["model"], "harnessStatus": record["harnessStatus"],
                  "packetSha256": record["packetSha256"]},
        "windows": {"before/after seconds": WINDOWS.get(packet["sport"], DEFAULT_WINDOW)},
        "variety": {"methods": sorted(methods) or ["none"],
                    "similarityThreshold": similarity_threshold,
                    "effectiveThreshold": round(effective, 3),
                    "minimumClips": MIN_REEL_CLIPS,
                    "duplicateGapSeconds": DUPLICATE_GAP_S,
                    "maxSimilarityKept": round(max(kept_sims), 3) if kept_sims else None},
        "colorMode": color_mode,
        "isDraft": True, "publicationApproved": False,
        "isModelProposalNotOwnerJudgment": True}
    recipe["recipeSha256"] = _digest({k: v for k, v in recipe.items() if k != "recipeSha256"})
    return recipe


def _has_audio(path: Path) -> bool:
    out = subprocess.run([require_tool("ffprobe"), "-v", "error", "-select_streams", "a",
                          "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, timeout=30, check=False)
    return bool(out.stdout.strip())


def _graph(n: int, durations: list[float], fade: bool, audio: bool, size,
           looks: list[str | None] | None = None) -> tuple[str, str, str | None, float]:
    w, h = size
    parts = []
    looks = looks or [None] * n
    for i in range(n):
        look = f"{looks[i]}," if looks[i] else ""
        parts.append(f"[{i}:v]{look}fps=30,scale={w}:{h}:force_original_aspect_ratio=decrease,"
                     f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p,"
                     f"setpts=PTS-STARTPTS[v{i}]")
        if audio:
            parts.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=mono,"
                         f"asetpts=PTS-STARTPTS[a{i}]")
    if not fade or n == 1:
        streams = "".join(f"[v{i}]" + (f"[a{i}]" if audio else "") for i in range(n))
        parts.append(f"{streams}concat=n={n}:v=1:a={1 if audio else 0}[vout]" +
                     ("[aout]" if audio else ""))
        return ";".join(parts), "[vout]", "[aout]" if audio else None, sum(durations)
    v, a, total = "[v0]", "[a0]" if audio else None, durations[0]
    for i in range(1, n):
        overlap = min(0.4, durations[i] / 5, durations[i - 1] / 5)
        parts.append(f"{v}[v{i}]xfade=transition=fade:duration={overlap:.3f}:"
                     f"offset={total - overlap:.3f}[vx{i}]")
        v = f"[vx{i}]"
        if audio:
            parts.append(f"{a}[a{i}]acrossfade=d={overlap:.3f}[ax{i}]")
            a = f"[ax{i}]"
        total += durations[i] - overlap
    return ";".join(parts), v, a, total


def _encode(inputs: list[list[str]], durations: list[float], fade: bool, audio: bool,
            target: Path, *, size, crf: int, preset: str, timeout: int,
            looks: list[str | None] | None = None) -> float:
    graph, vout, aout, total = _graph(len(inputs), durations, fade, audio, size, looks)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".klip-reel-", suffix=".mp4", dir=target.parent)
    os.close(fd)
    try:
        cmd = [require_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
               *[x for args in inputs for x in args], "-filter_complex", graph, "-map", vout,
               *(["-map", aout, "-c:a", "aac", "-b:a", "128k"] if aout else ["-an"]),
               "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
               "-movflags", "+faststart", temp]
        job = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        if job.returncode != 0 or Path(temp).stat().st_size < 1024:
            raise RuntimeError("ffmpeg failed: " + job.stderr.strip()[-300:])
        os.replace(temp, target)
    finally:
        Path(temp).unlink(missing_ok=True)
    return total


def _looks(recipe: dict) -> list[str | None]:
    """The colour filter each clip gets: only in underwater-v1 mode, only where it applies."""
    if recipe.get("colorMode", "none") == "none":
        return [None] * len(recipe["clips"])
    return [c["color"].get("filter") if c.get("color", {}).get("applies") else None
            for c in recipe["clips"]]


def render_preview(recipe: dict, target: Path) -> dict:
    """Draft reel from review clips only (960x540)."""
    clips = recipe["clips"]
    if not clips:
        raise ValueError("Recipe has no clips")
    paths = [Path(c["proxy"]) for c in clips]
    for p in paths:
        if p.is_symlink() or not p.is_file():
            raise ValueError(f"Review clip missing: {p}")
    durations = [c["proxyOutSeconds"] - c["proxyInSeconds"] for c in clips]
    audio = all(_has_audio(p) for p in paths)
    inputs = [["-ss", f'{c["proxyInSeconds"]:.3f}', "-t", f"{d:.3f}", "-i", str(p)]
              for c, d, p in zip(clips, durations, paths)]
    total = _encode(inputs, durations, recipe["transition"] == "crossfade", audio, target,
                    size=PREVIEW_SIZE, crf=23, preset="veryfast", timeout=900,
                    looks=_looks(recipe))
    return {"version": VERSION, "kind": "preview", "recipeSha256": recipe["recipeSha256"],
            "path": str(target), "sha256": file_sha(target), "clips": len(clips),
            "estimatedSeconds": round(total, 2), "audio": audio, "originalVideoReads": 0,
            "colorMode": recipe.get("colorMode", "none"),
            "isDraft": True, "publicationApproved": False}


def render_master(recipe: dict, target: Path, *, originals: dict[str, dict],
                  trusted_root: Path, approved: set[str], size=(1920, 1080)) -> dict:
    """Full-quality reel from camera originals; only for an owner-approved recipe.

    `originals` maps sourceSha256 -> {"path": str, "sizeBytes": int} (from the batch's
    sources.json and source.json). Originals are opened read-only by ffmpeg.
    """
    if recipe["recipeSha256"] not in approved:
        raise PermissionError("This recipe is not approved for a master render")
    if recipe["recipeSha256"] != _digest({k: v for k, v in recipe.items()
                                          if k != "recipeSha256"}):
        raise ValueError("Recipe changed after it was approved")
    root = trusted_root.resolve()
    inputs, durations, paths = [], [], []
    for c in recipe["clips"]:
        info = originals.get(c["sourceSha256"])
        if info is None:
            raise ValueError(f"No original recorded for {c['sourceSha256'][:12]}")
        path = Path(info["path"])
        if path.is_symlink() or not path.is_file() or root not in path.resolve().parents:
            raise ValueError(f"Original not found under the trusted source root: {path.name}")
        if path.stat().st_size != info["sizeBytes"]:
            raise ValueError(f"Original changed since it was verified: {path.name}")
        d = (c["originalOutMs"] - c["originalInMs"]) / 1000
        inputs.append(["-ss", f'{c["originalInMs"] / 1000:.3f}', "-t", f"{d:.3f}", "-i", str(path)])
        durations.append(d)
        paths.append(path)
    audio = all(_has_audio(p) for p in paths)
    total = _encode(inputs, durations, recipe["transition"] == "crossfade", audio, target,
                    size=size, crf=18, preset="medium", timeout=3600, looks=_looks(recipe))
    return {"version": VERSION, "kind": "master", "recipeSha256": recipe["recipeSha256"],
            "path": str(target), "sha256": file_sha(target), "clips": len(paths),
            "estimatedSeconds": round(total, 2), "audio": audio,
            "originalVideoReads": len(paths), "originalsModified": 0,
            "isDraft": False, "publicationApproved": False}


def gallery(reels: list[dict]) -> str:
    """One static page to watch every preview with why each clip is in it."""
    import html
    rows = []
    for r in reels:
        recipe, render = r["recipe"], r.get("preview")
        clips = "".join(
            f"<li><b>{html.escape(c['clockStart'] or '?')}</b> {html.escape(c['file'])} "
            f"{c['proxyInSeconds']:.0f}-{c['proxyOutSeconds']:.0f}s of scene {c['scene']} "
            f"({html.escape(c['anchor'])}): {html.escape(c.get('what') or '')}"
            f"<br><i>{html.escape(c.get('whyItMatters') or '')}</i></li>" for c in recipe["clips"])
        video = (f'<video controls preload="metadata" src="{html.escape(Path(render["path"]).name)}">'
                 f"</video>" if render else "<p>not rendered</p>")
        rows.append(f"<section><h2>{html.escape(recipe['outing'])}</h2>{video}"
                    f"<p>Draft, model-proposed (story by {html.escape(recipe['story']['model'])}, "
                    f"harness {html.escape(recipe['story']['harnessStatus'])}). Recipe "
                    f"<code>{recipe['recipeSha256'][:12]}</code></p><ol>{clips}</ol></section>")
    return ("<!doctype html><meta charset=utf-8><title>Klip highlight drafts</title>"
            "<style>body{font:15px system-ui;max-width:980px;margin:2em auto;padding:0 1em}"
            "video{width:100%;background:#000}section{margin-bottom:3em}</style>"
            "<h1>Highlight drafts</h1>" + "".join(rows))


# ---------------------------------------------------------------- blind colour A/B

def color_ab_deck(recipes: list[dict], folder: Path, *, max_pairs: int = 24,
                  seed: str = "") -> dict:
    """Same clip, original vs corrected, on a blind_ab page. Snippet names are random
    tokens, so nothing on the page says which is which."""
    import random
    from . import blind_ab
    rng = random.Random(seed or "klip-color-ab")
    candidates = [(r, c) for r in recipes for c in r["clips"]
                  if c.get("color", {}).get("applies")]
    rng.shuffle(candidates)
    folder.mkdir(parents=True, exist_ok=True)
    pairs = []
    for n, (recipe, c) in enumerate(candidates[:max_pairs], 1):
        d = c["proxyOutSeconds"] - c["proxyInSeconds"]
        paths = {}
        for look_name, look in (("corrected", c["color"]["filter"]), ("original", None)):
            token = sha256(f"{seed}|{recipe['recipeSha256']}|{c['scene']}|{look_name}".encode()
                           ).hexdigest()[:10]
            target = folder / f"clip-{token}.mp4"
            if not target.is_file():
                _encode([["-ss", f'{c["proxyInSeconds"]:.3f}', "-t", f"{d:.3f}",
                          "-i", c["proxy"]]], [d], False, False, target,
                         size=PREVIEW_SIZE, crf=20, preset="veryfast", timeout=600,
                         looks=[look])
            paths[look_name] = target
        pairs.append({"id": f"pair-{n:03d}", "variantPath": paths["corrected"],
                      "baselinePath": paths["original"],
                      "meta": {"recipeSha256": recipe["recipeSha256"], "outing": recipe["outing"],
                               "scene": c["scene"], "color": c["color"]}})
    return blind_ab.write_deck(folder, pairs, variant="corrected", baseline="original",
                               question="Which looks better? (colour and look only)",
                               seed=seed or "klip-color-ab")


def color_ab_score(key: dict, answers: dict) -> dict:
    from . import blind_ab
    r = blind_ab.score(key, answers)
    verdict = r["verdict"]
    if verdict == "corrected preferred":
        verdict = "correction preferred: use --color underwater-v1"
    elif verdict == "original preferred":
        verdict = "original preferred: keep --color none"
    elif verdict.startswith("no clear"):
        verdict = "no clear preference: keep --color none"
    return {**r, "preferCorrected": r["preferVariant"], "preferOriginal": r["preferBaseline"],
            "verdict": verdict}


def _sign_test(wins: int, losses: int) -> float:   # kept for callers of the old name
    from . import blind_ab
    return blind_ab.sign_test(wins, losses)


# ---------------------------------------------------------------- baseline reels

BASELINE_SIGNALS = ("motionPeak", "distinctiveness", "eventCandidates")


def baseline_doc(story_doc: dict, signal: str = "motionPeak") -> dict:
    """A story-shaped document whose shortlist ranks the same scenes by one plain signal,
    with as many scenes as the story picked. Plans with plan_reel exactly like a story,
    so a comparison differs only in which moments were chosen and in what order."""
    if signal not in BASELINE_SIGNALS:
        raise ValueError(f"signal must be one of {BASELINE_SIGNALS}")
    record, packet = story_doc["record"], story_doc["packet"]
    k = len(((record.get("story") or {}).get("shortlist")) or [])
    if k == 0:
        raise ValueError(f"{packet['outing']}: the story has no shortlist to match")
    usable = [s for s in packet["scenes"] if s["quality"]["verdict"] != "unusable"]
    ranked = sorted(usable, key=lambda s: (-(s["signals"].get(signal) or 0), s["id"]))
    return {"packet": packet, "record": {
        "model": f"baseline:{signal}", "harnessStatus": "not-applicable",
        "packetSha256": record["packetSha256"],
        "story": {"shortlist": [s["id"] for s in ranked[:k]], "moments": [], "beats": [],
                  "excluded": []}}}
