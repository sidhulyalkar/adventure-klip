"""What the library holds, day by day: the basis for choosing what to evaluate next.

One row per capture day and sport: recordings, footage hours, micro-recordings, review
segments, judged panels (complete / unjudgeable), where the sport label came from (owner
tag, model guess, catalog), story quality verdicts, owner labels of every kind, and
whether the day is held out. Totals by sport on top. Reads files only.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path

from . import holdout, owner_labels

VERSION = "klip-dataset-profile-v1"


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def profile(reports: Path, ledger_rows: list[dict] | None = None) -> dict:
    reports = Path(reports)
    banned = holdout.held_out(reports)
    days: dict[tuple, dict] = defaultdict(lambda: defaultdict(int))
    day_of_sha: dict[str, tuple] = {}
    for folder in sorted(reports.glob("discovery-*")):
        rows = _read(folder / "judge-sources.json") or _read(folder / "sources.json") or []
        origin = (_read(folder / "judge-sources.provenance.json") or {}).get("sportFrom", {})
        for row in rows:
            sha = row["sourceSha256"]
            rec = folder / sha
            source = _read(rec / "source.json") or {}
            day = holdout.day_of(Path(row["path"]).name) or "undated"
            key = (day, row["sport"])
            day_of_sha[sha] = key
            d = days[key]
            d["recordings"] += 1
            d["footageSeconds"] += (source.get("durationMs") or 0) / 1000
            d["microRecordings"] += (source.get("durationMs") or 10 ** 9) < 2000
            d[f"sportFrom:{origin.get(sha, 'batch')}"] += 1
            plan = _read(rec / "segments.json") or {"segments": []}
            d["reviewSegments"] += sum(s["role"] != "not_reviewed" for s in plan["segments"])
            for j in (rec / "judgments").glob("*.json"):
                doc = _read(j) or {}
                d["judgedComplete" if doc.get("complete") else "judgedPartial"] += 1
            d["unjudgeable"] += len(list((rec / "unjudgeable").glob("*.json")))
            d["heldOutRecordings"] += sha in banned
    for q in (reports / "outing-stories-v1" / "segments").glob("*/*.quality.json"):
        doc = _read(q) or {}
        key = day_of_sha.get(q.parent.name)
        if key:
            days[key][f"quality:{doc.get('verdict')}"] += 1
    for kind in owner_labels.KINDS:
        for l in owner_labels.load(reports, kind):
            key = day_of_sha.get(l.get("sourceSha256"))
            if key:
                days[key][f"owner{kind.title()}Labels"] += 1
    for r in ledger_rows or []:
        key = day_of_sha.get(r["sourceSha256"])
        if key:
            days[key][f"ownerLedger:{r['decision']}"] += 1
    table = []
    for (day, sport), d in sorted(days.items()):
        row = {"day": day, "sport": sport, **{k: (round(v / 3600, 2) if k == "footageSeconds" else v)
                                              for k, v in sorted(d.items())}}
        row["footageHours"] = row.pop("footageSeconds")
        row["heldOut"] = d["heldOutRecordings"] > 0
        table.append(row)
    totals: dict[str, Counter] = defaultdict(Counter)
    for row in table:
        for k, v in row.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                totals[row["sport"]][k] += v
    return {"version": VERSION, "days": table,
            "bySport": {s: {k: round(v, 2) for k, v in c.items()} for s, c in sorted(totals.items())},
            "heldOutDays": sum(r["heldOut"] for r in table)}


def markdown(doc: dict) -> str:
    cols = ["day", "sport", "recordings", "footageHours", "microRecordings", "reviewSegments",
            "judgedComplete", "unjudgeable", "ownerLedger:keep", "ownerLedger:skip",
            "ownerBoundaryLabels", "ownerFactLabels", "heldOut"]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in doc["days"]:
        lines.append("| " + " | ".join(str(r.get(c, 0)) for c in cols) + " |")
    lines += ["", "| sport | recordings | footage hours | review segments | judged |",
              "|---|---|---|---|---|"]
    for s, t in doc["bySport"].items():
        lines.append(f"| {s} | {int(t.get('recordings', 0))} | {t.get('footageHours', 0)} | "
                     f"{int(t.get('reviewSegments', 0))} | {int(t.get('judgedComplete', 0))} |")
    return "\n".join(lines) + "\n"
