"""Read-only truth-in-progress status for an archive-wide highlight campaign.

All counters have explicit denominators. An audit-only run, an exhausted
zero-input AI queue or a packet with zero playable clips can never be
reported as a complete source-video processing campaign.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
from pathlib import Path
import json

from .full_archive_campaign import AUDIT_VERSION,SCAN_VERSION,_load_json
from .highlight_curation_pilot import VERSION as PILOT_VERSION

VERSION="klip-full-archive-campaign-progress-v1"


def _bounded_input(root:Path,path:Path,required:bool=True)->Path:
    p=Path(path).expanduser().resolve()
    if (not p.is_relative_to(root) or
            not p.relative_to(root).parts or
            p.relative_to(root).parts[0] not in ("reports","exports")):
        raise ValueError("Campaign progress inputs must be private workspace reports")
    if required and not p.is_file():
        raise ValueError(f"Expected frozen campaign file missing: {p}")
    return p


def campaign_progress(workspace:Path,audit_file:Path,*,
                      scan_dir:Path|None=None,
                      packet_dir:Path|None=None)->dict:
    root=Path(workspace).expanduser().resolve()
    audit_path=_bounded_input(root,audit_file)
    audit,audit_sha=_load_json(audit_path)
    if (audit.get("version")!=AUDIT_VERSION or
            audit.get("workspace")!=str(root) or
            not isinstance(audit.get("assets"),list)):
        raise ValueError("Frozen campaign audit is invalid or from another workspace")
    assets=audit["assets"]
    ids={x["sourceAssetSha256"] for x in assets}
    if len(ids)!=len(assets):
        raise ValueError("Frozen campaign contains duplicate source identities")
    assert len(assets)==audit["catalogAssetCount"],"Invalid frozen audit count"
    catalog_total_ms=sum(x["durationMsFromCatalog"] or 0 for x in assets)
    scan={"available":False,"completed":0,"failed":0,"blocked":0,
          "interruptedOrUnattempted":len(assets),
          "completedDurationSeconds":0,
          "totalCatalogDurationSeconds":round(catalog_total_ms/1000,2),
          "completedOriginalGiBFromCatalog":0,
          "coarseSampleMeasurements":0,"candidateWindowsProposed":0,
          "sourceShaPassesAttempted":0,"originalMediaReads":0,
          "meanVerifiedDecodeSecondsPerWallSecond":None,
          "measuredSourcesWithTiming":0,
          "scanStateSha256":None,
          "reportIntegrityFailures":0,
          "scanSourceCoveragePercentOfEntireCatalog":0.0,
          "scanDurationCoveragePercentOfEntireCatalog":0.0}
    if scan_dir is not None:
        folder=_bounded_input(root,Path(scan_dir)/"scan-state.json").parent
        state,state_sha=_load_json(folder/"scan-state.json")
        if (state.get("version")!=SCAN_VERSION or
                state.get("campaignAuditSha256")!=audit_sha or
                not isinstance(state.get("assets"),dict)):
            raise ValueError("Scan checkpoint and audit fingerprints disagree")
        if not set(state["assets"]).issubset(ids):
            raise ValueError("Scan checkpoint contains unknown catalog sources")
        flags=Counter()
        completed_ms=0
        catalog_gib=0
        measurements=0
        candidates=0
        speeds=[]
        integrity=0
        for item in assets:
            aid=item["sourceAssetSha256"]
            status=state["assets"].get(aid,{}).get("status","unattempted")
            if status not in ("coarse_completed","failed",
                              "blocked_missing_local_media","unattempted"):
                raise ValueError(f"Unknown per-source status {status} in checkpoint")
            if status=="coarse_completed":
                report=folder/"per-source"/(aid+".json")
                receipt=state["assets"][aid].get("reportSha256")
                if report.is_symlink() or not report.is_file():
                    integrity+=1
                    flags["report_integrity_invalid"]+=1
                    continue
                data=report.read_bytes()
                if sha256(data).hexdigest()!=receipt:
                    integrity+=1
                    flags["report_integrity_invalid"]+=1
                    continue
                try:
                    record=json.loads(data)
                except (ValueError,UnicodeDecodeError):
                    integrity+=1
                    flags["report_integrity_invalid"]+=1
                    continue
                if (record.get("version")!=SCAN_VERSION
                        or record.get("sourceAssetSha256")!=aid
                        or record.get("verifiedOriginalSha256")!=aid
                        or record.get("shaVerifiedDuringThisScan") is not True
                        or record.get("sourceDurationMs")!=item["durationMsFromCatalog"]
                        or not isinstance(record.get("candidateHypotheses"),list)):
                    integrity+=1
                    flags["report_integrity_invalid"]+=1
                    continue
                completed_ms+=item["durationMsFromCatalog"] or 0
                catalog_gib+=(item["sourceBytesFromCatalog"] or 0)/1024**3
                measurements+=record.get("lowResolutionMotionSamples",0)
                candidates+=len(record["candidateHypotheses"])
                speed=record.get("measuredDecodeVideoSecondsPerWallSecond")
                if type(speed) in (int,float) and speed>0:
                    speeds.append(float(speed))
            flags[status]+=1
        complete=flags["coarse_completed"]
        scan={
            "available":True,
            "completed":complete,
            "failed":flags["failed"],
            "blocked":flags["blocked_missing_local_media"],
            "reportIntegrityFailures":integrity,
            "interruptedOrUnattempted":len(assets)-complete-flags["failed"]
                                      -flags["blocked_missing_local_media"]
                                      -flags["report_integrity_invalid"],
            "completedDurationSeconds":round(completed_ms/1000,2),
            "totalCatalogDurationSeconds":round(catalog_total_ms/1000,2),
            "completedOriginalGiBFromCatalog":round(catalog_gib,2),
            "coarseSampleMeasurements":measurements,
            "candidateWindowsProposed":candidates,
            "sourceShaPassesAttempted":state.get("sourceHashPasses",0),
            "originalMediaReads":state.get("sourceReadsPerformed",0),
            "meanVerifiedDecodeSecondsPerWallSecond":
                round(sum(speeds)/len(speeds),2) if speeds else None,
            "measuredSourcesWithTiming":len(speeds),
            "scanStateSha256":state_sha,
            "scanSourceCoveragePercentOfEntireCatalog":
                round(100*complete/len(assets),2) if assets else 0.0,
            "scanDurationCoveragePercentOfEntireCatalog":
                round(100*completed_ms/catalog_total_ms,2)
                if catalog_total_ms else 0.0,
        }
    packet={"available":False,"candidateWindows":0,
            "verifiedPlaybackClipsReported":0,
            "missingPlaybackClipsReported":0,
            "sourceIdentityCount":0,
            "reportIsIndependentlyRehashedNow":False,
            "matchesCurrentScanCheckpoint":None}
    if packet_dir is not None:
        folder=_bounded_input(root,Path(packet_dir)/"selection-report.json").parent
        report,_=_load_json(folder/"selection-report.json")
        manifest,manifest_digest=_load_json(folder/"curation-manifest.json")
        presentation,_=_load_json(folder/"presentation.json")
        if (report.get("version")!=PILOT_VERSION
                or report.get("campaignAuditSha256")!=audit_sha
                or report.get("manifestSha256")!=manifest_digest
                or presentation.get("manifestSha256")!=manifest_digest
                or not isinstance(manifest.get("records"),list)
                or not isinstance(presentation.get("records"),dict)):
            raise ValueError("Review packet artifacts refer to conflicting source evidence")
        entries=manifest["records"]
        if ({x["recordId"] for x in entries}!=set(presentation["records"])
                or len(entries)!=len({x["recordId"] for x in entries})):
            raise ValueError("Review packet records are incomplete/duplicated")
        ready=sum(1 for x in presentation["records"].values()
                  if x.get("verifiedProxyReady") is True)
        packet={
            "available":True,
            "candidateWindows":len(entries),
            "verifiedPlaybackClipsReported":ready,
            "missingPlaybackClipsReported":len(entries)-ready,
            "sourceIdentityCount":len({x["source"]["assetSha256"] for x in entries}),
            "reportIsIndependentlyRehashedNow":False,
            "matchesCurrentScanCheckpoint":(
                report.get("sourceScanFingerprint")==scan["scanStateSha256"]
                if scan["available"] else None),
            "note":"Proxy readiness is read from frozen packet receipt. Video bytes are not rehashed during this progress check.",
        }
    completed_all=(bool(assets) and scan["completed"]==len(assets)
                   and scan["failed"]==0 and scan["blocked"]==0
                   and scan["reportIntegrityFailures"]==0)
    ready_for_human=packet["verifiedPlaybackClipsReported"]>0
    blockers=[]
    if not scan["available"]:
        blockers.append("No source-coarse scan checkpoint; archive audit is metadata only")
    if scan["blocked"]:
        blockers.append(f'{scan["blocked"]} source recordings blocked because healthy local video is absent')
    if scan["failed"]:
        blockers.append(f'{scan["failed"]} source recordings failed identity, metadata or decode verification')
    if scan["interruptedOrUnattempted"]:
        blockers.append(f'{scan["interruptedOrUnattempted"]} recordings not attempted or interrupted')
    if scan["reportIntegrityFailures"]:
        blockers.append(f'{scan["reportIntegrityFailures"]} completed source reports do not verify')
    if not packet["available"]:
        blockers.append("No frozen candidate review packet yet")
    elif not ready_for_human:
        blockers.append("Review packet has zero verified local playable previews")
    elif packet["matchesCurrentScanCheckpoint"] is False:
        blockers.append("Review packet is from an older scan-state snapshot")
    blockers.append("Deep temporal sports/wildlife outcome qualification and human scoring remain separate stages")
    return {
        "version":VERSION,
        "catalogAssetCount":len(assets),
        "catalogSourceIdsWithValidDuration":sum(x["durationValid"] for x in assets),
        "catalogSourceIdsWithValidIdentity":sum(x["identityValid"] for x in assets),
        "studyRepresentedSourceCount":
            audit["frozenStudyCoverage"]["representedSourceCount"],
        "scan":scan,
        "reviewPacket":packet,
        "goNoGo":{
            "coarseScannedEveryCatalogSource":completed_all,
            "atLeastOnePlayableCandidate":ready_for_human,
            "allArchiveHighlightsDiscovered":False,
            "trainingReady":False,
            "sourceMediaOpenedByThisStatus":False,
            "modelsCalledByThisStatus":False,
        },
        "remainingBlockers":blockers,
        "recommendedNextStep":(
            "Stage/repair healthy local original recordings, reindex and run a NEW audit"
            if not scan["available"] or scan["blocked"]
            else "Resume the frozen coarse source scan to expose remaining recordings"
            if scan["interruptedOrUnattempted"] else
            "Investigate failed verification reports before retrying sources"
            if scan["failed"] or scan["reportIntegrityFailures"] else
            "Prepare playable source-backed H264 clips and perform blind human review"
            if not ready_for_human else
            "Review clips in the offline packet; then qualify events and refine action/filming cuts"
        ),
    }
