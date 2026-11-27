#!/usr/bin/env python3
"""Highlight-first funnel: search all stored footage, verify, review the best.

    encode-queries   SigLIP2 text embeddings of the search prompts (once; ~1 min, no video)
    search           stage A: candidates from stored embeddings, motion and loudness (no model)
    find-labels      locate earlier marine review exports and their frozen samples
    eval             recall of owner-confirmed animals; --sweep picks thresholds on development
    verify           stage B: one closed-question VLM call per candidate (resumable, locked)
    review           stage D: a blind page of verified moments, rejected samples and audits
    import-answers   store the page's answers as owner labels (kind "highlight")
    eval-review      search recall / verifier precision / species accuracy from those answers

Camera originals are only read by `verify`, and only for a candidate no review clip already
covers; the file must sit under --trusted-source-root and match the size recorded when it
was hashed. Nothing in the evidence store, the discovery folders or the ledgers is changed.

    R="$WS/reports"
    $PY scripts/klip_highlight_search.py encode-queries --reports "$R"
    $PY scripts/klip_highlight_search.py search --reports "$R" --roster "$ROSTER"
    $PY scripts/klip_highlight_search.py find-labels --roots "$WS" ~/Downloads
    $PY scripts/klip_highlight_search.py eval --reports "$R" --labels-from "$WS" ~/Downloads --sweep
    $PY scripts/klip_highlight_search.py verify --reports "$R" --trusted-source-root "$SRC" --max-calls 20
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import nullcontext
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import sys

import numpy as np

from adventure_intelligence import highlight_media as hm
from adventure_intelligence import highlight_review as review
from adventure_intelligence import highlight_search as hs
from adventure_intelligence import highlight_verify as hv
from adventure_intelligence import holdout
from adventure_intelligence import owner_labels
from adventure_intelligence import outing_roster as roster
from adventure_intelligence import run_lock
from adventure_intelligence import run_records

FOLDER = "highlight-search-v1"
DEFAULT_MODEL = "qwen3-vl:8b-instruct"
MAX_LABEL_FILE = 20_000_000


def _read(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(doc if isinstance(doc, str) else json.dumps(doc, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _home(reports: Path) -> Path:
    return Path(reports) / FOLDER


# ---------------------------------------------------------------- inputs

def load_queries(reports: Path) -> hs.Scorer:
    home = _home(reports)
    doc = _read(home / "queries.json")
    if not doc or not (home / "queries.npz").is_file():
        raise ValueError("No search prompts encoded yet: run encode-queries first")
    if doc.get("queriesSha256") != hs._digest(hs.QUERIES):
        raise ValueError("The search prompts changed since they were encoded: run "
                         "encode-queries again")
    with np.load(home / "queries.npz") as data:
        return hs.Scorer(doc, data["prompts"])


def discovery_rows(reports: Path) -> dict[str, dict]:
    """sha -> {path, sport, folder, source, motion, audio}, judge-sources relabels first."""
    out: dict[str, dict] = {}
    for folder in sorted(Path(reports).glob("discovery-*")):
        rows = _read(folder / "judge-sources.json") or _read(folder / "sources.json") or []
        for row in rows:
            sha = row["sourceSha256"]
            rec = folder / sha
            source = _read(rec / "source.json")
            if source is None or sha in out:
                continue
            out[sha] = {"path": row["path"], "sport": row["sport"], "folder": folder,
                        "source": source, "record": rec}
    return out


def catalog(workspace: Path) -> dict[str, dict]:
    """sha -> path, size and duration of each imported original (read-only)."""
    db_path = Path(workspace) / "catalog.sqlite3"
    if db_path.is_symlink() or not db_path.is_file():
        return {}
    db = sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)
    try:
        db.execute("PRAGMA query_only=ON")
        return {sha: {"path": path, "sizeBytes": size,
                      "durationMs": round(duration * 1000) if duration else None}
                for sha, path, size, duration in
                db.execute("SELECT sha256,stored_path,size_bytes,duration FROM assets")}
    finally:
        db.close()


def inventory(reports: Path, roster_doc: dict | None, scorer: hs.Scorer,
              log=lambda m: None) -> tuple[list[dict], dict]:
    """Every searchable recording: a sport, a length, and whatever evidence is stored."""
    found = discovery_rows(reports)
    cat = catalog(Path(reports).parent)
    activity = roster.activity_by_source(roster_doc) if roster_doc else {}
    outing = roster.outing_by_source(roster_doc) if roster_doc else {}
    guessed = roster.guessed_sources(roster_doc) if roster_doc else set()
    skip = (roster.excluded_sources(roster_doc) |
            roster.tagged_sources(roster_doc, roster.MAYBE_ACCIDENTAL_TAG)) if roster_doc else set()
    evidence = Path(reports) / "evidence"
    shas = set(found) | {p.name for p in evidence.iterdir() if p.is_dir()} if evidence.is_dir() \
        else set(found)
    out, notes = [], Counter()
    for sha in sorted(shas):
        if sha in skip:
            notes["skippedAccidental"] += 1
            continue
        d = found.get(sha)
        sport = activity.get(sha) if activity.get(sha) not in (None, roster.UNLABELLED) else (
            d["sport"] if d else None)
        if sport not in hs.MARINE + hs.SKI:
            notes["otherOrUnknownSport"] += 1
            continue
        rec = {"sourceSha256": sha, "sport": sport, "sportGuessed": sha in guessed,
               "times": None, "logits": None, "motion": None, "audio": None}
        meta = _read(evidence / sha / "meta.json")
        if meta and meta.get("imageModel") == scorer.doc["imageModel"] \
                and (evidence / sha / "frames.npz").is_file():
            with np.load(evidence / sha / "frames.npz") as f:
                rec["times"] = f["times"].astype(np.int64)
                rec["logits"] = scorer.logits(f["embeddings"].astype(np.float32))
        else:
            notes["noFrameEmbeddings"] += 1
        if d:
            rec["durationMs"] = d["source"]["durationMs"]
            rec["file"] = Path(d["path"]).name
            rec["motion"] = (_read(d["record"] / "motion.json") or {}).get("series")
            rec["audio"] = (_read(d["record"] / "audio.json") or {}).get("series")
        elif rec["times"] is not None and len(rec["times"]):
            c = cat.get(sha) or {}
            rec["durationMs"] = c.get("durationMs") or int(rec["times"][-1]) + hs.STEP_MS
            rec["file"] = Path(c["path"]).name if c.get("path") else None
            notes["evidenceOnlyNoMotion"] += 1
        else:
            notes["noEvidence"] += 1
            continue
        rec["day"] = holdout.day_of(rec["file"] or "")
        rec["outing"] = outing.get(sha) or (f"day-{rec['day']}" if rec["day"] else None)
        out.append(rec)
    notes["searchable"] = len(out)
    log(f"{len(out)} recordings to search ({dict(notes)})")
    return out, dict(notes)


def latest_run(reports: Path) -> tuple[Path, dict]:
    home = _home(reports)
    pointer = _read(home / "latest.json")
    if not pointer:
        raise ValueError("No search run yet: run search first")
    folder = home / "runs" / pointer["run"]
    doc = _read(folder / "candidates.json")
    if not doc:
        raise ValueError(f"Search run {pointer['run']} is missing its candidates.json")
    return folder, doc


def _hours(recordings: list[dict], held: set[str], sport: str) -> dict[str, float]:
    out = {"development": 0.0, "heldOut": 0.0}
    for r in recordings:
        if r["sport"] == sport:
            out["heldOut" if r["sourceSha256"] in held else "development"] += r["durationMs"] / 3.6e6
    return {k: round(v, 3) for k, v in out.items()}


# ---------------------------------------------------------------- commands

def cmd_encode(args, log) -> dict:
    from adventure_intelligence import moment_evidence as me
    home = _home(args.reports)
    vocab = _read(Path(args.reports) / "evidence" / "vocabulary.json")
    if not vocab:
        raise ValueError("No evidence store under reports/evidence: run the describe pass first")
    doc = _read(home / "queries.json")
    if (doc and doc.get("queriesSha256") == hs._digest(hs.QUERIES)
            and doc.get("imageModel") == vocab["imageModel"] and (home / "queries.npz").is_file()):
        return {"status": "already_encoded", "queriesSha256": doc["queriesSha256"][:12]}
    log(f"loading {vocab['imageModel']} (text side only is used)")
    image = me.ImageEncoder(vocab["imageModel"])
    doc, vectors = hs.encode_queries(image)
    home.mkdir(parents=True, exist_ok=True)
    tmp = home / "queries.partial.npz"
    with open(tmp, "wb") as handle:
        np.savez(handle, prompts=vectors)
    os.replace(tmp, home / "queries.npz")
    _write(home / "queries.json", doc)
    return {"status": "encoded", "prompts": doc["prompts"], "queriesSha256": doc["queriesSha256"][:12],
            "imageModel": doc["imageModel"]}


def _overrides(pairs: list[str] | None) -> dict:
    out = {}
    for p in pairs or []:
        k, _, v = p.partition("=")
        out[k] = json.loads(v)
    return out


def _roster(args):
    return roster.validate(_read(args.roster)) if getattr(args, "roster", None) else None


def cmd_search(args, log) -> dict:
    scorer = load_queries(args.reports)
    recs, notes = inventory(args.reports, _roster(args), scorer, log)
    held = holdout.held_out(args.reports)
    doc = hs.search(recs, scorer, held, _overrides(args.set))
    doc["inventory"] = notes
    doc["heldOutFrozen"] = bool(held)
    doc["footageHours"] = {s: _hours(recs, held, s) for s in hs.MARINE + hs.SKI}
    key = f'{doc["settingsSha256"][:8]}-{doc["queriesSha256"][:8]}'
    folder = _home(args.reports) / "runs" / key
    _write(folder / "candidates.json", doc)
    _write(_home(args.reports) / "latest.json", {"run": key})
    kinds = Counter(c["kind"] for c in doc["candidates"])
    top, seen_top = [], Counter()
    for c in doc["candidates"]:
        if c["kind"] == "animal" and seen_top[c["top"]] < 2:
            seen_top[c["top"]] += 1
            top.append(c)
    windows = [(c["endMs"] - c["startMs"]) / 1000 for c in doc["candidates"]
               if c["kind"] in hs.MOMENT_KINDS]
    ski = [r for r in doc["recordings"] if r["sport"] == "ski"]
    return {"run": key, "candidates": dict(kinds), "audit": len(doc["audit"]),
            "footageHours": doc["footageHours"], "inventory": notes,
            "animalsFound": dict(Counter(c["top"] for c in doc["candidates"]
                                         if c["kind"] == "animal")),
            "topAnimals": [f'{c["file"]} {c["startMs"] // 1000}-{c["endMs"] // 1000}s '
                           f'{c["top"]} z={c["concepts"][0]["peakZ"]}'
                           f'{" scene-wide" if c.get("sceneWide") else ""}'
                           f'{" " + ",".join(c["qualityFlags"]) if c.get("qualityFlags") else ""}'
                           for c in top],
            "animalsMentioned": dict(Counter(x["concept"] for c in doc["candidates"]
                                             if c["kind"] == "animal" for x in c["concepts"])),
            "sceneWideAnimals": sum(1 for c in doc["candidates"]
                                    if c["kind"] == "animal" and c.get("sceneWide")),
            "animalsByOuting": dict(Counter(c.get("outing") for c in doc["candidates"]
                                            if c["kind"] == "animal").most_common(8)),
            "medianMomentSeconds": float(np.median(windows)) if windows else None,
            "skiRecordingsWithoutMotion": sum(not r.get("motionEvidence", True) for r in ski),
            "skiActionMedianOfP90": float(np.median([r["actionQuantiles"]["0.9"] for r in ski
                                                     if r.get("actionQuantiles")]))
            if any(r.get("actionQuantiles") for r in ski) else None,
            "file": str(folder / "candidates.json")}


def scan_labels(roots: list[Path]) -> dict:
    """Marine samples and their owner exports, paired by sampleId. Read-only."""
    samples, exports, seen, unreadable, scanned = {}, [], set(), [], Counter()
    wanted = {hs.marine_sample_version(): "sample", **{v: "export" for v in hs.marine_label_versions()}}
    for root in roots:
        root = Path(root).expanduser()
        if not root.is_dir():
            unreadable.append(f"{root}: not a folder")
            continue
        for folder, dirs, files in os.walk(root, onerror=lambda e: unreadable.append(
                f"{e.filename}: {e.strerror}")):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in
                             ("evidence", "proxies", "clips", "media", "node_modules"))
            for name in sorted(files):
                if not name.endswith(".json"):
                    continue
                path = Path(folder) / name
                try:
                    if path.is_symlink() or path.stat().st_size > MAX_LABEL_FILE:
                        continue
                    raw = path.read_bytes()
                except OSError:
                    continue
                scanned[str(root)] += 1
                if b"klip-marine" not in raw:
                    continue
                digest = sha256(raw).hexdigest()
                if digest in seen:
                    continue
                seen.add(digest)
                try:
                    doc = json.loads(raw)
                except ValueError:
                    continue
                role = wanted.get(doc.get("version")) if isinstance(doc, dict) else None
                if role == "sample":
                    samples[doc.get("sampleId")] = (path, doc)
                elif role == "export":
                    exports.append((path, doc))
    pairs, orphans = [], []
    for path, doc in exports:
        sample = samples.get(doc.get("sampleId"))
        if sample:
            pairs.append({"sample": sample[0], "export": path, "sampleDoc": sample[1], "exportDoc": doc})
        else:
            orphans.append(str(path))
    return {"pairs": pairs, "exportsWithoutSample": orphans, "samples": len(samples),
            "sampleFiles": [str(p) for p, _ in samples.values()],
            "jsonFilesScanned": dict(scanned), "unreadable": unreadable[:20]}


def cmd_find_labels(args, log) -> dict:
    got = scan_labels(args.roots)
    return {"pairs": [{"sample": str(p["sample"]), "export": str(p["export"]),
                       "reviews": len(p["exportDoc"].get("reviews") or []),
                       "version": p["exportDoc"]["version"]} for p in got["pairs"]],
            "samplesFound": got["samples"], "sampleFiles": got["sampleFiles"],
            "exportsWithoutSample": got["exportsWithoutSample"],
            "jsonFilesScanned": got["jsonFilesScanned"], "unreadable": got["unreadable"]}


def _truth(args) -> tuple[list[dict], list[str]]:
    truth, used = [], []
    newest: dict[tuple[str, str], tuple[float, dict]] = {}
    for p in scan_labels(args.labels_from)["pairs"]:
        try:
            rows = hs.marine_truth(p["sampleDoc"], p["exportDoc"])
        except (ValueError, KeyError, TypeError) as error:
            used.append(f'{p["export"]}: not used ({error})')
            continue
        used.append(f'{p["export"]}: {len(rows)} reviews')
        stamp = Path(p["export"]).stat().st_mtime
        for r in rows:             # a later export of the same clip replaces an earlier one
            key = (r["sampleId"], r["recordId"])
            if key not in newest or newest[key][0] < stamp:
                newest[key] = (stamp, r)
    truth = [r for _, r in newest.values()]
    return truth, used


def cmd_eval(args, log) -> dict:
    folder, doc = latest_run(args.reports)
    truth, used = _truth(args)
    if not truth:
        raise ValueError("No marine owner reviews found; run find-labels to see what is there")
    held = holdout.held_out(args.reports)
    searchable = {r["sourceSha256"] for r in doc["recordings"] if r["visualEvidence"]}
    hours = doc["footageHours"]["snorkeling"]
    result = hs.evaluate(doc["candidates"], truth, searchable=searchable, held_out=held,
                         snorkel_hours=hours)
    result["labelFiles"] = used
    result["run"] = folder.name
    if args.sweep:
        scorer = load_queries(args.reports)
        recs, _ = inventory(args.reports, _roster(args), scorer)
        dev = [r for r in recs if r["sourceSha256"] not in held and r["sport"] == "snorkeling"]
        dev_truth = [t for t in truth if t["sourceSha256"] not in held]
        points = []
        for z in hs.SWEEP_Z:
            for p in hs.SWEEP_P:
                got = hs.search(dev, scorer, held, {"zMin": z, "pMin": p})
                ev = hs.evaluate(got["candidates"], dev_truth, searchable=searchable,
                                 held_out=held, snorkel_hours=hours)["development"]
                points.append({"zMin": z, "pMin": p, "recall": ev["recall"],
                               "rightKind": ev["rightKind"], "falseAlarmRate": ev["falseAlarmRate"],
                               "candidatesPerHour": ev["candidatesPerHour"]})
        result["sweep"] = {"points": points, "chosen": hs.choose_operating_point(points),
                           "chosenOnDevelopmentOnly": True}
    _write(folder / "eval.json", result)
    return result




class Sources:
    """Where to cut a candidate's clip from: a review clip that covers it, else the original."""

    def __init__(self, reports: Path, trusted_root: Path | None):
        self.reports, self.trusted = Path(reports), trusted_root
        self.rows = discovery_rows(reports)
        self.catalog = catalog(Path(reports).parent)
        self._originals = None

    def covering_proxy(self, c: dict):
        d = self.rows.get(c["sourceSha256"])
        if not d:
            return None
        for p in sorted((d["record"] / "proxies").glob("*.mp4")):
            try:
                a, b = (int(x) for x in p.stem.split("-"))
            except ValueError:
                continue
            if a <= c["startMs"] and c["endMs"] <= b:
                return p, a
        return None

    def original(self, sha: str) -> Path:
        if self._originals is None:
            self._originals = hm.Originals(self.reports, self.trusted)
        return self._originals.path(sha)


def cut_clip(sources: Sources, c: dict, dest: Path) -> None:
    from adventure_intelligence.discovery_experiment import cut_proxy
    if dest.is_file():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    covered = sources.covering_proxy(c)
    if covered:
        proxy, offset = covered
        cut_proxy(proxy, dest, c["startMs"] - offset, c["endMs"] - offset, width=960)
    else:
        cut_proxy(sources.original(c["sourceSha256"]), dest, c["startMs"], c["endMs"], width=960)


def _slug(model: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ".-" else "_" for ch in model)


def verify_results(reports: Path, model: str) -> dict[str, dict]:
    folder = _home(reports) / "verify" / _slug(model)
    out = {}
    for f in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        doc = _read(f)
        if doc and doc.get("version") == hv.VERSION:
            out[doc["candidateId"]] = doc
    return out


def cmd_verify(args, log, transport=None) -> dict:
    from adventure_intelligence.panel_frames import sample_frames, video_span
    folder, doc = latest_run(args.reports)
    done = verify_results(args.reports, args.model)
    answered = {k for k, v in done.items() if v["status"] in ("accepted", "repaired")}
    kinds = tuple(args.kinds) if args.kinds else hv.PRIORITY
    todo = hv.queue(doc["candidates"], kinds=kinds, done=answered)
    if args.dry_run:
        return {"run": folder.name, "toVerify": dict(Counter(c["kind"] for c in todo)),
                "alreadyVerified": len(answered), "model": args.model,
                # ~60 s per 12-frame call on qwen3-vl:8b-instruct (52 s for 8 frames measured)
                "estimatedMinutes": round(len(todo[:args.max_calls] if args.max_calls else todo)
                                          * 60 / 60)}
    sources = Sources(args.reports, args.trusted_source_root)
    clips = _home(args.reports) / "clips"
    out_dir = _home(args.reports) / "verify" / _slug(args.model)
    asked = failed = 0
    with run_lock.exclusive(log=log) if transport is None else nullcontext():
        for c in todo:
            if args.max_calls is not None and asked >= args.max_calls:
                break
            clip = clips / f'{c["id"]}.mp4'
            try:
                cut_clip(sources, c, clip)
                length = c["endMs"] - c["startMs"]
                span = video_span(clip)
                if span is not None:
                    length = min(length, span)
                # A whole run has no peak: centre on its middle rather than its first second.
                peak = c["peakMs"] if c.get("peakMs") is not None else (c["startMs"] + c["endMs"]) // 2
                stamps = hv.frame_plan(length, peak - c["startMs"])
                images = sample_frames(clip, stamps)
            except (ValueError, OSError) as error:
                failed += 1
                log(f'{c["id"]} {c["kind"]} skipped: {error}')
                continue
            log(f'{c["id"]} {c["kind"]} {c.get("file")} {c["startMs"] // 1000}s')
            rec = hv.ask(c, images, stamps, length, model=args.model, transport=transport,
                         timeout=args.timeout)
            rec["searchRun"] = folder.name
            _write(out_dir / f'{c["id"]}.json', rec)
            asked += 1
    results = verify_results(args.reports, args.model)
    return {"asked": asked, "skipped": failed, **hv.summarize(doc["candidates"], results)}


def cmd_review(args, log) -> dict:
    folder, doc = latest_run(args.reports)
    results = verify_results(args.reports, args.model)
    deck = review.build_deck(doc["candidates"], doc["audit"], results,
                             max_items=args.max_items, max_audit=args.max_audit)
    out = _home(args.reports) / "review" / deck["deck"]
    clips = _home(args.reports) / "clips"
    sources = Sources(args.reports, args.trusted_source_root)
    missing: dict[str, str] = {}
    by_id = {c["id"]: c for c in doc["candidates"] + doc["audit"]}
    for item_id in deck["order"]:
        dest = clips / f"{item_id}.mp4"
        try:
            cut_clip(sources, by_id[item_id], dest)
        except (ValueError, OSError) as error:
            missing[item_id] = str(error)
            log(f"{item_id}: no clip ({error})")
    _write(out / "deck.json", deck)
    page = review.page(deck, out, lambda i: clips / f"{i}.mp4" if (clips / f"{i}.mp4").is_file()
                       else None, missing)
    _write(out / "index.html", page)
    return {"deck": deck["deck"], "counts": deck["counts"], "clipsMissing": len(missing),
            "whyMissing": dict(Counter(_why(e) for e in missing.values())),
            "page": str(out / "index.html")}


def _why(error: str) -> str:
    """An error message without the file name, so the same cause counts once."""
    for cause in ("not under --trusted-source-root", "not found", "changed since it was hashed",
                  "needs --trusted-source-root", "neither a discovery pass nor the catalog",
                  "extraction failed"):
        if cause in error:
            return cause
    return error[:80]


def _deck(reports: Path, deck_id: str | None) -> dict:
    base = _home(reports) / "review"
    if deck_id is None:
        decks = sorted(base.glob("*/deck.json"), key=lambda p: p.stat().st_mtime)
        if not decks:
            raise ValueError("No review deck yet: run review first")
        return _read(decks[-1])
    doc = _read(base / deck_id / "deck.json")
    if not doc:
        raise ValueError(f"No review deck {deck_id}")
    return doc


def cmd_import(args, log) -> dict:
    raw = Path(args.answers).expanduser().read_bytes()
    answers = json.loads(raw)
    deck = _deck(args.reports, answers.get("deck"))
    return review.import_answers(args.reports, deck, answers, raw)


def cmd_eval_review(args, log) -> dict:
    deck = _deck(args.reports, args.deck)
    return review.evaluate(deck, owner_labels.load(args.reports, "highlight"))


COMMANDS = {"encode-queries": cmd_encode, "search": cmd_search, "find-labels": cmd_find_labels,
            "eval": cmd_eval, "verify": cmd_verify, "review": cmd_review,
            "import-answers": cmd_import, "eval-review": cmd_eval_review}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    for name in COMMANDS:
        s = sub.add_parser(name)
        if name != "find-labels":
            s.add_argument("--reports", type=Path, required=True)
        if name in ("search", "eval"):
            s.add_argument("--roster", type=Path)
        if name == "search":
            s.add_argument("--set", nargs="*", metavar="KEY=JSON",
                           help="override a search setting, e.g. zMin=3.5")
        if name == "find-labels":
            s.add_argument("--roots", type=Path, nargs="+", required=True)
        if name == "eval":
            s.add_argument("--labels-from", type=Path, nargs="+", required=True)
            s.add_argument("--sweep", action="store_true")
        if name in ("verify", "review"):
            s.add_argument("--model", default=DEFAULT_MODEL)
            s.add_argument("--trusted-source-root", type=Path)
        if name == "verify":
            s.add_argument("--max-calls", type=int)
            s.add_argument("--kinds", nargs="*", choices=hv.PRIORITY)
            s.add_argument("--timeout", type=int, default=300)
            s.add_argument("--dry-run", action="store_true")
        if name == "review":
            s.add_argument("--max-items", type=int, default=120)
            s.add_argument("--max-audit", type=int, default=30,
                           help="audit windows in the deck (a reproducible subset)")
        if name == "import-answers":
            s.add_argument("--answers", type=Path, required=True)
        if name == "eval-review":
            s.add_argument("--deck")
    return p


def main(argv=None, *, transport=None) -> int:
    args = parser().parse_args(argv)

    def log(message):
        print(f"[highlights] {message}", file=sys.stderr, flush=True)
    try:
        run = COMMANDS[args.command]
        result = run(args, log, transport) if args.command == "verify" else run(args, log)
    except (ValueError, OSError, KeyError, RuntimeError) as error:
        print(f"[highlights] stopped: {error}", file=sys.stderr)
        return 3
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(run_records.main_recorded(main, "klip_highlight_search"))
