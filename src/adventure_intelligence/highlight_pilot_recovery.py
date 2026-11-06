"""Diagnose or safely reconstruct incomplete highlight curation pilot packets.

Strictly report-local. This never verifies, stats, opens, hashes or decodes a
catalog source recording, removable SD card or DJI original. A recovered
packet is a NEW directory with the EXACT original manifest bytes and SHA.
It includes ONLY checksummed previously prepared local review derivatives.
No unchecked footage is silently made reviewable.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
from pathlib import Path
from urllib.parse import quote
import json
import os
import re
import shutil
import stat

from .highlight_curation_pilot import (
    _safe_dest, VERSION as PILOT_VERSION
)
from .highlight_feedback_discovery import (
    _safe_input, VERSION as PLAN_VERSION
)
from .highlight_reel_studio import _read_local
from .highlight_curation_validate import audit_highlight_curation_manifest
from .studio_preview_judge import (
    preview_paths, _check_cached, MAX_PREVIEW_BYTES
)

VERSION="klip-curation-pilot-local-recovery-v1"
_SHA=re.compile(r"^[0-9a-f]{64}$")
_RID=re.compile(r"^[0-9a-f]{24}$")


def _file_state(path:Path,limit:int)->dict:
    if path.is_symlink():
        return {"status":"symlink_refused","bytes":None,"path":str(path)}
    if not path.is_file():
        return {"status":"missing","bytes":None,"path":str(path)}
    try:
        size=path.stat().st_size
    except OSError:
        return {"status":"unreadable","bytes":None,"path":str(path)}
    state="ready" if 1<=size<=limit else ("empty" if size==0 else "oversized")
    return {"status":state,"bytes":size,"path":str(path)}


def diagnose_local_pilot(workspace:Path,pilot_dir:Path,feedback_plan:Path|None=None)->dict:
    root=Path(workspace).expanduser().resolve()
    pilot=_safe_input(root,pilot_dir)
    files={
        "curationManifest":_file_state(pilot/"curation-manifest.json",20_000_000),
        "presentation":_file_state(pilot/"presentation.json",20_000_000),
        "selectionReport":_file_state(pilot/"selection-report.json",20_000_000),
        "reviewMediaDir":{
            "status":("symlink_refused" if (pilot/"media").is_symlink()
                      else "ready" if (pilot/"media").is_dir() else "missing"),
            "path":str(pilot/"media"),
        },
    }
    if feedback_plan is not None:
        plan_path=_safe_input(root,feedback_plan)
        files["feedbackPlan"]=_file_state(plan_path,20_000_000)
    digest=None
    mismatches=[]
    manifest=None
    if files["curationManifest"]["status"]=="ready":
        manifest,digest=_read_local(pilot/"curation-manifest.json")
        if manifest.get("version")!="klip-highlight-curation-manifest-v1":
            mismatches.append("pilot_manifest_version")
        else:
            audit_highlight_curation_manifest(pilot/"curation-manifest.json")
    if files["presentation"]["status"]=="ready" and digest:
        try:
            presentation,_=_read_local(pilot/"presentation.json")
            if (presentation.get("version")!=PILOT_VERSION or
                    presentation.get("manifestSha256")!=digest or
                    not isinstance(presentation.get("records"),dict)):
                mismatches.append("presentation_fingerprint_or_version")
            elif isinstance(manifest.get("records"),list) and (
                    {r["recordId"] for r in manifest["records"]}
                    !=set(presentation["records"])):
                mismatches.append("presentation_record_set")
        except ValueError:
            mismatches.append("presentation_malformed")
    if feedback_plan is not None and files["feedbackPlan"]["status"]=="ready" and digest:
        try:
            plan,_=_read_local(plan_path)
            if (plan.get("version")!=PLAN_VERSION
                    or plan.get("frozenPilotManifestSha256")!=digest):
                mismatches.append("feedback_plan_different_frozen_pilot")
        except ValueError:
            mismatches.append("feedback_plan_malformed")
    if files["selectionReport"]["status"]=="ready" and digest:
        try:
            report,_=_read_local(pilot/"selection-report.json")
            if report.get("manifestSha256")!=digest:
                mismatches.append("selection_report_different_frozen_pilot")
        except ValueError:
            mismatches.append("selection_report_malformed")
    required=("curationManifest","presentation")
    if feedback_plan is not None:
        required+=("feedbackPlan",)
    runnable=(all(files[k]["status"]=="ready" for k in required)
              and not mismatches
              and files["reviewMediaDir"]["status"]=="ready")
    return {
        "version":VERSION,
        "pilotDir":str(pilot),
        "originalManifestSha256":digest,
        "files":files,
        "mismatches":mismatches,
        "studioMetadataPreflightReady":runnable,
        "previewBytesIndependentlyVerifiedByDoctor":False,
        "suggestedNextAction":(
            "Use this pilot if its local verified previews are available."
            if runnable else
            "Run highlight-pilot-recover into a NEW workspace reports folder; "
            "do not overwrite or regenerate the original manifest."
        ),
        "originalMediaReads":0,"originalMediaStats":0,
        "modelsTrained":0,"filesDeleted":0,
    }


def _sha_file(path:Path,max_bytes:int)->str|None:
    """Read regular private cache files only, streaming without large allocations."""
    if path.is_symlink() or path.parent.is_symlink():
        return None
    fd=None
    try:
        fd=os.open(path,os.O_RDONLY|getattr(os,"O_NOFOLLOW",0))
        before=os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode)
                or not 64<=before.st_size<=max_bytes):
            return None
        digest=sha256()
        while True:
            part=os.read(fd,1024*1024)
            if not part:
                break
            digest.update(part)
        after=os.fstat(fd)
        check=lambda x:(x.st_dev,x.st_ino,x.st_size,x.st_mtime_ns,x.st_ctime_ns)
        return digest.hexdigest() if check(before)==check(after) else None
    except OSError:
        return None
    finally:
        if fd is not None:
            os.close(fd)


def recover_local_pilot(workspace:Path,pilot_dir:Path,
                        feedback_plan:Path,output_dir:Path)->dict:
    from .highlight_curation_review import render_blind_review

    root=Path(workspace).expanduser().resolve()
    pilot=_safe_input(root,pilot_dir)
    dest=_safe_dest(root,output_dir)
    if dest.is_relative_to(pilot) or pilot.is_relative_to(dest):
        raise ValueError("Recovered pilot must not overlap original pilot")
    manifest_path=pilot/"curation-manifest.json"
    manifest,manifest_sha=_read_local(manifest_path)
    if manifest.get("version")!="klip-highlight-curation-manifest-v1":
        raise ValueError("Original frozen pilot has wrong manifest version")
    valid=audit_highlight_curation_manifest(manifest_path)
    rows=manifest["records"]
    plan_path=_safe_input(root,feedback_plan)
    plan,_=_read_local(plan_path)
    if (plan.get("version")!=PLAN_VERSION or
            plan.get("frozenPilotManifestSha256")!=manifest_sha
            or not isinstance(plan.get("decisions"),list)):
        raise ValueError("Existing human feedback is NOT linked to this manifest; refusing recovery")
    existing={}
    original_presentation=pilot/"presentation.json"
    if _file_state(original_presentation,20_000_000)["status"]=="ready":
        try:
            presentation,_=_read_local(original_presentation)
            if (presentation.get("version")==PILOT_VERSION
                    and presentation.get("manifestSha256")==manifest_sha
                    and isinstance(presentation.get("records"),dict)):
                existing=presentation["records"]
        except ValueError:
            pass
    selection=None
    original_selection=pilot/"selection-report.json"
    if _file_state(original_selection,20_000_000)["status"]=="ready":
        record,_=_read_local(original_selection)
        if record.get("manifestSha256")!=manifest_sha:
            raise ValueError("Existing selection-report SHA does not match frozen manifest")
        selection=original_selection.read_bytes()
    if not isinstance(rows,list) or not rows:
        raise ValueError("Frozen pilot has no source-linked candidate windows")
    id_set={x["recordId"] for x in rows}
    if len(id_set)!=len(rows):
        raise ValueError("Duplicate frozen records")
    for x in rows:
        if not _RID.fullmatch(x["recordId"]):
            raise ValueError("Invalid frozen record ID")
    if existing and set(existing)!=id_set:
        raise ValueError("Existing presentation record IDs differ from original manifest")
    # Resolve only under the workspace's verified local report subtree.
    cache_base=(root/"reports"/"studio-preview-cache-v1").resolve()
    if not cache_base.is_relative_to(root):
        raise ValueError("Studio cache is redirected outside local workspace")
    staged=[]
    status=Counter()
    presentation_records={}
    for x in rows:
        rid=x["recordId"]
        aid=x["source"]["assetSha256"]
        start,end=x["event"]["startMs"],x["event"]["endMs"]
        if not _SHA.fullmatch(aid):
            raise ValueError("Invalid source SHA in frozen manifest")
        cache,receipt=preview_paths(root,aid,start,end)
        cache_ok=(not cache.is_symlink() and not receipt.is_symlink()
                  and not cache.parent.is_symlink() and
                  _check_cached(cache,receipt,aid=aid,start_ms=start,end_ms=end))
        old=existing.get(rid) if existing else None
        candidates=[]
        if cache_ok:
            entry=json.loads(receipt.read_text(encoding="utf-8"))
            expected=entry["previewSha256"]
            candidates.append(("verified_local_cache",cache,expected))
        if old and old.get("verifiedProxyReady") and (
                isinstance(old.get("proxySha256"),str)
                and _SHA.fullmatch(old["proxySha256"])):
            p=pilot/"media"/(rid+".mp4")
            if not (pilot/"media").is_symlink():
                candidates.append(("verified_prior_pilot",p,old["proxySha256"]))
        chosen=None
        for provenance,source,expected in candidates:
            if source.is_relative_to(root) and _sha_file(source,MAX_PREVIEW_BYTES)==expected:
                chosen=(provenance,source,expected)
                break
        if chosen is None:
            status["unavailable_without_source_reads"]+=1
            presentation_records[rid]={
                "verifiedProxyReady":False,
                "proxyUrl":None,"proxySha256":None,
                "requiresSourceReadForPreview":True,
            }
        else:
            provenance,source,expected=chosen
            staged.append((rid,source,expected))
            status[provenance]+=1
            presentation_records[rid]={
                "verifiedProxyReady":True,
                "proxyUrl":"media/"+quote(rid+".mp4",safe="."),
                "proxySha256":expected,
                "requiresSourceReadForPreview":False,
            }
    # Output is independent. If the process stops here, rerun with another
    # directory; no input files were modified or deleted.
    dest.mkdir(parents=True,mode=0o700,exist_ok=False)
    (dest/"media").mkdir(mode=0o700)
    (dest/"curation-manifest.json").write_bytes(manifest_path.read_bytes())
    if sha256((dest/"curation-manifest.json").read_bytes()).hexdigest()!=manifest_sha:
        raise ValueError("Frozen original manifest changed during recovery")
    if selection is not None:
        (dest/"selection-report.json").write_bytes(selection)
    for rid,source,expected in staged:
        target=dest/"media"/(rid+".mp4")
        try:
            os.link(source,target,follow_symlinks=False)
        except OSError:
            # Explicit bounded local-only copy fallback when hardlinks cannot
            # span workspace volumes. This is NOT a camera source file.
            with source.open("rb") as f, target.open("xb") as out:
                shutil.copyfileobj(f,out,length=1024*1024)
        if _sha_file(target,MAX_PREVIEW_BYTES)!=expected:
            target.unlink(missing_ok=True)
            presentation_records[rid]={
                "verifiedProxyReady":False,"proxyUrl":None,
                "proxySha256":None,"requiresSourceReadForPreview":True,
            }
            status["changed_during_recovery"]+=1
    recovered={
        "version":PILOT_VERSION,"manifestSha256":manifest_sha,
        "records":presentation_records,"sourceOriginalReads":0,
    }
    with (dest/"presentation.json").open("x",encoding="utf-8") as f:
        json.dump(recovered,f,sort_keys=True,indent=2,allow_nan=False)
        f.write("\n")
    with (dest/"index.html").open("x",encoding="utf-8") as f:
        f.write(render_blind_review(manifest,presentation_records,manifest_sha))
    ready=sum(1 for x in presentation_records.values()
              if x["verifiedProxyReady"])
    report={
        "version":VERSION,"status":"recovered_from_immutable_local_evidence",
        "sourceManifestSha256":manifest_sha,
        "recoveredManifestSha256":sha256((dest/"curation-manifest.json").read_bytes()).hexdigest(),
        "manifestBytesPreservedExactly":True,
        "originalPilotFilesModified":False,
        "originalVideoReads":0,"originalVideoStats":0,
        "localCacheChecksOnly":True,
        "trainingAuthorized":False,"newMediaCreatedByDecoding":False,
        "records":len(rows),"verifiedReviewProxies":ready,
        "missingReviewProxies":len(rows)-ready,
        "originalSelectionReportCopied":selection is not None,
        "sourceProvenanceCounts":dict(sorted(status.items())),
        "notes":[
            "A recovered video is a previously receipt-verified local derivative, not newly verified original camera footage.",
            "No failed/missing preview is marked observed.",
            "Original review export remains linked because frozen manifest SHA is identical.",
            "If all proxies are unavailable, source media must be recovered separately on healthy storage.",
        ],
    }
    with (dest/"recovery-report.json").open("x",encoding="utf-8") as f:
        json.dump(report,f,sort_keys=True,indent=2,allow_nan=False)
        f.write("\n")
    return {
        "status":"local_curation_pilot_recovered",
        "directory":str(dest),
        "manifest":str(dest/"curation-manifest.json"),
        "presentation":str(dest/"presentation.json"),
        "recoveryReport":str(dest/"recovery-report.json"),
        "verifiedReviewProxies":ready,
        "missingReviewProxies":len(rows)-ready,
        "frozenManifestSha256":manifest_sha,
        "originalSourceReads":0,"originalPilotFilesModified":False,
    }
