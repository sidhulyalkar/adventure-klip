#!/usr/bin/env python3
"""One entry point from indexed footage to highlight reels.

    status   where every stage stands across the library (reads files only)
    reels    recipes from validated outing stories (near-duplicates dropped), preview
             reels from review clips, a gallery page; --ab-color writes a blind colour A/B
             deck; --color underwater-v1 applies the correction; --approve RECIPE_SHA
             --master renders full quality from the camera originals for approved recipes
    ab-score score a downloaded colour A/B answers file (exact sign test)
    run      judge pass -> stories (quality, describe, story) -> reels, each resumable

Stages and where they write (all new files; originals and earlier reports are not edited):
    REPORTS/discovery-NNN/              index batches (klip_value_experiment.py index)
    REPORTS/discovery-NNN/*/judgments/  judge pass (klip_judge_batches.py)
    REPORTS/outing-stories-v1/          stories (klip_outing_stories.py)
    REPORTS/highlights-v1/              recipes, previews, gallery, approvals, masters

The library index (a full read of the footage) is never started from here.

    klip_pipeline.py status --reports "$WS/reports"
    klip_pipeline.py run --reports "$WS/reports" --roster "$ROSTER" --trusted-source-root "$SSD"
    klip_pipeline.py reels --reports "$WS/reports"
    open "$WS/reports/highlights-v1/index.html"
    klip_pipeline.py reels --reports "$WS/reports" --trusted-source-root "$SSD" \\
        --approve <recipeSha256> --master
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import sys

from adventure_intelligence import discovery_experiment as experiment
from adventure_intelligence import highlight_compile as compile_

HERE = Path(__file__).resolve().parent


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(doc if isinstance(doc, str) else json.dumps(doc, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _batches(reports: Path) -> list[Path]:
    return sorted(p for p in reports.glob("discovery-*") if (p / "sources.json").is_file())


def _rows(folder: Path) -> list[dict]:
    return _read(folder / "judge-sources.json") or _read(folder / "sources.json") or []


def status(reports: Path) -> dict:
    batches = _batches(reports)
    index = {"batches": len(batches), "recordings": 0, "failedRecordings": 0}
    judge = Counter()
    for folder in batches:
        rows = _rows(folder)
        index["recordings"] += len(rows)
        index["failedRecordings"] += ((_read(folder / "report.json") or {})
                                      .get("totals", {}).get("failedSources", 0))
        frozen = _read(folder / "experiment.json")
        if frozen:
            work = experiment.judge_workload(folder, rows, frozen["config"]["models"])
            for k in ("segmentsForReview", "segmentsSkippedUnknownSport", "judgeCallsExpected",
                      "judgeCallsAnswered", "judgeCallsLeft", "panelsComplete"):
                judge[k] += work[k]
    stories_dir = reports / "outing-stories-v1"
    seg = stories_dir / "segments"
    described = [_read(p) or {} for p in seg.glob("*/*.description.json")]
    stories = [_read(p) or {} for p in (stories_dir / "stories").glob("*.json")]
    hl = reports / "highlights-v1"
    recipes = list(hl.glob("*.recipe.json"))
    return {
        "index": index,
        "judge": dict(judge),
        "stories": {"qualityMeasured": len(list(seg.glob("*/*.quality.json"))),
                    "qualityVerdicts": dict(Counter((_read(p) or {}).get("verdict")
                                                    for p in seg.glob("*/*.quality.json"))),
                    "described": sum(d.get("status") in ("accepted", "repaired")
                                     for d in described),
                    "descriptionsFailed": sum(d.get("status") not in ("accepted", "repaired")
                                              for d in described),
                    "outingStories": len(stories),
                    "byHarnessStatus": dict(Counter(s["record"]["harnessStatus"]
                                                    for s in stories if "record" in s))},
        "reels": {"recipes": len(recipes),
                  "previews": len(list(hl.glob("*.preview.mp4"))),
                  "approved": len((_read(hl / "approvals.json") or {}).get("approved", [])),
                  "masters": len(list(hl.glob("*.master.mp4")))},
    }


def _locator(reports: Path):
    homes = {}
    for folder in _batches(reports):
        for row in _rows(folder):
            homes.setdefault(row["sourceSha256"], folder / row["sourceSha256"])

    def locate(sha, start, end):
        rec = homes.get(sha)
        if rec is None:
            raise ValueError(f"No discovery folder holds {sha[:12]}")
        plan = _read(rec / "segments.json") or {"segments": []}
        segment = next((s for s in plan["segments"]
                        if s["startMs"] == start and s["endMs"] == end), None)
        return {"proxy": rec / "proxies" / f"{start}-{end}.mp4", "segment": segment,
                "motion": (_read(rec / "motion.json") or {}).get("series", [])}
    return locate


def _originals(reports: Path) -> dict[str, dict]:
    out = {}
    for folder in _batches(reports):
        for row in _read(folder / "sources.json") or []:
            source = _read(folder / row["sourceSha256"] / "source.json")
            if source:
                out[row["sourceSha256"]] = {"path": row["path"], "sizeBytes": source["sizeBytes"]}
    return out


def _embedder(reports: Path):
    """Stored SigLIP2 interval embeddings, if the description store exists."""
    folder = reports / "evidence"
    if not (folder / "vocabulary.json").is_file():
        return None
    try:
        from adventure_intelligence.moment_evidence import Store
        store = Store(folder)
    except (ValueError, OSError):
        return None

    def embed(sha, start, end):
        got = store.interval(sha, start, end)
        return None if got is None else got.get("embedding")
    return embed


def reels(reports: Path, *, target_seconds: float, order: str, master: bool,
          approve: list[str], trusted_root: Path | None, log, color_mode: str = "none",
          ab_color: bool = False, similarity_threshold: float = compile_.SIMILARITY_THRESHOLD,
          sampler=compile_.sample_rgb) -> dict:
    hl = reports / "highlights-v1"
    approvals_file = hl / "approvals.json"
    approvals = _read(approvals_file) or {"approved": []}
    known = {a["recipeSha256"] for a in approvals["approved"]}
    for sha in approve or []:
        if sha not in known:  # the owner's explicit approval, recorded with its time
            approvals["approved"].append({"recipeSha256": sha,
                                          "at": datetime.now(timezone.utc).isoformat()})
            known.add(sha)
    if approve:
        _write(approvals_file, approvals)
    locate = _locator(reports)
    embed = _embedder(reports)
    made = Counter()
    shown = []
    for story_file in sorted((reports / "outing-stories-v1" / "stories").glob("*.json")):
        doc = _read(story_file)
        if not doc or not (doc.get("record") or {}).get("story"):
            made["noStory"] += 1
            continue
        try:
            recipe = compile_.plan_reel(doc, locate, target_seconds=target_seconds, order=order,
                                        embed=embed, sampler=sampler, color_mode=color_mode,
                                        similarity_threshold=similarity_threshold)
        except ValueError as error:
            log(str(error))
            made["noShortlist"] += 1
            continue
        stem = story_file.stem
        recipe_file = hl / f"{stem}.recipe.json"
        old = _read(recipe_file)
        if old and old["recipeSha256"] != recipe["recipeSha256"]:
            n = len(list((hl / "history").glob(f"{stem}.*.recipe.json"))) + 1
            _write(hl / "history" / f"{stem}.{n:03d}.recipe.json", old)
        _write(recipe_file, recipe)
        preview_file, preview_meta = hl / f"{stem}.preview.mp4", hl / f"{stem}.preview.json"
        meta = _read(preview_meta)
        if not (meta and meta["recipeSha256"] == recipe["recipeSha256"] and preview_file.is_file()):
            log(f"rendering preview for {recipe['outing']} ({len(recipe['clips'])} clips)")
            meta = compile_.render_preview(recipe, preview_file)
            _write(preview_meta, meta)
            made["previews"] += 1
        if master and recipe["recipeSha256"] in known:
            if trusted_root is None:
                raise ValueError("--master needs --trusted-source-root")
            master_file, master_meta = hl / f"{stem}.master.mp4", hl / f"{stem}.master.json"
            mm = _read(master_meta)
            if not (mm and mm["recipeSha256"] == recipe["recipeSha256"] and master_file.is_file()):
                log(f"rendering master for {recipe['outing']} from the camera originals")
                _write(master_meta, compile_.render_master(
                    recipe, master_file, originals=_originals(reports),
                    trusted_root=trusted_root, approved=known))
                made["masters"] += 1
        shown.append({"recipe": recipe, "preview": meta})
        made["recipes"] += 1
    _write(hl / "index.html", compile_.gallery(shown))
    ab = None
    if ab_color:
        ab = compile_.color_ab_deck([x["recipe"] for x in shown], hl / "color-ab",
                                    seed="|".join(sorted(x["recipe"]["recipeSha256"] for x in shown)))
        log(f"colour A/B deck: {ab['pairs']} pairs -> {ab['page']}")
    return {**dict(made), "gallery": str(hl / "index.html"), "colorAB": ab,
            "dropped": Counter(d["reason"].split(" (")[0].split(" of ")[0]
                               for x in shown for d in x["recipe"]["dropped"]),
            "recipes": [{"outing": s["recipe"]["outing"],
                         "recipeSha256": s["recipe"]["recipeSha256"],
                         "clips": len(s["recipe"]["clips"]),
                         "seconds": s["preview"]["estimatedSeconds"] if s["preview"] else None,
                         "approved": s["recipe"]["recipeSha256"] in known} for s in shown]}


def main(argv=None, *, transport=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "reels", "run", "ab-score"):
        p = sub.add_parser(name)
        p.add_argument("--reports", type=Path, required=True)
    sub.choices["ab-score"].add_argument("--answers", type=Path, required=True)
    for p in (sub.choices["reels"], sub.choices["run"]):
        p.add_argument("--target-seconds", type=float, default=60.0)
        p.add_argument("--order", choices=("clock", "rank"), default="clock")
        p.add_argument("--trusted-source-root", type=Path)
        p.add_argument("--color", choices=compile_.COLOR_MODES, default="none")
        p.add_argument("--similarity-threshold", type=float,
                       default=compile_.SIMILARITY_THRESHOLD)
    sub.choices["reels"].add_argument("--approve", nargs="+", metavar="RECIPE_SHA256")
    sub.choices["reels"].add_argument("--master", action="store_true")
    sub.choices["reels"].add_argument("--ab-color", action="store_true",
                                      help="write a blind colour A/B deck")
    run = sub.choices["run"]
    run.add_argument("--roster", type=Path, required=True)
    run.add_argument("--through", choices=("judge", "stories", "reels"), default="reels")
    run.add_argument("--skip-judge", action="store_true")
    run.add_argument("--timeout", type=int, default=600)
    run.add_argument("--describe-model", default="qwen3-vl:32b")
    run.add_argument("--story-model", default="gemma3:27b")
    args = parser.parse_args(argv)

    def log(message):
        print("[pipeline]", message, file=sys.stderr, flush=True)

    try:
        reports = args.reports.expanduser()
        if args.command == "status":
            print(json.dumps(status(reports), indent=2))
            return 0
        if args.command == "ab-score":
            folder = reports / "highlights-v1" / "color-ab"
            key = _read(folder / "key.json")
            if key is None:
                raise ValueError("No colour A/B deck; run reels --ab-color first")
            result = compile_.color_ab_score(key, json.loads(args.answers.read_text(encoding="utf-8")))
            n = len(list(folder.glob("result-*.json"))) + 1
            _write(folder / f"result-{n:03d}.json", {**result, "answersFile": str(args.answers),
                                                     "at": datetime.now(timezone.utc).isoformat()})
            print(json.dumps(result, indent=2))
            return 0
        if args.command == "reels":
            print(json.dumps(reels(reports, target_seconds=args.target_seconds, order=args.order,
                                   master=args.master, approve=args.approve or [],
                                   trusted_root=args.trusted_source_root, log=log,
                                   color_mode=args.color, ab_color=args.ab_color,
                                   similarity_threshold=args.similarity_threshold), indent=2))
            return 0
        common = ["--reports", str(reports), "--roster", str(args.roster)]
        if not args.skip_judge:
            if args.trusted_source_root is None:
                raise ValueError("The judge pass needs --trusted-source-root (or --skip-judge)")
            log("stage: judge pass")
            code = _script("klip_judge_batches").main(
                common + ["--trusted-source-root", str(args.trusted_source_root),
                          "--timeout", str(args.timeout)])
            if code:
                return code
        if args.through in ("stories", "reels"):
            log("stage: stories (quality, describe, story)")
            code = _script("klip_outing_stories").main(
                common + ["--timeout", str(args.timeout), "--describe-model", args.describe_model,
                          "--story-model", args.story_model], transport=transport)
            if code:
                return code
        if args.through == "reels":
            log("stage: reels")
            print(json.dumps(reels(reports, target_seconds=args.target_seconds, order=args.order,
                                   master=False, approve=[], trusted_root=None, log=log,
                                   color_mode=args.color,
                                   similarity_threshold=args.similarity_threshold), indent=2))
        print(json.dumps(status(reports), indent=2))
    except (ValueError, KeyError, OSError, RuntimeError, PermissionError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
