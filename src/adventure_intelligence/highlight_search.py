"""Highlight-first search (stage A): find the moments worth a model's attention.

The calibration phase described a broad sample of review segments and showed that most model
time went to footage nobody keeps (lift lines, chairlifts, empty blue water). This stage turns
the funnel around: it searches EVERY stored second of a recording for what the owner says he
keeps - animal encounters, close companions, and fast or airborne skiing inside a real run -
and hands only those windows to the verifier (highlight_verify) and the storyteller.

No video is decoded and no model is called. It reads what earlier passes stored:
  * SigLIP2 frame embeddings every 2 s (moment_evidence store), scored against this stage's
    own query prompts. The query text embeddings are cached in this stage's output folder,
    so the evidence store's vocabulary (and its digest) is never rewritten;
  * the 2 fps pixel-change timeline (motion.json) and 1 s loudness (audio.json) that the
    discovery pass wrote for every scanned recording.

Scores are relative: a concept's logit is compared with that concept's distribution over the
DEVELOPMENT frames of the same sport (robust z: median and MAD), so a manta ray stands out
against ordinary reef rather than against a fixed probability. Held-out days are never used
to set a baseline or a threshold; they are scored with the frozen ones (see evaluate()).

Everything this module writes is a model-derived PROPOSAL with its inputs' versions and
digests attached. Nothing here is an owner label.
"""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import json
import math

import numpy as np

VERSION = "klip-highlight-search-v1"
QUERY_VERSION = "klip-highlight-queries-v1"
STEP_MS = 2000          # the evidence store samples a frame every 2 s

# Owner goals, as prompts. Several phrasings per concept; a concept scores its best phrasing.
QUERIES: dict[str, dict[str, list[str]]] = {
    "animal": {
        "manta_ray": ["a manta ray", "a giant manta ray swimming underwater",
                      "a large black and white manta ray gliding through blue water"],
        "eagle_ray": ["a spotted eagle ray", "an eagle ray with white spots swimming underwater"],
        "stingray": ["a stingray", "a stingray on the sandy sea floor",
                     "a stingray swimming underwater"],
        "shark": ["a shark", "a reef shark swimming underwater",
                  "a nurse shark resting on the sea floor"],
        "sea_turtle": ["a sea turtle", "a green sea turtle swimming underwater",
                       "a sea turtle resting on the reef"],
        "octopus": ["an octopus", "an octopus on the reef"],
        "dolphin": ["a dolphin", "dolphins swimming underwater"],
        "eel": ["a moray eel", "an eel poking its head out of the reef"],
    },
    "reef_scene": {
        "vivid_reef": ["a vibrant colorful coral reef in very clear water"],
        "fish_school": ["a large school of fish surrounding the camera"],
    },
    "companion": {
        "companion_close": ["a snorkeler swimming right next to the camera",
                            "a person's face in a snorkel mask close to the camera",
                            "a person waving at the camera underwater"],
    },
    "marine_background": {
        "open_water": ["empty blue open water"], "sand": ["a sandy sea floor"],
        "reef": ["a coral reef underwater"], "surface": ["the water surface seen from below"],
    },
    "ski_action": {
        "fast": ["a skier going fast down a steep slope", "a first person view skiing fast"],
        "powder": ["deep powder snow spraying", "skiing in deep fresh powder snow"],
        "jump": ["a skier in the air off a jump", "a first person view in the air above snow"],
        "trees": ["skiing through trees in a snowy forest"],
        "steep": ["looking down a steep ski run"],
    },
    "ski_companion": {
        "skier_ahead": ["another skier skiing just ahead of the camera"],
    },
    "ski_context": {
        "lift": ["a chairlift or gondola", "riding a chairlift above snow",
                 "the safety bar of a chairlift"],
        "queue": ["people waiting in a lift line", "a crowd of skiers standing in line"],
        "indoors": ["inside a ski lodge or building", "a parking lot"],
    },
    "quality": {
        "murky": ["murky cloudy water with poor visibility"],
        "blurry": ["a blurry out of focus image"], "dark": ["a very dark image"],
    },
}

# Owner marine-review animal classes (marine_encounter_review.ANIMAL_TYPES) -> our concepts.
OWNER_CLASS = {"manta_ray": "ray", "eagle_ray": "ray", "stingray": "ray", "shark": "shark",
               "sea_turtle": "turtle", "octopus": "octopus", "eel": "fish",
               "dolphin": "other"}

DEFAULTS = {
    "zMin": 4.0,            # robust z of a concept against its sport's development frames
    "pMin": 0.01,           # and a floor on SigLIP's own match probability
    "sceneZMin": 5.0,       # reef scenes are common: a higher bar, few per recording
    "scenesPerRecording": 2,
    "contextZ": 3.0,        # lift / queue / indoors: unusual for this sport's frames,
    "contextP": 0.05,       # or SigLIP's own match probability (a lift can fill a third of a
                            # ski day, so it is not unusual and z alone misses it)
    "qualityZ": 3.0,        # flag only
    "mergeGapMs": 4000,     # hits closer than this are one encounter
    "padMs": 3000,          # context around the first and last hit
    "coreMs": 3000,         # the tight window either side of the peak
    "stopRatio": 0.25,      # motion under this share of the sport's median = not moving
                            # (a quantile would call a fixed share of every day "stopped")
    "runGapMs": 30000,      # a stop longer than this ends a run
    "minRunMs": 20000,      # riding time for a stretch to count as a run
    "actionSmoothSteps": 4,  # 8 s
    "actionMin": 1.5,       # smoothed action score a ski moment must reach
    "momentsPerRun": 2,
    "momentSeparationMs": 20000,
    "skiBeforeMs": 4000, "skiAfterMs": 6000,
    "weights": {"speed": 0.5, "visual": 0.35, "companion": 0.15},
    "auditRate": 0.05, "auditWindowMs": 10000, "seed": 0,
}


def _digest(obj) -> str:
    return sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def settings(overrides: dict | None = None) -> dict:
    cfg = {**DEFAULTS, **(overrides or {})}
    unknown = set(cfg) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"Unknown search setting(s): {sorted(unknown)}")
    if set(cfg["weights"]) != set(DEFAULTS["weights"]):
        raise ValueError("weights must name speed, visual and companion")
    return cfg


# ---------------------------------------------------------------- queries

def query_rows(queries: dict | None = None) -> list[tuple[str, str, str]]:
    """(group, concept, prompt) for every prompt, in a stable order."""
    queries = queries or QUERIES
    return [(g, c, p) for g in queries for c in queries[g] for p in queries[g][c]]


def encode_queries(image, queries: dict | None = None) -> tuple[dict, np.ndarray]:
    """Text embeddings of every prompt with the evidence store's image model (SigLIP2)."""
    rows = query_rows(queries)
    vectors = image.texts([p for _, _, p in rows]).astype(np.float32)
    doc = {"version": QUERY_VERSION, "queries": queries or QUERIES,
           "queriesSha256": _digest(queries or QUERIES), "imageModel": image.model_id,
           "imageRevision": image.revision, "imageScale": image.scale,
           "imageBias": image.bias, "prompts": len(rows)}
    return doc, vectors


class Scorer:
    """Concept logits per frame: the best of the concept's prompts."""

    def __init__(self, doc: dict, vectors: np.ndarray):
        self.doc, self.rows = doc, query_rows(doc["queries"])
        if len(self.rows) != len(vectors):
            raise ValueError("Query embeddings do not match the query list; encode again")
        self.vectors = np.asarray(vectors, np.float32)
        self.concepts = list(dict.fromkeys(f"{g}:{c}" for g, c, _ in self.rows))
        self.index = {c: i for i, c in enumerate(self.concepts)}
        self._of = np.array([self.index[f"{g}:{c}"] for g, c, _ in self.rows])

    def logits(self, embeddings: np.ndarray) -> np.ndarray:
        raw = (self.doc["imageScale"] * (np.asarray(embeddings, np.float32) @ self.vectors.T)
               + self.doc["imageBias"])
        out = np.full((len(raw), len(self.concepts)), -np.inf, np.float32)
        for k in range(len(self.concepts)):
            out[:, k] = raw[:, self._of == k].max(axis=1)
        return out

    def group(self, name: str) -> list[str]:
        return [c for c in self.concepts if c.split(":", 1)[0] == name]


def sigmoid(x):
    return 1 / (1 + np.exp(-np.asarray(x, np.float64)))


# ---------------------------------------------------------------- baselines

def robust(values: np.ndarray, floor: float) -> tuple[float, float]:
    v = np.asarray(values, np.float64)
    v = v[np.isfinite(v)]
    if not len(v):
        return 0.0, floor
    med = float(np.median(v))
    return med, max(float(1.4826 * np.median(np.abs(v - med))), floor)


def baseline(recordings: list[dict], scorer: Scorer, held_out: set[str]) -> dict:
    """Per sport: each concept's median and spread over development frames, and the motion
    and loudness distributions. Held-out recordings never contribute."""
    by_sport: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for r in recordings:
        if r["sourceSha256"] in held_out:
            continue
        acc = by_sport[r["sport"]]
        if r.get("logits") is not None and len(r["logits"]):
            acc["logits"].append(r["logits"])
        grid = r.get("grid")
        if grid is not None:
            acc["motion"].append(grid["motion"])
            acc["loudness"].append(grid["loudness"])
    out = {}
    for sport, acc in by_sport.items():
        doc = {"frames": 0, "concepts": {}}
        if acc["logits"]:
            stack = np.concatenate(acc["logits"])
            doc["frames"] = int(len(stack))
            for k, name in enumerate(scorer.concepts):
                med, spread = robust(stack[:, k], 0.25)
                doc["concepts"][name] = {"median": round(med, 4), "spread": round(spread, 4)}
        for key, floor in (("motion", 1e-4), ("loudness", 0.5)):
            vals = np.concatenate(acc[key]) if acc[key] else np.zeros(0)
            vals = vals[np.isfinite(vals)]
            med, spread = robust(vals, floor)
            doc[key] = {"median": round(med, 6), "spread": round(spread, 6), "steps": int(len(vals)),
                        "quantiles": {str(q): round(float(np.quantile(vals, q)), 6)
                                      for q in (0.1, 0.3, 0.5, 0.7, 0.9)} if len(vals) else {}}
        out[sport] = doc
    return out


def zscores(logits: np.ndarray, scorer: Scorer, base: dict) -> np.ndarray:
    med = np.array([base["concepts"].get(c, {}).get("median", 0.0) for c in scorer.concepts])
    spread = np.array([base["concepts"].get(c, {}).get("spread", 1.0) for c in scorer.concepts])
    return (logits - med) / spread


# ---------------------------------------------------------------- timelines

def step_grid(times_ms, duration_ms: int) -> np.ndarray:
    """The evidence store's frame times, else a uniform 2 s grid over the recording."""
    if times_ms is not None and len(times_ms):
        return np.asarray(times_ms, np.int64)
    return np.arange(0, max(duration_ms, 1), STEP_MS, dtype=np.int64)


def resample(series: list[dict], key: str, grid: np.ndarray, step_ms: int = STEP_MS,
             transform=None) -> np.ndarray:
    """Mean of series[key] inside each [t, t + step); NaN where nothing was measured."""
    rows = sorted((r["t_ms"], float(r[key])) for r in series or []
                  if isinstance(r, dict) and type(r.get("t_ms")) is int
                  and isinstance(r.get(key), (int, float)) and math.isfinite(r[key]))
    out = np.full(len(grid), np.nan)
    if not rows:
        return out
    t = np.array([a for a, _ in rows])
    v = np.array([b for _, b in rows])
    if transform is not None:
        v = transform(v)
    lo = np.searchsorted(t, grid, side="left")
    hi = np.searchsorted(t, grid + step_ms, side="left")
    for i, (a, b) in enumerate(zip(lo, hi)):
        if b > a:
            out[i] = v[a:b].mean()
    return out


def loudness_db(rms: np.ndarray) -> np.ndarray:
    return 20 * np.log10(np.maximum(rms, 1e-6))


def timeline(rec: dict) -> dict:
    grid = step_grid(rec.get("times"), rec["durationMs"])
    return {"t": grid,
            "motion": resample(rec.get("motion") or [], "change", grid),
            "loudness": resample(rec.get("audio") or [], "rms", grid, transform=loudness_db)}


# ---------------------------------------------------------------- events

def _candidate_id(kind: str, sha: str, start: int, end: int) -> str:
    return sha256(f"{VERSION}|{kind}|{sha}|{start}|{end}".encode()).hexdigest()[:16]


def _runs(mask: np.ndarray, times: np.ndarray, gap_ms: int) -> list[tuple[int, int]]:
    """(first index, last index) of hit runs, joining hits at most gap_ms apart."""
    idx = np.flatnonzero(mask)
    if not len(idx):
        return []
    out, first, last = [], idx[0], idx[0]
    for i in idx[1:]:
        if times[i] - times[last] <= gap_ms:
            last = i
        else:
            out.append((first, last))
            first = last = i
    out.append((first, last))
    return out


def _flags(z_row: np.ndarray, scorer: Scorer, cfg: dict) -> list[str]:
    return [c.split(":", 1)[1] for c in scorer.group("quality")
            if z_row[scorer.index[c]] >= cfg["qualityZ"]]


def concept_events(rec: dict, z: np.ndarray, probs: np.ndarray, scorer: Scorer, group: str,
                   kind: str, cfg: dict, z_min: float) -> list[dict]:
    """Encounters with any concept of `group`, merged across concepts when they overlap
    (the same ray is often scored as both stingray and eagle ray)."""
    times = np.asarray(rec["times"])
    pieces = []
    for name in scorer.group(group):
        k = scorer.index[name]
        hit = (z[:, k] >= z_min) & (probs[:, k] >= cfg["pMin"])
        for a, b in _runs(hit, times, cfg["mergeGapMs"]):
            peak = a + int(np.argmax(z[a:b + 1, k]))
            pieces.append({"concept": name.split(":", 1)[1], "first": int(times[a]),
                           "last": int(times[b]), "hits": int(hit[a:b + 1].sum()),
                           "peakMs": int(times[peak]), "peakIndex": peak,
                           "peakZ": round(float(z[peak, k]), 2),
                           "peakProb": round(float(probs[peak, k]), 4)})
    pieces.sort(key=lambda p: p["first"])
    merged: list[list[dict]] = []
    for p in pieces:
        if merged and p["first"] - max(q["last"] for q in merged[-1]) <= cfg["mergeGapMs"]:
            merged[-1].append(p)
        else:
            merged.append([p])
    out = []
    duration = rec["durationMs"]
    for group_pieces in merged:
        best = max(group_pieces, key=lambda p: p["peakZ"])
        first = min(p["first"] for p in group_pieces)
        last = max(p["last"] for p in group_pieces)
        start = max(0, first - cfg["padMs"])
        end = min(duration, last + STEP_MS + cfg["padMs"])
        if end <= start:
            continue
        concepts = {}
        for p in sorted(group_pieces, key=lambda p: -p["peakZ"]):
            c = concepts.setdefault(p["concept"], {"concept": p["concept"], "peakZ": p["peakZ"],
                                                   "peakProb": p["peakProb"], "hits": 0,
                                                   "peakMs": p["peakMs"]})
            c["hits"] += p["hits"]
        hits = sum(c["hits"] for c in concepts.values())
        background = None
        bg = scorer.group("marine_background")
        if bg and kind != "ski_jump":
            row = z[best["peakIndex"]]
            background = max(bg, key=lambda c: row[scorer.index[c]]).split(":", 1)[1]
        out.append({
            "id": _candidate_id(kind, rec["sourceSha256"], start, end), "kind": kind,
            "sourceSha256": rec["sourceSha256"], "sport": rec["sport"], "file": rec.get("file"),
            "day": rec.get("day"), "outing": rec.get("outing"),
            "startMs": int(start), "endMs": int(end), "peakMs": best["peakMs"],
            "coreStartMs": max(0, best["peakMs"] - cfg["coreMs"]),
            "coreEndMs": min(duration, best["peakMs"] + cfg["coreMs"]),
            "top": best["concept"], "concepts": list(concepts.values()),
            "visibleSeconds": hits * STEP_MS / 1000,
            "score": round(best["peakZ"] + 0.5 * math.log1p(hits), 3),
            "background": background,
            "qualityFlags": _flags(z[best["peakIndex"]], scorer, cfg),
        })
    return out


# ---------------------------------------------------------------- ski runs and moments

def ski_states(rec: dict, tl: dict, z: np.ndarray | None, scorer: Scorer, base: dict,
               cfg: dict, probs: np.ndarray | None = None) -> np.ndarray:
    """'lift', 'queue', 'indoors', 'stopped' or 'riding' for every step."""
    n = len(tl["t"])
    state = np.array(["riding"] * n, dtype=object)
    median = base.get("motion", {}).get("median")
    stop_at = cfg["stopRatio"] * median if median else None
    motion = tl["motion"]
    if stop_at is not None:
        state[np.isfinite(motion) & (motion < stop_at)] = "stopped"
    state[~np.isfinite(motion)] = "stopped"
    if z is not None:
        for name in ("indoors", "queue", "lift"):   # later wins: a lift outranks a line
            key = f"ski_context:{name}"
            if key in scorer.index:
                hit = z[:, scorer.index[key]] >= cfg["contextZ"]
                if probs is not None:
                    hit |= probs[:, scorer.index[key]] >= cfg["contextP"]
                if name == "queue":
                    hit &= state == "stopped"
                if name == "lift":   # rides are long: a 5-step majority fills dropped frames
                    padded = np.pad(hit.astype(int), 2, mode="edge")
                    hit = np.convolve(padded, np.ones(5), "valid") >= 3
                state[hit] = name
    return state


def ski_runs(rec: dict, tl: dict, state: np.ndarray, cfg: dict) -> list[dict]:
    """Riding stretches between lifts, lines and long stops. A run that follows a lift
    started at the top; one that ends at a lift reached the bottom."""
    t = tl["t"]
    runs, i, n = [], 0, len(t)
    while i < n:
        if state[i] != "riding":
            i += 1
            continue
        j, last_ride = i, i
        while j + 1 < n and state[j + 1] in ("riding", "stopped"):
            j += 1
            if state[j] == "riding":
                if t[j] - t[last_ride] > cfg["runGapMs"]:
                    j = last_ride
                    break
                last_ride = j
        riding = int((state[i:last_ride + 1] == "riding").sum())
        if riding * STEP_MS >= cfg["minRunMs"]:
            before = next((state[k] for k in range(i - 1, -1, -1) if state[k] != "stopped"), None)
            after = next((state[k] for k in range(last_ride + 1, n) if state[k] != "stopped"), None)
            runs.append({"first": i, "last": last_ride, "startMs": int(t[i]),
                         "endMs": int(min(rec["durationMs"], t[last_ride] + STEP_MS)),
                         "ridingSeconds": riding * STEP_MS / 1000,
                         "afterLift": before == "lift", "beforeLift": after == "lift",
                         "recordingStart": before is None, "recordingEnd": after is None})
        i = max(j, last_ride) + 1
    for k, r in enumerate(runs):
        r["index"] = k
    return runs


def _clip(x):
    return np.clip(np.nan_to_num(x, nan=0.0), -3, 6)


def ski_action(tl: dict, z: np.ndarray | None, scorer: Scorer, base: dict,
               cfg: dict) -> dict:
    """Per-step speed index (motion and wind noise), visual action and companion scores."""
    m, l = base.get("motion", {}), base.get("loudness", {})
    zm = (tl["motion"] - m.get("median", 0)) / m.get("spread", 1)
    zl = (tl["loudness"] - l.get("median", 0)) / l.get("spread", 1)
    have = np.isfinite(zm).astype(float) + np.isfinite(zl).astype(float)
    speed = np.where(have > 0, (np.nan_to_num(zm) + np.nan_to_num(zl)) / np.maximum(have, 1),
                     np.nan)
    n = len(tl["t"])
    visual = np.zeros(n)
    which = np.array([None] * n, dtype=object)
    companion = np.zeros(n)
    if z is not None:
        names = scorer.group("ski_action")
        cols = z[:, [scorer.index[c] for c in names]]
        visual = cols.max(axis=1)
        which = np.array([names[k].split(":", 1)[1] for k in cols.argmax(axis=1)], dtype=object)
        comp = scorer.group("ski_companion")
        if comp:
            companion = z[:, [scorer.index[c] for c in comp]].max(axis=1)
    w = cfg["weights"]
    action = w["speed"] * _clip(speed) + w["visual"] * _clip(visual) + w["companion"] * _clip(companion)
    return {"speed": speed, "visual": visual, "visualConcept": which, "companion": companion,
            "action": action, "z": z}


def ski_moments(rec: dict, tl: dict, runs: list[dict], parts: dict, state: np.ndarray,
                cfg: dict, scorer: Scorer) -> list[dict]:
    t, duration = tl["t"], rec["durationMs"]
    width = max(1, cfg["actionSmoothSteps"])
    act = np.where(state == "riding", parts["action"], np.nan)
    smooth = np.array([np.nanmean(act[max(0, i - width // 2):i + width - width // 2])
                       if np.isfinite(act[max(0, i - width // 2):i + width - width // 2]).any()
                       else np.nan for i in range(len(t))])
    out = []
    jump_k = scorer.index.get("ski_action:jump")
    for run in runs:
        lo, hi = run["first"], run["last"]
        seg = smooth[lo:hi + 1].copy()
        picked = 0
        while picked < cfg["momentsPerRun"] and np.isfinite(seg).any():
            k = int(np.nanargmax(seg))
            if seg[k] < cfg["actionMin"]:
                break
            i = lo + k
            peak = int(t[i])
            start = max(0, peak - cfg["skiBeforeMs"])
            end = min(duration, peak + cfg["skiAfterMs"])
            reasons = []
            if np.isfinite(parts["speed"][i]) and parts["speed"][i] >= 2:
                reasons.append("speed")
            if parts["visual"][i] >= 2 and parts["visualConcept"][i]:
                reasons.append(parts["visualConcept"][i])
            if parts["companion"][i] >= 2:
                reasons.append("skier_ahead")
            airborne = (jump_k is not None and parts["z"] is not None
                        and parts["z"][i, jump_k] >= cfg["zMin"])
            kind = "ski_jump" if airborne else "ski_action"
            span = run["endMs"] - run["startMs"]
            out.append({
                "id": _candidate_id(kind, rec["sourceSha256"], start, end), "kind": kind,
                "sourceSha256": rec["sourceSha256"], "sport": rec["sport"], "file": rec.get("file"),
                "day": rec.get("day"), "outing": rec.get("outing"),
                "startMs": int(start), "endMs": int(end), "peakMs": peak,
                "coreStartMs": max(0, peak - cfg["coreMs"]),
                "coreEndMs": min(duration, peak + cfg["coreMs"]),
                "score": round(float(seg[k]), 3), "reasons": reasons,
                "speedIndex": None if not np.isfinite(parts["speed"][i]) else
                round(float(parts["speed"][i]), 2),
                "run": {"index": run["index"], "startMs": run["startMs"], "endMs": run["endMs"],
                        "ridingSeconds": run["ridingSeconds"], "afterLift": run["afterLift"],
                        "beforeLift": run["beforeLift"],
                        "position": round((peak - run["startMs"]) / span, 2) if span else 0.0},
                "visualEvidence": parts["z"] is not None,
            })
            picked += 1
            sep = cfg["momentSeparationMs"]
            for q in range(len(seg)):
                if abs(int(t[lo + q]) - peak) < sep:
                    seg[q] = np.nan
    return out


def run_candidates(rec: dict, runs: list[dict], parts: dict) -> list[dict]:
    """Whole runs, for a top-to-bottom reel: speed and terrain summarised over the run."""
    out = []
    for run in runs:
        sl = slice(run["first"], run["last"] + 1)
        speed = parts["speed"][sl]
        speed = speed[np.isfinite(speed)]
        p90 = float(np.quantile(speed, 0.9)) if len(speed) else 0.0
        vis = float(np.max(parts["visual"][sl])) if run["last"] >= run["first"] else 0.0
        score = 0.5 * min(max(p90, -3), 6) + 0.3 * min(max(vis, -3), 6) + 0.2 * math.log1p(
            run["ridingSeconds"] / 60)
        out.append({
            "id": _candidate_id("ski_run", rec["sourceSha256"], run["startMs"], run["endMs"]),
            "kind": "ski_run", "sourceSha256": rec["sourceSha256"], "sport": rec["sport"],
            "file": rec.get("file"), "day": rec.get("day"), "outing": rec.get("outing"),
            "startMs": run["startMs"], "endMs": run["endMs"],
            "score": round(score, 3), "speedP90": round(p90, 2), "visualPeak": round(vis, 2),
            "run": {k: run[k] for k in ("index", "ridingSeconds", "afterLift", "beforeLift",
                                        "recordingStart", "recordingEnd")},
            "visualEvidence": parts["z"] is not None,
        })
    return out


# ---------------------------------------------------------------- search

MARINE = ("snorkeling",)
SKI = ("ski",)


def search_recording(rec: dict, scorer: Scorer, base: dict, cfg: dict) -> dict:
    """Candidates and a timeline summary for one recording."""
    z = probs = None
    if rec.get("logits") is not None and len(rec["logits"]):
        z = zscores(rec["logits"], scorer, base)
        probs = sigmoid(rec["logits"])
    found: list[dict] = []
    summary = {"sourceSha256": rec["sourceSha256"], "sport": rec["sport"],
               "durationMs": rec["durationMs"], "visualEvidence": z is not None}
    if rec["sport"] in MARINE and z is not None:
        found += concept_events(rec, z, probs, scorer, "animal", "animal", cfg, cfg["zMin"])
        found += concept_events(rec, z, probs, scorer, "companion", "companion", cfg, cfg["zMin"])
        scenes = concept_events(rec, z, probs, scorer, "reef_scene", "reef_scene", cfg,
                                cfg["sceneZMin"])
        found += sorted(scenes, key=lambda c: -c["score"])[:cfg["scenesPerRecording"]]
    elif rec["sport"] in SKI:
        tl = timeline(rec)
        state = ski_states(rec, tl, z, scorer, base, cfg, probs)
        runs = ski_runs(rec, tl, state, cfg)
        parts = ski_action(tl, z, scorer, base, cfg)
        found += ski_moments(rec, tl, runs, parts, state, cfg, scorer)
        found += run_candidates(rec, runs, parts)
        summary["seconds"] = {s: round(float((state == s).sum()) * STEP_MS / 1000, 1)
                              for s in ("riding", "stopped", "lift", "queue", "indoors")}
        summary["runs"] = len(runs)
    summary["candidates"] = len(found)
    return {"candidates": found, "summary": summary}


MOMENT_KINDS = ("animal", "companion", "reef_scene", "ski_action", "ski_jump")


def audit_sample(recordings: list[dict], candidates: list[dict], cfg: dict) -> list[dict]:
    """A reproducible random sample of the footage the search did NOT propose, so the owner
    can tell how much it misses. Windows overlapping a proposed moment are not eligible;
    whole-run candidates do not count (they cover most of a ski day)."""
    taken = defaultdict(list)
    for c in candidates:
        if c["kind"] in MOMENT_KINDS:
            taken[c["sourceSha256"]].append((c["startMs"], c["endMs"]))
    pools: dict[str, list] = defaultdict(list)
    w = cfg["auditWindowMs"]
    for r in recordings:
        if r["sport"] not in MARINE + SKI:
            continue
        for start in range(0, max(0, r["durationMs"] - w // 2), w):
            end = min(r["durationMs"], start + w)
            if any(a < end and start < b for a, b in taken[r["sourceSha256"]]):
                continue
            key = sha256(f'{cfg["seed"]}|{r["sourceSha256"]}|{start}'.encode()).hexdigest()
            pools[r["sport"]].append((key, r, start, end))
    out = []
    for sport, pool in sorted(pools.items()):
        k = math.ceil(cfg["auditRate"] * len(pool)) if pool else 0
        for _, r, start, end in sorted(pool, key=lambda x: x[0])[:k]:
            out.append({"id": _candidate_id("audit", r["sourceSha256"], start, end),
                        "kind": "audit", "sourceSha256": r["sourceSha256"], "sport": sport,
                        "file": r.get("file"), "day": r.get("day"), "outing": r.get("outing"),
                        "startMs": start, "endMs": end, "peakMs": (start + end) // 2,
                        "score": None})
    return out


def search(recordings: list[dict], scorer: Scorer, held_out: set[str],
           overrides: dict | None = None) -> dict:
    """Stage A over every recording. recordings: dicts with sourceSha256, sport, durationMs,
    times (ms, the stored frame times) and logits (Scorer.logits of the stored embeddings),
    motion and audio series, and file/day/outing for display."""
    cfg = settings(overrides)
    for r in recordings:
        if r["sport"] in SKI:
            r["grid"] = timeline(r)
    base = baseline(recordings, scorer, held_out)
    found, summaries = [], []
    for r in recordings:
        b = base.get(r["sport"])
        if b is None:
            continue
        got = search_recording(r, scorer, b, cfg)
        found += got["candidates"]
        summaries.append({**got["summary"], "heldOut": r["sourceSha256"] in held_out})
    for c in found:
        c["heldOut"] = c["sourceSha256"] in held_out
    found.sort(key=lambda c: (c["kind"], -(c["score"] or 0), c["sourceSha256"], c["startMs"]))
    audit = audit_sample(recordings, found, cfg)
    return {"version": VERSION, "settings": cfg, "settingsSha256": _digest(cfg),
            "queriesSha256": scorer.doc["queriesSha256"], "imageModel": scorer.doc["imageModel"],
            "baseline": base, "candidates": found, "audit": audit, "recordings": summaries,
            "isModelProposalNotOwnerJudgment": True}


# ---------------------------------------------------------------- evaluation

def marine_sample_version() -> str:
    from .marine_encounter_review import VERSION as sample_version
    return sample_version


def marine_label_versions() -> tuple[str, str]:
    from .marine_encounter_review import LABEL_VERSION, LEGACY_LABEL_VERSION
    return LEGACY_LABEL_VERSION, LABEL_VERSION


def marine_truth(sample: dict, export: dict) -> list[dict]:
    """Owner animal labels from a frozen marine sample and its review export (v1 or v2)."""
    from .marine_encounter_review import validate_review
    clips = {c["recordId"]: c for c in sample["clips"]}
    out = []
    for r in validate_review(sample, export):
        clip = clips[r["recordId"]]
        span = ((r["eventStartMs"], r["eventEndMs"]) if r["eventStartMs"] is not None else None)
        out.append({"sourceSha256": r["sourceSha256"], "recordId": r["recordId"],
                    "sampleId": sample["sampleId"],
                    "clip": (clip["sourceStartMs"], clip["sourceEndMs"]), "span": span,
                    "presence": r["animalPresence"], "animalType": r["animalType"],
                    "decision": r["decision"]})
    return out


def _overlap(a: tuple[int, int], b: tuple[int, int]) -> int:
    return max(0, min(a[1], b[1]) - max(a[0], b[0]))


def _union_cover(span: tuple[int, int], windows: list[tuple[int, int]]) -> int:
    pts = sorted((max(a, span[0]), min(b, span[1])) for a, b in windows
                 if _overlap((a, b), span) > 0)
    covered, cur = 0, None
    for a, b in pts:
        if cur is None or a > cur[1]:
            if cur:
                covered += cur[1] - cur[0]
            cur = [a, b]
        else:
            cur[1] = max(cur[1], b)
    if cur:
        covered += cur[1] - cur[0]
    return covered


def evaluate(candidates: list[dict], truth: list[dict], *, searchable: set[str],
             held_out: set[str], snorkel_hours: dict[str, float] | None = None) -> dict:
    """Recall of owner-confirmed animals, false alarms on owner 'no animal' clips, and how
    tight the windows are, development and held-out apart.

    The marine sample was itself chosen by an earlier scout, so this recall is recall
    among clips that scout already surfaced, not over all footage; the audit sample is
    what estimates misses elsewhere."""
    animals = defaultdict(list)
    for c in candidates:
        if c["kind"] == "animal":
            animals[c["sourceSha256"]].append(c)
    rows = {"development": defaultdict(list), "heldOut": defaultdict(list)}
    unsearchable = 0
    for t in truth:
        if t["sourceSha256"] not in searchable:
            unsearchable += 1
            continue
        split = "heldOut" if t["sourceSha256"] in held_out else "development"
        target = t["span"] or t["clip"]
        near = [c for c in animals[t["sourceSha256"]]
                if _overlap((c["startMs"], c["endMs"]), target) > 0]
        rows[split][t["presence"]].append((t, near, target))
    out = {"version": VERSION, "unsearchable": unsearchable,
           "recallIsConditionalOnEarlierScoutSelection": True}
    for split, by in rows.items():
        present = by.get("present", [])
        hit = [r for r in present if r[1]]
        typed = [r for r in present if r[0]["animalType"] in set(OWNER_CLASS.values()) - {"other"}]
        type_hit = [r for r in typed if any(OWNER_CLASS.get(c["top"]) == r[0]["animalType"]
                                            for c in r[1])]
        by_type = defaultdict(lambda: [0, 0])
        for t, near, _ in present:
            by_type[t["animalType"]][0] += 1
            by_type[t["animalType"]][1] += bool(near)
        spans = [r for r in hit if r[0]["span"]]
        cover = [(_union_cover(r[2], [(c["startMs"], c["endMs"]) for c in r[1]]),
                  r[2][1] - r[2][0], sum(c["endMs"] - c["startMs"] for c in r[1]))
                 for r in spans]
        absent = by.get("absent", [])
        out[split] = {
            "present": len(present), "found": len(hit),
            "recall": round(len(hit) / len(present), 3) if present else None,
            "typedPresent": len(typed),
            "rightKind": round(len(type_hit) / len(typed), 3) if typed else None,
            "byOwnerType": {k: {"present": a, "found": b} for k, (a, b) in sorted(by_type.items())},
            "absentClips": len(absent),
            "falseAlarmRate": round(sum(bool(r[1]) for r in absent) / len(absent), 3)
            if absent else None,
            "possibleOrUnclear": len(by.get("possible", [])) + len(by.get("unclear", [])),
            "ownerSpansFound": len(spans),
            "medianSpanCovered": round(float(np.median([a / b for a, b, _ in cover])), 3)
            if cover else None,
            "medianWindowSeconds": round(float(np.median([w for *_, w in cover])) / 1000, 1)
            if cover else None,
            "missed": [{"sourceSha256": t["sourceSha256"], "recordId": t["recordId"],
                        "animalType": t["animalType"], "targetMs": list(target)}
                       for t, near, target in present if not near][:50],
        }
        hours = (snorkel_hours or {}).get(split)
        n = sum(1 for c in candidates if c["kind"] == "animal"
                and (c["sourceSha256"] in held_out) == (split == "heldOut"))
        out[split]["animalCandidates"] = n
        out[split]["candidatesPerHour"] = round(n / hours, 1) if hours else None
    return out


SWEEP_Z = (2.5, 3.0, 3.5, 4.0, 5.0, 6.0)
SWEEP_P = (0.001, 0.003, 0.01, 0.03)


def choose_operating_point(points: list[dict], recall_target: float = 0.9) -> dict | None:
    """Fewest candidates per hour among settings reaching the recall target on development
    labels; else the highest recall. Chosen on development only, then frozen."""
    usable = [p for p in points if p.get("recall") is not None]
    if not usable:
        return None
    ok = [p for p in usable if p["recall"] >= recall_target]
    if ok:
        return min(ok, key=lambda p: (p["candidatesPerHour"] or 0, -p["recall"]))
    return max(usable, key=lambda p: (p["recall"], -(p["candidatesPerHour"] or 0)))
