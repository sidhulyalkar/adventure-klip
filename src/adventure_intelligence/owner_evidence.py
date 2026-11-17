"""Every owner judgment Klip holds, in one place, read-only.

Sources:
- the append-only evidence ledger (marine, calibration, ski-run imports);
- the catalog, read live: Studio example reviews, Studio pairwise choices and
  archive benchmark reviews. Only the latest revision of each counts, so a
  correction replaces what it corrects;
- immutable review files under reports/: blind pair reviews (matched to their
  frozen manifest by SHA-256) and curated highlight manifests;
- the tap/grid ledger written by value_pairs.

`collect` never writes. It also lists every review file it recognises but does
not consume, so an unused label is visible rather than silently ignored.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json
import sqlite3

from . import evidence_ledger as ledger

VERSION = "klip-owner-evidence-collection-v1"
MAX_FILE_BYTES = 40_000_000
_HIGHLIGHT = {"keep": "keep", "skip": "skip", "neutral": "unsure"}
_PAIR_CHOICE = {"left": 1.0, "right": 0.0, "tie": 0.5}
# Review schemas Klip has written. Anything listed here and found on disk is
# reported as consumed or not, so nothing is dropped without a trace.
REVIEW_SCHEMAS = {
    "klip-marine-owner-labels-v1": "ledger import --kind marine",
    "klip-marine-owner-labels-v2": "ledger import --kind marine",
    "klip-v2-human-calibration-review-v1": "ledger import --kind calibration",
    "klip-owner-run-annotations-v1": "ledger import --kind ski-run",
    "social-blind-pair-reviews-v1": "read automatically",
    "klip-highlight-curation-manifest-v1": "read automatically when it holds observer judgments",
    "klip-highlight-curation-review-export-v1": "import with highlight_curation_review first",
    "klip-owner-moment-annotations-v2": "not yet read: ratings and trims of composed moments",
    "klip-reel-timed-reactions-v1": "not yet read: timed reactions while watching a reel",
}


def _sha(text: str) -> str:
    return sha256(text.encode()).hexdigest()


def judgment(*, source: str, start: int, end: int, decision: str, selection: str,
             goal: str, origin: str, provenance: str, session: str | None = None,
             ratings: dict | None = None) -> dict | None:
    """Normalized owner judgment; the same keys pairs are built from."""
    if not (ledger._is_sha(source) and type(start) is int and type(end) is int
            and 0 <= start < end and decision in ledger.DECISIONS):
        return None
    return {"sourceSha256": source, "startMs": start, "endMs": end, "decision": decision,
            "selection": selection, "goal": goal, "origin": origin,
            "evidenceId": _sha(provenance)[:32], "session": session or origin,
            "ratings": {k: v for k, v in (ratings or {}).items()
                        if type(v) in (int, float)}}


def _side(source, start, end) -> dict | None:
    if ledger._is_sha(source) and type(start) is int and type(end) is int and 0 <= start < end:
        return {"sourceSha256": source, "startMs": start, "endMs": end}
    return None


def direct_pair(left, right, outcome, *, origin, provenance, goal) -> dict | None:
    if left is None or right is None or outcome is None:
        return None
    return {"left": left, "right": right, "outcome": outcome, "origin": origin,
            "goal": goal, "evidenceId": _sha(provenance)[:32]}


# ---- ledger -------------------------------------------------------------------

def from_ledger(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        ratings = {k: r["detail"].get(k) for k in
                   ("visibility", "framing", "technicalQuality", "narrativeCompleteness",
                    "behaviorInterest")} if isinstance(r.get("detail"), dict) else {}
        item = judgment(source=r["sourceSha256"], start=r["startMs"], end=r["endMs"],
                        decision=r["decision"], selection=r["selection"], goal=r["goal"],
                        origin=r["schema"], provenance="ledger:" + r["evidenceId"],
                        session=r.get("sampleId") or r["exportSha256"], ratings=ratings)
        if item:
            item["evidenceId"] = r["evidenceId"]
            out.append(item)
    return out


# ---- catalog --------------------------------------------------------------------

def _latest(rows: list[sqlite3.Row], key: str) -> list[sqlite3.Row]:
    """Latest revision per key: never superseded by a later one; then newest time."""
    replaced = {r["previous_revision_id"] for r in rows if "previous_revision_id" in r.keys()
                and r["previous_revision_id"]}
    best: dict[str, sqlite3.Row] = {}
    for r in rows:
        if r["revision_id"] in replaced:
            continue
        current = best.get(r[key])
        if current is None or (r["created_at"], r["revision_id"]) > (
                current["created_at"], current["revision_id"]):
            best[r[key]] = r
    return [best[k] for k in sorted(best)]


def _table(db, name: str) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                      (name,)).fetchone() is not None


def from_catalog(workspace: Path) -> dict:
    database = Path(workspace) / "catalog.sqlite3"
    if database.is_symlink() or not database.is_file():
        return {"judgments": [], "pairs": [], "counts": {"catalog": "missing"}}
    judgments, pairs, counts = [], [], {}
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        if _table(db, "example_revisions"):
            rows = _latest(db.execute("SELECT * FROM example_revisions").fetchall(), "example_id")
            links = {}
            if _table(db, "review_proposal_links"):
                links = {r["revision_id"]: r for r in db.execute(
                    "SELECT * FROM review_proposal_links").fetchall()}
            for r in rows:
                link = links.get(r["revision_id"])
                # The owner drew this interval, or moved a proposal's edges:
                # it is a marked span. An untouched proposal is a selected window.
                marked = link is None or link["proposal_source"] == "manual" or (
                    (link["proposed_start_ms"], link["proposed_end_ms"])
                    != (r["start_ms"], r["end_ms"]))
                item = judgment(source=r["asset_id"], start=r["start_ms"], end=r["end_ms"],
                                decision=_HIGHLIGHT[r["highlight"]],
                                selection="owner_marked_span" if marked
                                else "pipeline_selected_window",
                                goal="studio_highlight", origin="studio-example",
                                provenance="catalog:example:" + r["revision_id"],
                                session="studio")
                if item:
                    judgments.append(item)
            counts["studioExamples"] = len(rows)
        if _table(db, "benchmark_review_revisions"):
            rows = _latest(db.execute("SELECT * FROM benchmark_review_revisions").fetchall(),
                           "review_id")
            used = 0
            for r in rows:
                try:
                    payload = json.loads(r["payload_json"])
                except ValueError:
                    continue
                decision = {"keep": "keep", "skip": "skip", "uncertain": "unsure"}.get(
                    payload.get("reviewDecision"))
                item = decision and judgment(
                    source=r["asset_id"], start=r["start_ms"], end=r["end_ms"],
                    decision=decision, selection="pipeline_selected_window",
                    goal="archive_benchmark", origin="archive-benchmark",
                    provenance="catalog:benchmark:" + r["revision_id"],
                    session=r["benchmark_id"])
                if item:
                    judgments.append(item)
                    used += 1
            counts["benchmarkReviews"] = used
        if _table(db, "pairwise_revisions"):
            rows = _latest(db.execute("SELECT * FROM pairwise_revisions").fetchall(),
                           "comparison_id")
            for r in rows:
                pair = direct_pair(
                    _side(r["left_asset_id"], r["left_start_ms"], r["left_end_ms"]),
                    _side(r["right_asset_id"], r["right_start_ms"], r["right_end_ms"]),
                    _PAIR_CHOICE.get(r["preference"]), origin="studio-pairwise",
                    provenance="catalog:pairwise:" + r["revision_id"], goal="studio_highlight")
                if pair:
                    pairs.append(pair)
            counts["studioPairwise"] = len(rows)
    return {"judgments": judgments, "pairs": pairs, "counts": counts}


# ---- review files -----------------------------------------------------------------

def _scan(workspace: Path, extra: list[Path] | None = None) -> list[tuple[Path, dict, str]]:
    found, seen = [], set()
    for root in [Path(workspace) / "reports", *(extra or [])]:
        if root.is_dir():
            found += [f for f in _scan_root(root) if f[2] not in seen and not seen.add(f[2])]
    return found


def _scan_root(root: Path) -> list[tuple[Path, dict, str]]:
    found = []
    for path in sorted(root.rglob("*.json")):
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            continue
        raw = path.read_bytes()
        if b'"version"' not in raw[:4096] and b'"version"' not in raw[-4096:]:
            continue
        try:
            doc = json.loads(raw)
        except ValueError:
            continue
        if isinstance(doc, dict) and isinstance(doc.get("version"), str):
            found.append((path, doc, sha256(raw).hexdigest()))
    return found


def _blind_pairs(manifest: dict, reviews: dict, digest: str) -> list[dict]:
    pairs = {p["pairId"]: p for p in manifest.get("pairs", []) if isinstance(p, dict)}
    out = []
    for review in reviews.get("reviews", []):
        pair = pairs.get(review.get("pairId")) if isinstance(review, dict) else None
        if pair is None:
            continue
        outcome = {"a": 1.0, "b": 0.0, "tie": 0.5}.get(review.get("choice"))
        made = direct_pair(*(_side(pair[s].get("assetId"), pair[s].get("startMs"),
                                   pair[s].get("endMs")) for s in ("a", "b")),
                           outcome, origin="social-blind-pairs",
                           provenance=f"file:{digest}:{pair['pairId']}", goal="blind_pair")
        if made:
            out.append(made)
    return out


def _curated(doc: dict, digest: str) -> list[dict]:
    out = []
    for record in doc.get("records", []):
        source = (record.get("source") or {}).get("assetSha256")
        event = record.get("event") or {}
        for i, obs in enumerate(record.get("observerJudgments") or []):
            decision = {"keep": "keep", "skip": "skip"}.get(obs.get("preference"))
            ratings = {k: obs.get(k) for k in
                       ("technicalQuality", "aestheticQuality", "eventReadability")}
            item = decision and judgment(
                source=source, start=event.get("startMs"), end=event.get("endMs"),
                decision=decision, selection="pipeline_selected_window",
                goal="curation_" + str(obs.get("highlightGoal") or "unspecified"),
                origin="highlight-curation", session=digest, ratings=ratings,
                provenance=f"file:{digest}:{record.get('recordId')}:{obs.get('annotatorId')}:{i}")
            if item:
                out.append(item)
    return out


def _shown(path: Path, workspace: Path) -> str:
    return str(path.relative_to(workspace)) if path.is_relative_to(workspace) else str(path)


def from_files(workspace: Path, ledger_exports: set[str],
               extra: list[Path] | None = None) -> dict:
    files = _scan(workspace, extra)
    manifests = {d: doc for _, doc, d in files if doc["version"] == "social-blind-pairs-v1"}
    judgments, pairs, report = [], [], []
    seen_curated = set()
    for path, doc, digest in files:
        version = doc["version"]
        if version not in REVIEW_SCHEMAS:
            continue
        entry = {"file": _shown(path, Path(workspace)), "version": version,
                 "sha256": digest[:16]}
        if version == "social-blind-pairs-v1":
            continue  # a manifest; counted through its reviews
        if version == "social-blind-pair-reviews-v1":
            manifest = manifests.get(doc.get("manifestSha256"))
            made = _blind_pairs(manifest, doc, digest) if manifest else []
            pairs += made
            entry.update(used=bool(made), items=len(made),
                         reason=None if manifest else "frozen manifest not found under reports/")
        elif version == "klip-highlight-curation-manifest-v1":
            if not any(r.get("observerJudgments") for r in doc.get("records", [])):
                continue  # a pilot without reviews
            made = [] if digest in seen_curated else _curated(doc, digest)
            seen_curated.add(digest)
            judgments += made
            entry.update(used=bool(made), items=len(made), reason=None)
        elif version in ("klip-marine-owner-labels-v1", "klip-marine-owner-labels-v2",
                         "klip-v2-human-calibration-review-v1",
                         "klip-owner-run-annotations-v1"):
            imported = digest in ledger_exports
            entry.update(used=imported, items=None,
                         reason=None if imported else "not in the evidence ledger yet: "
                         + REVIEW_SCHEMAS[version])
        else:
            entry.update(used=False, items=None, reason=REVIEW_SCHEMAS[version])
        report.append(entry)
    return {"judgments": judgments, "pairs": pairs, "files": report}


# ---- everything together -------------------------------------------------------------

def locate(roots: list[Path]) -> dict:
    """Every catalog and owner review file under these folders, with label counts. Read-only."""
    catalogs, files = [], {}
    for root in roots:
        root = Path(root).expanduser()
        if not root.is_dir():
            continue
        for db_path in sorted(root.rglob("catalog.sqlite3")):
            if db_path.is_symlink():
                continue
            entry = {"workspace": str(db_path.parent)}
            try:
                with sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True) as db:
                    for table in ("assets", "example_revisions", "pairwise_revisions",
                                  "benchmark_review_revisions"):
                        entry[table] = (db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                                        if _table(db, table) else None)
            except sqlite3.Error as error:
                entry["error"] = str(error)
            catalogs.append(entry)
        for path, doc, digest in _scan_root(root):
            if doc["version"] in REVIEW_SCHEMAS and digest not in files:
                files[digest] = {"file": str(path), "version": doc["version"]}
    by_folder: dict[str, dict[str, int]] = {}
    for f in files.values():
        folder = str(Path(f["file"]).parent)
        by_folder.setdefault(folder, {})
        by_folder[folder][f["version"]] = by_folder[folder].get(f["version"], 0) + 1
    return {"catalogs": catalogs, "reviewFiles": len(files),
            "reviewFilesByFolder": dict(sorted(by_folder.items()))}


def collect(workspace: Path | None, ledger_rows: list[dict],
            extra_reports: list[Path] | None = None) -> dict:
    """All judgments and direct pairs. Duplicates merge; contradictions are set aside."""
    judgments = from_ledger(ledger_rows)
    pairs, counts, files = [], {"ledger": len(ledger_rows)}, []
    if workspace is not None:
        catalog = from_catalog(workspace)
        found = from_files(workspace, {r["exportSha256"] for r in ledger_rows}, extra_reports)
        judgments += catalog["judgments"] + found["judgments"]
        pairs += catalog["pairs"] + found["pairs"]
        counts.update(catalog["counts"])
        files = found["files"]
    merged: dict[tuple, list[dict]] = {}
    for j in judgments:
        merged.setdefault((j["sourceSha256"], j["startMs"], j["endMs"], j["goal"]), []).append(j)
    kept, contradictions, duplicates = [], [], 0
    for key, group in sorted(merged.items()):
        decisions = {j["decision"] for j in group} - {"unsure"}
        if len(decisions) > 1:
            contradictions.append({"sourceSha256": key[0], "startMs": key[1], "endMs": key[2],
                                   "goal": key[3], "origins": sorted({j["origin"] for j in group}),
                                   "resolution": "excluded_needs_owner_decision"})
            continue
        duplicates += len(group) - 1
        # Prefer the record with the owner's own boundaries, then any with ratings.
        group.sort(key=lambda j: (j["selection"] != "owner_marked_span", not j["ratings"],
                                  j["evidenceId"]))
        kept.append(group[0])
    unique_pairs = {}
    for p in pairs:
        unique_pairs.setdefault(p["evidenceId"], p)
    return {"version": VERSION, "judgments": kept, "pairs": list(unique_pairs.values()),
            "counts": {**counts, "judgments": len(kept), "directPairs": len(unique_pairs),
                       "duplicateJudgmentsMerged": duplicates,
                       "contradictionsExcluded": len(contradictions)},
            "contradictions": contradictions, "files": files}
