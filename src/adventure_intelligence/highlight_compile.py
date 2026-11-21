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

Every recipe carries the story's model, harness status and packet SHA-256 and is marked
a draft; nothing here publishes or approves anything.
"""
from __future__ import annotations

from hashlib import sha256
import json
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
              order: str = "clock") -> dict:
    """Recipe from a story file ({record, packet}). `locate(sha, start, end)` returns
    {"proxy": Path, "segment": dict | None, "motion": list} for a scene."""
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
    clips, used, dropped = [], 0.0, []
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
        if used + (b - a) > target_seconds and clips:
            dropped.append({"scene": sid, "reason": "over the duration budget"})
            continue
        used += b - a
        m = moment_of.get(sid, {})
        clips.append({
            "scene": sid, "rank": rank, "sourceSha256": s["sourceSha256"], "file": s["file"],
            "clockStart": s["clockStart"], "proxy": str(where["proxy"]),
            "proxyInSeconds": a, "proxyOutSeconds": b,
            "originalInMs": s["startMs"] + int(a * 1000),
            "originalOutMs": s["startMs"] + int(b * 1000),
            "anchorSeconds": round(at, 3), "anchor": why,
            "what": m.get("what"), "whyItMatters": m.get("whyItMatters"),
            "quality": s["quality"]})
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
        "isDraft": True, "publicationApproved": False,
        "isModelProposalNotOwnerJudgment": True}
    recipe["recipeSha256"] = _digest({k: v for k, v in recipe.items() if k != "recipeSha256"})
    return recipe


def _has_audio(path: Path) -> bool:
    out = subprocess.run([require_tool("ffprobe"), "-v", "error", "-select_streams", "a",
                          "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, timeout=30, check=False)
    return bool(out.stdout.strip())


def _graph(n: int, durations: list[float], fade: bool, audio: bool, size) -> tuple[str, str, str | None, float]:
    w, h = size
    parts = []
    for i in range(n):
        parts.append(f"[{i}:v]fps=30,scale={w}:{h}:force_original_aspect_ratio=decrease,"
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
            target: Path, *, size, crf: int, preset: str, timeout: int) -> float:
    graph, vout, aout, total = _graph(len(inputs), durations, fade, audio, size)
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
                    size=PREVIEW_SIZE, crf=23, preset="veryfast", timeout=900)
    return {"version": VERSION, "kind": "preview", "recipeSha256": recipe["recipeSha256"],
            "path": str(target), "sha256": file_sha(target), "clips": len(clips),
            "estimatedSeconds": round(total, 2), "audio": audio, "originalVideoReads": 0,
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
                    size=size, crf=18, preset="medium", timeout=3600)
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
