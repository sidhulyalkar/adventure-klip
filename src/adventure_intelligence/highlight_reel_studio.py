"""Locally verified, bounded-parallel, interactive highlight reel assembly.

Original DJI/SD-card paths are never opened, stat()'d, resolved, decoded,
hashed, or modified. All media comes from the previous source-verified
local, private curation preview cache, and is copied into the review packet.
Only human-Keep windows are promoted as highlights. Separate sport scouting
never implies successful actions or editorial approval.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha256
from importlib.resources import files
from pathlib import Path
import json
import os
import re
import stat
import tempfile
import sys

from .highlight_curation_pilot import _safe_dest, VERSION as PILOT_VERSION
from .highlight_feedback_discovery import VERSION as PLAN_VERSION, _safe_input
from .highlight_curation_validate import audit_highlight_curation_manifest

VERSION="klip-interactive-highlight-reel-studio-v1"
_REVIEW_MEDIA_MAX=45*1024*1024
_RID=re.compile(r"^[a-f0-9]{24}$")
_SHA=re.compile(r"^[a-f0-9]{64}$")
_SPORTS=("ski","bike","snowboard","mountain_bike","skateboard","longboard","surf","soccer")
_GOALS={
    "nature_encounter":"nature",
    "visual_quality":"cinematic",
    "personal_memory":"personal",
    "athletic_skill":"athletic",
    "story_payoff":"story",
    "humor":"story",
    "technical_documentation":"athletic",
    "other":"story",
}


def _read_local(path:Path,max_bytes:int=20_000_000)->tuple[dict,str]:
    if path.is_symlink() or not path.is_file() or not 1<=path.stat().st_size<=max_bytes:
        raise ValueError("A required local evidence file is missing, symlinked or oversized")
    data=path.read_bytes()
    try:
        value=json.loads(data)
    except (ValueError,UnicodeDecodeError) as exc:
        raise ValueError("Malformed local evidence JSON") from exc
    if not isinstance(value,dict):
        raise ValueError("Expected JSON evidence object")
    return value,sha256(data).hexdigest()


def _required_clip(record:dict,presentation:dict,decision:dict|None,scout:bool)->dict:
    rid=record["recordId"]
    source=record["source"]
    start,end=record["event"]["startMs"],record["event"]["endMs"]
    goals=decision["observerGoals"] if decision else []
    category=_GOALS.get(goals[0],"story") if not scout else "athletic_scout"
    return {
        "clipId":rid,
        "sourceFamilyId":source["sourceFamilyId"],
        "sourceAssetSha256":source["assetSha256"],
        "startMs":start,"endMs":end,
        "durationMs":end-start,
        "sportFromCatalog":record["sportContext"]["sport"],
        "category":category,
        "provenance":"human_keep_window" if not scout else "unreviewed_scout_window",
        "humanApproved":not scout,
        "modelObservedOutcome":False,
        "eventCompletionVerified":False,
        "priorHumanGoals":goals,
        "priorHumanRationale":(
            [r["rationale"] for r in decision["ratingsByReviewer"]]
            if decision else []),
        "originalPreviewSha256":presentation.get("proxySha256"),
        "previewAvailability":bool(presentation.get("verifiedProxyReady")),
        "videoUrl":None,
        "videoError":None,
    }


def _copy_verified_proxy(pilot:Path,media:Path,clip:dict)->tuple[str,bool,str|None]:
    """Copy only report-local cached derivative using a bounded streaming hash.

    Uses O_NOFOLLOW where supported; cross-checks fstat before/after to avoid
    races and never passes an original source file path to an encoder.
    Every task isolates failures; other independent clips keep processing.
    """
    rid=clip["clipId"]
    expected=clip["originalPreviewSha256"]
    if not _RID.fullmatch(rid):
        return rid,False,"invalid_record_identity"
    if not clip["previewAvailability"]:
        return rid,False,"verified_proxy_missing"
    if not isinstance(expected,str) or not _SHA.fullmatch(expected):
        return rid,False,"invalid_proxy_digest"
    src=pilot/"media"/(rid+".mp4")
    # Reject a local cache directory redirected toward removable original
    # media through a symlink, including when the leaf itself is regular.
    if src.parent.is_symlink():
        return rid,False,"unsafe_media_directory_symlink"
    if src.is_symlink():
        return rid,False,"unsafe_proxy_symlink"
    fd=None
    tmp=None
    try:
        fd=os.open(src,os.O_RDONLY|getattr(os,"O_NOFOLLOW",0))
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 64<=before.st_size<=_REVIEW_MEDIA_MAX:
            return rid,False,"proxy_not_bounded_regular_media"
        dest_fd,tmp=tempfile.mkstemp(dir=media,prefix=".klip-reel-",suffix=".mp4")
        digest=sha256()
        try:
            os.fchmod(dest_fd,0o600)
            with os.fdopen(dest_fd,"wb") as target,os.fdopen(fd,"rb",closefd=False) as origin:
                while True:
                    block=origin.read(1024*1024)
                    if not block:
                        break
                    digest.update(block)
                    target.write(block)
                target.flush()
                os.fsync(target.fileno())
            after=os.fstat(fd)
            identity=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
            if identity(before)!=identity(after):
                return rid,False,"proxy_changed_during_copy"
            if digest.hexdigest()!=expected:
                return rid,False,"proxy_sha256_mismatch"
            destination=media/(rid+".mp4")
            os.replace(tmp,destination)
            tmp=None
            return rid,True,None
        finally:
            if tmp is not None:
                os.unlink(tmp)
    except (OSError,ValueError):
        return rid,False,"local_proxy_unavailable"
    finally:
        if fd is not None:
            os.close(fd)


def assemble_reel_studio(workspace:Path,pilot_dir:Path,
                         feedback_plan:Path,output_dir:Path,*,
                         workers:int=2,scout_limit:int=12)->dict:
    """Create offline auto-advancing playable playlists and timecode comments."""
    if type(workers) is not int or not 1<=workers<=4:
        raise ValueError("Cache-only copy workers must be 1..4")
    if type(scout_limit) is not int or not 0<=scout_limit<=80:
        raise ValueError("Sport scouting windows must be 0..80")
    root=Path(workspace).expanduser().resolve()
    pilot=_safe_input(root,pilot_dir)
    dest=_safe_dest(root,output_dir)
    if dest.is_relative_to(pilot) or pilot.is_relative_to(dest):
        raise ValueError("Reel output must be separate from immutable pilot")
    plan_file=_safe_input(root,feedback_plan)
    if plan_file.suffix!=".json":
        raise ValueError("Expected a versioned review-discovery JSON")
    frozen,frozen_sha=_read_local(pilot/"curation-manifest.json")
    presentation,_=_read_local(pilot/"presentation.json")
    plan,plan_sha=_read_local(plan_file)
    preflight=audit_highlight_curation_manifest(pilot/"curation-manifest.json")
    if (frozen.get("version")!="klip-highlight-curation-manifest-v1"
            or presentation.get("version")!=PILOT_VERSION
            or presentation.get("manifestSha256")!=frozen_sha
            or plan.get("version")!=PLAN_VERSION
            or plan.get("frozenPilotManifestSha256")!=frozen_sha
            or not isinstance(frozen.get("records"),list)
            or not isinstance(presentation.get("records"),dict)
            or not isinstance(plan.get("decisions"),list)):
        raise ValueError("Reel inputs do not share a matching frozen pilot")
    all_records={}
    for record in frozen["records"]:
        rid=record["recordId"]
        if (not isinstance(rid,str) or not _RID.fullmatch(rid)
                or rid in all_records):
            raise ValueError("Malformed/duplicate pilot record identity")
        all_records[rid]=record
    if set(all_records)!=set(presentation["records"]):
        raise ValueError("Missing or extra preview evidence record")
    reviewed={}
    for decision in plan["decisions"]:
        rid=decision["recordId"]
        if rid not in all_records or rid in reviewed:
            raise ValueError("Review decision missing from source manifest or duplicated")
        source=all_records[rid]
        if (decision["sourceAssetSha256"]!=source["source"]["assetSha256"]
                or decision["startMs"]!=source["event"]["startMs"]
                or decision["endMs"]!=source["event"]["endMs"]
                or decision["sourceFamilyId"]!=source["source"]["sourceFamilyId"]):
            raise ValueError("Human-reviewed source identity/time does not match frozen manifest")
        reviewed[rid]=decision
    if not reviewed:
        raise ValueError("Need completed human feedback before assembling highlight reels")
    selected=[]
    for record in frozen["records"]:
        rid=record["recordId"]
        d=reviewed.get(rid)
        if d is not None and d["route"]=="provisional_highlight":
            selected.append(_required_clip(record,presentation["records"][rid],d,False))
    # Unreviewed candidate material is a separate, visibly experimental
    # sports scout playlist, never a trained or human-approved highlight.
    candidate_scout=[]
    seen_sources=set()
    for record in frozen["records"]:
        if scout_limit==0:
            break
        rid=record["recordId"]
        source=record["source"]["sourceFamilyId"]
        sport=record["sportContext"]["sport"]
        if (rid in reviewed or sport not in _SPORTS or source in seen_sources
                or not presentation["records"][rid].get("verifiedProxyReady")):
            continue
        seen_sources.add(source)
        candidate_scout.append(_required_clip(
            record,presentation["records"][rid],None,True))
        if len(candidate_scout)>=scout_limit:
            break
    selected+=candidate_scout
    if len(selected)>200:
        raise ValueError("Reel candidate count exceeds bound")
    dest.mkdir(parents=True,mode=0o700,exist_ok=False)
    media=dest/"media"
    media.mkdir(mode=0o700)
    result_map={}
    with ThreadPoolExecutor(max_workers=workers,thread_name_prefix="klip-reel-cache") as pool:
        futures={pool.submit(_copy_verified_proxy,pilot,media,clip):
                 clip["clipId"] for clip in selected}
        for completed in as_completed(futures):
            rid,ready,reason=completed.result()
            result_map[rid]=(ready,reason)
            print(f"[reel-studio] verified local clip {len(result_map)}/{len(selected)} "
                  f"{rid[:10]}: {'ready' if ready else reason}",
                  file=sys.stderr,flush=True)
    errors=Counter()
    for clip in selected:
        ready,reason=result_map[clip["clipId"]]
        if ready:
            clip["videoUrl"]="media/"+clip["clipId"]+".mp4"
        else:
            clip["videoError"]=reason
            errors[reason]+=1
    # Reel playback lists refer only to media-verified clips. Show empty lane
    # explicitly instead of inventing successful skiing/biking examples.
    available=[x for x in selected if x["videoUrl"] is not None]
    ids=lambda pred:[x["clipId"] for x in available if pred(x)]
    reels=[
        {"reelId":"human-selected","label":"Your selected moments",
         "description":"All existing human-Keep windows, across goals",
         "clipIds":ids(lambda x:x["humanApproved"]),
         "status":"human_preference_not_expert_adjudicated"},
        {"reelId":"nature","label":"Nature encounters",
         "description":"Human-selected encounters, not verified species classifications",
         "clipIds":ids(lambda x:x["category"]=="nature"),
         "status":"human_preference_not_expert_adjudicated"},
        {"reelId":"cinematic","label":"Cinematic travel",
         "description":"Human-selected camera craft and visual perspectives",
         "clipIds":ids(lambda x:x["category"]=="cinematic"),
         "status":"human_preference_not_expert_adjudicated"},
        {"reelId":"personal","label":"Personal memories",
         "description":"Meaningful human-selected moments despite imperfect technical quality",
         "clipIds":ids(lambda x:x["category"]=="personal"),
         "status":"human_preference_not_expert_adjudicated"},
        {"reelId":"athletic","label":"Athletic highlights",
         "description":"Requires explicit human-Keep assessment of athletic moments",
         "clipIds":ids(lambda x:x["category"]=="athletic" and x["humanApproved"]),
         "status":"human_preference_not_expert_adjudicated"},
        {"reelId":"sport-scout","label":"Sport scouting",
         "description":"Unreviewed candidates; none are claimed to be achievements",
         "clipIds":ids(lambda x:x["provenance"]=="unreviewed_scout_window"),
         "status":"unreviewed_hypotheses_only"},
    ]
    manifest={
        "version":VERSION,"frozenPilotSha256":frozen_sha,
        "humanFeedbackPlanSha256":plan_sha,
        "workersUsedForLocalPreviewCopy":workers,
        "originalVideoReads":0,"originalVideoDecodes":0,
        "externalMediaPathsExamined":0,"modelInferenceCalls":0,
        "trainingPerformed":False,"sourceFilesModified":False,
        "clipsRenderedToNewSingleMp4":0,
        "clips":selected,"reels":reels,
        "missingOrInvalidPreviews":dict(sorted(errors.items())),
        "sourceOutingLeakageNotResolved":True,
        "pilotPreflightRecords":preflight["records"],
        "notes":[
            "Reels are interactive playlists of verified local source-derived proxies, not finalized full-resolution exports.",
            "Human Keep is a subjective preference for an exact window and goal, not independently verified sporting excellence.",
            "Unreviewed sport scouting clips are explicitly separate from selected highlights.",
            "All source/outcome/rarity/risk assertions remain subject to evidence and qualified expert checks.",
            "The upload contains no original media; any mp4 files were copied only from immutable locally verified pilot derivatives.",
            "If no sport clip is approved or available, the athletic reel remains empty rather than inventing one.",
        ],
    }
    manifest_bytes=(json.dumps(manifest,indent=2,sort_keys=True,allow_nan=False)+"\n").encode("utf-8")
    digest=sha256(manifest_bytes).hexdigest()
    page=(files("adventure_intelligence").joinpath(
        "studio_assets/highlight_reel_studio_v1.html").read_text(encoding="utf-8"))
    # Encode JSON safely in inline script without allowing </script> injection.
    embedded=json.dumps({
        "version":VERSION,
        "manifestSha256":digest,
        "clips":selected,
        "reels":reels,
    },ensure_ascii=True,separators=(",",":"))
    embedded=(embedded.replace("&","\\u0026").replace("<","\\u003c")
              .replace(">","\\u003e"))
    page=page.replace("__KLIP_STUDIO_PAYLOAD__",embedded)
    with (dest/"reel-manifest.json").open("xb") as fp:
        fp.write(manifest_bytes)
    with (dest/"index.html").open("x",encoding="utf-8") as fp:
        fp.write(page)
    return {
        "status":"interactive_reel_studio_created",
        "directory":str(dest),
        "index":str(dest/"index.html"),
        "manifest":str(dest/"reel-manifest.json"),
        "manifestSha256":digest,
        "localCacheWorkers":workers,
        "humanSelectedAvailable":sum(x["humanApproved"] and x["videoUrl"] for x in selected),
        "sportScoutingAvailable":sum(not x["humanApproved"] and x["videoUrl"] for x in selected),
        "missingOrInvalidPreviews":dict(sorted(errors.items())),
        "reelClipCounts":{x["reelId"]:len(x["clipIds"]) for x in reels},
        "sourceOriginalReads":0,"sourceFilesModified":False,
        "modelInferenceCalls":0,
    }
