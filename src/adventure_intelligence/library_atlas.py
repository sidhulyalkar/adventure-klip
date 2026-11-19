"""A read-only map of the footage library: what was filmed, how, and what is labelled.

Before modelling "what is worth keeping", know the data:

- **How the library is made up:** trips, days and activities, with recording counts and
  footage minutes.
- **How each recording was shot:** camera, resolution, frame rate, colour, and whether the
  file carries a metadata track (DJI `djmd`, which may hold telemetry).
- **How the owner filmed:** when recording started and stopped, how long, retakes, and
  bursts of recordings minutes apart. Pressing record is the owner's first editing
  decision. These are facts about behaviour, never labels.
- **What the labels can and cannot answer:** which question (goal) each label source asked,
  on which activities and days, and whether a held-out evaluation is even possible per
  activity.

Only container headers are read (no decoding). Location values are never stored; only
whether a file has a location tag. Output stays in the workspace (it names files).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
import json
import statistics
import subprocess

from . import outing_roster as roster

VERSION = "klip-library-atlas-v1"
HEADER_VERSION = "klip-header-probe-v1"
SHORT_S = 3.0          # shorter than this is usually a mis-press
SEQUENCE_GAP_S = 120.0  # recordings this close belong to one burst of filming
RETAKE_GAP_S = 30.0     # stopped and started again almost at once
HIGH_FPS = 90.0         # a slow-motion mode: chosen on purpose
_TAG_KEYS = ("encoder", "major_brand", "make", "model", "firmware", "software")
_PRIVATE = ("location", "gps", "latitude", "longitude")


def _fps(rate) -> float | None:
    try:
        num, den = (float(x) for x in str(rate).split("/"))
        return round(num / den, 3) if den else None
    except ValueError:
        return None


def header(path: Path, run=subprocess.run) -> dict:
    """Container facts from ffprobe; reads headers only. Location values are dropped."""
    out = run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json",
               str(path)], capture_output=True, text=True, check=True, timeout=120)
    raw = json.loads(out.stdout)
    fmt, streams = raw.get("format", {}), raw.get("streams", [])
    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), {})
    vtags = {k.lower(): v for k, v in (video.get("tags") or {}).items()}
    rotation = next((d.get("rotation") for d in video.get("side_data_list") or []
                     if "rotation" in d), vtags.get("rotate"))
    keep = {k: str(v)[:80] for k, v in tags.items()
            if any(word in k for word in _TAG_KEYS) and not any(p in k for p in _PRIVATE)}
    return {
        "version": HEADER_VERSION,
        "tags": dict(sorted(keep.items())),
        "hasLocationTag": any(any(p in k for p in _PRIVATE) for k in tags)
                          or any(any(p in k.lower() for p in _PRIVATE)
                                 for s in streams for k in (s.get("tags") or {})),
        "video": {"codec": video.get("codec_name"), "profile": video.get("profile"),
                  "pixFmt": video.get("pix_fmt"), "width": video.get("width"),
                  "height": video.get("height"), "fps": _fps(video.get("avg_frame_rate")),
                  "colorTransfer": video.get("color_transfer"),
                  "colorPrimaries": video.get("color_primaries"),
                  "bitRate": int(video["bit_rate"]) if str(video.get("bit_rate", "")).isdigit()
                  else None,
                  "rotation": rotation},
        "audio": [{"codec": s.get("codec_name"), "channels": s.get("channels"),
                   "sampleRate": s.get("sample_rate")}
                  for s in streams if s.get("codec_type") == "audio"],
        "data": [{"tag": s.get("codec_tag_string"),
                  "handler": (s.get("tags") or {}).get("handler_name")}
                 for s in streams if s.get("codec_type") in ("data", "subtitle")
                 or (s.get("codec_type") == "video" and s is not video
                     and s.get("codec_tag_string") == "djmd")],
        "durationS": float(fmt["duration"]) if fmt.get("duration") else None,
    }


def headers(cache: Path, recordings: list[dict], *, probe=header, workers: int = 4,
            log=lambda message: None) -> dict[str, dict]:
    """Header facts per recording, cached by SHA and file size (append-only JSONL)."""
    cache = Path(cache)
    known: dict[str, dict] = {}
    if cache.is_file():
        for line in cache.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue  # a line cut short when a run was stopped
            if row.get("version") == HEADER_VERSION:
                known[row["sourceSha256"]] = row
    todo = []
    for r in recordings:
        size = Path(r["path"]).stat().st_size if Path(r["path"]).is_file() else None
        cached = known.get(r["sha"])
        if size is not None and not (cached and cached.get("sizeBytes") == size):
            todo.append((r, size))

    def one(item):
        r, size = item
        try:
            return {**probe(Path(r["path"])), "sourceSha256": r["sha"], "sizeBytes": size}
        except (subprocess.SubprocessError, OSError, ValueError, KeyError) as error:
            return {"version": HEADER_VERSION, "sourceSha256": r["sha"], "sizeBytes": size,
                    "failure": f"{type(error).__name__}: {str(error)[:160]}"}

    if todo:
        log(f"reading headers of {len(todo)} recordings ({len(known)} cached)")
        cache.parent.mkdir(parents=True, exist_ok=True)
        with ThreadPoolExecutor(max_workers=workers) as pool, \
                open(cache, "a", encoding="utf-8") as handle:
            for k, row in enumerate(pool.map(one, todo), 1):
                handle.write(json.dumps(row) + "\n")
                handle.flush()  # a stopped run keeps what it read
                known[row["sourceSha256"]] = row
                if k % 25 == 0 or k == len(todo):
                    log(f"headers {k}/{len(todo)}")
    return known


def mode(head: dict | None) -> str:
    """Shooting mode as one readable string, e.g. '2688x1512 50fps 10-bit HLG'."""
    if not head or "video" not in head:
        return "unknown"
    v = head["video"]
    depth = "10-bit" if "10" in str(v.get("pixFmt") or "") else "8-bit"
    colour = {"arib-std-b67": "HLG", "smpte2084": "PQ"}.get(v.get("colorTransfer"), "SDR")
    w, h = v.get("width"), v.get("height")
    if v.get("rotation") in (90, -90, 270, -270, "90", "-90", "270"):
        w, h = h, w
    fps = v.get("fps")
    return f"{w}x{h} {fps:g}fps {depth} {colour}" if fps else f"{w}x{h} {depth} {colour}"


def shooting(recordings: list[dict], day_of) -> dict[str, dict]:
    """Per recording: where it sits in its day and how it relates to its neighbours."""
    by_day: dict[str, list] = {}
    for r in recordings:
        when, _ = roster._clock(r.get("captureTime"))
        if when is not None:
            by_day.setdefault(day_of(r["sha"]), []).append((when, r))
    out: dict[str, dict] = {}
    for day, rows in by_day.items():
        rows.sort(key=lambda pair: (pair[0], pair[1]["sha"]))
        sequence, previous_end = 0, None
        for k, (when, r) in enumerate(rows):
            gap = None if previous_end is None else (when - previous_end).total_seconds()
            if gap is not None and gap > SEQUENCE_GAP_S:
                sequence += 1
            out[r["sha"]] = {"positionInDay": k + 1, "recordingsInDay": len(rows),
                             "gapBeforeS": None if gap is None else round(gap, 1),
                             "sequence": f"{day}#{sequence + 1}",
                             "retake": gap is not None and gap <= RETAKE_GAP_S}
            previous_end = when + timedelta(seconds=r.get("durationS") or 0)
    sizes: dict[str, int] = {}
    for row in out.values():
        sizes[row["sequence"]] = sizes.get(row["sequence"], 0) + 1
    for row in out.values():
        row["sequenceSize"] = sizes[row["sequence"]]
    return out


def _quantiles(values: list[float]) -> dict:
    if not values:
        return {}
    values = sorted(values)
    pick = lambda q: round(values[min(len(values) - 1, int(q * len(values)))], 1)  # noqa: E731
    return {"p10": pick(0.1), "median": round(statistics.median(values), 1), "p90": pick(0.9),
            "max": round(values[-1], 1)}


def build(recordings: list[dict], *, roster_doc: dict | None, heads: dict[str, dict],
          scanned: set[str], described: set[str], judgments: list[dict],
          pairs: list[dict]) -> dict:
    """Atlas summary plus one row per recording.

    recordings: [{"sha", "path", "durationS", "captureTime", "activity"}] from the catalog.
    """
    outing = roster.outing_by_source(roster_doc) if roster_doc else {}
    trip_of_outing = roster.trip_by_outing(roster_doc) if roster_doc else {}
    activity = roster.activity_by_source(roster_doc) if roster_doc else {}
    confirmed = bool(roster_doc and roster_doc.get("status") == "owner_confirmed")

    def day_of(sha):
        return roster.group_of(outing, sha)

    def act(sha, fallback):
        own = activity.get(sha, roster.UNLABELLED)
        return own if own != roster.UNLABELLED else roster._activity(fallback)

    shot = shooting(recordings, day_of)
    labels: dict[str, dict] = {}
    for j in judgments:
        row = labels.setdefault(j["sourceSha256"], {"keep": 0, "skip": 0, "unsure": 0, "goals": set()})
        row[j["decision"]] = row.get(j["decision"], 0) + 1
        row["goals"].add(j.get("goal") or "unspecified")
    in_pairs: dict[str, int] = {}
    for p in pairs:
        for side in ("left", "right"):
            in_pairs[p[side]["sourceSha256"]] = in_pairs.get(p[side]["sourceSha256"], 0) + 1

    rows, excluded = [], 0
    for r in sorted(recordings, key=lambda r: (str(r.get("captureTime")), r["path"])):
        sha, head = r["sha"], heads.get(r["sha"])
        a = act(sha, r.get("activity"))
        if a == roster.EXCLUDED:
            excluded += 1
        day = day_of(sha)
        fps = ((head or {}).get("video") or {}).get("fps")
        lab = labels.get(sha, {})
        rows.append({
            "sha12": sha[:12], "file": Path(r["path"]).name,
            "trip": trip_of_outing.get(day, day), "day": day, "activity": a,
            "captureTime": r.get("captureTime"), "durationS": round(r.get("durationS") or 0, 1),
            "short": (r.get("durationS") or 0) < SHORT_S,
            "mode": mode(head), "highFps": bool(fps and fps >= HIGH_FPS),
            "camera": (head or {}).get("tags", {}).get("encoder")
                      or (head or {}).get("tags", {}).get("model") or "unknown",
            "dataTracks": ",".join(sorted({d["tag"] or "?" for d in (head or {}).get("data", [])})),
            "hasLocationTag": bool((head or {}).get("hasLocationTag")),
            "headerFailure": (head or {}).get("failure"),
            **{k: v for k, v in shot.get(sha, {}).items()},
            "scanned": sha in scanned, "described": sha in described,
            "keeps": lab.get("keep", 0), "skips": lab.get("skip", 0),
            "unsure": lab.get("unsure", 0),
            "labelGoals": ",".join(sorted(lab.get("goals", ()))), "inPairs": in_pairs.get(sha, 0),
        })

    live = [x for x in rows if x["activity"] != roster.EXCLUDED]
    by_sha = {r["sha"]: r for r in recordings}
    act_of = {r["sha"]: act(r["sha"], r.get("activity")) for r in recordings}

    def tally(key):
        out: dict[str, dict] = {}
        for x in live:
            t = out.setdefault(str(x[key]), {"recordings": 0, "minutes": 0.0})
            t["recordings"] += 1
            t["minutes"] = round(t["minutes"] + x["durationS"] / 60, 1)
        return dict(sorted(out.items(), key=lambda kv: -kv[1]["minutes"]))

    trips: dict[str, dict] = {}
    for x in live:
        t = trips.setdefault(x["trip"], {"days": {}, "recordings": 0, "minutes": 0.0})
        d = t["days"].setdefault(x["day"], {"recordings": 0, "minutes": 0.0, "activities": {},
                                            "short": 0, "highFps": 0, "sequences": set(),
                                            "retakes": 0, "scanned": 0, "described": 0,
                                            "labelledRecordings": 0, "inPairs": 0})
        for target in (t, d):
            target["recordings"] += 1
            target["minutes"] = round(target["minutes"] + x["durationS"] / 60, 1)
        d["activities"][x["activity"]] = d["activities"].get(x["activity"], 0) + 1
        d["short"] += x["short"]
        d["highFps"] += x["highFps"]
        d["retakes"] += bool(x.get("retake"))
        if x.get("sequence"):
            d["sequences"].add(x["sequence"])
        d["scanned"] += x["scanned"]
        d["described"] += x["described"]
        d["labelledRecordings"] += bool(x["keeps"] or x["skips"] or x["unsure"])
        d["inPairs"] += x["inPairs"]
    for t in trips.values():
        for d in t["days"].values():
            d["sequences"] = len(d["sequences"])

    # Which question each label source asked, and where.
    asked: dict[str, dict] = {}
    for j in judgments:
        sha = j["sourceSha256"]
        g = asked.setdefault(j.get("origin", "unknown"), {}).setdefault(
            j.get("goal") or "unspecified",
            {"keep": 0, "skip": 0, "unsure": 0, "recordings": set(), "days": set(),
             "activities": {}})
        g[j["decision"]] = g.get(j["decision"], 0) + 1
        g["recordings"].add(sha)
        g["days"].add(day_of(sha))
        a = act_of.get(sha, "not in catalog")
        g["activities"][a] = g["activities"].get(a, 0) + 1
    for goals in asked.values():
        for g in goals.values():
            g["recordings"], g["days"] = len(g["recordings"]), len(g["days"])

    # Can a held-out evaluation be run per activity? Pairs need both sides scanned.
    evaluable: dict[str, dict] = {}
    for p in pairs:
        if not p.get("evalEligible", True):
            continue
        a = act_of.get(p["left"]["sourceSha256"], "not in catalog")
        e = evaluable.setdefault(a, {"pairs": 0, "pairsBothScanned": 0, "days": set(),
                                     "trips": set(), "goals": {}})
        e["pairs"] += 1
        both = p["left"]["sourceSha256"] in scanned and p["right"]["sourceSha256"] in scanned
        e["pairsBothScanned"] += both
        if both:
            for group in p["groups"]:
                e["days"].add(group)
                e["trips"].add(trip_of_outing.get(group, group))
        goal = p.get("goal") or p.get("tier")
        e["goals"][goal] = e["goals"].get(goal, 0) + 1
    for e in evaluable.values():
        e["daysWithScoredPairs"], e["tripsWithScoredPairs"] = len(e.pop("days")), len(e.pop("trips"))
        e["tripHoldoutPossible"] = e["tripsWithScoredPairs"] >= 5
        e["dayHoldoutPossible"] = e["daysWithScoredPairs"] >= 5

    data_tracks: dict[str, int] = {}
    for x in live:
        for tag in filter(None, x["dataTracks"].split(",")):
            data_tracks[tag] = data_tracks.get(tag, 0) + 1
    gaps = [x["gapBeforeS"] for x in live if x.get("gapBeforeS") is not None]
    summary = {
        "version": VERSION, "rosterConfirmed": confirmed,
        "library": {
            "recordings": len(live), "excludedAccidental": excluded,
            "hours": round(sum(x["durationS"] for x in live) / 3600, 2),
            "undated": sum(1 for x in live if x.get("positionInDay") is None),
            "short": sum(x["short"] for x in live), "highFps": sum(x["highFps"] for x in live),
            "durationS": _quantiles([x["durationS"] for x in live]),
            "gapBetweenRecordingsInADayS": _quantiles(gaps),
            "retakes": sum(bool(x.get("retake")) for x in live),
            "sequences": len({x["sequence"] for x in live if x.get("sequence")}),
            "withLocationTag": sum(x["hasLocationTag"] for x in live),
            "dataTracks": data_tracks,
            "headerFailures": sum(bool(x["headerFailure"]) for x in live),
            "notProbed": sum(x["mode"] == "unknown" and not x["headerFailure"] for x in live),
        },
        "byActivity": tally("activity"), "byMode": tally("mode"), "byCamera": tally("camera"),
        "coverage": {"scanned": sum(x["scanned"] for x in live),
                     "described": sum(x["described"] for x in live),
                     "labelled": sum(bool(x["keeps"] or x["skips"] or x["unsure"]) for x in live),
                     "inPairs": sum(bool(x["inPairs"]) for x in live), "of": len(live)},
        "trips": trips,
        "labelsAskedWhat": asked,
        "evaluableByActivity": dict(sorted(evaluable.items())),
        "labelsOnRecordingsNotInCatalog": sum(1 for j in judgments if j["sourceSha256"] not in by_sha),
    }
    return {"summary": summary, "rows": rows}
