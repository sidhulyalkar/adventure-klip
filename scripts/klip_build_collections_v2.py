#!/usr/bin/env python3
"""Plan and optionally render non-destructive ski/snorkel category reels.

Requires a frozen, completely SHA-qualified campaign and receipt-verified packet.
Renders *only* local silent H.264 derivatives. No source camera video, GPU/LLM,
external network, publication, or catalog mutation. New destination per run.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
from html import escape
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from adventure_intelligence.campaign_reels import (
    _inside_workspace, _json, _packet_candidates, _file_sha,
)
from adventure_intelligence.full_archive_campaign_status import campaign_progress
from adventure_intelligence.highlight_curation_pilot import _safe_dest
from adventure_intelligence.moment_edit_v2 import (
    VERSION, COLLECTIONS, build_plan, annotation_template,
)
from adventure_intelligence.moment_label_studio_v2 import render_label_studio


def _atomic_json(path: Path, body: dict) -> None:
    target = path.with_name(path.name + ".partial")
    with target.open("x", encoding="utf-8") as file:
        json.dump(body, file, indent=2, sort_keys=True, allow_nan=False)
        file.write("\n")
    os.replace(target, path)


def _render_one(ffmpeg: str, media: Path, target: Path, window: dict) -> dict:
    """Trim around source-relative proxy offsets, keeping original bytes intact."""
    start = window["relativeStartMs"] / 1000
    length = window["durationMs"] / 1000
    if not 0 <= start < 86400 or not 0.5 <= length <= 120:
        raise ValueError("Unsupported proxy trim interval")
    partial = target.with_name("." + target.name + ".partial.mp4")
    vf = ("scale=960:540:force_original_aspect_ratio=decrease,"
          "pad=960:540:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p")
    cmd = [
        ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{start:.3f}", "-i", str(media),
        "-t", f"{length:.3f}", "-map", "0:v:0",
        "-an", "-sn", "-vf", vf, "-c:v", "libx264",
        "-preset", "veryfast", "-crf", "23", "-threads", "2",
        "-video_track_timescale", "90000", "-movflags", "+faststart",
        str(partial),
    ]
    try:
        run = subprocess.run(cmd, stdout=subprocess.DEVNULL,
                             stderr=subprocess.PIPE, timeout=180, check=False)
        if run.returncode or not partial.is_file() or not 100 <= partial.stat().st_size <= 350*1024*1024:
            raise ValueError("Preview trim failed: " + run.stderr.decode(errors="replace")[-250:])
        # Reject empty/too-short outputs rather than treating them as finished edits.
        probe = shutil.which("ffprobe")
        if probe:
            p = subprocess.run(
                [probe, "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "format=duration", "-of", "default=nw=1:nk=1",
                 str(partial)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=20, check=False,
            )
            if p.returncode:
                raise ValueError("Cannot validate rendered video duration")
            actual = float(p.stdout.strip())
            if not .45 <= actual <= length + 1.0:
                raise ValueError("Rendered clip duration does not match selected cut")
        os.replace(partial, target)
        return {"sha256": _file_sha(target), "bytes": target.stat().st_size,
                "durationMsRequested": window["durationMs"],
                "nativePtsVerified": False}
    finally:
        partial.unlink(missing_ok=True)


def _render(plan: dict, verified: dict[str, dict], folder: Path) -> dict:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise ValueError("Render requires ffmpeg and ffprobe")
    folder.mkdir(mode=0o700, exist_ok=False)
    clips_dir = folder / "clips"
    clips_dir.mkdir(mode=0o700)
    report = {"version": VERSION, "mode": "silent_proxy_only",
              "originalSourceReads": 0, "published": False,
              "renderedUniqueCuts": {}, "reels": {}, "errors": {}}
    for category, collection in plan["collections"].items():
        clips = collection["selected"]
        if not clips:
            report["reels"][category] = {"status": "no_eligible_moments"}
            continue
        normalized = []
        try:
            for item in clips:
                rid = item["momentId"]
                row = verified[rid]
                src = Path(row["mediaPath"])
                # Recheck original packet receipt before rendering each new unique cut.
                if (src.is_symlink() or not src.is_file()
                        or _file_sha(src) != item["sourceProxySha256"]):
                    raise ValueError("Source preview is missing or changed")
                cut = item["variant"]
                name = sha256((rid + ":" + str(cut["relativeStartMs"]) + ":" +
                               str(cut["relativeEndMs"])).encode()).hexdigest()[:24]
                target = clips_dir / (name + ".mp4")
                if name not in report["renderedUniqueCuts"]:
                    report["renderedUniqueCuts"][name] = _render_one(ffmpeg, src, target, cut)
                normalized.append(target)
            listing = folder / ("." + category + ".concat.txt")
            partial = folder / ("." + category + ".partial.mp4")
            try:
                with listing.open("x") as file:
                    for path in normalized:
                        file.write("file 'clips/" + path.name + "'\n")
                result = subprocess.run([
                    ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                    "-y", "-f", "concat", "-safe", "1", "-i", str(listing),
                    "-c", "copy", "-movflags", "+faststart", str(partial),
                ], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                   timeout=180, check=False)
                if result.returncode or not partial.is_file() or partial.stat().st_size < 100:
                    raise ValueError("Cannot concatenate verified preview cuts")
                final = folder / (category + ".mp4")
                os.replace(partial, final)
                report["reels"][category] = {
                    "status": "preview_rendered_unreviewed",
                    "path": str(final), "sha256": _file_sha(final),
                    "clips": len(normalized), "silent": True,
                }
            finally:
                listing.unlink(missing_ok=True)
                partial.unlink(missing_ok=True)
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            report["reels"][category] = {"status": "blocked_render_failed"}
            report["errors"][category] = type(exc).__name__ + ": " + str(exc)[:280]
    report["status"] = "partial_render_requires_review" if report["errors"] else "completed_preview_renders"
    return report


def _gallery(plan: dict) -> str:
    """Private local offline review gallery using verified hard-linked proxies."""
    sections = []
    for key, collection in plan["collections"].items():
        cards = []
        for item in collection["selected"]:
            rid = item["momentId"]
            cut = item["variant"]
            rel_a = cut["relativeStartMs"] / 1000
            rel_b = cut["relativeEndMs"] / 1000
            categories = ", ".join(item["categoryTags"]) or "Category not yet reviewed"
            needs = ", ".join(cut["reviewRequired"]) or "No unresolved trim flags"
            cards.append(
                "<article><video controls preload='metadata' playsinline "
                f"src='media/{rid}.mp4' data-start='{rel_a:.3f}' data-end='{rel_b:.3f}'></video>"
                f"<h3>{escape(categories)}</h3>"
                f"<p>Source {escape(item['sourceAssetSha256'][:12])} • "
                f"{cut['sourceStartMs']/1000:.2f}–{cut['sourceEndMs']/1000:.2f}s"
                f" • {escape(cut['editorialVariant'])}</p>"
                f"<p class='muted'>{escape(needs)}</p></article>"
            )
        sections.append(f"<section><h2>{escape(key.replace('_',' ').title())} "
                        f"<small>({len(cards)})</small></h2><div class='grid'>"
                        + "".join(cards or ["<p>No qualified moments yet.</p>"]) + "</div></section>")
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; media-src 'self' file:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'; img-src 'none'">
<title>Klip V2 | Moment and edit laboratory</title>
<style>
:root{color-scheme:dark;background:#10171e;color:#e6ecf0;font:14px/1.5 system-ui}
body{margin:0 auto;max-width:1200px;padding:32px}h1{font-size:26px}h2{font-size:18px;margin-top:40px}
h3{font-size:14px}.muted,p,small{color:#99abb7}.alert{padding:12px;background:#28313a;border-left:3px solid #b6a58e}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:18px}
article{padding:12px;border:1px solid #384957;border-radius:10px;background:#1a242d}video{width:100%;background:black;aspect-ratio:16/9}article p{font-size:12px}
</style></head><body><h1>Klip / Moment &amp; Edit Laboratory</h1>
<p class="alert">Exploratory cuts from verified local previews. Owner tags are unadjudicated.
Clips are silent and frame-exact source PTS has not been verified. No publication approval.</p>
""" + "\n".join(sections) + """
<script>
for(const v of document.querySelectorAll('video')){
  const start=Number(v.dataset.start), end=Number(v.dataset.end);
  v.addEventListener('loadedmetadata',()=>{if(Number.isFinite(start))v.currentTime=start;});
  v.addEventListener('play',()=>{if(v.currentTime>=end-.05)v.currentTime=start;});
  v.addEventListener('timeupdate',()=>{if(v.currentTime>=end){v.pause();v.currentTime=start;}});
}
</script></body></html>"""


def run(args: argparse.Namespace) -> dict:
    ws = args.workspace.expanduser().resolve(strict=True)
    campaign = _inside_workspace(ws, args.campaign_dir)
    packet = _inside_workspace(ws, args.packet_dir)
    audit = _inside_workspace(ws, campaign/"audit/campaign-audit.json")
    scan = _inside_workspace(ws, campaign/"coarse-scan")
    status = campaign_progress(ws, audit, scan_dir=scan, packet_dir=packet)
    if status["goNoGo"]["coarseScannedEveryCatalogSource"] is not True:
        raise ValueError("Archive campaign is not fully SHA-qualified")
    if (status["reviewPacket"]["matchesCurrentScanCheckpoint"] is not True
            or status["reviewPacket"]["verifiedPlaybackClipsReported"] < 1):
        raise ValueError("Packet is incomplete/stale or has zero verified previews")
    if args.mode == "reviewed" and args.reviews is None:
        raise ValueError("Reviewed mode requires an actual blind-review export")
    reviews = _inside_workspace(ws, args.reviews) if args.reviews else None
    rows, checks = _packet_candidates(ws, packet, status, reviews)
    if checks["invalidProxyReceipts"] > 0:
        raise ValueError("Corrupted packet proxies detected")
    annotations = None
    if args.annotations:
        p = _inside_workspace(ws, args.annotations)
        annotations, _ = _json(p, 2_000_000)
    plan = build_plan(rows, annotations, checks["manifestSha256"],
                      mode=args.mode, seconds=args.seconds,
                      max_clips=args.max_clips, per_source=args.per_source)
    dest = _safe_dest(ws, args.output_dir)
    if dest.exists():
        raise ValueError("Output folder already exists; choose a new versioned run directory")
    dest.mkdir(parents=True, mode=0o700)
    (dest/"media").mkdir(mode=0o700)
    # Link ALL source-verified packet proxies to enable labeling even clips
    # the current sampler did not select into a reel. No video bytes are copied.
    by_id = {x["recordId"]: x for x in rows}
    for rid in sorted(by_id):
        row = by_id[rid]
        src = Path(row["mediaPath"])
        if src.is_symlink() or _file_sha(src) != row["proxySha256"]:
            raise ValueError("Proxy changed during gallery preparation")
        target = dest/"media"/(rid + ".mp4")
        os.link(src, target, follow_symlinks=False)
        if _file_sha(target) != row["proxySha256"]:
            raise ValueError("Staged preview hash mismatch")
    _atomic_json(dest/"moment-plan.json", plan)
    _atomic_json(dest/"annotation-template.json",
                 annotation_template(rows, checks["manifestSha256"]))
    (dest/"review.html").write_text(_gallery(plan), encoding="utf-8")
    (dest/"label.html").write_text(
        render_label_studio(rows, checks["manifestSha256"]), encoding="utf-8")
    render_status = None
    if args.render:
        render_status = _render(plan, by_id, dest/"renders")
        _atomic_json(dest/"render-report.json", render_status)
    summary = {
        "version": VERSION, "mode": args.mode, "outputDir": str(dest),
        "reviewPage": str(dest/"review.html"),
        "ownerLabelStudio": str(dest/"label.html"),
        "ownerAnnotationTemplate": str(dest/"annotation-template.json"),
        "momentPlan": str(dest/"moment-plan.json"),
        "catalogSourceCount": status["catalogAssetCount"],
        "verifiedCandidateRows": len(rows),
        "momentCount": len(plan["moments"]),
        "ownerAnnotated": plan["ownerAnnotatedMomentCount"],
        "collections": {k: v["selectedCount"] for k,v in plan["collections"].items()},
        "render": render_status["status"] if render_status else "not_requested",
        "originalMediaRead": False, "publicationApproved": False,
    }
    _atomic_json(dest/"summary.json", summary)
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workspace", required=True, type=Path)
    ap.add_argument("--campaign-dir", required=True, type=Path)
    ap.add_argument("--packet-dir", required=True, type=Path)
    ap.add_argument("--output-dir", required=True, type=Path)
    ap.add_argument("--annotations", type=Path, help="Private owner-tagged source-time JSON")
    ap.add_argument("--reviews", type=Path, help="Existing validated blind-review export")
    ap.add_argument("--mode", choices=("exploratory", "reviewed"), default="exploratory")
    ap.add_argument("--seconds", type=int, default=150)
    ap.add_argument("--max-clips", type=int, default=8)
    ap.add_argument("--per-source", type=int, default=2)
    ap.add_argument("--render", action="store_true", help="Render silent proxy-only MP4s")
    args = ap.parse_args()
    try:
        print(json.dumps(run(args), indent=2, sort_keys=True))
        return 0
    except (ValueError, TypeError, KeyError, OSError) as exc:
        print(f"Klip V2 blocked safely: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
