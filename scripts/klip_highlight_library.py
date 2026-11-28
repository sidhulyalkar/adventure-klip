#!/usr/bin/env python3
"""The highlights library: every moment you have judged worth keeping, in one place.

    build           gather grid keeps and stars, tap preferences, gallery trims, highlights-
                    page answers and (optionally) verified search proposals into moments
    top             the best moments so far, per sport
    page            one review page of the library (rate 1-5, trim, tick what it shows, note);
                    with --trusted-source-root, moments no review clip covers are cut from the
                    original with a few seconds of context, so every moment can be watched
    import-answers  store that page's answers; the next build picks them up
    export          cut each kept moment exactly to its window from the original: a master at
                    full resolution, a 1080p web file without camera metadata, a poster frame
    showcase        the best exported moments as a dark gallery page plus highlights.json and
                    media/, ready to copy into a website (nothing is uploaded)

Reads the ledgers and the review clips; never writes a ledger except through
import-answers (append-only owner labels). Camera originals are only read, only under
--trusted-source-root, and only if unchanged since they were hashed.

    R="$WS/reports"; L="$R/owner-ledgers"; SRC=/Volumes/Sid_DJI_SSD
    $PY scripts/klip_highlight_library.py build --reports "$R" --ledgers "$L"
    $PY scripts/klip_highlight_library.py top --reports "$R"
    $PY scripts/klip_highlight_library.py page --reports "$R" --trusted-source-root "$SRC"
    $PY scripts/klip_highlight_library.py import-answers --reports "$R" --answers ~/Downloads/highlight-answers-<deck>.json
    $PY scripts/klip_highlight_library.py build --reports "$R" --ledgers "$L"     # picks up the trims
    $PY scripts/klip_highlight_library.py export --reports "$R" --trusted-source-root "$SRC" --dry-run
    $PY scripts/klip_highlight_library.py export --reports "$R" --trusted-source-root "$SRC"
    $PY scripts/klip_highlight_library.py showcase --reports "$R" --ledgers "$L" --limit 12
"""
from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
import shutil
import sys

from adventure_intelligence import highlight_library as lib
from adventure_intelligence import highlight_media as hm
from adventure_intelligence import highlight_review as review
from adventure_intelligence import holdout
from adventure_intelligence import outing_roster as roster
from adventure_intelligence import owner_labels
from adventure_intelligence import run_records
from adventure_intelligence import scanned_recordings
from adventure_intelligence import value_pairs

FOLDER = "highlight-library-v1"
SEARCH = "highlight-search-v1"


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


def discovery(reports: Path) -> dict[str, dict]:
    """sha -> {file, sport, proxies: [(start, end, path)]}, as the review app finds them."""
    return scanned_recordings.scanned(reports)


def search_media(reports: Path) -> tuple[list[dict], dict[str, dict]]:
    """The latest search run's candidates and every stage B result (any model)."""
    home = Path(reports) / SEARCH
    pointer = _read(home / "latest.json")
    doc = _read(home / "runs" / pointer["run"] / "candidates.json") if pointer else None
    verified = {}
    for f in sorted((home / "verify").glob("*/*.json")) if (home / "verify").is_dir() else []:
        r = _read(f)
        if r and r.get("candidateId"):
            verified[r["candidateId"]] = r
    return (doc or {}).get("candidates", []), verified


def context(reports: Path, roster_doc: dict | None, disc: dict) -> dict[str, dict]:
    held = holdout.held_out(reports)
    activity = roster.activity_by_source(roster_doc) if roster_doc else {}
    outing = roster.outing_by_source(roster_doc) if roster_doc else {}
    tags = {}
    for o in (roster_doc or {}).get("outings", []):
        for row in o["sources"]:
            tags[row["sourceSha256"]] = row.get("tags") or []
    cat = scanned_recordings.catalog(Path(reports).parent)
    out = {}
    for sha in set(disc) | set(activity) | set(cat):
        file = (disc.get(sha) or {}).get("file") or (
            Path(cat[sha]["path"]).name if cat.get(sha, {}).get("path") else None)
        sport = activity.get(sha)
        if sport in (None, roster.UNLABELLED):
            sport = (disc.get(sha) or {}).get("sport")
        out[sha] = {"sport": sport, "outing": outing.get(sha), "file": file,
                    "day": holdout.day_of(file or ""), "heldOut": sha in held,
                    "tags": tags.get(sha, [])}
    return out


def cmd_build(args) -> dict:
    ledgers = Path(args.ledgers or Path(args.reports) / "owner-ledgers").expanduser()
    pairs = value_pairs.load(ledgers / "owner-pairs.jsonl")
    roster_file = ledgers / "roster.json"
    roster_doc = roster.validate(_read(roster_file)) if roster_file.is_file() else None
    evidence = (lib.grid_evidence(pairs) + lib.tap_evidence(pairs)
                + lib.boundary_evidence(owner_labels.load(args.reports, "boundary"))
                + lib.review_evidence(owner_labels.load(args.reports, "highlight")))
    if not args.owner_only:
        candidates, verified = search_media(args.reports)
        evidence += lib.proposal_evidence(candidates, verified)
    disc = discovery(args.reports)
    library = lib.build(evidence, context(args.reports, roster_doc, disc))
    library["inputs"] = {"pairsLedger": str(ledgers / "owner-pairs.jsonl"),
                         "pairsRows": len(pairs), "roster": roster_doc is not None}
    home = Path(args.reports) / FOLDER
    _write(home / "snapshots" / f'{library["evidenceSha256"][:12]}.json', library)
    _write(home / "library.json", library)
    top = lib.ranked(library, limit=10)
    return {"counts": library["counts"], "perOuting": library["perOuting"],
            "top": [_line(m) for m in top], "file": str(home / "library.json")}


def _line(m: dict) -> str:
    stars = f' ★{m["stars"]}' if m.get("stars") else ""
    what = f' [{", ".join(m["what"])}]' if m.get("what") else ""
    flag = " (conflict)" if m.get("conflict") else ""
    seq = m.get("sequence")
    part = f' [part {seq["index"]}/{seq["of"]}]' if seq else ""
    return (f'{m["tier"]}/{m.get("purpose")}{stars}{part} {m.get("sport")} {m.get("outing") or m.get("day")} '
            f'{m.get("file")} {m["startMs"] / 1000:.0f}-{m["endMs"] / 1000:.0f}s{what}{flag}')


def _library(reports: Path) -> dict:
    doc = _read(Path(reports) / FOLDER / "library.json")
    if not doc:
        raise ValueError("No library yet: run build first")
    return doc


def cmd_top(args) -> dict:
    library = _library(args.reports)
    sports = [args.sport] if args.sport else sorted(library["counts"]["bySport"])
    return {s: [_line(m) for m in lib.ranked(library, sport=s, limit=args.limit)] for s in sports}


def media_finder(reports: Path):
    disc = discovery(reports)
    candidates, _ = search_media(reports)
    clips = Path(reports) / SEARCH / "clips"
    cut = [(c["sourceSha256"], c["startMs"], c["endMs"], clips / f'{c["id"]}.mp4')
           for c in candidates if (clips / f'{c["id"]}.mp4').is_file()]

    def media_of(m: dict):
        sha, s, e = m["sourceSha256"], m["startMs"], m["endMs"]
        options = [(a, b, p) for a, b, p in (disc.get(sha) or {}).get("proxies", [])]
        options += [(a, b, p) for x, a, b, p in cut if x == sha]
        covering = [o for o in options if o[0] <= s and e <= o[1]]
        if covering:
            a, b, p = min(covering, key=lambda o: o[1] - o[0])
            return p, a, b
        partial = [o for o in options if o[0] < e and s < o[1]]
        if partial:            # shows what exists of the moment; the page plays that part
            a, b, p = max(partial, key=lambda o: min(o[1], e) - max(o[0], s))
            return p, a, b
        return None
    return media_of


def best_per_sport(library: dict, n: int, tiers: tuple[str, ...]) -> list[dict]:
    """The first n moments of each sport in library order (tier, then rating, then
    preferences), so a short page shows the best of every sport rather than one."""
    taken: dict[str, int] = {}
    out = []
    for m in library["moments"]:
        if m["tier"] not in tiers and not m["conflict"]:
            continue
        sport = m.get("sport") or "unknown"
        if taken.get(sport, 0) < n:
            taken[sport] = taken.get(sport, 0) + 1
            out.append(m)
    return out


def cmd_page(args) -> dict:
    library = _library(args.reports)
    tiers = tuple(args.tiers)
    if args.top:
        library = {**library, "moments": best_per_sport(library, args.top, tiers)}
    home = Path(args.reports) / FOLDER
    find = media_finder(args.reports)
    originals = (hm.Originals(args.reports, args.trusted_source_root)
                 if args.trusted_source_root else None)
    pad = int(args.pad_s * 1000)
    more_pad = int(args.more_pad_s * 1000)
    missing: dict[str, str] = {}
    clips = home / "clips"

    def count() -> int:
        return len(list(clips.glob("*.mp4"))) if clips.is_dir() else 0
    before = count()

    def media_of(m: dict):
        found = find(m)
        more = m.get("wantMore") or {}
        before = more_pad if more.get("before") else pad
        after = more_pad if more.get("after") else pad
        want_lo = max(0, m["startMs"] - before)
        if found and found[1] <= want_lo and m["endMs"] + after <= found[2]:
            return found                 # an existing clip already has context around it
        if originals is None:
            if found is None:
                missing[m["id"]] = ("no review clip covers this moment; pass "
                                    "--trusted-source-root to cut one from the original")
            return found
        try:
            return hm.preview(originals, m["sourceSha256"], m["startMs"], m["endMs"],
                              clips, pad_ms=before, after_ms=after)
        except (ValueError, OSError) as error:
            if found is None:
                missing[m["id"]] = str(error)
            print(f"[library] {m['id']} {m.get('file')}: {error}", file=sys.stderr)
            return found
    deck = lib.deck(library, media_of, tiers=tiers)
    # Keep the moment's window inside what its clip actually covers.
    for item in deck["items"].values():
        item["startMs"] = max(item["startMs"], item["mediaStartMs"])
        item["endMs"] = min(item["endMs"], item["mediaEndMs"])
    out = home / "review" / deck["deck"]
    _write(out / "deck.json", deck)
    media = deck["media"]
    page = review.page(deck, out, lambda i: Path(media[i]) if i in media else None, missing)
    _write(out / "index.html", page)
    return {"deck": deck["deck"], "moments": len(deck["items"]), "byTier": deck["counts"],
            "withoutClip": sum(1 for i in deck["items"] if i not in media),
            "clipsCutFromOriginals": count() - before, "whyMissing": _causes(missing.values()),
            "page": str(out / "index.html")}


def _causes(errors) -> dict:
    """Error messages counted by cause, without file names."""
    out: dict[str, int] = {}
    for e in errors:
        key = next((c for c in ("not under --trusted-source-root", "not found",
                                "changed since it was hashed", "neither a discovery pass",
                                "ffmpeg failed", "extraction failed") if c in e), e[:80])
        out[key] = out.get(key, 0) + 1
    return out


def _selected(library: dict, args) -> list[dict]:
    rows = []
    for m in library["moments"]:
        if m["tier"] not in args.tiers or (m["conflict"] and not args.include_conflicts):
            continue
        if args.min_stars and (m.get("stars") or 0) < args.min_stars and m["tier"] != "owner_best":
            continue
        if "bad_footage" in (m.get("what") or []):
            continue
        if args.purpose and m.get("purpose") not in args.purpose:
            continue
        rows.append(m)
    return rows[:args.limit] if args.limit else rows


def cmd_export(args) -> dict:
    library = _library(args.reports)
    chosen = _selected(library, args)
    home = Path(args.reports) / FOLDER / "exports"
    if args.dry_run:
        return {"wouldExport": len(chosen), "seconds": round(sum(
            (m["endMs"] - m["startMs"]) / 1000 for m in chosen), 1),
                "moments": [_line(m) for m in chosen]}
    originals = hm.Originals(args.reports, args.trusted_source_root)
    profile = hm.Profile(preset=args.preset)
    done, reused, failed = [], 0, {}
    for n, m in enumerate(chosen, 1):
        try:
            record = hm.export_moment(originals.path(m["sourceSha256"]), m, home / m["id"], profile)
        except (ValueError, OSError) as error:
            failed[m["id"]] = str(error)
            print(f"[library] {n}/{len(chosen)} {m['id']} {m.get('file')}: {error}", file=sys.stderr)
            continue
        reused += record["reused"]
        done.append(m["id"])
        print(f"[library] {n}/{len(chosen)} {'kept' if record['reused'] else 'cut'} "
              f"{_line(m)}", file=sys.stderr)
    seq_done = []
    if args.sequences:
        by_id = {m["id"]: m for m in library["moments"]}
        chosen_ids = {m["id"] for m in chosen}
        for seq in library.get("sequences") or []:
            if not chosen_ids & set(seq["members"]):
                continue
            members = [by_id[i] for i in seq["members"] if i in by_id]
            try:
                parts = [(originals.path(m["sourceSha256"]), m) for m in members]
                record = hm.export_sequence(parts, home / f'seq-{seq["id"]}', seq["id"], profile)
            except (ValueError, OSError) as error:
                failed[f'seq-{seq["id"]}'] = str(error)
                print(f"[library] sequence {seq['id']}: {error}", file=sys.stderr)
                continue
            seq_done.append(seq["id"])
            print(f"[library] {'kept' if record['reused'] else 'cut'} sequence {seq['id']} "
                  f"({len(members)} parts)", file=sys.stderr)
    index = _read(home / "index.json") or {"version": hm.EXPORT_VERSION, "exports": {}}
    for m in chosen:
        if m["id"] in done:
            index["exports"][m["id"]] = {k: m.get(k) for k in (
                "sourceSha256", "startMs", "endMs", "windowBasis", "tier", "stars", "what",
                "sport", "outing", "day", "file")}
    _write(home / "index.json", index)
    return {"exported": len(done) - reused, "reused": reused, "sequences": len(seq_done),
            "failed": len(failed),
            "whyFailed": _causes(failed.values()), "folder": str(home)}


# ---------------------------------------------------------------- showcase

def _titles(ledgers: Path | None) -> dict[str, dict]:
    """outing -> {title, place, trip} from the roster, when there is one."""
    roster_file = Path(ledgers).expanduser() / "roster.json" if ledgers else None
    doc = _read(roster_file) if roster_file and roster_file.is_file() else None
    if not doc:
        return {}
    trips = doc.get("trips") or {}
    out = {}
    for o in doc.get("outings", []):
        trip = trips.get(o.get("trip")) or {}
        out[o["outing"]] = {"title": o.get("title"), "place": o.get("place") or trip.get("place"),
                            "trip": trip.get("title") or None}
    return out


SPORT_NAMES = {"ski": "Skiing", "snorkeling": "Snorkeling", "kayak": "Kayaking",
               "bike": "Mountain biking", "hike": "Hiking", "surf": "Surfing"}


def _describe(m: dict, titles: dict) -> dict:
    t = titles.get(m.get("outing") or "") or {}
    sport = SPORT_NAMES.get(m.get("sport") or "", (m.get("sport") or "Adventure").title())
    seen = [w.replace("_", " ") for w in m.get("what") or []
            if w not in ("nothing_special", "bad_footage", "lift_or_line")]
    title = t.get("title") or (seen[0].capitalize() if seen else sport)
    where = ", ".join(x for x in (t.get("place"), t.get("trip")) if x)
    return {"title": title, "sport": m.get("sport"), "sportName": sport, "where": where or None,
            "date": m.get("day"), "tags": seen}


def _owner_rows(library: dict, args) -> list[dict]:
    rows = []
    for m in library["moments"]:
        if m["tier"] not in ("owner_best", "owner_keep") or m["conflict"]:
            continue
        if m.get("purpose") not in args.purpose:
            continue
        if args.min_stars and (m.get("stars") or 0) < args.min_stars and m["tier"] != "owner_best":
            continue
        rows.append(m)
    return rows


def cmd_showcase(args) -> dict:
    """One card per highlight, playing the owner's exact trim. With --sequences, a highlight
    that is part of a linked sequence gets one card for the whole sequence: it still plays the
    trimmed highlight, with "full sequence" to watch the parts together (the run-up, the rest
    of the line). Only if the highlight itself was never exported does the card play the
    sequence."""
    library = _library(args.reports)
    exports = Path(args.reports) / FOLDER / "exports"
    out = Path(args.out).expanduser() if args.out else Path(args.reports) / FOLDER / "showcase"
    titles = _titles(args.ledgers or Path(args.reports) / "owner-ledgers")
    seqs = {s["id"]: s for s in library.get("sequences") or []} if args.sequences else {}
    taken: dict[str, int] = {}
    rows, stale, shown_seqs = [], [], set()
    for m in _owner_rows(library, args):
        seq = m.get("sequence") if m.get("sequence", {}).get("id") in seqs else None
        if seq and seq["id"] in shown_seqs:
            continue                     # the sequence already has a card
        own = _read(exports / m["id"] / "export.json")
        whole = _read(exports / f'seq-{seq["id"]}' / "export.json") if seq else None
        if not own and not whole:
            stale.append(m["id"])
            continue
        sport = m.get("sport") or "unknown"
        if args.per_sport and taken.get(sport, 0) >= args.per_sport:
            continue
        taken[sport] = taken.get(sport, 0) + 1
        if seq:
            shown_seqs.add(seq["id"])
        rows.append((m, own, whole, seq))
        if args.limit and len(rows) >= args.limit:
            break
    media = out / "media"
    media.mkdir(parents=True, exist_ok=True)
    base = (args.base_url.rstrip("/") + "/") if args.base_url else "media/"

    def copy(record: dict, folder: Path, name: str) -> None:
        for src, dest in (("web.mp4", f"{name}.mp4"), ("poster.jpg", f"{name}.jpg")):
            target, info = media / dest, record["files"][src]
            if not (target.is_file() and target.stat().st_size == info["bytes"]
                    and hm.file_sha256(target) == info["sha256"]):
                shutil.copyfile(folder / src, target)

    items = []
    for rank, (m, own, whole, seq) in enumerate(rows, 1):
        seq_name = f'seq-{seq["id"]}' if seq else None
        name = m["id"] if own else seq_name
        record = own or whole
        copy(record, exports / name, name)
        web = record["files"]["web.mp4"]
        item = {"id": name, "rank": rank, **_describe(m, titles),
                "stars": m.get("stars"), "tier": m["tier"].removeprefix("owner_"),
                "durationS": round(web.get("durationS") or (m["endMs"] - m["startMs"]) / 1000, 1),
                "width": web.get("width"), "height": web.get("height"),
                "video": base + f"{name}.mp4", "poster": base + f"{name}.jpg",
                **({"note": m["notes"][-1]} if args.include_notes and m.get("notes") else {})}
        if own and whole:
            copy(whole, exports / seq_name, seq_name)
            item["sequence"] = {"video": base + f"{seq_name}.mp4", "parts": seq["of"],
                                "durationS": round(whole["files"]["web.mp4"].get("durationS") or 0, 1)}
        elif whole:
            item["parts"] = seq["of"]
        items.append(item)
    doc = {"version": "klip-highlight-showcase-v1", "count": len(items), "highlights": items}
    _write(out / "highlights.json", doc)
    # The local page always plays the copies next to it, wherever the site will serve them.
    local = [{**it, "video": f'media/{it["id"]}.mp4', "poster": f'media/{it["id"]}.jpg',
              **({"sequence": {**it["sequence"], "video": "media/" + it["sequence"]["video"]
                               .rsplit("/", 1)[-1]}} if it.get("sequence") else {})}
             for it in items]
    _write(out / "index.html", showcase_page(local))
    return {"highlights": len(items), "bySport": taken,
            "withFullSequence": sum(1 for it in items if it.get("sequence")),
            "keptButNotExported": len(stale), "folder": str(out),
            "page": str(out / "index.html"),
            "next": "run export first for kept moments without files" if stale else None}


def _dur(seconds: float) -> str:
    if seconds < 10:
        return f"{seconds:.1f} s"
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}" if seconds >= 60 else f"{seconds:.0f} s"


def showcase_page(items: list[dict]) -> str:
    def h(x) -> str:
        return html.escape(str(x if x is not None else ""))
    cards = []
    for it in items:
        sub = " · ".join(h(x) for x in (it["sportName"], it.get("where"), it.get("date")) if x)
        tags = "".join(f"<li>{h(t)}</li>" for t in it.get("tags") or [])
        cards.append(
            f'<figure class="card" data-id="{h(it["id"])}">'
            f'<button class="thumb" aria-label="Play {h(it["title"])}">'
            f'<video muted loop playsinline preload="none" poster="{h(it["poster"])}" '
            f'src="{h(it["video"])}"></video><span class="dur">{_dur(it["durationS"])}</span></button>'
            f'<figcaption><strong>{h(it["title"])}</strong><span>{sub}</span>'
            + (f'<button class="seqbtn" data-src="{h(it["sequence"]["video"])}">'
               f'Full sequence \u00b7 {it["sequence"]["parts"]} parts \u00b7 '
               f'{_dur(it["sequence"]["durationS"])}</button>' if it.get("sequence") else "")
            + f'<ul class="tags">{tags}</ul></figcaption></figure>')
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>Highlights</title><style>" + _SHOWCASE_CSS + "</style></head><body>"
            "<header><h1>Highlights</h1><p>" + str(len(items))
            + (" moment" if len(items) == 1 else " moments") + "</p></header>"
            "<main class=\"grid\">" + "".join(cards) + "</main>"
            "<dialog id=\"player\"><video controls playsinline></video>"
            "<button id=\"close\" aria-label=\"Close\">\u00d7</button></dialog>"
            "<script>" + _SHOWCASE_JS + "</script></body></html>")


_SHOWCASE_CSS = """
:root{color-scheme:dark}
body{margin:0;background:#050506;color:#ececf0;font:15px/1.45 system-ui,sans-serif}
header{padding:28px 24px 8px;max-width:1400px;margin:0 auto}
h1{margin:0;font-size:28px;letter-spacing:.02em} header p{margin:4px 0 0;color:#8d8d96}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:18px;
  padding:16px 24px 48px;max-width:1400px;margin:0 auto}
.card{margin:0;background:#111114;border-radius:12px;overflow:hidden;border:1px solid #1f1f24}
.thumb{all:unset;display:block;width:100%;position:relative;cursor:pointer;aspect-ratio:16/9;background:#000}
.thumb video{width:100%;height:100%;object-fit:cover;display:block}
.thumb:focus-visible{outline:3px solid #4b7bd1}
.dur{position:absolute;right:8px;bottom:8px;background:#000a;padding:1px 6px;border-radius:4px;font-size:12px}
figcaption{padding:10px 12px 12px;display:flex;flex-direction:column;gap:2px}
figcaption span{color:#8d8d96;font-size:13px}
.seqbtn{align-self:flex-start;margin-top:6px;font:inherit;font-size:13px;color:#e8e8e8;
  background:#1d1d22;border:1px solid #33333a;border-radius:6px;padding:3px 10px;cursor:pointer}
.seqbtn:hover{background:#2a2a31}
.tags{list-style:none;margin:6px 0 0;padding:0;display:flex;flex-wrap:wrap;gap:6px}
.tags li{font-size:12px;background:#1d1d22;border-radius:999px;padding:1px 8px;color:#c4c4cc}
dialog{padding:0;border:0;background:#000;max-width:min(96vw,1600px);width:96vw}
dialog::backdrop{background:#000e} dialog video{width:100%;max-height:90vh;display:block}
#close{position:absolute;top:6px;right:10px;background:#000a;color:#fff;border:0;font-size:28px;
  cursor:pointer;border-radius:6px;padding:0 10px}
"""

_SHOWCASE_JS = """
const dlg = document.getElementById('player'), big = dlg.querySelector('video');
document.querySelectorAll('.card').forEach(card => {
  const v = card.querySelector('video'), b = card.querySelector('.thumb');
  b.addEventListener('mouseenter', () => { v.play().catch(() => {}); });
  b.addEventListener('mouseleave', () => { v.pause(); v.currentTime = 0; });
  b.addEventListener('click', () => {
    v.pause(); big.src = v.getAttribute('src'); big.poster = v.getAttribute('poster');
    dlg.showModal(); big.play().catch(() => {});
  });
});
document.querySelectorAll('.seqbtn').forEach(b => b.addEventListener('click', () => {
  big.src = b.dataset.src; big.removeAttribute('poster'); dlg.showModal(); big.play().catch(() => {});
}));
const shut = () => { big.pause(); big.removeAttribute('src'); big.load(); dlg.close(); };
document.getElementById('close').addEventListener('click', shut);
dlg.addEventListener('cancel', e => { e.preventDefault(); shut(); });
dlg.addEventListener('click', e => { if (e.target === dlg) shut(); });
"""


def cmd_import(args) -> dict:
    raw = Path(args.answers).expanduser().read_bytes()
    answers = json.loads(raw)
    deck = _read(Path(args.reports) / FOLDER / "review" / str(answers.get("deck")) / "deck.json")
    if not deck:
        raise ValueError(f"No library page {answers.get('deck')}: were these answers from the "
                         "highlights review page instead? Use klip_highlight_search.py import-answers")
    return review.import_answers(args.reports, deck, answers, raw)


COMMANDS = {"build": cmd_build, "top": cmd_top, "page": cmd_page, "import-answers": cmd_import,
            "export": cmd_export, "showcase": cmd_showcase}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    for name in COMMANDS:
        s = sub.add_parser(name)
        s.add_argument("--reports", type=Path, required=True)
        if name == "build":
            s.add_argument("--ledgers", type=Path)
            s.add_argument("--owner-only", action="store_true",
                           help="leave out verified search proposals")
        if name == "top":
            s.add_argument("--sport")
            s.add_argument("--limit", type=int, default=20)
        if name == "page":
            s.add_argument("--tiers", nargs="+", default=["owner_best", "owner_keep", "owner_maybe"],
                           choices=lib.TIERS)
            s.add_argument("--trusted-source-root", type=Path,
                           help="footage drive; moments without a review clip are cut from it")
            s.add_argument("--top", type=int, help="only the best N moments of each sport")
            s.add_argument("--pad-s", type=float, default=4.0,
                           help="seconds of context each side when cutting from an original")
            s.add_argument("--more-pad-s", type=float, default=20.0,
                           help="context on a side you marked 'needs more before/after'")
        if name == "export":
            s.add_argument("--trusted-source-root", type=Path, required=True)
            s.add_argument("--tiers", nargs="+", default=["owner_best", "owner_keep"],
                           choices=lib.TIERS)
            s.add_argument("--min-stars", type=float, help="kept moments need this rating "
                           "(starred-best moments always qualify)")
            s.add_argument("--limit", type=int, help="at most this many, best first")
            s.add_argument("--include-conflicts", action="store_true")
            s.add_argument("--purpose", nargs="+", choices=["highlight", "keep", "memory", "maybe"],
                           help="only moments rated as these (default: every kept moment)")
            s.add_argument("--sequences", action="store_true",
                           help="also cut each sequence of linked moments as one file")
            s.add_argument("--preset", default="veryfast",
                           help="x264 preset; slower is smaller at the same quality")
            s.add_argument("--dry-run", action="store_true")
        if name == "showcase":
            s.add_argument("--ledgers", type=Path, help="for outing and trip titles (roster.json)")
            s.add_argument("--limit", type=int, default=12)
            s.add_argument("--per-sport", type=int)
            s.add_argument("--min-stars", type=float)
            s.add_argument("--purpose", nargs="+", default=["highlight"],
                           choices=["highlight", "keep", "memory"],
                           help="default: highlights only (starred best, or rated 4-5)")
            s.add_argument("--sequences", action="store_true",
                           help="show an exported sequence once in place of its parts")
            s.add_argument("--base-url", help="where media/ will be served from, e.g. /highlights")
            s.add_argument("--include-notes", action="store_true",
                           help="put your private review notes in highlights.json")
            s.add_argument("--out", type=Path, help="default: <reports>/highlight-library-v1/showcase")
        if name == "import-answers":
            s.add_argument("--answers", type=Path, required=True)
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        result = COMMANDS[args.command](args)
    except (ValueError, OSError, KeyError) as error:
        print(f"[library] stopped: {error}", file=sys.stderr)
        return 3
    print(json.dumps(result, indent=2, default=str, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(run_records.main_recorded(main, "klip_highlight_library"))
