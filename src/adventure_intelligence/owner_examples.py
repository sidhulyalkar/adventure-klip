"""The owner's own past judgments as examples for an outing story.

For each outing, the owner's keep/skip judgments (evidence ledger) most similar to its
scenes (stored SigLIP2 interval embeddings) are shown to the storyteller with the owner's
own words (scene description, why keep). No leakage: examples never come from held-out
recordings, from the outing's own recordings or from the same capture day. Without stored
embeddings no examples are chosen (no random filler). At most MAX_KEEPS keeps, so the
storyteller sees what the owner skips too.
"""
from __future__ import annotations

import re

VERSION = "klip-owner-examples-v1"
MAX_EXAMPLES = 8
MAX_KEEPS = 5
_DAY = re.compile(r"DJI_(\d{8})")


def _day(name: str | None) -> str | None:
    m = _DAY.search(name or "")
    return m.group(1) if m else None


def _text(row: dict) -> tuple[str, str | None]:
    d = row.get("detail") or {}
    what = d.get("sceneDescription") or d.get("sceneType") or d.get("activity") or "a clip"
    if d.get("animalType") and d.get("animalType") not in str(what):
        what = f"{what} ({d['animalType']})"
    return str(what)[:240], (str(d["whyKeep"])[:240] if d.get("whyKeep") else None)


def select(scenes: list[dict], ledger_rows: list[dict], *, embed, names: dict[str, str],
           held_out: set[str], k: int = MAX_EXAMPLES, max_keeps: int = MAX_KEEPS) -> list[dict]:
    """scenes: [{sourceSha256, startMs, endMs, file}]; embed(sha, a, b) -> unit vector | None."""
    import numpy as np
    if embed is None:
        return []
    own = {s["sourceSha256"] for s in scenes}
    days = {_day(s.get("file")) for s in scenes} - {None}
    vectors = [v for v in (embed(s["sourceSha256"], s["startMs"], s["endMs"]) for s in scenes)
               if v is not None]
    if not vectors:
        return []
    scene_m = np.stack([np.asarray(v, np.float32) for v in vectors])
    scored = []
    for row in ledger_rows:
        sha = row["sourceSha256"]
        if row["decision"] not in ("keep", "skip") or sha in held_out or sha in own:
            continue
        day = _day(names.get(sha))
        if day in days:
            continue
        v = embed(sha, row["startMs"], row["endMs"])
        if v is None:
            continue
        sim = float((scene_m @ np.asarray(v, np.float32)).max())
        what, why = _text(row)
        scored.append({"decision": row["decision"], "text": what, "reason": why,
                       "similarity": round(sim, 3), "sourceSha256": sha,
                       "evidenceId": row.get("evidenceId")})
    scored.sort(key=lambda e: -e["similarity"])
    out, keeps, seen = [], 0, set()
    for e in scored:
        if len(out) >= k:
            break
        if (e["sourceSha256"], e["text"]) in seen:
            continue
        if e["decision"] == "keep":
            if keeps >= max_keeps:
                continue
            keeps += 1
        seen.add((e["sourceSha256"], e["text"]))
        out.append(e)
    return out
