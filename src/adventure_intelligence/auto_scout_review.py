"""Pure Auto-Scout review summaries and local HTML gallery rendering.

No video decoding, model calls or catalog access. Keep this separate from
checksum verification and the running checkpointed experiment service.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from html import escape
from pathlib import Path

VERSION = "klip-v2-proxy-autoscout-experiment-v1"
PRIORITIES = ("review_first", "investigate", "routine")

def _group_queue(records: list[dict]) -> list[dict]:
    # Source diversity first within each priority tier. Keep controls visible.
    tiers = {k: i for i,k in enumerate(PRIORITIES)}
    bands = defaultdict(list)
    for r in records:
        if r["status"] != "done":
            continue
        proposal = r["analysis"]["proposal"]
        bands[proposal["reviewPriority"]].append(r)
    ordered = []
    for priority in PRIORITIES:
        remaining = bands[priority][:]
        used_sources = set()
        while remaining:
            choice = min(remaining, key=lambda r: (
                r["sourceSha256"] in used_sources,
                r["analysis"]["proposal"]["activity"] not in ("ski","snorkeling"),
                len(r["analysis"]["proposal"]["categories"]) == 0,
                r["recordId"],
            ))
            ordered.append(choice)
            used_sources.add(choice["sourceSha256"])
            remaining.remove(choice)
    return ordered


def build_report(receipts: dict[str, dict], *, total: int, processed_limit: int,
                 source_plan_sha: str, model: str, pilot: Path) -> dict:
    done = [r for r in receipts.values() if r["status"] == "done"]
    failed = [r for r in receipts.values() if r["status"] == "failed"]
    ordered = _group_queue(done)
    activity = Counter(x["analysis"]["proposal"]["activity"] for x in done)
    categories = Counter(tag["tag"] for x in done
                         for tag in x["analysis"]["proposal"]["categories"])
    priorities = Counter(x["analysis"]["proposal"]["reviewPriority"] for x in done)
    defects = Counter(flag["kind"] for x in done
                      for flag in x["analysis"]["proposal"]["qualityFlags"])
    failure_stages = Counter(x.get("failureStage", "unknown_legacy")
                             for x in failed)
    failure_codes = Counter(x.get("failureCode", "unknown_legacy")
                            for x in failed)
    return {
        "version": VERSION, "inputPlanSha256": source_plan_sha,
        "sourcePilot": str(pilot), "model": model,
        "totalCandidates": total, "requestedLimit": processed_limit,
        "processed": len(done), "failed": len(failed),
        "remaining": max(0, total-len(done)-len(failed)),
        "distinctAnalyzedSources": len({x["sourceSha256"] for x in done}),
        "activityProposals": dict(sorted(activity.items())),
        "categoryProposals": dict(sorted(categories.items())),
        "qualityFlagProposals": dict(sorted(defects.items())),
        "failureStages": dict(sorted(failure_stages.items())),
        "failureCodes": dict(sorted(failure_codes.items())),
        "reviewPriorities": dict(sorted(priorities.items())),
        "reviewQueue": [{
            "recordId": rec["recordId"],
            "sourceSha256": rec["sourceSha256"],
            "sourceStartMs": rec["sourceStartMs"],
            "sourceEndMs": rec["sourceEndMs"],
            "activityProposal": rec["analysis"]["proposal"]["activity"],
            "categoryProposals": [x["tag"] for x in rec["analysis"]["proposal"]["categories"]],
            "qualityFlagProposals": [x["kind"] for x in rec["analysis"]["proposal"]["qualityFlags"]],
            "reviewPriority": rec["analysis"]["proposal"]["reviewPriority"],
            "summary": rec["analysis"]["proposal"]["summary"],
            "uncertainty": rec["analysis"]["proposal"]["uncertainty"],
            "qualityMeasurements": rec["analysis"]["numericQuality"],
        } for rec in ordered],
        "failedRecordIds": [rec["recordId"] for rec in failed],
        "modelProposalsAreNotOwnerVerified": True,
        "originalVideosRead": 0, "originalVideosModified": 0,
        "autoEditBoundariesChosen": 0, "masterRendersCreated": 0,
        "note": "Seven stills plus grayscale proxy metrics. This is a candidate review ordering, not an event-detection or reel-ranking accuracy benchmark.",
    }


def review_html(report: dict) -> str:
    """Self-contained source-verified local proxy review deck (no network)."""
    fragments = []
    for i, clip in enumerate(report["reviewQueue"], 1):
        rid = clip["recordId"]
        hint = clip["activityProposal"]
        tags = ", ".join(clip["categoryProposals"]) or "No specific category proposed"
        defects = ", ".join(clip["qualityFlagProposals"]) or "No visible defect proposed"
        fragments.append(
            "<article><div class='meta'>"
            + escape(str(i)) + " · " + escape(hint) + " · "
            + escape(clip["reviewPriority"].replace("_", " "))
            + "</div><video preload='metadata' playsinline controls src='media/"
            + rid + ".mp4'></video><h3>" + escape(tags.replace("_"," "))
            + "</h3><p>" + escape(clip["summary"])
            + "</p><div class='meta'>"
            + escape(clip["sourceSha256"][:12]) + " · "
            + escape(str(round(clip["sourceStartMs"]/1000, 2))) + "–"
            + escape(str(round(clip["sourceEndMs"]/1000, 2)))
            + "s</div><p><strong>Possible defects:</strong> " + escape(defects)
            + "</p><p class='muted'><strong>Uncertainty:</strong> "
            + escape(clip["uncertainty"]) + "</p></article>"
        )
    return """<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; media-src 'self' file:; img-src 'none'; script-src 'none'; connect-src 'none'; object-src 'none'; base-uri 'none'">
<title>Klip / V2 Auto-Scout Review</title>
<style>
:root{color-scheme:dark;background:#0c141b;color:#e7f2f3;font:14px/1.55 system-ui}
body{max-width:1220px;margin:0 auto;padding:28px}
h1{font-size:23px}h3{font-size:15px;margin:10px 0 4px}
small,.meta,.muted{color:#98afbc}.meta{font-size:12px;margin:6px 0}
.header{margin-bottom:25px;border-bottom:1px solid #42535f;padding-bottom:18px}
.note{background:#20333b;border-left:3px solid #8ec9bd;padding:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:16px}
article{border:1px solid #354755;background:#18232b;border-radius:12px;padding:12px}
video{width:100%;aspect-ratio:16/9;object-fit:contain;background:black;border-radius:8px}
p{margin:8px 0;font-size:13px}
</style></head><body><header class="header"><h1>Klip / Auto-Scout</h1>
<p class="note">All classifications are unverified model suggestions from seven separated stills.
Camera motion and grayscale changes are NOT speed, skill or reliable blur detection.
Do not skip unseen events or export final edits from these guesses.</p>
<p><strong>""" + escape(str(report["processed"])) + """</strong> inspected proxies from <strong>""" + escape(str(report["totalCandidates"])) + """</strong> packet candidates.</p>
</header><main class="grid">""" + "\n".join(fragments) + "</main></body></html>"
