#!/usr/bin/env python3
"""Grounded outing stories over the discovery folders (see story_harness).

Stages (each resumable, each writes only new files under --output):
  quality   no model: sharpness, exposure, covered lens, shake per review segment
  describe  VLM: dated observations per review segment, from the clips already cut
  story     LLM: one validated story, moments and shortlist per outing

No camera original is opened. Sports come from judge-sources.json when a judge pass wrote
one (roster relabels, with provenance), else from the batch's sources.json. Outings come
from --roster, else the capture day in the DJI file name.

    klip_outing_stories.py --reports "$WS/reports" --roster "$ROSTER" --dry-run
    klip_outing_stories.py --reports "$WS/reports" --roster "$ROSTER" --stages quality
    klip_outing_stories.py --reports "$WS/reports" --roster "$ROSTER" --max-calls 3
    caffeinate -i klip_outing_stories.py --reports "$WS/reports" --roster "$ROSTER"
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import nullcontext
import json
import os
from pathlib import Path
import re
from statistics import median
import sys

from adventure_intelligence import judge_panel as panel
from adventure_intelligence import judge_reliability
from adventure_intelligence import outing_roster as roster
from adventure_intelligence import run_lock
from adventure_intelligence import story_harness as harness
from adventure_intelligence.discovery_experiment import FRAME_MARGIN_MS, MIN_VIDEO_MS
from adventure_intelligence.panel_frames import video_span

STAGES = ("quality", "describe", "story")
SKIP_SPORTS = {"unknown"}


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2) if not isinstance(doc, str) else doc,
                   encoding="utf-8")
    os.replace(tmp, path)


def collect(reports: Path, outing_of: dict[str, str]) -> list[dict]:
    """Every review segment with a clip, across discovery folders, with its context."""
    out = []
    for folder in sorted(reports.glob("discovery-*")):
        listing = folder / "judge-sources.json"
        rows = _read(listing) or _read(folder / "sources.json") or []
        for row in rows:
            sha, rec = row["sourceSha256"], folder / row["sourceSha256"]
            plan, source = _read(rec / "segments.json"), _read(rec / "source.json")
            if not plan or not source:
                continue
            name = Path(row["path"]).name
            capture = harness.capture_start(name)
            outing = outing_of.get(sha) or (f"day-{capture:%Y-%m-%d}" if capture else
                                            f"recording-{sha[:12]}")
            motion = (_read(rec / "motion.json") or {}).get("series", [])
            for s in plan["segments"]:
                if s["role"] == "not_reviewed":
                    continue
                key = f'{s["startMs"]}-{s["endMs"]}'
                proxy = rec / "proxies" / f"{key}.mp4"
                if proxy.is_file():
                    out.append({"folder": folder.name, "sourceSha256": sha, "file": name,
                                "sport": row["sport"], "outing": outing, "capture": capture,
                                "segment": s, "key": key, "proxy": proxy,
                                "judged": _read(rec / "judgments" / f"{key}.json"),
                                "durationMs": source["durationMs"], "motion": motion,
                                "allSegments": plan["segments"]})
    return out


def _times(item: dict, max_frames: int) -> list[int]:
    judged = item["judged"]
    if judged and judged.get("frameTimesMs"):
        return judged["frameTimesMs"]
    s = item["segment"]
    length = s["endMs"] - s["startMs"]
    span = video_span(item["proxy"])   # plan inside the clip's real video
    if span is not None:
        if span < MIN_VIDEO_MS:
            raise ValueError(f"review clip has no usable video ({span} ms)")
        length = min(length, span - FRAME_MARGIN_MS)
    return panel.frame_plan(0, length, [t - s["startMs"] for t in s["eventCandidatesMs"]],
                            max_frames=max_frames)


def run_quality(items: list[dict], out: Path, log) -> int:
    done = 0
    by_rec = defaultdict(list)
    for it in items:
        by_rec[it["sourceSha256"]].append(it)
    for sha, rec_items in by_rec.items():
        todo = [it for it in rec_items if not (out / "segments" / sha / f'{it["key"]}.quality.json').is_file()]
        if not todo:
            continue
        log(f"{sha[:12]} measuring quality of {len(rec_items)} segments")
        metrics, broken = {}, {}
        for it in rec_items:  # the whole recording, so 'soft' is relative to it
            try:
                times = _times(it, 8)
                metrics[it["key"]] = (times, [harness.frame_metrics(g)
                                              for g in harness.decode_gray(it["proxy"], times)])
            except ValueError as error:   # one bad clip is unusable, not a failed stage
                broken[it["key"]] = str(error)[:200]
        values = [m["sharpness"] for _, ms in metrics.values() for m in ms]
        sharp = median(values) if values else 0.0
        series = rec_items[0]["motion"]
        jit = [harness.jitter(series, s["startMs"], s["endMs"])
               for s in rec_items[0]["allSegments"]]
        jit_median = median([j for j in jit if j]) if any(jit) else None
        for it in todo:
            if it["key"] in broken:
                _write(out / "segments" / sha / f'{it["key"]}.quality.json',
                       {"version": harness.VERSION, "sourceSha256": sha, "segment": it["key"],
                        "verdict": "unusable", "flags": ["no_usable_video"],
                        "reason": broken[it["key"]], "thresholds": harness.QUALITY,
                        "isMeasurementNotOwnerJudgment": True})
                log(f"{sha[:12]} {it['key']} ms unusable: {broken[it['key']]}")
                done += 1
                continue
            times, ms = metrics[it["key"]]
            j = harness.jitter(series, it["segment"]["startMs"], it["segment"]["endMs"])
            facts = (it["judged"] or {}).get("panel", {}).get("facts")
            q = harness.segment_quality(ms, sharp, j / jit_median if j and jit_median else None,
                                        facts)
            _write(out / "segments" / sha / f'{it["key"]}.quality.json',
                   {"version": harness.VERSION, "sourceSha256": sha, "segment": it["key"],
                    "frameTimesMs": times, "recordingMedianSharpness": round(sharp, 2), **q})
            done += 1
    return done


def run_describe(items: list[dict], out: Path, model: str, budget: list | None,
                 timeout: int, log, transport=None) -> int:
    from adventure_intelligence.panel_frames import sample_frames
    done = 0
    for it in items:
        target = out / "segments" / it["sourceSha256"] / f'{it["key"]}.description.json'
        quality = _read(target.with_name(f'{it["key"]}.quality.json'))
        if quality and quality.get("verdict") == "unusable":
            continue   # never shortlisted, so not worth a VLM call
        previous = _read(target)
        if previous and previous.get("status") in ("accepted", "repaired") \
                and previous.get("model") == model:
            continue
        if budget is not None and budget[0] <= 0:
            break
        s = it["segment"]
        length = s["endMs"] - s["startMs"]
        times = _times(it, 16)
        log(f'{it["sourceSha256"][:12]} describing {it["key"]} ms with {model}')
        images = sample_frames(it["proxy"], times)
        answer = harness.call_json(
            model, [{"role": "user", "content": harness.describe_prompt(it["sport"], times, length),
                     "images": images}],
            schema=harness.describe_schema(), transport=transport, timeout=timeout,
            check=lambda d: harness.check_description(d, length, times))
        if budget is not None:
            budget[0] -= 1
        _write(target, {"version": harness.VERSION, "sourceSha256": it["sourceSha256"],
                        "segment": it["key"], "frameTimesMs": times,
                        "isModelProposalNotOwnerJudgment": True, **answer})
        done += 1
    return done


def groups(items: list[dict]) -> dict[tuple[str, str], list[dict]]:
    out = defaultdict(list)
    for it in items:
        out[(it["outing"], it["sport"])].append(it)
    return dict(out)


def _facts(it: dict, reliability: dict | None) -> dict | None:
    """Panel facts, with disputes settled where measured judge reliability allows."""
    facts = (it["judged"] or {}).get("panel", {}).get("facts")
    if facts and reliability:
        return judge_reliability.apply(facts, it["sport"], reliability)
    return facts


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()[:80]


def run_story(items: list[dict], out: Path, model: str, budget: list | None, timeout: int,
              log, transport=None, reliability: dict | None = None) -> int:
    done = 0
    for (outing, sport), members in sorted(groups(items).items()):
        scenes, footage = [], {}
        for it in members:
            base = out / "segments" / it["sourceSha256"] / it["key"]
            quality = _read(base.with_name(base.name + ".quality.json"))
            if quality is None:
                raise ValueError("Run the quality stage first")
            described = _read(base.with_name(base.name + ".description.json")) or {}
            s = it["segment"]
            scenes.append({
                "file": it["file"], "sourceSha256": it["sourceSha256"],
                "startMs": s["startMs"], "endMs": s["endMs"], "capture": it["capture"],
                "quality": quality,
                "facts": _facts(it, reliability),
                "description": described.get("result") if described.get("status") in (
                    "accepted", "repaired") else None,
                "signals": {k: v for k, v in (("motionPeak", s.get("peakChange")),
                                              ("eventCandidates", len(s["eventCandidatesMs"])),
                                              ("distinctiveness", s.get("distinctiveness")))
                            if v is not None}})
            footage[it["sourceSha256"]] = it["durationMs"]
        packet = harness.build_packet(f"{outing} / {sport}", sport, scenes,
                                      footage_ms=sum(footage.values()))
        target = out / "stories" / f"{_slug(outing)}--{sport}.json"
        previous = _read(target)
        if (previous and previous["record"]["packetSha256"] == harness.packet_digest(packet)
                and previous["record"]["model"] == model and previous["record"].get("story")):
            continue
        if budget is not None and budget[0] <= 0:
            break
        if previous:  # keep the story this one replaces
            n = len(list((out / "stories" / "history").glob(f"{target.stem}.*.json"))) + 1
            _write(out / "stories" / "history" / f"{target.stem}.{n:03d}.json", previous)
        log(f"story for {outing} / {sport}: {len(scenes)} scenes with {model}")
        record = harness.tell(packet, model, transport=transport, timeout=timeout)
        if budget is not None:
            budget[0] -= 1
        _write(target, {"record": record, "packet": packet})
        _write(target.with_suffix(".md"), harness.markdown(record, packet))
        done += 1
    return done


def main(argv=None, *, transport=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reports", type=Path, required=True)
    parser.add_argument("--roster", type=Path, help="outings (a guessed roster is fine here)")
    parser.add_argument("--output", type=Path, help="default REPORTS/outing-stories-v1")
    parser.add_argument("--stages", nargs="+", choices=STAGES, default=list(STAGES))
    parser.add_argument("--describe-model", default="qwen3-vl:32b")
    parser.add_argument("--story-model", default="gemma3:27b")
    parser.add_argument("--max-calls", type=int, help="stop after this many model calls")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    def log(message):
        print("[stories]", message, file=sys.stderr, flush=True)

    try:
        reports = args.reports.expanduser()
        out = (args.output or reports / "outing-stories-v1").expanduser()
        outing_of = {}
        if args.roster:
            outing_of = roster.outing_by_source(roster.validate(json.loads(
                args.roster.read_text(encoding="utf-8"))))
        everything = collect(reports, outing_of)
        items = [it for it in everything if it["sport"] not in SKIP_SPORTS]
        missing_q = sum(not (out / "segments" / it["sourceSha256"] / f'{it["key"]}.quality.json').is_file()
                        for it in items)
        def _needs_description(it):
            base = out / "segments" / it["sourceSha256"]
            if (_read(base / f'{it["key"]}.quality.json') or {}).get("verdict") == "unusable":
                return False
            return (_read(base / f'{it["key"]}.description.json') or {}).get("status") not in (
                "accepted", "repaired")
        missing_d = sum(_needs_description(it) for it in items)
        summary = {"segments": len(items), "skippedUnknownSport": len(everything) - len(items),
                   "outings": len(groups(items)), "qualityToMeasure": missing_q,
                   "descriptionsToMake": missing_d, "output": str(out)}
        print(json.dumps(summary, indent=2))
        if args.dry_run:
            return 0
        needed = ([args.describe_model] if "describe" in args.stages else []) + \
                 ([args.story_model] if "story" in args.stages else [])
        if needed and transport is None:
            from adventure_intelligence.discovery_experiment import missing_models
            missing = missing_models(needed)
            if missing:
                raise ValueError("Pull first: " + " && ".join(f"ollama pull {m}" for m in missing))
        budget = [args.max_calls] if args.max_calls is not None else None
        result = {}
        if "quality" in args.stages:
            result["qualityMeasured"] = run_quality(items, out, log)
        uses_models = {"describe", "story"} & set(args.stages)
        with run_lock.exclusive(log=log) if uses_models and transport is None else nullcontext():
            if "describe" in args.stages:
                result["described"] = run_describe(items, out, args.describe_model, budget,
                                                   args.timeout, log, transport)
            if "story" in args.stages:
                rel = _read(args.reports.expanduser() / "judge-reliability.json")
                result["stories"] = run_story(items, out, args.story_model, budget,
                                              args.timeout, log, transport, reliability=rel)
        status = defaultdict(int)
        for f in (out / "stories").glob("*.json"):
            doc = _read(f)
            if doc:
                status[doc["record"]["harnessStatus"]] += 1
        print(json.dumps({**result, "storiesByHarnessStatus": dict(status)}, indent=2))
    except (ValueError, KeyError, OSError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
