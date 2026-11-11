"""Source-safe sport reel proposals and optional silent preview-proxy renders.

All media come from a frozen, SHA-rehashed campaign review packet inside the
workspace. This module NEVER opens original DJI recordings, modifies the
catalog, infers event truth from motion, or publishes anything. Keep decisions
are used only if a user-supplied blind review export validates against the
exact frozen packet. Reels are preliminary: preview fast-seek timing is
approximate, audio is absent and video quality is reduced.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
from html import escape
from pathlib import Path
import json
import math
import os
import re
import subprocess
import tempfile
from urllib.parse import quote

from .full_archive_campaign_status import campaign_progress
from .highlight_curation_pilot import (
    VERSION as PILOT_VERSION, MANIFEST_VERSION, _safe_dest
)
from .highlight_curation_review import REVIEW_VERSION, _review
from .review_utility_v2 import editorial_route, VERSION as ROUTING_VERSION
from .media import require_tool

VERSION = "klip-reviewed-proxy-sport-reels-v1"
SPORTS = ("ski", "bike", "snorkeling")
ROLES = {"motion_change_hypothesis", "uniform_timeline_control"}
MAX_PROXY_BYTES = 45 * 1024 * 1024
MAX_READ_BYTES = 12 * 1024 * 1024
_ID = re.compile(r"^[a-f0-9]{24}$")
_SHA = re.compile(r"^[a-f0-9]{64}$")


def _inside_workspace(root: Path, path: Path) -> Path:
    # Refuse symlink escapes, including a symlinked reports subdirectory.
    absolute = Path(os.path.abspath(os.path.expanduser(str(path))))
    if (not absolute.is_relative_to(root) or absolute == root
            or absolute.relative_to(root).parts[0] not in ("reports", "exports")):
        raise ValueError("Only workspace reports/ or exports/ are allowed")
    cursor = absolute
    while cursor != root:
        if cursor.is_symlink():
            raise ValueError("Symlinked report or output path refused")
        cursor = cursor.parent
    return absolute


def _json(path: Path, max_size: int = MAX_READ_BYTES) -> tuple[dict, str]:
    if (path.is_symlink() or not path.is_file()
            or not 2 <= path.stat().st_size <= max_size):
        raise ValueError(f"Missing or unsafe local JSON report: {path}")
    body = path.read_bytes()
    try:
        result = json.loads(body)
    except (UnicodeError, ValueError) as exc:
        raise ValueError(f"Invalid JSON report: {path}") from exc
    if not isinstance(result, dict):
        raise ValueError("Report must be a JSON object")
    return result, sha256(body).hexdigest()


def _file_sha(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _human_evidence(reviews_file: Path | None, manifest: dict,
                    manifest_sha: str, ready: set[str]) -> dict[str, dict]:
    if reviews_file is None:
        return {}
    p = Path(reviews_file).expanduser()
    if p.is_symlink() or not p.is_file():
        raise ValueError("Human review export missing or symlinked")
    doc, _ = _json(p, 2_000_000)
    rows = doc.get("reviews")
    if (doc.get("version") != REVIEW_VERSION
            or doc.get("manifestSha256") != manifest_sha
            or not isinstance(rows, list)):
        raise ValueError("Human feedback file does not match this frozen review packet")
    by_id = {row["recordId"]: row for row in manifest["records"]}
    if len(rows) > len(by_id):
        raise ValueError("Review export has more entries than packet")
    result = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("recordId") not in ready:
            raise ValueError("Human label refers to missing/unverified preview")
        rid = row["recordId"]
        if rid in result:
            raise ValueError("Duplicate human vote for one candidate")
        result[rid] = _review(row, by_id[rid], True)
    return result


def _rating_score(review: dict) -> float | None:
    # A *human review* preference tie-breaker, not action/event probability.
    ratings = [
        (review.get("eventReadability"), 0.4),
        (review.get("aestheticQuality"), 0.35),
        (review.get("technicalQuality"), 0.25),
    ]
    available = [(value, weight) for value, weight in ratings if value is not None]
    if not available:
        return None
    return round(sum(value * w for value, w in available)
                 / sum(w for _, w in available), 3)


def _explanation(role: str, reviewed: dict | None) -> list[dict]:
    result = [{
        "basis": "coarse_scan_proposal",
        "text": (
            "A 1-fps grayscale camera-motion change suggested inspecting this "
            "interval. That is not evidence of a jump, landing or valuable clip."
            if role == "motion_change_hypothesis" else
            "This interval was selected as a spaced timeline control to keep "
            "quiet or unusual footage visible to reviewers. It is not a negative label."
        ),
    }]
    if reviewed is None:
        result.append({
            "basis": "unreviewed",
            "text": "No human visual judgment supplied. Event, outcome, quality and personal value remain unknown.",
        })
    else:
        result.append({
            "basis": "human_packet_review",
            "text": (
                f"Human review chose {reviewed['preference']} for "
                f"{reviewed['highlightGoal']}. This is a user's goal-specific "
                "editorial decision, not independent event ground truth."
            ),
        })
        if reviewed.get("rationale"):
            result.append({"basis": "human_written_reason",
                           "text": reviewed["rationale"]})
        if reviewed["outcomeVisible"] == "yes" and reviewed["observedOutcome"] != "unknown":
            result.append({
                "basis": "human_reported_visible_outcome",
                "text": f"Reviewer reported a visible {reviewed['observedOutcome']} outcome; independent qualification pending.",
            })
    return result


def _packet_candidates(root: Path, packet_dir: Path, status: dict,
                       reviews_file: Path | None) -> tuple[list[dict], dict]:
    manifest, manifest_sha = _json(packet_dir / "curation-manifest.json")
    selection, _ = _json(packet_dir / "selection-report.json")
    presentation, _ = _json(packet_dir / "presentation.json")
    if (manifest.get("version") != MANIFEST_VERSION
            or selection.get("version") != PILOT_VERSION
            or presentation.get("version") != PILOT_VERSION
            or selection.get("manifestSha256") != manifest_sha
            or presentation.get("manifestSha256") != manifest_sha
            or selection.get("sourceScanFingerprint") != status["scan"]["scanStateSha256"]
            or not isinstance(manifest.get("records"), list)
            or not isinstance(selection.get("selections"), list)
            or not isinstance(presentation.get("records"), dict)):
        raise ValueError("Packet provenance or scan fingerprint mismatch")

    records = manifest["records"]
    by_id = {x["recordId"]: x for x in records}
    if (len(by_id) != len(records)
            or set(by_id) != set(presentation["records"])
            or len(selection["selections"]) != len(records)):
        raise ValueError("Packet roster duplicates or missing records")
    selected = {}
    for row in selection["selections"]:
        rid = row.get("recordId")
        if not _ID.fullmatch(str(rid)) or rid in selected or rid not in by_id:
            raise ValueError("Invalid or duplicated packet selection ID")
        selected[rid] = row
    if set(selected) != set(by_id):
        raise ValueError("Packet selection/manifest divergence")

    ready = set()
    media_paths = {}
    counts = Counter()
    for rid, record in by_id.items():
        source = record.get("source") or {}
        event = record.get("event") or {}
        sport = (record.get("sportContext") or {}).get("sport")
        role = selected[rid].get("selectionRole")
        aid = source.get("assetSha256")
        start, end = event.get("startMs"), event.get("endMs")
        if (not _SHA.fullmatch(str(aid)) or sport not in (*SPORTS, "unknown")
                or type(start) is not int or type(end) is not int
                or not 0 <= start < end <= source.get("sourceDurationMs", -1)
                or selected[rid].get("sourceAssetId") != aid
                or selected[rid].get("startMs") != start
                or selected[rid].get("endMs") != end or role not in ROLES):
            raise ValueError("Packet source/time/role mismatch")
        entry = presentation["records"][rid]
        if entry.get("verifiedProxyReady") is not True:
            counts["missingVerifiedProxy"] += 1
            continue
        expected_url = "media/" + rid + ".mp4"
        expected_sha = entry.get("proxySha256")
        media = _inside_workspace(root, packet_dir / expected_url)
        if (entry.get("proxyUrl") != expected_url
                or not _SHA.fullmatch(str(expected_sha))
                or not media.is_file() or not 64 <= media.stat().st_size <= MAX_PROXY_BYTES
                or _file_sha(media) != expected_sha):
            counts["invalidProxy"] += 1
            continue
        ready.add(rid)
        media_paths[rid] = media
    reviews = _human_evidence(reviews_file, manifest, manifest_sha, ready)
    rows = []
    for rid in ready:
        record = by_id[rid]
        item = selected[rid]
        reviewed = reviews.get(rid)
        sport = record["sportContext"]["sport"]
        rows.append({
            "recordId": rid,
            "sourceAssetSha256": record["source"]["assetSha256"],
            "sourceStartMs": record["event"]["startMs"],
            "sourceEndMs": record["event"]["endMs"],
            "sport": sport,
            "selectionRole": item["selectionRole"],
            "coarseMotionEvidenceForAuditOnly": item.get("heuristicScoreForAuditOnly"),
            "mediaPath": str(media_paths[rid]),
            "proxySha256": presentation["records"][rid]["proxySha256"],
            "verifiedProxy": True,
            "humanPreference": reviewed["preference"] if reviewed else None,
            "humanGoal": reviewed["highlightGoal"] if reviewed else None,
            "humanRatings": {
                "technicalQuality": reviewed["technicalQuality"],
                "aestheticQuality": reviewed["aestheticQuality"],
                "eventReadability": reviewed["eventReadability"],
            } if reviewed else None,
            "humanPreferenceTieBreaker": _rating_score(reviewed) if reviewed else None,
            "humanCurationV2": editorial_route(reviewed) if reviewed else None,
            "eventUnderstandingStatus": "human_reported_unadjudicated" if reviewed else "unknown",
            "why": _explanation(item["selectionRole"], reviewed),
            "publicationApproved": False,
            "sourceVideoAccessed": False,
        })
    return sorted(rows, key=lambda r: (r["sport"], r["sourceAssetSha256"],
                                        r["sourceStartMs"])), {
        "packetCandidates": len(records),
        "verifiedPreviewCandidates": len(ready),
        "missingVerifiedProxies": counts["missingVerifiedProxy"],
        "invalidProxyReceipts": counts["invalidProxy"],
        "humanReviewedCandidates": len(reviews),
        "manifestSha256": manifest_sha,
    }


def _choose(rows: list[dict], sport: str, mode: str,
            duration_budget_s: float, max_per_sport: int) -> list[dict]:
    options = [
        r for r in rows if r["sport"] == sport
        and (mode == "exploratory" or (
            r["humanPreference"] == "keep"
            and (r.get("humanCurationV2") or {}).get(
                "eligibleForSportHighlightDraft") is True))
    ]
    # Advisory human rationale routes analysis/memories out of sport drafts.
    # An editorial preference must never be treated as verified event truth.
    if mode == "reviewed":
        tiers = {"explicit_top_pick": 0, "strong_positive": 1,
                 "ordinary_keep_or_unknown": 2}
        options.sort(key=lambda r: (
            1 if r["humanCurationV2"]["destination"] == "scenic_transition" else 0,
            tiers[r["humanCurationV2"]["editorialPriority"]],
            -(r["humanPreferenceTieBreaker"] or 0),
            r["sourceAssetSha256"], r["sourceStartMs"],
        ))
    else:
        options.sort(key=lambda r: (
            0 if r["selectionRole"] == "motion_change_hypothesis" else 1,
            r["sourceAssetSha256"], r["sourceStartMs"],
        ))
    chosen, seen_sources = [], set()
    budget_ms = int(duration_budget_s * 1000)
    for novelty_round in (True, False):
        for r in options:
            if len(chosen) >= max_per_sport:
                break
            if r in chosen or (novelty_round and r["sourceAssetSha256"] in seen_sources):
                continue
            if sum(x["sourceEndMs"]-x["sourceStartMs"] for x in chosen) + (
                    r["sourceEndMs"]-r["sourceStartMs"]) > budget_ms:
                continue
            chosen.append(r)
            seen_sources.add(r["sourceAssetSha256"])
        if len(chosen) >= max_per_sport:
            break
    # Group same-source moments together to avoid arbitrary jumps in the
    # middle of continuous footage; SHA ordering is NOT trip chronology.
    return sorted(chosen, key=lambda r: (
        r["sourceAssetSha256"], r["sourceStartMs"]
    ))


def _review_gallery_html(reels: dict, packet: Path, dest: Path, mode: str) -> str:
    """Generate a browser-only local gallery; no JS, CDN, upload or remote media."""
    cards = []
    for sport in SPORTS:
        plan = reels[sport]
        clips = []
        for i, item in enumerate(plan["selected"], 1):
            # Stage checksum-verified local hardlinks under the gallery
            # to avoid file:// cross-directory browser restrictions.
            url = "media/" + quote(item["recordId"], safe="") + ".mp4"
            explanation = "".join(
                f"<p><strong>{escape(x['basis'].replace('_',' ').title())}:</strong> "
                f"{escape(x['text'])}</p>" for x in item["why"]
            )
            notes = item.get("humanRatings")
            editorial = item.get("humanCurationV2")
            editorial_note = (
                "<p class='meta'>Human-intended collection: " +
                escape(editorial["destination"].replace("_", " ")) +
                " · Follow-up: " +
                escape(", ".join(editorial["editActions"]) or "none") + "</p>"
                if editorial else ""
            )
            ratings = (
                "<p class='meta'>Human ratings: " +
                escape(str(notes)) + "</p>"
                if notes is not None else
                "<p class='meta'>Not human-rated; this is an exploration candidate.</p>"
            )
            clips.append(
                "<article class='clip'>"
                f"<video controls playsinline preload='metadata' src='{escape(url, quote=True)}'></video>"
                f"<h3>{i}. Source {escape(item['sourceAssetSha256'][:12])}</h3>"
                f"<p class='meta'>Source {item['sourceStartMs']/1000:.2f}–"
                f"{item['sourceEndMs']/1000:.2f}s · "
                f"{escape(item['selectionRole'].replace('_',' '))}</p>"
                f"{ratings}{editorial_note}<details><summary>Evidence and selection reasoning</summary>"
                f"{explanation}</details></article>"
            )
        if not clips:
            clips.append("<p class='empty'>No eligible clips with this review policy.</p>")
        cards.append(
            f"<section id='{sport}'><header><h2>{escape(sport.title())}</h2>"
            f"<span>{len(plan['selected'])} selected · "
            f"{escape(plan['transitionPolicy'].replace('_',' '))}</span></header>"
            f"<div class='grid'>{''.join(clips)}</div></section>"
        )
    return ("""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'self' data: file:; media-src 'self' file:; style-src 'unsafe-inline'; script-src 'none'; connect-src 'none'; object-src 'none'">
<title>Adventure-Klip · Sport Reel Research Review</title>
<style>
:root{color-scheme:dark;font:15px/1.55 system-ui,sans-serif;background:#0a121a;color:#e9f1f6}
*{box-sizing:border-box}body{margin:0}main{max-width:1280px;margin:auto;padding:26px}
h1{font-size:clamp(24px,4vw,42px);margin:0 0 8px;letter-spacing:-.03em}
h2{font-size:24px;margin:0}h3{font-size:14px;margin:9px 0 3px}
.hero{padding:34px 0 22px;border-bottom:1px solid #294252;margin-bottom:30px}
.subtitle,.meta,.small{color:#a5b5c1}.meta{font-size:12px;margin:5px 0}
nav{display:flex;gap:16px;flex-wrap:wrap;margin-top:18px}
a{color:#89ccce}section{margin:0 0 42px}
section header{display:flex;justify-content:space-between;align-items:baseline;gap:20px;margin:0 0 16px}
section header span{font-size:12px;color:#b2c4cc}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:15px}
.clip{padding:12px;background:#162330;border:1px solid #2c4051;border-radius:12px}
video{display:block;width:100%;aspect-ratio:16/9;object-fit:contain;background:#070a10;border-radius:7px}
details{margin:12px 0 0;border-top:1px solid #2c4051;padding-top:9px}
details p{font-size:13px;color:#c6d2d9}summary{cursor:pointer;color:#86d0ca;font-size:13px}
.empty{color:#99acbb}.alert{background:#222e38;border-left:3px solid #ce9a80;padding:12px 14px}
</style></head><body><main><div class="hero">
<h1>Adventure-Klip · Reel Laboratory</h1>
<p class="subtitle">Local, evidence-grounded preview selection. Nothing here has been published.</p>
<p class="alert">These are silent, reduced-resolution, approximate-seek preview derivatives.
Motion hypotheses are not proven sporting actions. Human Keep decisions are goal-specific
editorial feedback, not permission to publish or factual event verification.</p>
<nav><a href="#ski">Skiing</a><a href="#bike">Mountain biking</a>
<a href="#snorkeling">Snorkeling</a></nav>
<p class="small">Mode: """ + escape(mode) + """ · Only checksum-verified local proxy clips are shown.</p>
</div>""" + "".join(cards) + "</main></body></html>\n")


def plan_sport_reels(workspace: Path, audit_file: Path, scan_dir: Path,
                     packet_dir: Path, output_dir: Path, *,
                     mode: str = "exploratory", reviews_file: Path | None = None,
                     duration_budget_s: float = 150.0,
                     max_per_sport: int = 8, render: bool = False) -> dict:
    if mode not in ("exploratory", "reviewed"):
        raise ValueError("mode must be exploratory or reviewed")
    if mode == "reviewed" and reviews_file is None:
        raise ValueError("Reviewed mode requires a frozen human review export")
    if (type(duration_budget_s) not in (int, float)
            or not math.isfinite(duration_budget_s)
            or not 20 <= duration_budget_s <= 600):
        raise ValueError("Duration budget must be 20..600 seconds")
    if type(max_per_sport) is not int or not 1 <= max_per_sport <= 16:
        raise ValueError("max_per_sport must be 1..16")
    root = Path(workspace).expanduser().resolve()
    audit = _inside_workspace(root, audit_file)
    scans = _inside_workspace(root, scan_dir)
    packet = _inside_workspace(root, packet_dir)
    # Check all SHA-backed receipts through existing Klip campaign logic,
    # not merely the latest '0 sources remaining' shell text.
    progress = campaign_progress(root, audit, scan_dir=scans, packet_dir=packet)
    if not progress["goNoGo"]["coarseScannedEveryCatalogSource"]:
        raise ValueError("Full source campaign is not SHA-qualified")
    if progress["reviewPacket"]["matchesCurrentScanCheckpoint"] is not True:
        raise ValueError("Packet is stale against the current scan checkpoint")
    if progress["reviewPacket"]["verifiedPlaybackClipsReported"] < 1:
        raise ValueError("Packet has no verified playable clips")
    rows, checks = _packet_candidates(root, packet, progress, reviews_file)
    if checks["invalidProxyReceipts"]:
        raise ValueError("Packet has corrupted preview media: investigate before planning")
    dest = _safe_dest(root, output_dir)
    plans = {}
    for sport in SPORTS:
        chosen = _choose(rows, sport, mode, duration_budget_s, max_per_sport)
        plans[sport] = {
            "sport": sport,
            "status": "ready_for_draft_review" if chosen else "no_eligible_reviewed_clip",
            "transitionPolicy": "short_crossfade" if sport == "snorkeling" else "hard_cut",
            "orderPolicy": "same_source_contiguity_then_source_sha_not_real_trip_chronology",
            "proxyDurationBudgetSeconds": duration_budget_s,
            "selected": chosen,
            "selectionCount": len(chosen),
            "sourceCount": len({r["sourceAssetSha256"] for r in chosen}),
            "publicationApproved": False,
            "containsVerifiedActionClaims": False,
        }
    auxiliary = {"technique_analysis": [], "personal_memory": [],
                 "needs_human_routing": []}
    for r in rows:
        tag = r.get("humanCurationV2") or {}
        group = tag.get("destination")
        if r["humanPreference"] == "keep" and group in auxiliary:
            auxiliary[group].append({
                "recordId": r["recordId"],
                "sport": r["sport"],
                "sourceAssetSha256": r["sourceAssetSha256"],
                "sourceStartMs": r["sourceStartMs"],
                "sourceEndMs": r["sourceEndMs"],
                "humanCurationV2": tag,
                "requiresSeparateHumanReview": True,
            })
    destination_counts = Counter(
        r["humanCurationV2"]["destination"] for r in rows
        if r.get("humanCurationV2") is not None)
    # Includes requests attached to skipped cuts. These are private,
    # source-linked *instructions for future review*, not media mutations.
    followups = []
    for r in rows:
        route = r.get("humanCurationV2") or {}
        if route.get("editActions"):
            followups.append({
                "recordId": r["recordId"],
                "sourceAssetSha256": r["sourceAssetSha256"],
                "sourceStartMs": r["sourceStartMs"],
                "sourceEndMs": r["sourceEndMs"],
                "humanPreference": r["humanPreference"],
                "editActions": route["editActions"],
                "humanTimeAnchorsMs": route["humanTimeAnchorsMs"],
                "sourceVideoInspectedForFollowup": False,
            })
    doc = {
        "version": VERSION,
        "humanRouting": {
            "version": ROUTING_VERSION,
            "reviewCount": sum(destination_counts.values()),
            "destinationCounts": dict(sorted(destination_counts.items())),
            "sourceVideoInspectedByRouting": False,
            "userDataTrained": False,
        },
        "additionalReviewedCollections": auxiliary,
        "humanEditorialFollowups": followups,
        "manualOnlyDiscardHolds": [
            request for request in followups
            if "manual_only_discard_request" in request["editActions"]
        ],
        "mode": mode,
        "status": "draft_only_needs_human_review_and_master_render",
        "campaignAudit": str(audit),
        "packet": str(packet),
        "scanStateSha256": progress["scan"]["scanStateSha256"],
        "packetValidation": checks,
        "reels": plans,
        "sourceOriginalReads": 0,
        "rawVideoModified": False,
        "modelInferencePerformed": False,
        "soundtrack": "none_packet_previews_are_silent",
        "editingLimitations": [
            "Proxies are low-resolution silent H264 and use approximate fast-seek time alignment.",
            "Selection role is coarse sampling evidence, not demonstrated event truth.",
            "A human Keep is goal-specific preference, not publishing authorization.",
            "Sport labels come from catalog and can be unknown or incorrect.",
            "Final narrative and source-time trim require video review and optional master render.",
            "Free-text routing and edit intent are heuristic; editActions do not trigger cuts.",
            "Personal memories and analyzed failures remain separate, not silently discarded.",
        ],
    }
    # Avoid leaving a silently completed packet if a validation failed.
    dest.mkdir(parents=True, mode=0o700, exist_ok=False)
    media_dir = dest / "media"
    media_dir.mkdir(mode=0o700)
    unique_selected = {
        r["recordId"]: r for plan in plans.values() for r in plan["selected"]
    }
    for rid, item in unique_selected.items():
        source = Path(item["mediaPath"])
        target = media_dir / (rid + ".mp4")
        if source.is_symlink() or _file_sha(source) != item["proxySha256"]:
            raise ValueError("Local preview changed before staging the reel gallery")
        # Hardlink validated packet clips: no raw SSD video access or extra
        # video storage. Both paths are inside the same private workspace.
        os.link(source, target, follow_symlinks=False)
        if _file_sha(target) != item["proxySha256"]:
            target.unlink(missing_ok=True)
            raise ValueError("Local gallery preview does not match its receipt")
    (dest / "reel-plan.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    (dest / "reel-review.html").write_text(
        _review_gallery_html(plans, packet, dest, mode), encoding="utf-8")
    if render:
        for sport, recipe in plans.items():
            if not recipe["selected"]:
                continue
            target = dest / f"{sport}-{mode}-draft.mp4"
            result = render_proxy_reel(recipe, target)
            recipe["render"] = result
        (dest / "render-results.json").write_text(
            json.dumps({k:v.get("render") for k,v in plans.items()},
                       indent=2, sort_keys=True) + "\n")
    return {
        "status": doc["status"], "directory": str(dest),
        "reviewGallery": str(dest / "reel-review.html"),
        "mode": mode, "packetValidation": checks,
        "selected": {s: plans[s]["selectionCount"] for s in SPORTS},
        "rendered": {s: plans[s].get("render", {}).get("path") for s in SPORTS},
        "sourceOriginalReads": 0, "published": False,
    }


def _probe_proxy_duration(path: Path) -> float:
    tool = require_tool("ffprobe")
    cmd = [tool, "-v", "error", "-select_streams", "v:0",
           "-show_entries", "format=duration", "-of", "json", str(path)]
    result = subprocess.run(cmd, capture_output=True, text=True,
                            timeout=30, check=True)
    data = json.loads(result.stdout)
    duration = float((data.get("format") or {}).get("duration") or 0)
    if not math.isfinite(duration) or not 0.5 <= duration <= 65:
        raise ValueError("Invalid verified preview duration")
    return duration


def render_proxy_reel(recipe: dict, target: Path) -> dict:
    """Visual-only draft: source-action hard cuts; scenic underwater dissolves."""
    segments = recipe["selected"]
    if not segments:
        raise ValueError("No verified preview segments")
    if len(segments) > 16:
        raise ValueError("Too many segments for bounded local preview render")
    paths, durations = [], []
    for item in segments:
        path = Path(item["mediaPath"])
        if path.is_symlink() or not path.is_file() or _file_sha(path) != item["proxySha256"]:
            raise ValueError("Verified packet preview changed before rendering")
        paths.append(path)
        durations.append(_probe_proxy_duration(path))
    ffmpeg = require_tool("ffmpeg")
    transition = "fade" if recipe["transitionPolicy"] == "short_crossfade" else "cut"
    inputs = []
    filters = []
    for i, (path, duration) in enumerate(zip(paths, durations)):
        inputs += ["-i", str(path)]
        filters.append(
            f"[{i}:v]fps=30,scale=960:540:force_original_aspect_ratio=decrease,"
            f"pad=960:540:(ow-iw)/2:(oh-ih)/2,setsar=1,"
            f"format=yuv420p,trim=duration={duration:.3f},setpts=PTS-STARTPTS[v{i}]"
        )
    current = "[v0]"
    total = durations[0]
    for i in range(1, len(paths)):
        previous = f"[joined{i}]"
        if transition == "fade":
            overlap = min(0.40, durations[i] / 5, durations[i-1] / 5)
            offset = total - overlap
            filters.append(
                f"{current}[v{i}]xfade=transition=fade:duration={overlap:.3f}:"
                f"offset={offset:.3f}{previous}"
            )
            total += durations[i] - overlap
        else:
            filters.append(f"{current}[v{i}]concat=n=2:v=1:a=0{previous}")
            total += durations[i]
        current = previous
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".klip-reel-", suffix=".mp4", dir=target.parent)
    os.close(fd)
    try:
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error",
               "-nostdin", "-y", *inputs, "-filter_complex", ";".join(filters),
               "-map", current, "-an", "-c:v", "libx264",
               "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
               "-threads", "2", "-movflags", "+faststart", temp]
        subprocess.run(cmd, check=True, capture_output=True, timeout=900)
        if not Path(temp).is_file() or Path(temp).stat().st_size < 512:
            raise RuntimeError("FFmpeg did not produce a usable local draft")
        os.replace(temp, target)
    finally:
        Path(temp).unlink(missing_ok=True)
    return {
        "path": str(target),
        "bytes": target.stat().st_size,
        "sha256": _file_sha(target),
        "segments": len(paths),
        "estimatedDurationSeconds": round(total, 3),
        "transition": transition,
        "audio": False, "originalVideoReads": 0,
        "quality": "960x540 silent proxy-only draft",
        "publicationApproved": False,
    }
