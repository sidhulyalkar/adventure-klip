"""Scientific archive-wide highlight campaign: metadata audit and gated local coarse scan.

The audit never stats/opens/realpaths catalog source video files, including
removable storage. The opt-in scan requires a separately trusted local source
root, verifies each camera source SHA once per attempted scan, then decodes
a deliberately cheap motion timeline and proposes motion/control windows.

The proposals are NOT identified tricks, injuries, successful landings,
species, ratings, or human-approved highlight clips. Nothing here trains
models or silently treats an unreadable source as a negative example.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from urllib.parse import quote
import fcntl
import json
import math
import os
import sqlite3
import stat
import sys
import tempfile

from .archive_pilot import _open_catalog, _table
from .highlight_curation_pilot import _safe_dest
from .media import motion_series, probe

AUDIT_VERSION="klip-full-archive-campaign-audit-v1"
SCAN_VERSION="klip-full-archive-coarse-scan-v1"
_SHA="0123456789abcdef"


def _sha_id(value):
    return isinstance(value,str) and len(value)==64 and all(x in _SHA for x in value)


def _lexical(path):
    # Only lexical normalization. Calling Path.resolve() here may traverse a
    # hung removable-volume symlink even during a purported dry-run.
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def _location_class(path,root):
    if not isinstance(path,str) or not path.startswith("/"):
        return "invalid_path"
    p=_lexical(path)
    if p.is_relative_to(root/"originals"):
        return "managed_workspace_original"
    if p.is_relative_to(root):
        return "other_workspace_path"
    if p.is_relative_to(Path("/Volumes")):
        return "mounted_external_unverified"
    return "other_external_or_local_unverified"


def _atomic_json(path:Path,obj:dict):
    fd,temp=tempfile.mkstemp(prefix=".klip-campaign-",suffix=".tmp",dir=path.parent)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as handle:
            json.dump(obj,handle,indent=2,sort_keys=True,allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp,path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _load_json(path:Path,limit=40_000_000):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Missing or symlinked frozen campaign input: {path}")
    size=path.stat().st_size
    if not 2<=size<=limit:
        raise ValueError(f"Frozen campaign input has invalid size {size}: {path}")
    data=path.read_bytes()
    try:
        obj=json.loads(data)
    except (ValueError,UnicodeDecodeError) as exc:
        raise ValueError(f"Frozen campaign JSON is invalid: {path}") from exc
    if not isinstance(obj,dict):
        raise ValueError("Frozen campaign artifact must be a JSON object")
    return obj,sha256(data).hexdigest()


def _study_coverage(study:Path|None,source_ids:set[str]):
    if study is None:
        return {"provided":False,"rankedWindows":None,
                "representedSourceCount":None,"unrepresentedSourceCount":None}
    doc,digest=_load_json(study)
    ranked=doc.get("rankedCandidates")
    if not isinstance(ranked,list):
        raise ValueError("Frozen study does not contain rankedCandidates list")
    represented=set()
    for candidate in ranked:
        if not isinstance(candidate,dict):
            raise ValueError("Malformed ranked candidate in frozen study")
        aid=candidate.get("sourceAssetId",candidate.get("assetId"))
        if _sha_id(aid) and aid in source_ids:
            represented.add(aid)
    return {
        "provided":True,"frozenStudySha256":digest,
        "rankedWindows":len(ranked),
        "representedSourceCount":len(represented),
        "unrepresentedSourceCount":len(source_ids-represented),
        "representedSourceIds":sorted(represented),
        "note":"Study candidates are hypotheses, not ground truth; coverage is by source identity.",
    }


def _round_robin_assets(rows):
    groups=defaultdict(list)
    for row in rows:
        activity=row["catalogActivity"]
        group=activity if activity in ("ski","bike","snorkeling") else "unknown"
        groups[group].append(row)
    for arr in groups.values():
        arr.sort(key=lambda x:(
            x["episodeCount"]>0,
            x["semanticFrameCount"]>0,
            x["sourceAssetSha256"]))
    result=[]
    keys=("ski","bike","snorkeling","unknown")
    while any(groups.values()):
        for k in keys:
            if groups[k]:
                result.append(groups[k].pop(0))
    return result


def create_campaign_audit(workspace:Path,output_dir:Path,*,study:Path|None=None):
    root=Path(workspace).expanduser().resolve()
    dest=_safe_dest(root,output_dir)
    with closing(_open_catalog(root)) as db:
        if not _table(db,"assets"):
            raise ValueError("No Klip assets table in frozen catalog")
        assets=[dict(r) for r in db.execute(
            "SELECT id,sha256,stored_path,size_bytes,duration,activity FROM assets")]
        frame_counts={}
        if _table(db,"semantic_frames"):
            frame_counts={
                r["asset_id"]:r["n"] for r in db.execute(
                    "SELECT asset_id,COUNT(*) n FROM semantic_frames GROUP BY asset_id")}
        episode_counts={}
        if _table(db,"episodes"):
            episode_counts={
                r["asset_id"]:r["n"] for r in db.execute(
                    "SELECT asset_id,COUNT(*) n FROM episodes GROUP BY asset_id")}
        analysis_counts=Counter()
        if _table(db,"analyses"):
            analysis_counts.update({
                f"{r['stage']}:{r['status']}":r["n"] for r in db.execute(
                    "SELECT stage,status,COUNT(*) n FROM analyses GROUP BY stage,status")})
    seen=set()
    records=[]
    groups=Counter()
    quality=Counter()
    duration_total=0
    bytes_total=0
    for item in assets:
        aid=item["id"]
        digest=item["sha256"]
        if aid in seen:
            raise ValueError("Duplicate asset identity in catalog")
        seen.add(aid)
        valid_identity=(_sha_id(aid) and aid==digest)
        duration=item["duration"]
        valid_duration=(type(duration) in (int,float)
                        and math.isfinite(duration)
                        and duration>0 and duration<=24*3600)
        size=item["size_bytes"]
        valid_size=(type(size) is int and 0<size<=2*1024**4)
        path=item["stored_path"]
        location=_location_class(path,root)
        groups[location]+=1
        if not valid_identity:
            quality["invalidIdentity"]+=1
        if not valid_duration:
            quality["missingOrInvalidDuration"]+=1
        if not valid_size:
            quality["invalidSourceByteCount"]+=1
        if location not in ("managed_workspace_original",):
            quality["notVerifiedManagedLocalSource"]+=1
        if valid_duration:
            duration_total+=float(duration)
        if valid_size:
            bytes_total+=size
        records.append({
            "sourceAssetSha256":aid,
            "storedPathAsCatalogued":path,
            "sourceLocationClass":location,
            "sourceBytesFromCatalog":size if valid_size else None,
            "durationMsFromCatalog":int(float(duration)*1000) if valid_duration else None,
            "catalogActivity":item["activity"] if isinstance(item["activity"],str) else "unknown",
            "identityValid":bool(valid_identity),
            "durationValid":bool(valid_duration),
            "sizeValid":bool(valid_size),
            "semanticFrameCount":int(frame_counts.get(aid,0)),
            "episodeCount":int(episode_counts.get(aid,0)),
            "coarseScanStatus":"not_attempted",
            "originalSourceAccessedDuringAudit":False,
        })
    records=_round_robin_assets(records)
    coverage=_study_coverage(study,set(seen))
    summary={
        "version":AUDIT_VERSION,
        "createdAtUtc":datetime.now(timezone.utc).isoformat(),
        "workspace":str(root),
        "catalogAssetCount":len(records),
        "sourcePathsActuallyVerified":0,
        "validSourceIdentityCount":sum(x["identityValid"] for x in records),
        "validDurationCount":sum(x["durationValid"] for x in records),
        "totalDurationHoursFromCatalog":round(duration_total/3600,3),
        "totalSourceGiBFromCatalog":round(bytes_total/1024**3,2),
        "distinctSourceHashes":len(seen),
        "sourcesWithExistingEpisodes":sum(x["episodeCount"]>0 for x in records),
        "sourcesWithoutExistingEpisodes":sum(x["episodeCount"]==0 for x in records),
        "sourcesWithCachedSemanticFrames":sum(x["semanticFrameCount"]>0 for x in records),
        "sourcePathClasses":dict(sorted(groups.items())),
        "catalogQualityGaps":dict(sorted(quality.items())),
        "analysisStates":dict(sorted(analysis_counts.items())),
        "frozenStudyCoverage":coverage,
        "originalSourcePathsOpened":0,
        "originalSourcePathsStated":0,
        "originalSourceFramesDecoded":0,
        "sourceContentHashPasses":0,
        "metadataNotLiveAvailability":True,
        "trainingPerformed":False,
        "readinessGates":[
            {
                "gate":"G0_source_storage",
                "passed":False,
                "reason":"Path classification alone cannot verify physical availability or source checksums.",
                "next":"Copy originals to healthy local storage through a separately authorized ingest/index workflow.",
            },
            {
                "gate":"G1_coarse_complete",
                "passed":False,
                "reason":"No full-recording temporal scan has been run by this campaign.",
            },
            {
                "gate":"G2_event_grounding",
                "passed":False,
                "reason":"Sparse prior episode candidates and motion changes do not prove real sporting events.",
            },
        ],
        "limits":[
            "The frozen study and SQLite may represent different observation dates.",
            "A source path under a local-looking directory may still be a symlink; scan rechecks it.",
            "Source size/duration are catalog assertions, not independently reverified.",
            "No activity is inferred from a stored filename.",
            "This audit does not claim any human acceptance or video completeness.",
        ],
        "assets":records,
    }
    dest.mkdir(parents=True,mode=0o700,exist_ok=False)
    _atomic_json(dest/"campaign-audit.json",summary)
    public={k:v for k,v in summary.items() if k!="assets"}
    with (dest/"summary.json").open("x",encoding="utf-8") as handle:
        json.dump(public,handle,indent=2,sort_keys=True,allow_nan=False)
        handle.write("\n")
    return {
        "status":"catalog_only_archive_audit_written",
        "audit":str(dest/"campaign-audit.json"),
        "summary":str(dest/"summary.json"),
        "catalogAssets":len(records),
        "studyRepresentedSources":coverage["representedSourceCount"],
        "studyUnrepresentedSources":coverage["unrepresentedSourceCount"],
        "managedLocalPathCount":groups["managed_workspace_original"],
        "sourceOriginalReads":0,
        "readyForFullArchiveClaim":False,
    }


def _require_local_path(path,root,trusted_root:Path|None,allow_mounted:bool):
    # Do not resolve/stat an untrusted removable path before the lexical gate.
    p=_lexical(path)
    managed=_lexical(root/"originals")
    explicit=_lexical(trusted_root) if trusted_root is not None else None
    allowed=[managed]
    if explicit:
        if (explicit in (Path("/"),Path("/Volumes"),Path.home())
                or explicit==root or root.is_relative_to(explicit)):
            raise ValueError("Trusted local source root must be a specific footage directory, not a broad filesystem root")
        if (explicit.is_relative_to(Path("/Volumes")) and not allow_mounted):
            raise ValueError("Mounted external input requires --allow-mounted-ssd; never use the stalled SDXC card")
        allowed.append(explicit)
    if not any(p.is_relative_to(x) for x in allowed):
        return None
    # The lexical path qualifies; now safely check symlink targets and actual
    # existence only inside one of the explicitly authorized trusted roots.
    real=p.resolve()
    trusted_real=[x.resolve() for x in allowed]
    if not any(real.is_relative_to(x) for x in trusted_real):
        raise ValueError("Camera source path redirected outside trusted local root")
    if real.is_relative_to(Path("/Volumes")) and not allow_mounted:
        raise ValueError("Trusted source symlink resolves onto mounted media; requires explicit mounted-SSD authorization")
    if not real.is_file():
        return None
    return real


def _hash_verified(path:Path,expected:str):
    digest=sha256()
    total=path.stat().st_size
    count=0
    last=0
    with path.open("rb") as source:
        while True:
            data=source.read(4*1024*1024)
            if not data:
                break
            count+=len(data)
            digest.update(data)
            if count-last>=256*1024*1024:
                print(f"[archive-scan] SHA {path.name} {count}/{total} bytes",
                      file=sys.stderr,flush=True)
                last=count
    if count!=total or digest.hexdigest()!=expected:
        raise ValueError("Source SHA mismatch / truncated original. Keep source quarantined.")
    return total


def _proposals(series:list[dict],duration_ms:int):
    # Motion measures POV change, not sporting achievement. Always include
    # uniform controls to retain low-motion wildlife / personal moments.
    duration_ms=max(duration_ms,1000)
    windows=[]
    seen=set()
    def add(center,role,indicator=None):
        center=max(0,min(duration_ms-1,int(center)))
        start=max(0,center-9000)
        end=min(duration_ms,start+20000)
        start=max(0,end-20000)
        if end-start<1000:
            return
        key=(start,end)
        if key in seen:
            return
        seen.add(key)
        windows.append({
            "startMs":start,"endMs":end,
            "role":role,
            "motionChangeEvidence":indicator,
            "athleticOutcomeVerified":False,
            "wildlifeSpeciesVerified":False,
            "sourceVideoReadForThisProposal":True,
            "highlightApprovedByHuman":False,
        })
    for k in range(8):
        add(int(duration_ms*(k+.5)/8),"uniform_timeline_control")
    values=[(float(row["change"]),int(row["t_ms"])) for row in series
            if isinstance(row,dict) and type(row.get("t_ms")) is int
            and type(row.get("change")) in (int,float)
            and math.isfinite(row["change"])]
    values.sort(key=lambda p:(-p[0],p[1]))
    peaks=[]
    for intensity,ms in values:
        if len(peaks)>=8:
            break
        if all(abs(ms-other)>12000 for _,other in peaks):
            peaks.append((intensity,ms))
    for intensity,ms in peaks:
        add(ms,"motion_change_hypothesis",round(intensity,6))
    return windows


def _read_status(results:Path):
    path=results/"scan-state.json"
    if not path.is_file() or path.is_symlink():
        return None
    state,_=_load_json(path)
    if state.get("version")!=SCAN_VERSION or not isinstance(state.get("assets"),dict):
        raise ValueError("Invalid scan state")
    return state


def scan_campaign(workspace:Path,campaign_audit:Path,output_dir:Path,*,
                  trusted_local_root:Path|None=None,allow_mounted_ssd:bool=False,
                  max_assets:int=8,workers:int=1,fps:int=1,
                  resume:bool=False,retry_failed:bool=False):
    """Explicit, gated, serial verified video scan (workers reserved for future).

    For now workers MUST be 1 to protect predictable memory/IO behavior.
    Future separately benchmarked FFmpeg parallelism can be added without
    invalidating this source-provenance contract.
    """
    if type(workers) is not int or workers!=1:
        raise ValueError("Source scanning is single-stream in v1; use --workers 1")
    if type(max_assets) is not int or not 1<=max_assets<=256:
        raise ValueError("Select 1..256 assets per explicit run")
    if type(fps) is not int or fps not in (1,2):
        raise ValueError("Low-cost coarse scan FPS must be 1 or 2")
    root=Path(workspace).expanduser().resolve()
    audit_path=Path(campaign_audit).expanduser().resolve()
    if (not audit_path.is_relative_to(root) or
            audit_path.relative_to(root).parts[0] not in ("reports","exports")):
        raise ValueError("Audit must belong to this Klip workspace")
    audit,digest=_load_json(audit_path)
    if (audit.get("version")!=AUDIT_VERSION
            or audit.get("workspace")!=str(root)
            or not isinstance(audit.get("assets"),list)):
        raise ValueError("A matching frozen Klip full archive audit is required")
    # The frozen audit must describe the CURRENT catalog source identities.
    # A prior reindex/ingest can legitimately move managed source paths, but
    # requires a NEW audit to avoid silently mixing provenance snapshots.
    with closing(_open_catalog(root)) as db:
        current={
            r["id"]:dict(r)
            for r in db.execute(
                "SELECT id,sha256,stored_path,size_bytes,duration,activity FROM assets")
        }
    if len(current)!=len(audit["assets"]):
        raise ValueError("Catalog has changed since campaign audit; create a NEW audit")
    for item in audit["assets"]:
        row=current.get(item["sourceAssetSha256"])
        if row is None:
            raise ValueError("Catalog identity changed since frozen campaign audit")
        duration=row["duration"]
        ms=(int(float(duration)*1000)
            if type(duration) in (int,float) and math.isfinite(duration)
            and duration>0 and duration<=24*3600 else None)
        if (row["sha256"]!=item["sourceAssetSha256"]
                or row["stored_path"]!=item["storedPathAsCatalogued"]
                or row["size_bytes"]!=item["sourceBytesFromCatalog"]
                or ms!=item["durationMsFromCatalog"]
                or row["activity"]!=item["catalogActivity"]):
            raise ValueError("Catalog changed since frozen campaign audit; create a NEW audit")
    dest=_lexical(output_dir)
    if (not dest.is_relative_to(root)
            or dest==root
            or dest.relative_to(root).parts[0] not in ("reports","exports")
            or dest==audit_path.parent
            or dest.is_relative_to(audit_path.parent)):
        raise ValueError("Scan must use a separate private reports/ or exports/ directory")
    if dest.exists() and not resume:
        raise ValueError("Scan output already exists; use --resume, never overwrite")
    if not dest.exists() and resume:
        raise ValueError("No existing checkpoint directory to resume")
    # Before creating output, require a trusted local root and at least
    # one eligible frozen source. Never use arbitrary catalog volumes.
    trusted=Path(trusted_local_root).expanduser() if trusted_local_root else None
    eligible=[x for x in audit["assets"]
              if x.get("identityValid") and x.get("durationValid")
              and x.get("sizeValid")
              and (x.get("sourceLocationClass")=="managed_workspace_original"
                   or (trusted is not None and
                       _lexical(x["storedPathAsCatalogued"]).is_relative_to(_lexical(trusted))))]
    if not eligible:
        raise ValueError("BLOCKED: no valid catalog sources under managed originals or explicit trusted local root")
    if trusted is not None and _lexical(trusted).is_relative_to(Path("/Volumes")) and not allow_mounted_ssd:
        raise ValueError("Mounted SSD reads are opt-in. Use healthy local originals or explicitly authorize mounted SSD.")
    if not dest.exists():
        dest.mkdir(parents=True,mode=0o700,exist_ok=False)
        (dest/"per-source").mkdir(mode=0o700)
        state={
            "version":SCAN_VERSION,"campaignAuditSha256":digest,
            "fps":fps,"trustedLocalRoot":str(_lexical(trusted)) if trusted else None,
            "allowMountedSsd":bool(allow_mounted_ssd),
            "assets":{},
            "sourceReadsPerformed":0,
            "sourceHashPasses":0,
            "modelInferenceCalls":0,
            "filesDeleted":0,
        }
        _atomic_json(dest/"scan-state.json",state)
    state=_read_status(dest)
    if (state.get("campaignAuditSha256")!=digest or
            state.get("fps")!=fps or
            state.get("trustedLocalRoot")!=(str(_lexical(trusted)) if trusted else None)
            or state.get("allowMountedSsd")!=bool(allow_mounted_ssd)):
        raise ValueError("Resume requires exactly the frozen audit, trusted root and scan settings")
    lock_path=dest/".scan.lock"
    queued=0
    changed=0
    failed=0
    blocked=0
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("This campaign is already scanning in another process") from exc
        for item in eligible:
            if queued>=max_assets:
                break
            aid=item["sourceAssetSha256"]
            prev=state["assets"].get(aid)
            if prev and prev.get("status")=="coarse_completed":
                # Completed reports are verified by their frozen receipt. This
                # does not assert the present camera file is still unchanged.
                out=dest/"per-source"/(aid+".json")
                if (not out.is_symlink() and out.is_file()
                        and sha256(out.read_bytes()).hexdigest()==prev.get("reportSha256")):
                    continue
            if prev and prev.get("status")=="failed" and not retry_failed:
                continue
            queued+=1
            try:
                path=_require_local_path(
                    item["storedPathAsCatalogued"],root,trusted,allow_mounted_ssd)
                if path is None:
                    blocked+=1
                    status="blocked_missing_local_media"
                    reason="Source is not present inside authorized healthy local root"
                    result=None
                else:
                    before=path.stat()
                    if (not stat.S_ISREG(before.st_mode)
                            or before.st_size!=item["sourceBytesFromCatalog"]):
                        raise ValueError("Catalog size mismatch or non-regular trusted local file")
                    state["sourceHashPasses"]+=1
                    state["sourceReadsPerformed"]+=1
                    _hash_verified(path,aid)
                    meta=probe(path)
                    actual=float(meta["duration"])
                    expected=item["durationMsFromCatalog"]/1000
                    if not math.isfinite(actual) or abs(actual-expected)>max(10,expected*.05):
                        raise ValueError("ffprobe duration differs from frozen catalog; requires human metadata review")
                    samples=motion_series(path,fps=fps,total_seconds=expected)
                    after=path.stat()
                    proof=lambda st:(st.st_dev,st.st_ino,st.st_size,
                                     st.st_mtime_ns,st.st_ctime_ns)
                    if proof(before)!=proof(after):
                        raise ValueError("Local source changed during scan; results must not be accepted")
                    proposals=_proposals(samples,item["durationMsFromCatalog"])
                    result={
                        "version":SCAN_VERSION,
                        "sourceAssetSha256":aid,
                        "sourceDurationMs":item["durationMsFromCatalog"],
                        "originalSizeBytes":before.st_size,
                        "verifiedOriginalSha256":aid,
                        "shaVerifiedDuringThisScan":True,
                        "ffprobeDurationSeconds":actual,
                        "sampleFps":fps,
                        "lowResolutionMotionSamples":len(samples),
                        "candidateHypotheses":proposals,
                        "noActionOutcomePredicted":True,
                        "noSemanticSpeciesClaim":True,
                        "humanApprovalRequired":True,
                        "modelInferenceCalls":0,
                    }
                    status="coarse_completed"
                    reason=None
                if result is not None:
                    out=dest/"per-source"/(aid+".json")
                    _atomic_json(out,result)
                    receipt=sha256(out.read_bytes()).hexdigest()
                    changed+=1
                else:
                    receipt=None
            except Exception as exc:
                failed+=1
                status="failed"
                reason=f"{type(exc).__name__}: {exc}"
                receipt=None
                print(f"[archive-campaign] ERROR {aid[:12]} {reason}",
                      file=sys.stderr,flush=True)
            state["assets"][aid]={
                "status":status,
                "reportSha256":receipt,
                "reason":reason,
            }
            _atomic_json(dest/"scan-state.json",state)
            print(f"[archive-campaign] {queued}/{max_assets} {aid[:12]} {status}",
                  file=sys.stderr,flush=True)
        fcntl.flock(lock,fcntl.LOCK_UN)
    counts=Counter(x["status"] for x in state["assets"].values())
    pending=sum(not (state["assets"].get(x["sourceAssetSha256"],{}).get("status")
                      in ("coarse_completed","failed","blocked_missing_local_media"))
                for x in eligible)
    return {
        "version":SCAN_VERSION,
        "status":("incomplete_more_sources_remaining" if pending else
                  "blocked_or_failed" if failed or counts["failed"]
                  or counts["blocked_missing_local_media"] else
                  "coarse_scanned_all_eligible_local_sources"),
        "checkpoint":str(dest/"scan-state.json"),
        "frozenCampaignSha256":digest,
        "eligibleTrustedLocalSourcePaths":len(eligible),
        "totalCatalogSourceCount":len(audit["assets"]),
        "eligibleSourceSubsetNotEntireArchive":len(eligible)<len(audit["assets"]),
        "completedSources":counts["coarse_completed"],
        "failedSources":counts["failed"],
        "blockedSourcePaths":counts["blocked_missing_local_media"],
        "notYetAttemptedEligible":pending,
        "justCompleted":changed,
        "sourceHashPasses":state["sourceHashPasses"],
        "sourceMediaReadsWereExplicitlyAuthorized":True,
        "sourceFilesModified":False,
        "humanApprovedHighlights":0,
        "trainingPerformed":False,
        "nextStep":"Refine candidate windows and verify temporal outcomes before claiming any highlights.",
    }
