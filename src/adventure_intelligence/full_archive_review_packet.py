"""Produce replayable, provenance-checked blind review packets from full scan.

Cached-only is the default. Explicit --prepare-missing verifies each unique
trusted source SHA once, then optionally encodes several independent short
local derivatives with at most two CPU FFmpeg workers per source. No source
media is read for a cached-only review packet. No athlete/animal/event
claims, training permission or subjective highlight labels are fabricated.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha256
from pathlib import Path
import json
import os
import subprocess
import tempfile

from .full_archive_campaign import (
    AUDIT_VERSION, SCAN_VERSION, _load_json, _require_local_path,
    _hash_verified, _sha_id, _atomic_json
)
from .highlight_curation_pilot import (
    VERSION as PILOT_VERSION, MANIFEST_VERSION,
    _record, _safe_dest
)
from .highlight_curation_review import render_blind_review
from .highlight_curation_validate import audit_highlight_curation_manifest
from .studio_preview_judge import (
    VERSION as PREVIEW_VERSION, MAX_PREVIEW_BYTES,
    _check_cached, preview_paths
)
from .media import require_tool

VERSION="klip-full-archive-review-packet-v1"


def _candidates(audit:dict,scan:dict,scan_dir:Path,limit:int,per_source:int):
    asset_by_id={x["sourceAssetSha256"]:x for x in audit["assets"]}
    groups=defaultdict(list)
    for aid,entry in scan["assets"].items():
        if entry.get("status")!="coarse_completed":
            continue
        record=scan_dir/"per-source"/(aid+".json")
        if (record.is_symlink() or not record.is_file()
                or sha256(record.read_bytes()).hexdigest()!=entry.get("reportSha256")):
            raise ValueError(f"Completed source report changed or missing: {aid[:12]}")
        values,_=_load_json(record,10_000_000)
        meta=asset_by_id.get(aid)
        if (meta is None or values.get("version")!=SCAN_VERSION or
                values.get("sourceAssetSha256")!=aid or
                values.get("verifiedOriginalSha256")!=aid or
                values.get("shaVerifiedDuringThisScan") is not True or
                not isinstance(values.get("candidateHypotheses"),list)):
            raise ValueError("Coarse source scan identity/provenance mismatch")
        for proposal in values["candidateHypotheses"]:
            start,end=proposal["startMs"],proposal["endMs"]
            role=proposal["role"]
            if (type(start) is not int or type(end) is not int
                    or not 0<=start<end<=meta["durationMsFromCatalog"]
                    or not 1000<=end-start<=60000
                    or role not in ("motion_change_hypothesis","uniform_timeline_control")):
                raise ValueError("Unbounded or unsupported coarse source interval")
            groups[aid].append({
                "asset":meta,"startMs":start,"endMs":end,
                "selectionRole":role,
                "motionChangeForAuditOnly":proposal["motionChangeEvidence"],
            })
    # Role balance prevents motion-only POV bias against still wildlife
    # or personally meaningful moments. This is sampling, NOT ranking.
    for v in groups.values():
        v.sort(key=lambda x:(
            x["selectionRole"]!="motion_change_hypothesis",
            x["startMs"],x["endMs"]))
    ids=sorted(groups)
    chosen=[]
    counts=Counter()
    seen=set()
    while len(chosen)<limit and any(groups.values()):
        changed=False
        for aid in ids:
            if counts[aid]>=per_source:
                groups[aid]=[]
                continue
            while groups[aid]:
                row=groups[aid].pop(0)
                key=(aid,row["startMs"],row["endMs"])
                if key not in seen:
                    seen.add(key)
                    break
            else:
                continue
            chosen.append(row)
            counts[aid]+=1
            changed=True
            if len(chosen)>=limit:
                break
        if not changed:
            break
    return chosen


def _generate_cached(root:Path,source:Path,aid:str,start:int,end:int):
    target,receipt=preview_paths(root,aid,start,end)
    if _check_cached(target,receipt,aid=aid,start_ms=start,end_ms=end):
        return True,None
    target.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix=".klip-campaign-preview-",suffix=".mp4",
                            dir=target.parent)
    os.close(fd)
    try:
        ffmpeg=require_tool("ffmpeg")
        cmd=[ffmpeg,"-hide_banner","-loglevel","error","-nostdin","-y",
             "-ss",f"{start/1000:.3f}","-i",str(source),
             "-t",f"{(end-start)/1000:.3f}",
             "-map","0:v:0","-an","-sn",
             "-vf","scale='min(960,iw)':-2",
             "-c:v","libx264","-preset","veryfast","-crf","29",
             "-threads","2","-pix_fmt","yuv420p",
             "-movflags","+faststart","-f","mp4",tmp]
        res=subprocess.run(cmd,stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE,timeout=180,check=False)
        if res.returncode or not 64<=os.path.getsize(tmp)<=MAX_PREVIEW_BYTES:
            return False,"local_ffmpeg_preview_failed"
        digest=sha256(Path(tmp).read_bytes()).hexdigest()
        info={
            "version":PREVIEW_VERSION,"assetId":aid,"startMs":start,
            "endMs":end,"previewSha256":digest,
            "originalSha256Verified":True,
            "codec":"h264","generatedFromOriginal":True,
            "sourceTimeAlignment":"approximate_fast_seek_needs_human_boundary_review",
        }
        os.replace(tmp,target)
        _atomic_json(receipt,info)
        return True,None
    except (OSError,subprocess.TimeoutExpired) as exc:
        return False,type(exc).__name__
    finally:
        Path(tmp).unlink(missing_ok=True)


def prepare_campaign_packet(workspace:Path,audit_file:Path,scan_dir:Path,
                            output_dir:Path,*,limit:int=80,per_source:int=4,
                            prepare_missing:bool=False,workers:int=2,
                            trusted_local_root:Path|None=None,
                            allow_mounted_ssd:bool=False):
    if type(limit) is not int or not 1<=limit<=500:
        raise ValueError("Review-packet limit must be 1..500")
    if type(per_source) is not int or not 1<=per_source<=12:
        raise ValueError("Review-packet per-source limit must be 1..12")
    if type(workers) is not int or not 1<=workers<=2:
        raise ValueError("Local preview encoding workers must be 1..2")
    root=Path(workspace).expanduser().resolve()
    audit_path=Path(audit_file).expanduser().resolve()
    scans=Path(scan_dir).expanduser().resolve()
    for p in (audit_path,scans):
        if (not p.is_relative_to(root) or
                p.relative_to(root).parts[0] not in ("reports","exports")):
            raise ValueError("Campaign inputs must belong to the same private workspace")
    dest=_safe_dest(root,output_dir)
    audit,audit_sha=_load_json(audit_path)
    scanned,_=_load_json(scans/"scan-state.json")
    if (audit.get("version")!=AUDIT_VERSION
            or scanned.get("version")!=SCAN_VERSION
            or scanned.get("campaignAuditSha256")!=audit_sha
            or not isinstance(scanned.get("assets"),dict)):
        raise ValueError("Source scan and campaign audit fingerprints do not match")
    choices=_candidates(audit,scanned,scans,limit,per_source)
    if not choices:
        raise ValueError("BLOCKED: no qualified source-level coarse windows; scan healthy local originals first")
    records=[_record({
        "id":r["asset"]["sourceAssetSha256"],
        "duration":r["asset"]["durationMsFromCatalog"]/1000,
        "activity":r["asset"]["catalogActivity"],
    },r["startMs"],r["endMs"]) for r in choices]
    manifest={"version":MANIFEST_VERSION,"records":records}
    chosen_by_asset=defaultdict(list)
    for item,rec in zip(choices,records):
        chosen_by_asset[rec["source"]["assetSha256"]].append((item,rec))
    newly_generated=0
    hashed_originals=0
    source_errors={}
    media_states={}
    # Before writing the review packet, stage local cache derivatives.
    # Default path is fully cached, and never accesses the originals.
    if prepare_missing:
        for aid,group in chosen_by_asset.items():
            needed=[]
            for item,rec in group:
                start,end=item["startMs"],item["endMs"]
                video,receipt=preview_paths(root,aid,start,end)
                if not _check_cached(video,receipt,
                                     aid=aid,start_ms=start,end_ms=end):
                    needed.append((item,rec))
            if not needed:
                continue
            try:
                source=_require_local_path(
                    group[0][0]["asset"]["storedPathAsCatalogued"],
                    root,trusted_local_root,allow_mounted_ssd)
                if source is None:
                    source_errors[aid]="local_source_missing"
                    continue
                before=source.stat()
                if before.st_size!=group[0][0]["asset"]["sourceBytesFromCatalog"]:
                    source_errors[aid]="source_size_changed"
                    continue
                hashed_originals+=1
                _hash_verified(source,aid)  # one SHA pass for this asset
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures={
                        pool.submit(_generate_cached,root,source,aid,
                                    r["startMs"],r["endMs"]):rec["recordId"]
                        for r,rec in needed
                    }
                    results={futures[future]:future.result()
                             for future in as_completed(futures)}
                after=source.stat()
                fields=lambda x:(x.st_dev,x.st_ino,x.st_size,
                                 x.st_mtime_ns,x.st_ctime_ns)
                if fields(before)!=fields(after):
                    source_errors[aid]="source_changed_during_preview_generation"
                    # A potentially changed source's new previews must NOT
                    # count as verified for this packet.
                    for item,rec in needed:
                        v,j=preview_paths(root,aid,item["startMs"],item["endMs"])
                        v.unlink(missing_ok=True)
                        j.unlink(missing_ok=True)
                    continue
                for rid,(ok,err) in results.items():
                    media_states[rid]=err if not ok else "prepared"
                    if ok:
                        newly_generated+=1
            except (ValueError,OSError) as exc:
                source_errors[aid]=type(exc).__name__+":"+str(exc)[:180]
                continue
    dest.mkdir(parents=True,mode=0o700,exist_ok=False)
    (dest/"media").mkdir(mode=0o700)
    manifest_path=dest/"curation-manifest.json"
    manifest_bytes=(json.dumps(manifest,sort_keys=True,indent=2,
                               allow_nan=False)+"\n").encode("utf-8")
    manifest_path.write_bytes(manifest_bytes)
    audit_highlight_curation_manifest(manifest_path)
    manifest_sha=sha256(manifest_bytes).hexdigest()
    presentation={}
    ready=0
    for item,rec in zip(choices,records):
        aid=rec["source"]["assetSha256"]
        rid=rec["recordId"]
        start,end=item["startMs"],item["endMs"]
        original,receipt=preview_paths(root,aid,start,end)
        ok=_check_cached(original,receipt,aid=aid,start_ms=start,end_ms=end)
        url=None
        digest=None
        if ok and original.is_file() and not original.is_symlink():
            info=json.loads(receipt.read_text())
            expected=info["previewSha256"]
            target=dest/"media"/(rid+".mp4")
            try:
                os.link(original,target,follow_symlinks=False)
            except OSError:
                # No silent nonverified fallback from original media.
                target=None
            if target is not None:
                if sha256(target.read_bytes()).hexdigest()==expected:
                    url="media/"+rid+".mp4"
                    digest=expected
                    ready+=1
                else:
                    target.unlink(missing_ok=True)
        presentation[rid]={
            "verifiedProxyReady":url is not None,
            "proxyUrl":url,"proxySha256":digest,
            "requiresSourceReadForPreview":url is None,
        }
    report={
        "version":PILOT_VERSION,"manifestSha256":manifest_sha,
        "campaignAuditSha256":audit_sha,
        "sourceScanFingerprint":sha256((scans/"scan-state.json").read_bytes()).hexdigest(),
        "maxRecordsRequested":limit,"maxPerSource":per_source,
        "selectedRecords":len(records),
        "selectedUniqueSources":len(chosen_by_asset),
        "selectionRoles":dict(sorted(Counter(
            row["selectionRole"] for row in choices).items())),
        "verifiedReviewProxies":ready,
        "missingReviewProxies":len(records)-ready,
        "originalSourceHashPassesDuringPacket":hashed_originals,
        "sourceOriginalReads":hashed_originals,
        "newVerifiedPreviewDerivatives":newly_generated,
        "sourcePreparationErrors":source_errors,
        "noHumanOutcomeInferred":True,
        "trainingAuthorized":False,
        "selectionIsBalancedExplorationNotQualityRanking":True,
        "selections":[{
            "recordId":rec["recordId"],
            "sourceAssetId":rec["source"]["assetSha256"],
            "startMs":item["startMs"],"endMs":item["endMs"],
            "selectionRole":item["selectionRole"],
            "heuristicScoreForAuditOnly":item["motionChangeForAuditOnly"],
        } for item,rec in zip(choices,records)],
        "limitations":[
            "Uniform time controls are unknown footage, not negative highlights.",
            "Camera motion is an imperfect proxy and may ignore quiet encounters.",
            "Frame-exact alignment of fast-seek derivatives is not proven.",
            "Sporting success and wildlife species require visible human/source evidence.",
            "All sources have conservative unresolved outing grouping.",
            "Any absent preview remains unavailable; no invented video or label.",
        ],
    }
    _atomic_json(dest/"presentation.json",{
        "version":PILOT_VERSION,"manifestSha256":manifest_sha,
        "records":presentation,
        "sourceOriginalReads":hashed_originals,
    })
    _atomic_json(dest/"selection-report.json",report)
    (dest/"index.html").write_text(
        render_blind_review(manifest,presentation,manifest_sha),encoding="utf-8")
    return {
        "version":VERSION,"status":(
            "review_packet_ready" if ready>0 else "blocked_no_verified_previews"),
        "directory":str(dest),"reviewPage":str(dest/"index.html"),
        "manifest":str(manifest_path),"manifestSha256":manifest_sha,
        "candidateWindows":len(records),"sourceCount":len(chosen_by_asset),
        "verifiedReviewableClips":ready,
        "missingVerifiedPreviews":len(records)-ready,
        "distinctOriginalHashPasses":hashed_originals,
        "newPreviewDerivatives":newly_generated,
        "sourceErrors":source_errors,
        "modelTrainingPerformed":False,
    }
