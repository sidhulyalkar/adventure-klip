"""Klip V2 proxy Auto-Scout: seven-frame hypotheses and objective visual diagnostics.

Reads ONLY checksum-verified H.264 proxies from an existing private V2 pilot.
Never reads DJI originals, mutates catalog, trains, trims, deletes, publishes,
or treats a model-suggested category / quality issue as ground truth.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
from html import escape
import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess
import tempfile

from . import adaptive_judge_v2 as ollama
from . import temporal_second_look as temporal
from . import studio_preview_judge as local
from .media import require_tool, probe
from .moment_edit_v2 import TAGS, DEFECTS, SHA, ID, VERSION as MOMENT_VERSION

VERSION = "klip-v2-proxy-autoscout-experiment-v1"
ACTIVITIES = ("ski", "snorkeling", "bike", "other", "unknown")
PROPOSAL_TAGS = sorted(set().union(*TAGS.values()))
QUALITY_TAGS = sorted(DEFECTS)
PRIORITIES = ("review_first", "investigate", "routine")
POLICY = (
    "You are a conservative, source-grounded adventure video discovery assistant. "
    "You see exactly SEVEN independent stills in chronological order from one "
    "short local proxy. You DID NOT view intervening frames or video motion, "
    "listen to audio, see neighboring footage, or observe any reliable speed or GPS. "
    "Identify broad ski, snorkeling, bike, other or unknown activity only where "
    "supported by visible frame evidence. Suggest MULTIPLE category tags only "
    "when their visual evidence is directly present. Ski park boxes and rails "
    "refer to visible FEATURES, not a completed trick, and ski_speed refers to "
    "candidate perceptual flow, NOT measured velocity. A jump feature does not "
    "prove takeoff or landing. Trees must be a recognizable forest ski line, not "
    "just trees in the distance. Marine_ray is a possible ray-like shape, "
    "NOT verified species. Never infer rare animals, species, destinations, "
    "athlete skill, trick names, success, natural interaction, personal meaning, "
    "or action that happens between sampled stills. Underwater poor clarity "
    "does not mean uninteresting; protect uncertain animals for review. "
    "Filming quality proposals must identify observable frames. Shake, speed "
    "and blur from motion cannot reliably be inferred from seven stills: "
    "report only visible clues and use uncertainty if ambiguous. Do not "
    "suggest edit cut points or recommend deleting content. Prioritize rare-looking "
    "encounters, distinctive terrain, and unclear interesting scenes for human "
    "investigation, not as automatically approved highlights. "
    "Give short independent observations grounded in indices 0..6. "
    "Return strict JSON matching the provided schema."
)


def json_schema() -> dict:
    proposal = {
        "type": "object", "additionalProperties": False,
        "required": ["tag", "frames", "observation"],
        "properties": {
            "tag": {"type": "string", "enum": PROPOSAL_TAGS},
            "frames": {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 6},
                       "minItems": 1, "maxItems": 7},
            "observation": {"type": "string", "minLength": 3, "maxLength": 180},
        },
    }
    defect = {
        "type": "object", "additionalProperties": False,
        "required": ["kind", "frames", "observation"],
        "properties": {
            "kind": {"type": "string", "enum": QUALITY_TAGS},
            "frames": {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 6},
                       "minItems": 1, "maxItems": 7},
            "observation": {"type": "string", "minLength": 3, "maxLength": 180},
        },
    }
    properties = {
        "activity": {"type": "string", "enum": list(ACTIVITIES)},
        "activityFrames": {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 6},
                           "maxItems": 7},
        "categories": {"type": "array", "items": proposal, "maxItems": 6},
        "qualityFlags": {"type": "array", "items": defect, "maxItems": 8},
        "eventContinuity": {"type": "string", "enum": ["unresolved", "visually_plausible", "not_observed"]},
        "reviewPriority": {"type": "string", "enum": list(PRIORITIES)},
        "summary": {"type": "string", "minLength": 4, "maxLength": 240},
        "uncertainty": {"type": "string", "minLength": 4, "maxLength": 240},
    }
    return {"type": "object", "additionalProperties": False,
            "required": list(properties), "properties": properties}


def fingerprint(model: str) -> str:
    return sha256(json.dumps({
        "version": VERSION, "model": model, "policy": POLICY,
        "schema": json_schema(), "fractions": temporal.SAMPLE_FRACTIONS,
        "temperature": 0,
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_proposal(raw: dict) -> dict:
    schema = json_schema()
    if not isinstance(raw, dict) or set(raw) != set(schema["required"]):
        raise ValueError("Model returned unexpected/missing auto-scout fields")
    activity = raw["activity"]
    if activity not in ACTIVITIES or raw["reviewPriority"] not in PRIORITIES:
        raise ValueError("Model activity/priority is not allowed")
    if raw["eventContinuity"] not in ("unresolved", "visually_plausible", "not_observed"):
        raise ValueError("Invalid continuity proposal")
    for field, bound in (("summary", 240), ("uncertainty", 240)):
        if not isinstance(raw[field], str) or not 4 <= len(raw[field]) <= bound:
            raise ValueError("Invalid scout summary/uncertainty")
    def indices(indices_obj: object, *, allow_empty: bool = False) -> None:
        if (not isinstance(indices_obj, list) or
                not (0 if allow_empty else 1) <= len(indices_obj) <= 7 or
                any(type(v) is not int or not 0 <= v < 7 for v in indices_obj) or
                len(set(indices_obj)) != len(indices_obj)):
            raise ValueError("Invalid or duplicated sampled-frame evidence")
    indices(raw["activityFrames"], allow_empty=activity in ("other", "unknown"))
    if not isinstance(raw["categories"], list) or len(raw["categories"]) > 6:
        raise ValueError("Invalid category proposal list")
    if not isinstance(raw["qualityFlags"], list) or len(raw["qualityFlags"]) > 8:
        raise ValueError("Invalid quality proposal list")
    normalized = []
    seen = set()
    for item in raw["categories"]:
        if not isinstance(item, dict) or set(item) != {"tag", "frames", "observation"}:
            raise ValueError("Invalid category proposal fields")
        tag = item["tag"]
        if (not isinstance(tag, str) or tag not in TAGS.get(activity, ()) or tag in seen):
            raise ValueError("Invalid, duplicated or cross-sport category proposal")
        seen.add(tag)
        indices(item["frames"])
        if not isinstance(item["observation"], str) or not 3 <= len(item["observation"]) <= 180:
            raise ValueError("Invalid category evidence text")
        normalized.append(item)
    flagged, seen_defects = [], set()
    for item in raw["qualityFlags"]:
        if not isinstance(item, dict) or set(item) != {"kind", "frames", "observation"}:
            raise ValueError("Invalid quality observation fields")
        kind = item["kind"]
        if kind not in DEFECTS or kind in seen_defects:
            raise ValueError("Invalid or duplicated quality flag")
        seen_defects.add(kind)
        indices(item["frames"])
        if not isinstance(item["observation"], str) or not 3 <= len(item["observation"]) <= 180:
            raise ValueError("Invalid quality observation text")
        flagged.append(item)
    return {
        "activity": activity, "activityFrames": raw["activityFrames"],
        "categories": sorted(normalized, key=lambda row: row["tag"]),
        "qualityFlags": sorted(flagged, key=lambda row: row["kind"]),
        "eventContinuity": raw["eventContinuity"],
        "reviewPriority": raw["reviewPriority"],
        "summary": raw["summary"], "uncertainty": raw["uncertainty"],
        "observationKind": "seven_sampled_stills_not_verified_events",
        "humanApprovalRequired": True, "automaticEditAuthorized": False,
        "speciesVerified": False, "trickOrLandingVerified": False,
    }


def _bounded_workspace_path(ws: Path, path: Path, *, existing: bool) -> Path:
    root = ws.expanduser().resolve(strict=True)
    raw = Path(os.path.abspath(os.path.expanduser(str(path))))
    if (not raw.is_relative_to(root) or raw == root or
            raw.relative_to(root).parts[0] != "reports"):
        raise ValueError("Auto-scout inputs/outputs must be in private workspace reports/")
    cursor = raw
    while cursor != root:
        if cursor.is_symlink():
            raise ValueError("Symlinked private report refused")
        cursor = cursor.parent
    if existing and (not raw.exists() or not raw.is_dir()):
        raise ValueError("Existing private report directory is required")
    return raw


def load_pilot(ws: Path, pilot: Path) -> tuple[list[dict], str]:
    """Validate plan and source-backed proxy identity before inference."""
    pilot = _bounded_workspace_path(ws, pilot, existing=True)
    plan = pilot / "moment-plan.json"
    if (plan.is_symlink() or not plan.is_file()
            or not 20 <= plan.stat().st_size <= 15_000_000):
        raise ValueError("Missing or unsafe frozen Klip V2 moment plan")
    body = plan.read_bytes()
    document = json.loads(body)
    if (document.get("version") != MOMENT_VERSION
            or not SHA.fullmatch(str(document.get("manifestSha256")))
            or not isinstance(document.get("moments"), list)
            or not 1 <= len(document["moments"]) <= 1000):
        raise ValueError("V2 moment plan is invalid")
    seen = set()
    rows = []
    for item in document["moments"]:
        if not isinstance(item, dict):
            raise ValueError("Invalid V2 moment row")
        rid, source, proxy_sha = (
            item.get("momentId"), item.get("sourceAssetSha256"),
            item.get("sourceProxySha256"))
        a, b = item.get("sourceStartMs"), item.get("sourceEndMs")
        if (not ID.fullmatch(str(rid)) or rid in seen
                or not SHA.fullmatch(str(source)) or not SHA.fullmatch(str(proxy_sha))
                or type(a) is not int or type(b) is not int
                or not 0 <= a < b <= 86_400_000
                or item.get("sportHint") not in (*TAGS, "unknown")):
            raise ValueError("Unqualified V2 moment identity")
        seen.add(rid)
        video = pilot / "media" / (rid + ".mp4")
        if (video.is_symlink() or not video.is_file()
                or not 100 <= video.stat().st_size <= 45*1024*1024):
            raise ValueError("Missing or unsafe frozen proxy, cannot scout " + rid)
        # Re-hash every input proxy; a filename and prior receipt are insufficient.
        hashed = sha256(video.read_bytes()).hexdigest()
        if hashed != proxy_sha:
            raise ValueError("Proxy SHA mismatch, aborting source verification")
        rows.append({
            "recordId": rid, "sourceSha256": source,
            "proxySha256": proxy_sha, "sourceStartMs": a, "sourceEndMs": b,
            "sportHint": item["sportHint"],
            "selectionRole": item.get("selectionRole", "unknown"),
            "video": video,
        })
    return rows, sha256(body).hexdigest()


def diversify(rows: list[dict]) -> list[dict]:
    """Round-robin sources to avoid monopolization by a single recording."""
    groups = defaultdict(list)
    for item in rows:
        groups[item["sourceSha256"]].append(item)
    for source in groups:
        groups[source].sort(key=lambda r: (r["sourceStartMs"], r["recordId"]))
    order = []
    while any(groups.values()):
        for source in sorted(groups):
            if groups[source]:
                order.append(groups[source].pop(0))
    return order


def quality_measurements(path: Path) -> dict:
    """Cheap numeric proxy measurements, not defect classification or speed."""
    meta = probe(path)
    if not 1 <= float(meta["duration"]) <= 61:
        raise ValueError("Unbounded preview duration")
    ffmpeg = require_tool("ffmpeg")
    width, height, fps = 64, 36, 2
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
         "-i", str(path), "-vf", f"fps={fps},scale={width}:{height},format=gray",
         "-an", "-sn", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=65,
        check=False)
    data = proc.stdout
    frame_bytes = width * height
    if (proc.returncode != 0 or len(data) < 2*frame_bytes or
            len(data) % frame_bytes != 0 or len(data) > 130*frame_bytes):
        raise ValueError("Bounded preview motion/brightness decode failed")
    frames = [memoryview(data)[i:i+frame_bytes]
              for i in range(0, len(data), frame_bytes)]
    luma = [sum(frame)/(frame_bytes*255) for frame in frames]
    deltas = [
        sum(abs(a-b) for a,b in zip(prev,now))/(frame_bytes*255)
        for prev,now in zip(frames,frames[1:])
    ]
    return {
        "sampledFrames": len(frames), "approximateSamplingFps": fps,
        "meanBrightness": round(statistics.mean(luma), 5),
        "darkFrameFraction": round(sum(v < .12 for v in luma)/len(luma), 4),
        "brightFrameFraction": round(sum(v > .88 for v in luma)/len(luma), 4),
        "meanInterFramePixelDifference": round(statistics.mean(deltas), 5),
        "maxInterFramePixelDifference": round(max(deltas), 5),
        "interpretation": "grayscale proxy signal only; camera shake, subject motion, cuts and actual travel speed are not separated",
    }


def infer_proxy(path: Path, *, model: str, transport=None) -> dict:
    """One Ollama request using seven sampled stills; no original media."""
    if not local.valid_local_model(model):
        raise ValueError("Invalid local Ollama model name")
    images, stamps = temporal._samples(path)
    if len(images) != 7 or len(stamps) != 7:
        raise ValueError("Seven independent still frames are required")
    payload = {
        "model": model, "stream": False, "format": json_schema(),
        "options": {"temperature": 0},
        "messages": [{"role": "user", "content": POLICY, "images": images}],
    }
    response = ollama._send_local(payload, transport=transport)
    try:
        parsed = json.loads(response["message"]["content"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Local model produced invalid structured JSON") from exc
    prediction = validate_proposal(parsed)
    return {
        "proposal": prediction, "sampledProxyTimeMs": stamps,
        "model": model, "policyFingerprint": fingerprint(model),
        "backend": "ollama_loopback_only",
        "modelTagMayChange": True,
    }


def _safe_preview_link(directory: Path, row: dict) -> Path:
    """Stage only a hard link to an existing verified proxy, never copy source."""
    source = row["video"]
    target_dir = directory / "media"
    target_dir.mkdir(mode=0o700, exist_ok=True)
    target = target_dir / (row["recordId"] + ".mp4")
    if target.is_symlink():
        raise ValueError("Symlink in output preview path")
    if not target.exists():
        os.link(source, target, follow_symlinks=False)
    if (not target.is_file() or
            sha256(target.read_bytes()).hexdigest() != row["proxySha256"]):
        raise ValueError("Staged preview does not match verified input")
    return target


def save_json(path: Path, document: dict) -> None:
    if path.is_symlink():
        raise ValueError("Unsafe output symlink")
    with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=".klip-scout-", suffix=".partial", delete=False) as file:
        temporary = Path(file.name)
        try:
            json.dump(document, file, indent=2, sort_keys=True, allow_nan=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_receipts(folder: Path, rows: list[dict], plan_sha: str,
                  policy_sha: str) -> dict[str, dict]:
    known = {row["recordId"]: row for row in rows}
    result = {}
    receipts = folder / "receipts"
    if not receipts.exists():
        return result
    for path in sorted(receipts.glob("*.json")):
        if path.is_symlink():
            raise ValueError("Symlinked receipt refused")
        rid = path.stem
        if rid not in known or path.stat().st_size > 24_000:
            raise ValueError("Unrecognized or oversized scout receipt")
        rec = json.loads(path.read_text())
        row = known[rid]
        if (rec.get("version") != VERSION or rec.get("recordId") != rid
                or rec.get("inputPlanSha256") != plan_sha
                or rec.get("policyFingerprint") != policy_sha
                or rec.get("proxySha256") != row["proxySha256"]
                or rec.get("sourceSha256") != row["sourceSha256"]
                or rec.get("status") not in ("done", "failed")):
            raise ValueError("Scout receipt differs from frozen input/policy")
        if rec["status"] == "done":
            if (not isinstance(rec.get("analysis"), dict) or
                    not isinstance(rec["analysis"].get("proposal"), dict)):
                raise ValueError("Scout receipt missing model output")
            validate_proposal({k: rec["analysis"]["proposal"][k]
                               for k in json_schema()["required"]})
        result[rid] = rec
    return result


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
