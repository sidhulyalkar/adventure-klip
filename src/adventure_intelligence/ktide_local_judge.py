"""Local multimodal *proposal* judge for the K-TIDE source-assignment smoke.

Explicit opt-in read+SHA verification of selected existing video originals;
bounded FFmpeg snapshots; Ollama only via local loopback. The judge cannot
declare trip identity, verified event phases/species, training truth, or edit
permissions. No archive catalog updates.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import base64
import json
from pathlib import Path
import re
import sqlite3
from urllib import request

from .encoder_dataset import KEY, SHA
from .ktide_planner import ASSIGNMENTS_VERSION
from .scene_profiles import SUBSPORT_PRIORS, DIMENSIONS
from .scene_relevance import GOAL_WEIGHTS

VERSION = "klip-ktide-local-vision-judge-v1"
PREFIX = "provisional_"
MODEL = "gemma3:4b"
LOCAL_URL = "http://127.0.0.1:11434"
MAX_SOURCES = 32
MAX_FRAMES = 8
MAX_RESPONSE_BYTES = 150_000
GOAL_FOR = {
    "snorkeling_reef": "nature_encounter",
    "snorkeling_ray_shark": "nature_encounter",
    "snorkeling_fish_school": "nature_encounter",
    "snorkeling_shallow_coral": "nature_encounter",
    "kayaking_scenic": "scenic_transition",
    "kayaking_family": "personal_memory",
    "general_adventure": "scenic_transition",
    "skiing_crash_analysis": "technique_analysis",
}
PROMPT = """You are examining sparse ORDERED video stills, NOT watching the whole
video. Output one strict JSON object only with exactly these keys:
subsport, sceneSummary, sceneContext, tags, frameObservations, eventHints,
featureCandidates, uncertainties, cameraQuality, confidence.
Choose subsport from the allowed exact names below or general_adventure if
uncertain. Do NOT guess a location, animal species, personal identity, physical
speed, event completion, crash cause, or entire trip from sparse stills.
Do NOT infer hidden content between sampled frames. Distinguish seeing an
animal-like silhouette from knowing its species. Event hints must refer
exclusively to the numbered stills actually provided.
frameObservations: array of {frameIndex: integer, observation: string}
eventHints: array of {kind: string, firstFrameIndex: integer,
lastFrameIndex: integer}. The indices must be 0..N-1 with last>=first.
tags: <=10 short visible visual elements. uncertainties: <=6 short strings.
sceneContext: one of snow_mountain, underwater_reef, underwater_open_water,
forest_trail, coastal_outdoor, general_outdoor, indoor, unknown.
featureCandidates: <=8 objects with {dimension, frameIndices, observation}.
Each dimension MUST be one of the exact names listed below and ONLY refer
to directly visible cues in sampled frames; DO NOT supply scores or weights.
Leave unsupported dimensions unmentioned. Mention uncertainty whenever needed.
Supported dimensions: {dimensions}
cameraQuality: one of clear, mixed, obscured, unknown.
confidence: 0..1, only subjective uncalibrated model certainty.
Keep sceneSummary <=300 characters. No marketing or inferred narrative.
Allowed subsports: {allowed}
Sample order and requested source-relative seconds: {times}
"""


def capture_dt(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def provisional_capture_groups(entries: list[dict], *, gap_days: int = 14) -> dict[str, str]:
    """Conservatively COLLAPSE adjacent capture periods, never certify trips.

    Unknown timestamps all share a single quarantine group. This can still
    accidentally split one real trip; never use for held-out science.
    """
    if not 2 <= gap_days <= 60:
        raise ValueError("Invalid conservative capture grouping horizon")
    dated = []
    group = {}
    for row in entries:
        aid = row.get("sourceSha256")
        if not isinstance(aid, str) or not SHA.fullmatch(aid) or aid in group:
            raise ValueError("Invalid or duplicate inventory source SHA")
        date = capture_dt(row.get("captureTimeMetadata"))
        if date is None:
            group[aid] = PREFIX + "unknown-capture"
        else:
            dated.append((date, aid))
            group[aid] = ""
    dated.sort()
    anchor = prev = None
    label = None
    for date, aid in dated:
        if prev is None or date - prev > timedelta(days=gap_days):
            anchor = date
            label = PREFIX + "capture-" + anchor.strftime("%Y%m%d") + "-" + sha256(
                anchor.isoformat().encode()).hexdigest()[:8]
        group[aid] = label
        prev = date
    return group


def select_judge_pilot(inventory: dict, *, limit: int = 8,
                       grouping_days: int = 14) -> dict:
    if (not isinstance(inventory, dict)
            or inventory.get("version") != "klip-ktide-feature-inventory-v1"
            or inventory.get("provider") not in {"histogram", "siglip2"}
            or not isinstance(inventory.get("entries"), list)
            or type(limit) is not int or not 1 <= limit <= MAX_SOURCES):
        raise ValueError("Expected bounded completed-feature inventory JSON")
    entries = [r for r in inventory["entries"]
               if isinstance(r, dict) and r.get("likelyWindowReady") is True]
    # Only use actual available, previously receipt-checked caches.
    all_groups = provisional_capture_groups(entries, gap_days=grouping_days)
    buckets = defaultdict(list)
    for row in entries:
        buckets[all_groups[row["sourceSha256"]]].append(row)
    for rows in buckets.values():
        rows.sort(key=lambda r: (-r["usableCachedSamples"], r["sourceSha256"]))
    chosen = []
    for index in range(max(map(len,buckets.values()), default=0)):
        for group in sorted(buckets):
            if index < len(buckets[group]) and len(chosen) < limit:
                row = buckets[group][index]
                chosen.append({
                    "sourceSha256": row["sourceSha256"],
                    "captureTimeMetadata": row.get("captureTimeMetadata"),
                    "durationMs": row.get("durationMs"),
                    "availableCachedFrames": row["usableCachedSamples"],
                    "groupId": all_groups[row["sourceSha256"]],
                })
    return {
        "version": "klip-ktide-judge-plan-v1",
        "provider": inventory["provider"],
        "selected": chosen,
        "provisionalCaptureGroups": len(set(x["groupId"] for x in chosen)),
        "groupIndependenceVerified": False,
        "rawMediaRead": False,
        "modelCalled": False,
    }


def requested_stills(duration_ms: int, count: int) -> list[int]:
    if (type(duration_ms) is not int or not 1000 <= duration_ms <= 86_400_000
            or type(count) is not int or not 2 <= count <= MAX_FRAMES):
        raise ValueError("Invalid video duration or still count")
    return [round(duration_ms * (i+1)/(count+1)) for i in range(count)]


def verified_video_source(root: Path, aid: str) -> Path:
    """Read from the catalog but never edit it, verify original SHA-256."""
    from .sources import verified_digest
    dbfile = root / "catalog.sqlite3"
    if dbfile.is_symlink() or not dbfile.is_file():
        raise ValueError("Missing local archive catalog")
    with sqlite3.connect(dbfile.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        source = db.execute("SELECT stored_path FROM assets WHERE id=?", (aid,)).fetchone()
        if source is None:
            raise ValueError("Selected SHA is absent from the source catalog")
        candidates = [source["stored_path"]] + [
            row["original_path"] for row in db.execute(
                "SELECT original_path FROM imports WHERE asset_id=?", (aid,))]
    for raw in dict.fromkeys(candidates):
        if not isinstance(raw, str):
            continue
        path = Path(raw)
        if not path.is_file() or path.is_symlink():
            continue
        try:
            if verified_digest(path) == aid:
                return path
        except (OSError, ValueError):
            continue
    raise ValueError("No original file matches selected SHA; original not modified")


def sample_source_frames(video: Path, duration_ms: int, count: int) -> list[dict]:
    from .frame_timing import extract_frame_png
    frames = []
    before = video.stat()
    for i, timestamp in enumerate(requested_stills(duration_ms, count)):
        image_bytes, pts = extract_frame_png(video, timestamp, width=384)
        if not image_bytes.startswith(b"\x89PNG\r\n\x1a\n") or len(image_bytes)>3_000_000:
            raise ValueError("Invalid or excessive decoded still image")
        frames.append({
            "frameIndex": i,
            "requestedMs": timestamp,
            "decodedPtsMs": pts.get("decodedPtsMs"),
            "timestampBasis": pts.get("timestampBasis"),
            "imageSha256": sha256(image_bytes).hexdigest(),
            "imageBytes": image_bytes,
        })
    after = video.stat()
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, f) != getattr(after, f) for f in fields):
        raise ValueError("Original source changed during sparse-frame review")
    return frames


def local_ollama_json(frames: list[dict], *, model: str = MODEL,
                      endpoint: str = LOCAL_URL, timeout: int = 240) -> dict:
    """Endpoint is fixed to an explicit loopback Ollama API, never a cloud URL."""
    if endpoint not in {LOCAL_URL, "http://localhost:11434"}:
        raise ValueError("Only local loopback Ollama is permitted")
    if (not isinstance(model, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{1,100}", model)
            or type(timeout) is not int or not 10 <= timeout <= 1200):
        raise ValueError("Invalid local model or request timeout")
    if not 2 <= len(frames) <= MAX_FRAMES:
        raise ValueError("Need 2..8 sparsely sampled verified stills")
    from .ktide_judge_contract import VISUAL_DIMENSIONS, ollama_schema
    payload = {
        "model": model, "stream": False,
        "format": ollama_schema(len(frames)),
        "options": {"temperature": 0},
        "messages": [{
            "role": "user",
            "content": (PROMPT
                        .replace("{allowed}", ", ".join(sorted(SUBSPORT_PRIORS)))
                        .replace("{times}", str([round(x["requestedMs"]/1000,2)
                                                  for x in frames]))
                        .replace("{dimensions}", ", ".join(VISUAL_DIMENSIONS))),
            "images": [base64.b64encode(frame["imageBytes"]).decode("ascii")
                       for frame in frames],
        }],
    }
    req = request.Request(endpoint + "/api/chat",
                          data=json.dumps(payload).encode("utf-8"),
                          headers={"Content-Type":"application/json"},
                          method="POST")
    with request.urlopen(req, timeout=timeout) as response:
        raw = response.read(MAX_RESPONSE_BYTES+1)
    if len(raw)>MAX_RESPONSE_BYTES:
        raise ValueError("Local model response too large")
    result = json.loads(raw)
    message = result.get("message") if isinstance(result, dict) else None
    raw_answer = message.get("content") if isinstance(message, dict) else None
    if not isinstance(raw_answer,str) or len(raw_answer)>MAX_RESPONSE_BYTES:
        raise ValueError("Local judge returned no structured text")
    parsed = json.loads(raw_answer)
    # The orchestration layer validates once before storing any proposal.
    # Re-validating an already normalized result would reject its attached
    # evidenceSource/measuredValue markers.
    return parsed


def _short_string(value, max_length, *, allow_empty=False) -> str:
    if (not isinstance(value,str) or len(value)>max_length
            or (not allow_empty and not value.strip())):
        raise ValueError("Malformed local judge description")
    return value.strip()


def validate_judge_proposal(value: dict, frame_count: int) -> dict:
    required = {"subsport","sceneSummary","sceneContext","tags","frameObservations",
                "eventHints","featureCandidates","uncertainties",
                "cameraQuality","confidence"}
    if not isinstance(value,dict) or set(value)!=required:
        raise ValueError("Incorrect vision judge JSON schema")
    subsport=value["subsport"]
    if not isinstance(subsport,str) or subsport not in SUBSPORT_PRIORS:
        raise ValueError("Unsupported sub-sport suggestion")
    summary=_short_string(value["sceneSummary"],300)
    if not isinstance(value["sceneContext"], str) or value["sceneContext"] not in {
            "snow_mountain","underwater_reef","underwater_open_water",
            "forest_trail","coastal_outdoor","general_outdoor","indoor","unknown"}:
        raise ValueError("Unsupported scene context proposal")
    if (not isinstance(value["cameraQuality"], str)
            or value["cameraQuality"] not in {"clear","mixed","obscured","unknown"}):
        raise ValueError("Unknown camera quality judgment")
    certainty=value["confidence"]
    if (type(certainty) not in (float,int) or not 0<=certainty<=1):
        raise ValueError("Invalid model certainty")
    result={"subsport":subsport,"sceneSummary":summary,
            "sceneContext":value["sceneContext"],
            "cameraQuality":value["cameraQuality"],"confidence":float(certainty)}
    for key,maxitems,maxlen in (("tags",10,50),("uncertainties",6,150)):
        items=value[key]
        if not isinstance(items,list) or len(items)>maxitems:
            raise ValueError("Invalid scene tags/unknowns")
        result[key]=[_short_string(x,maxlen) for x in items]
    rows=value["frameObservations"]
    if not isinstance(rows,list) or len(rows)>MAX_FRAMES:
        raise ValueError("Invalid per-frame observations")
    result["frameObservations"]=[]
    for row in rows:
        if (not isinstance(row,dict)
                or set(row)!={"frameIndex","observation"}
                or type(row["frameIndex"]) is not int
                or not 0<=row["frameIndex"]<frame_count):
            raise ValueError("Observation references unknown sampled still")
        result["frameObservations"].append({
            "frameIndex":row["frameIndex"],
            "observation":_short_string(row["observation"],240)})
    features=value["featureCandidates"]
    if not isinstance(features,list) or len(features)>8:
        raise ValueError("Invalid scene feature candidate count")
    result["featureCandidates"]=[]
    for item in features:
        if (not isinstance(item,dict)
                or set(item)!={"dimension","frameIndices","observation"}
                or not isinstance(item["dimension"],str)
                or item["dimension"] not in DIMENSIONS):
            raise ValueError("Unsupported scene dimension proposal")
        indices=item["frameIndices"]
        if (not isinstance(indices,list) or not 1<=len(indices)<=frame_count
                or any(type(idx) is not int or not 0<=idx<frame_count
                       for idx in indices)
                or len(indices)!=len(set(indices))):
            raise ValueError("Feature cites invalid sampled frames")
        result["featureCandidates"].append({
            "dimension":item["dimension"],
            "frameIndices":indices,
            "observation":_short_string(item["observation"],240),
            "evidenceSource":"model_hypothesis",
            "measuredValue":None,
        })
    rows=value["eventHints"]
    if not isinstance(rows,list) or len(rows)>8:
        raise ValueError("Invalid event proposals")
    result["eventHints"]=[]
    for row in rows:
        if (not isinstance(row,dict) or set(row)!={
                "kind","firstFrameIndex","lastFrameIndex"}):
            raise ValueError("Invalid event hint")
        a,b=row["firstFrameIndex"],row["lastFrameIndex"]
        if type(a) is not int or type(b) is not int or not 0<=a<=b<frame_count:
            raise ValueError("Event outside observed frame indices")
        result["eventHints"].append({
            "kind":_short_string(row["kind"],90),
            "firstFrameIndex":a,"lastFrameIndex":b})
    return result


def merge_smoke_assignments(plan: dict, proposals: list[dict],
                            existing: dict | None = None) -> dict:
    """Existing nonprovisional owner overrides win, never rewritten in place.

    Provisional outputs are strictly exploratory and must not be used for a
    scientific held-out benchmark. The goal/subsport are model proposals.
    """
    by_id={}
    if existing is not None:
        if (not isinstance(existing,dict)
                or existing.get("version")!=ASSIGNMENTS_VERSION
                or not isinstance(existing.get("sources"),list)):
            raise ValueError("Invalid preexisting assignments schema")
        for row in existing["sources"]:
            if not isinstance(row,dict) or not isinstance(row.get("sourceSha256"),str):
                raise ValueError("Invalid preexisting source mapping")
            by_id[row["sourceSha256"]]=row
    judged={row["sourceSha256"]:row for row in proposals}
    output=[]
    for selected in plan["selected"]:
        aid=selected["sourceSha256"]
        candidate=judged.get(aid,{})
        suggestion=candidate.get("judgeProposal") or {}
        subsport=suggestion.get("subsport","general_adventure")
        goal=GOAL_FOR.get(subsport,"sport_action")
        old=by_id.get(aid,{})
        old_group=old.get("groupId")
        group=old_group if isinstance(old_group,str) and KEY.fullmatch(old_group) else selected["groupId"]
        old_subsport=old.get("subsport")
        if isinstance(old_subsport,str) and old_subsport in SUBSPORT_PRIORS and old_subsport!="general_adventure":
            subsport=old_subsport
            goal=GOAL_FOR.get(subsport,"sport_action")
        old_goal=old.get("goal")
        if isinstance(old_goal,str) and old_goal in GOAL_WEIGHTS and old_group and not old_group.startswith(PREFIX):
            goal=old_goal
        output.append({
            "sourceSha256":aid,"groupId":group,
            "goal":goal,"subsport":subsport})
    return {"version":ASSIGNMENTS_VERSION,"sources":output}



def _validated_previous_success(previous: dict, plan: dict, *,
                                model: str, frame_count: int) -> dict[str, dict]:
    """Re-use only successful receipts from the exact same immutable source plan."""
    if (not isinstance(previous, dict)
            or previous.get("version") != VERSION
            or previous.get("model") != model
            or previous.get("frameCountPerVideo") != frame_count
            or not isinstance(previous.get("proposals"), list)):
        raise ValueError("Previous judge run/model/frame parameters do not match")
    expected = {entry["sourceSha256"]: entry for entry in plan["selected"]}
    if len(expected) != len(plan["selected"]):
        raise ValueError("Duplicate source in current judge plan")
    by_id = {}
    for row in previous["proposals"]:
        if not isinstance(row, dict):
            raise ValueError("Malformed previous judge record")
        aid = row.get("sourceSha256")
        if (not isinstance(aid, str) or aid not in expected
                or aid in by_id or row.get("provisionalGroupId")
                != expected[aid]["groupId"]):
            raise ValueError("Prior judged source roster/group does not match current plan")
        by_id[aid] = row
    if set(by_id) != set(expected):
        raise ValueError("Prior judging roster incomplete or changed")
    reusable = {}
    for aid, entry in by_id.items():
        if entry.get("judgeStatus") != "model_hypothesis":
            continue
        if entry.get("sourceShaVerifiedForJudge") is not True:
            raise ValueError("Cannot reuse previously unverified source judgment")
        frames = entry.get("observedFrames")
        if (not isinstance(frames, list) or len(frames) != frame_count):
            raise ValueError("Incomplete previous frame receipts")
        for i, frame in enumerate(frames):
            if (not isinstance(frame, dict)
                    or frame.get("frameIndex") != i
                    or frame.get("requestedMs") != requested_stills(
                        expected[aid]["durationMs"], frame_count)[i]
                    or not isinstance(frame.get("imageSha256"), str)
                    or not SHA.fullmatch(frame["imageSha256"])):
                raise ValueError("Previous frame receipt provenance mismatch")
        proposal = entry.get("judgeProposal")
        if not isinstance(proposal, dict):
            raise ValueError("Cannot reuse malformed judgment")
        # Judge proposals are normalized on first write, with explicit unknown
        # numeric values. Reconstruct and validate the original strict schema.
        raw = dict(proposal)
        candidates = raw.get("featureCandidates")
        if not isinstance(candidates, list):
            raise ValueError("Invalid previous feature evidence")
        raw["featureCandidates"] = []
        for feature in candidates:
            if (not isinstance(feature, dict)
                    or feature.get("evidenceSource") != "model_hypothesis"
                    or "measuredValue" not in feature
                    or feature["measuredValue"] is not None):
                raise ValueError("Previous judge evidence must remain unverified")
            raw["featureCandidates"].append({
                key: feature[key]
                for key in ("dimension", "frameIndices", "observation")
                if key in feature
            })
        normalized = validate_judge_proposal(raw, frame_count)
        if normalized != proposal:
            raise ValueError("Previous normalized judge proposal mismatch")
        reusable[aid] = entry
    return reusable


def run_judge_pilot(workspace: Path, plan: dict, *, model: str = MODEL,
                    frame_count: int = 4, existing: dict | None = None,
                    judge=None, source_loader=None, frame_loader=None,
                    previous: dict | None = None) -> dict:
    if (not isinstance(plan,dict)
            or plan.get("version")!="klip-ktide-judge-plan-v1"
            or not isinstance(plan.get("selected"),list)
            or not 1<=len(plan["selected"])<=MAX_SOURCES
            or type(frame_count) is not int or not 2<=frame_count<=MAX_FRAMES):
        raise ValueError("Invalid bounded opt-in local judge plan")
    root=workspace.resolve(strict=True)
    judge = judge or (lambda frames:model_call(frames,model))
    source_loader = source_loader or verified_video_source
    frame_loader = frame_loader or sample_source_frames
    reusable = (_validated_previous_success(previous, plan, model=model,
                                           frame_count=frame_count)
                if previous is not None else {})
    judged=[]
    for selection in plan["selected"]:
        aid=selection["sourceSha256"]
        if aid in reusable:
            judged.append(reusable[aid])
            continue
        entry={
            "sourceSha256":aid,"provisionalGroupId":selection["groupId"],
            "sourceShaVerifiedForJudge":False,
            "judgeStatus":"not_run","judgeProposal":None,
            "observedFrames":[],
            "mediaDeleted":False,"catalogUpdated":False,
        }
        stage="source_verification"
        try:
            video=source_loader(root,aid)
            entry["sourceShaVerifiedForJudge"]=True
            stage="frame_decode"
            frames=frame_loader(video,selection["durationMs"],frame_count)
            entry["observedFrames"]=[{
                k:frame[k] for k in ("frameIndex","requestedMs",
                                    "decodedPtsMs","timestampBasis","imageSha256")}
                for frame in frames]
            # Never retain unvalidated model output in an assignment report.
            stage="local_model"
            proposal=judge(frames)
            stage="schema_validation"
            from .ktide_judge_contract import normalize_model_hypothesis
            normalized, validation_status, recovery_reasons = (
                normalize_model_hypothesis(proposal,len(frames)))
            entry["judgeProposal"]=normalized
            entry["judgeValidationStatus"]=validation_status
            entry["schemaRecoveryReasons"]=recovery_reasons
            entry["judgeStatus"]="model_hypothesis"
        except (OSError,ValueError,RuntimeError,TimeoutError,ConnectionError) as exc:
            # Fail independently; do not leak full source paths in reports.
            entry["judgeStatus"]="failed"
            entry["failureType"]=type(exc).__name__
            entry["failureStage"]=stage
            if stage == "schema_validation":
                from .ktide_judge_contract import reason_code
                entry["failureReasonCode"]=reason_code(exc)
        judged.append(entry)
    assignments=merge_smoke_assignments(plan,judged,existing)
    valid=[r["judgeProposal"] for r in judged
           if r["judgeStatus"]=="model_hypothesis"]
    from .ktide_judge_contract import VISUAL_DIMENSIONS
    summary={
        "legacyAcceptedJudgments":sum(
            "judgeValidationStatus" not in r
            for r in judged if r["judgeStatus"]=="model_hypothesis"),
        "legacyUnobservableDimensionHints": dict(sorted(Counter(
            x["dimension"] for p in valid for x in p["featureCandidates"]
            if x["dimension"] not in VISUAL_DIMENSIONS).items())),
        "strictlyValidatedJudgments":sum(
            r.get("judgeValidationStatus")=="strict"
            for r in judged if r["judgeStatus"]=="model_hypothesis"),
        "partiallyRecoveredJudgments":sum(
            r.get("judgeValidationStatus")=="partial"
            for r in judged if r["judgeStatus"]=="model_hypothesis"),
        "schemaRecoveryReasons":dict(sorted(Counter(
            reason for r in judged if r["judgeStatus"]=="model_hypothesis"
            for reason in r.get("schemaRecoveryReasons",[])).items())),
        "failureReasonCodes":dict(sorted(Counter(
            r.get("failureReasonCode","unknown")
            for r in judged if r.get("failureStage")=="schema_validation").items())),
        "failureStages":dict(sorted(Counter(
            r.get("failureStage","unknown")
            for r in judged if r["judgeStatus"]=="failed").items())),
        "failureTypes":dict(sorted(Counter(
            r.get("failureType","unknown")
            for r in judged if r["judgeStatus"]=="failed").items())),
        "sceneContexts":dict(sorted(Counter(p["sceneContext"] for p in valid).items())),
        "suggestedSubsports":dict(sorted(Counter(p["subsport"] for p in valid).items())),
        "suggestedFeatureDimensions":dict(sorted(Counter(
            x["dimension"] for p in valid for x in p["featureCandidates"]
            if x["dimension"] in VISUAL_DIMENSIONS).items())),
        "uncertainOrFailedSources":len(judged)-len(valid),
        "allStatisticsAreModelProposals":True,
    }
    return {
        "version":VERSION,"model":model,
        "frameCountPerVideo":frame_count,
        "sourceCount":len(judged),
        "successes":sum(x["judgeStatus"]=="model_hypothesis" for x in judged),
        "reusedSuccessfulJudgments":len(reusable),
        "freshlyJudgedSources":len(judged)-len(reusable),
        "proposals":judged,
        "hypothesisSummary":summary,
        "smokeAssignments":assignments,
        "groupingStatus":"provisional_capture_period_not_verified_trip",
        "featuresAreHumanVerified":False,
        "scientificHeldoutEligible":False,
        "rawSourceVideoRead":True,
        "sourceVideoModified":False,
        "networkRestrictedToLoopbackOllama":True,
        "trainingPerformed":False,
    }


def model_call(frames: list[dict],model: str) -> dict:
    return local_ollama_json(frames,model=model)
