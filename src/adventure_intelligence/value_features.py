"""Evidence about one source interval, for ranking what the owner would keep.

Reads derived files only: a discovery experiment's motion, audio, segment and
judge receipts, and optionally cached frame embeddings from the catalog. No
video is opened. Every value here is a measurement or a model observation
about the footage. None of it says a moment is worth keeping; that is learned
from owner judgments in value_head and checked in value_eval.

Missing evidence is None and stays None. It is never scored as zero.
"""
from __future__ import annotations

from pathlib import Path
import json
import math
import sqlite3

import numpy as np

VERSION = "klip-value-features-v1"
SIGNALS = ("motion_mean", "motion_peak", "motion_peak_rel", "event_rate",
           "audio_mean", "audio_peak_rel", "audio_flux_peak",
           "novelty_outing", "novelty_archive", "duration_log", "position")
GUARD_MS = 30_000  # frames this close to an interval are the interval, not its context


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _robust(values: np.ndarray) -> tuple[float, float]:
    centre = float(np.median(values))
    spread = 1.4826 * float(np.median(np.abs(values - centre)))
    return centre, spread


def load_experiment(folder: Path) -> dict[str, dict]:
    """Per-source evidence from one discovery experiment output directory."""
    folder = Path(folder)
    frozen = _read(folder / "experiment.json")
    if not frozen or not isinstance(frozen.get("sources"), list):
        raise ValueError(f"Not a discovery experiment directory: {folder.name}")
    out = {}
    for sha in frozen["sources"]:
        identity = _read(folder / sha / "source.json")
        if not identity:
            continue  # failed or not yet verified; absent, not negative
        motion = _read(folder / sha / "motion.json") or {}
        audio = _read(folder / sha / "audio.json") or {}
        plan = _read(folder / sha / "segments.json") or {}
        facts = {}
        for path in sorted((folder / sha / "judgments").glob("*.json")) if (
                folder / sha / "judgments").is_dir() else []:
            doc = json.loads(path.read_text(encoding="utf-8"))
            facts[(doc["startMs"], doc["endMs"])] = {
                key: fact["outcome"] for key, fact in doc["panel"]["facts"].items()}
        m = np.array([(r["t_ms"], r["change"]) for r in motion.get("series", [])], dtype=float)
        a = np.array([(r["t_ms"], r["rms"],
                       np.nan if r.get("spectralFlux") is None else r["spectralFlux"])
                      for r in audio.get("series", [])], dtype=float)
        out[sha] = {
            "sourceSha256": sha, "sport": identity["sport"],
            "durationMs": identity["durationMs"], "experiment": folder.name,
            "motion": m.reshape(-1, 2), "audio": a.reshape(-1, 3),
            "segments": plan.get("segments", []), "facts": facts,
            "proxies": {(s["startMs"], s["endMs"]) for s in plan.get("segments", [])
                        if (folder / sha / "proxies" / f'{s["startMs"]}-{s["endMs"]}.mp4').is_file()},
            "frameTimes": None, "frames": None, "embeddingKey": None,
        }
    return out


def attach_embeddings(sources: dict[str, dict], workspace: Path,
                      *, provider: str = "siglip2") -> dict:
    """Add cached frame embeddings from the catalog where they exist. Read-only."""
    from .scene_atlas import frames_for_asset
    database = Path(workspace) / "catalog.sqlite3"
    if database.is_symlink() or not database.is_file():
        raise ValueError("No catalog in this workspace")
    keys: dict[str, int] = {}
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        loaded = {}
        for sha in sources:
            frames, key = frames_for_asset(db, Path(workspace), sha, provider)
            rows = {}
            for f in frames:
                t, v = f.get("tMs"), f.get("embedding")
                if type(t) is int and t >= 0 and isinstance(v, list) and v:
                    rows[t] = v
            if key and len(rows) >= 2:
                loaded[sha] = (key, rows)
                keys[key] = keys.get(key, 0) + 1
    # Vectors from different model revisions are not comparable; keep the commonest.
    chosen = max(keys, key=lambda k: (keys[k], k)) if keys else None
    for sha, (key, rows) in loaded.items():
        if key != chosen:
            continue
        times = sorted(rows)
        matrix = np.array([rows[t] for t in times], dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        if not np.isfinite(matrix).all() or (norms < 1e-8).any():
            continue
        sources[sha].update(frameTimes=np.array(times), frames=matrix / norms,
                            embeddingKey=key)
    return {"provider": provider, "modelKey": chosen,
            "sourcesWithEmbeddings": sum(s["frames"] is not None for s in sources.values()),
            "sourcesSkippedOtherModel": sum(k != chosen for k, _ in loaded.values())}


def _embedding(source: dict, start: int, end: int) -> np.ndarray | None:
    if source["frames"] is None:
        return None
    inside = (source["frameTimes"] >= start) & (source["frameTimes"] < end)
    if not inside.any():
        return None
    mean = source["frames"][inside].mean(axis=0)
    norm = float(np.linalg.norm(mean))
    return mean / norm if norm > 1e-8 else None


def _nearest(vector: np.ndarray, pool: np.ndarray | None, k: int) -> float | None:
    """1 - mean cosine similarity to the k most similar context frames."""
    if pool is None or len(pool) < k:
        return None
    sims = np.sort(pool @ vector)[-k:]
    return float(1.0 - sims.mean())


def _facts(source: dict, start: int, end: int) -> dict[str, str] | None:
    """Panel outcomes from the judged segment that covers at least half of this interval."""
    best, best_share = None, 0.0
    for (a, b), facts in source["facts"].items():
        shared = min(end, b) - max(start, a)
        share = shared / min(end - start, b - a) if shared > 0 else 0.0
        if share > best_share:
            best, best_share = facts, share
    return best if best_share >= 0.5 else None


def attach_described(sources: dict[str, dict], store) -> int:
    """Use description-pass embeddings (one model for every recording) where they exist."""
    count = 0
    for sha, source in sources.items():
        entry = store.recording(sha)
        if entry is not None and len(entry["times"]) >= 2:
            source.update(frameTimes=np.asarray(entry["times"]), frames=entry["embeddings"],
                          embeddingKey="described:" + store.doc["imageModel"])
            count += 1
    return count


def embedding_space(sources: dict[str, dict]) -> dict:
    """Which embedding space novelty and the head use, and what is set aside.

    Catalog and description-pass vectors come from different models (and sizes), so
    they are never compared with each other. The space covering the most recordings
    wins; on a tie the description pass wins, since it uses one model everywhere.
    """
    counts: dict[str, int] = {}
    for source in sources.values():
        if source["frames"] is not None:
            counts[source["embeddingKey"]] = counts.get(source["embeddingKey"], 0) + 1
    chosen = max(counts, key=lambda k: (counts[k], k.startswith("described:"), k)) if counts else None
    return {"space": chosen, "recordings": counts.get(chosen, 0),
            "setAside": {k: n for k, n in sorted(counts.items()) if k != chosen}}


def use_one_embedding_space(sources: dict[str, dict]) -> dict:
    """Drop vectors outside the chosen space (in memory only) and report the choice."""
    report = embedding_space(sources)
    for source in sources.values():
        if source["frames"] is not None and source["embeddingKey"] != report["space"]:
            source.update(frameTimes=None, frames=None, embeddingKey=None)
    return report


def build_items(sources: dict[str, dict], intervals: list[tuple[str, int, int]],
                group_of, *, seed: int = 0, archive_cap: int = 4000,
                evidence=None) -> dict[str, dict]:
    """One item per distinct (source, start, end) that has any evidence.

    Novelty compares an interval with the rest of its own outing (is this
    unlike the other footage from that day) and with other outings (is this
    unlike the archive). Both use only frames outside the interval.
    """
    space = embedding_space(sources)["space"]

    def in_space(source: dict) -> bool:
        return source["frames"] is not None and source["embeddingKey"] == space

    by_group: dict[str, list[str]] = {}
    for sha, source in sources.items():
        if in_space(source):
            by_group.setdefault(group_of(sha), []).append(sha)
    rng = np.random.default_rng(seed)
    stats = {}
    for sha, source in sources.items():
        motion, audio = source["motion"], source["audio"]
        stats[sha] = (_robust(motion[:, 1]) if len(motion) else None,
                      _robust(audio[:, 1]) if len(audio) else None)
    archive_pool: dict[str, np.ndarray | None] = {}

    def archive(group: str):
        if group not in archive_pool:
            parts = [sources[s]["frames"] for g, shas in sorted(by_group.items())
                     if g != group for s in sorted(shas)]
            pool = np.concatenate(parts) if parts else None
            if pool is not None and len(pool) > archive_cap:
                pool = pool[np.sort(rng.choice(len(pool), archive_cap, replace=False))]
            archive_pool[group] = pool
        return archive_pool[group]

    items: dict[str, dict] = {}
    for sha, start, end in sorted(set(intervals)):
        source = sources.get(sha)
        if source is None or not 0 <= start < end:
            continue
        group = group_of(sha)
        signals: dict[str, float | None] = {k: None for k in SIGNALS}
        seconds = (end - start) / 1000
        signals["duration_log"] = math.log(seconds)
        signals["position"] = min(1.0, ((start + end) / 2) / source["durationMs"])
        motion, audio = source["motion"], source["audio"]
        inside = motion[(motion[:, 0] >= start) & (motion[:, 0] < end), 1] if len(motion) else []
        if len(inside):
            centre, spread = stats[sha][0]
            signals["motion_mean"] = float(np.mean(inside))
            signals["motion_peak"] = float(np.max(inside))
            signals["motion_peak_rel"] = (float(np.max(inside)) - centre) / (spread + 1e-6)
            events = [t for s in source["segments"] for t in s.get("eventCandidatesMs", [])
                      if start <= t < end]
            signals["event_rate"] = len(events) / seconds
        heard = audio[(audio[:, 0] >= start) & (audio[:, 0] < end)] if len(audio) else []
        if len(heard):
            centre, spread = stats[sha][1]
            signals["audio_mean"] = float(np.mean(heard[:, 1]))
            signals["audio_peak_rel"] = (float(np.max(heard[:, 1])) - centre) / (spread + 1e-6)
            flux = heard[:, 2][np.isfinite(heard[:, 2])]
            signals["audio_flux_peak"] = float(np.max(flux)) if len(flux) else None
        vector = _embedding(source, start, end) if in_space(source) else None
        if vector is not None:
            context = []
            for other in by_group.get(group, []):
                frames, times = sources[other]["frames"], sources[other]["frameTimes"]
                if other == sha:
                    frames = frames[(times < start - GUARD_MS) | (times >= end + GUARD_MS)]
                context.append(frames)
            pool = np.concatenate(context) if context else None
            signals["novelty_outing"] = _nearest(vector, pool, 5)
            signals["novelty_archive"] = _nearest(vector, archive(group), 10)
        facts = _facts(source, start, end)
        role = next((s["role"] for s in source["segments"]
                     if s["startMs"] == start and s["endMs"] == end), "owner_interval")
        described = evidence.interval(sha, start, end) if evidence is not None else None
        items[item_id(sha, start, end)] = {
            "itemId": item_id(sha, start, end), "sourceSha256": sha,
            "startMs": start, "endMs": end, "group": group, "sport": source["sport"],
            "role": role, "signals": signals, "facts": facts, "embedding": vector,
            "hasProxy": (start, end) in source["proxies"],
            # Zero-shot concept peaks and when they peak: evidence, not judgments.
            "concepts": described["concepts"] if described else None,
            "peaksMs": described["peaksMs"] if described else None,
        }
    return items


def item_id(sha: str, start: int, end: int) -> str:
    return f"{sha}:{start}-{end}"


def segment_intervals(sources: dict[str, dict]) -> list[tuple[str, int, int]]:
    """Every segment of every source: the full-timeline pool a ranking chooses from."""
    return [(sha, s["startMs"], s["endMs"])
            for sha, source in sources.items() for s in source["segments"]]
