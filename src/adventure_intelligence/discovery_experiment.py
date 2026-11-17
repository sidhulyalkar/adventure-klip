"""One resumable run: verify sources, scan motion and audio, segment, cut proxies, judge, report.

Scope is an explicit, owner-written list of recordings (at most MAX_SOURCES),
never the whole catalog. Every stage writes a receipt and is skipped when its
receipt exists, so an interrupted run continues where it stopped and nothing
is decoded twice. Sources are opened read-only after a SHA-256 check; all
output goes to a separate directory. Model calls go to Ollama on loopback.

The report describes model observations and segment proposals. It contains no
owner judgments and is not evidence that any clip is a highlight.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json
import subprocess
import time

from . import evidence_ledger as ledger
from . import judge_panel as panel
from . import timeline_segments as ts
from .full_archive_campaign import (MOTION_VERSION, _atomic_json, _hash_verified,
                                    _require_local_path)
from .media import motion_series, probe, require_tool

VERSION = "klip-discovery-experiment-v1"
MAX_SOURCES = 12
STAGES = ("verify", "motion", "audio", "segments", "proxies", "judge")
AUDIO_VERSION = "klip-discovery-audio-timeline-v1"
DEFAULTS = {"fps": 2, "budgetFraction": 0.25, "controls": 2, "minSegmentMs": 4000,
            "sensitivity": 3.0, "maxFrames": 16, "proxyWidth": 960, "proxyAudio": True,
            "models": ["qwen3-vl:32b", "gemma3:27b"]}


def load_sources(path: Path) -> list[dict]:
    """[{"sourceSha256", "path", "sport"}], written by the owner for this run."""
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_SOURCES:
        raise ValueError(f"Source list must name 1..{MAX_SOURCES} recordings")
    seen = set()
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {"sourceSha256", "path", "sport"}
                or not ledger._is_sha(row["sourceSha256"]) or row["sourceSha256"] in seen
                or not isinstance(row["path"], str) or row["sport"] not in panel.RUBRICS):
            raise ValueError("Each source needs a unique sourceSha256, a path and a known sport")
        seen.add(row["sourceSha256"])
    return rows


def _config(overrides: dict | None) -> dict:
    config = {**DEFAULTS, **(overrides or {})}
    if set(config) != set(DEFAULTS):
        raise ValueError("Unknown experiment setting")
    if len({panel.family(m) for m in config["models"]}) < 2:
        raise ValueError("Use judge models from at least two different families")
    config["judgeFingerprints"] = {
        m: {s: panel.fingerprint(m, s) for s in sorted(panel.RUBRICS)}
        for m in config["models"]}
    return config


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def cut_proxy(source: Path, dest: Path, start_ms: int, end_ms: int, *, width: int,
              audio: bool = True) -> None:
    """Re-encode one interval to a small H.264 file. The source is only read.

    Audio is kept by default: the owner compares clips with sound, and a
    shout or a crash is part of what makes a moment.
    """
    partial = dest.with_suffix(".partial.mp4")
    seconds = (end_ms - start_ms) / 1000
    job = subprocess.run(
        [require_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin",
         "-ss", f"{start_ms / 1000:.3f}", "-i", str(source), "-t", f"{seconds:.3f}",
         *(["-c:a", "aac", "-b:a", "64k", "-ac", "1"] if audio else ["-an"]), "-sn", "-vf", f"scale='min({width},iw)':-2", "-c:v", "libx264",
         "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p", "-y", str(partial)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        timeout=max(120, int(seconds * 4)), check=False)
    if job.returncode != 0 or not partial.is_file() or partial.stat().st_size < 1000:
        partial.unlink(missing_ok=True)
        raise ValueError("Proxy extraction failed")
    partial.replace(dest)


def _source_stages(row: dict, folder: Path, config: dict, stages, *, resolve, decode,
                   audio, cut, frames, transport, log, trusted_root: Path) -> dict:
    sha = row["sourceSha256"]
    timing: dict[str, float] = {}

    def timed(name, work):
        began = time.monotonic()
        result = work()
        timing[name] = round(time.monotonic() - began, 2)
        return result

    identity = _read(folder / "source.json")
    path = None
    if identity is None:
        path = resolve(row["path"])
        if path is None:
            under = Path(row["path"]).is_relative_to(trusted_root)
            raise ValueError("Source file not found under the trusted root" if under else
                             "Source path is not under --trusted-source-root")
        size = timed("verify", lambda: _hash_verified(path, sha))
        duration_ms = int(float(probe(path)["duration"]) * 1000)
        identity = {"version": VERSION, "sourceSha256": sha, "sport": row["sport"],
                    "sizeBytes": size, "durationMs": duration_ms,
                    "shaVerifiedForThisRun": True}
        _atomic_json(folder / "source.json", identity)
    duration_ms = identity["durationMs"]

    def source_path():
        # Later stages reopen the file without re-hashing; guard against a swap.
        nonlocal path
        if path is None:
            path = resolve(row["path"])
            if path is None or path.stat().st_size != identity["sizeBytes"]:
                raise ValueError("Source missing or changed since it was verified")
        return path

    if "motion" in stages and _read(folder / "motion.json") is None:
        log(f"{sha[:12]} decoding motion timeline")
        series = timed("motion", lambda: decode(source_path(), config["fps"], duration_ms / 1000))
        _atomic_json(folder / "motion.json", {
            "version": MOTION_VERSION, "sourceAssetSha256": sha,
            "sourceDurationMs": duration_ms, "sampleFps": config["fps"], "series": series})
    motion = _read(folder / "motion.json")

    if "audio" in stages and _read(folder / "audio.json") is None:
        # One loudness row per second. An empty series means no audio stream,
        # which is recorded so the file is not opened again.
        log(f"{sha[:12]} decoding audio timeline")
        rows = timed("audio", lambda: audio(source_path()))
        _atomic_json(folder / "audio.json", {
            "version": AUDIO_VERSION, "sourceSha256": sha, "windowMs": 1000,
            "series": [{"t_ms": r["t_ms"], "rms": r["rms"],
                        "spectralFlux": r.get("spectralFlux")} for r in rows]})

    if "segments" in stages and motion and _read(folder / "segments.json") is None:
        plan = ts.propose(
            ts.segment(ts.change_from_motion(motion["series"]), duration_ms,
                       min_segment_ms=config["minSegmentMs"], sensitivity=config["sensitivity"]),
            budget_fraction=config["budgetFraction"], controls=config["controls"])
        _atomic_json(folder / "segments.json", plan)
    plan = _read(folder / "segments.json")
    chosen = [s for s in (plan or {}).get("segments", []) if s["role"] != "not_reviewed"]

    if "proxies" in stages and chosen:
        (folder / "proxies").mkdir(exist_ok=True)
        began = time.monotonic()
        for s in chosen:
            dest = folder / "proxies" / f'{s["startMs"]}-{s["endMs"]}.mp4'
            if not dest.is_file():
                log(f'{sha[:12]} cutting {s["startMs"]}-{s["endMs"]} ms')
                cut(source_path(), dest, s["startMs"], s["endMs"], width=config["proxyWidth"],
                    audio=config["proxyAudio"])
        timing["proxies"] = round(time.monotonic() - began, 2)

    if "judge" in stages and chosen:
        (folder / "judgments").mkdir(exist_ok=True)
        began = time.monotonic()
        for s in chosen:
            name = f'{s["startMs"]}-{s["endMs"]}'
            proxy, out = folder / "proxies" / (name + ".mp4"), folder / "judgments" / (name + ".json")
            previous = _read(out) or {"judgments": []}
            done = {j["model"]: j for j in previous["judgments"] if j["status"] == "answered"}
            if not proxy.is_file() or set(done) == set(config["models"]):
                continue
            length = s["endMs"] - s["startMs"]
            times = panel.frame_plan(0, length, [t - s["startMs"] for t in s["eventCandidatesMs"]],
                                     max_frames=config["maxFrames"])
            images = frames(proxy, times)
            log(f"{sha[:12]} judging {name} ms")
            # Only judges without an answer are asked again, so a retry is cheap.
            judgments = [done.get(m) or panel.ask(m, row["sport"], images, times,
                                                  transport=transport)
                         for m in config["models"]]
            _atomic_json(out, {"version": VERSION, "sourceSha256": sha,
                               "startMs": s["startMs"], "endMs": s["endMs"], "role": s["role"],
                               "frameTimesMs": times, "judgments": judgments,
                               "panel": panel.combine(judgments)})
        timing["judge"] = round(time.monotonic() - began, 2)
    return {"timingSeconds": timing}


def build_report(output: Path, rows: list[dict], config: dict, failures: dict,
                 timings: dict, ledger_rows: list[dict] | None) -> dict:
    sources, queue = [], []
    for row in rows:
        sha = row["sourceSha256"]
        folder = output / sha
        identity, plan, motion = (_read(folder / n) for n in
                                  ("source.json", "segments.json", "motion.json"))
        entry = {"sourceSha256": sha, "sport": row["sport"],
                 "failure": failures.get(sha), "timingSeconds": timings.get(sha, {})}
        if identity and plan:
            judged = sorted((folder / "judgments").glob("*.json")) if (
                folder / "judgments").is_dir() else []
            panels = [json.loads(p.read_text(encoding="utf-8")) for p in judged]
            entry.update(
                durationMs=identity["durationMs"],
                segments=len(plan["segments"]),
                proposedFraction=plan["proposedFraction"],
                reviewSegments=sum(s["role"] != "not_reviewed" for s in plan["segments"]),
                judgedSegments=len(panels),
                segmentFacts=[{
                    "startMs": p["startMs"], "endMs": p["endMs"], "role": p["role"],
                    "agreed": {k: v["outcome"][7:] for k, v in p["panel"]["facts"].items()
                               if v["outcome"].startswith("agreed_")},
                    "needsOwner": p["panel"]["needsOwner"],
                    "abstained": p["panel"]["abstained"],
                    "failedControls": p["panel"]["failedControls"],
                } for p in panels])
            queue += [{"sourceSha256": sha, "proxy": f'{sha}/proxies/{p["startMs"]}-{p["endMs"]}.mp4',
                       "questions": p["panel"]["needsOwner"]}
                      for p in panels if p["panel"]["needsOwner"]]
            kept = [(r["startMs"], r["endMs"]) for r in (ledger_rows or [])
                    if r["sourceSha256"] == sha and r["decision"] == "keep"
                    and r["selection"] == "owner_marked_span"]
            if kept and motion:
                entry["ownerKeptSpans"] = len(kept)
                entry["comparison"] = ts.compare_to_fixed_windows(
                    motion["series"], identity["durationMs"], kept)
        sources.append(entry)
    facts = [f for s in sources for f in s.get("segmentFacts", [])]
    asked = len(facts) * len(config["models"])
    return {
        "version": VERSION, "config": config, "sources": sources,
        "totals": {
            "sources": len(rows), "failedSources": len(failures),
            "judgedSegments": len(facts),
            "judgeAbstentions": sum(len(f["abstained"]) for f in facts),
            "judgeControlFailures": sum(len(f["failedControls"]) for f in facts),
            "judgeCallsExpected": asked,
            "segmentsWithDisputedFacts": len(queue),
            "sourcesWithOwnerSpans": sum("comparison" in s for s in sources),
        },
        "ownerReviewQueue": queue,
        "evidenceLevel": "real_media_model_observation_unreviewed",
        "containsOwnerJudgments": False,
    }


def run(sources_file: Path, output: Path, *, workspace: Path, trusted_root: Path,
        allow_mounted: bool = False, overrides: dict | None = None,
        stages=STAGES, ledger_file: Path | None = None, decode=None, audio=None,
        cut=cut_proxy, frames=None, transport=None, log=lambda message: None) -> dict:
    rows = load_sources(sources_file)
    config = _config(overrides)
    if not set(stages) <= set(STAGES):
        raise ValueError("Unknown stage")
    output, trusted = Path(output).absolute(), Path(trusted_root).absolute()
    if output == trusted or output.is_relative_to(trusted) or output.is_symlink():
        raise ValueError("Output must be outside the footage directory and not a symlink")
    output.mkdir(parents=True, exist_ok=True)
    frozen = _read(output / "experiment.json")
    if frozen is None:
        _atomic_json(output / "experiment.json", {"version": VERSION, "config": config,
                                                  "sources": [r["sourceSha256"] for r in rows]})
    elif frozen["config"] != config or frozen["sources"] != [r["sourceSha256"] for r in rows]:
        raise ValueError("Settings or sources differ from this output directory's run; "
                         "use a new output directory")
    decode = decode or (lambda path, fps, seconds: motion_series(
        path, fps=fps, total_seconds=seconds))
    if audio is None:
        from .acoustics import audio_series as audio
    if frames is None:
        from .panel_frames import sample_frames as frames

    def resolve(path):
        return _require_local_path(path, Path(workspace), trusted, allow_mounted)

    failures, timings = {}, {}
    for row in rows:
        sha = row["sourceSha256"]
        (output / sha).mkdir(exist_ok=True)
        try:
            timings[sha] = _source_stages(
                row, output / sha, config, stages, resolve=resolve, decode=decode,
                audio=audio, cut=cut, trusted_root=trusted, frames=frames, transport=transport, log=log)["timingSeconds"]
        except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            # One bad recording must not stop the others, and is never a negative.
            failures[sha] = f"{type(error).__name__}: {error}"
            log(f"{sha[:12]} failed: {error}")
    report = build_report(output, rows, config, failures, timings,
                          ledger.load(ledger_file) if ledger_file else None)
    _atomic_json(output / "report.json", report)
    return report


def missing_models(models: list[str], *, fetch=None) -> list[str]:
    """Judge models not pulled into local Ollama. Raises if Ollama is not running."""
    if fetch is None:
        from urllib.error import URLError
        from urllib.request import urlopen

        def fetch():
            try:
                with urlopen("http://127.0.0.1:11434/api/tags", timeout=10) as response:
                    return json.loads(response.read(2_000_000))
            except (URLError, TimeoutError, ValueError):
                raise RuntimeError("Ollama is not running on this machine") from None
    installed = {m.get("name") for m in fetch().get("models", [])}
    return [m for m in models if m not in installed and m + ":latest" not in installed]


def source_digest(path: Path) -> str:
    """SHA-256 of a file, for writing the source list."""
    digest = sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
