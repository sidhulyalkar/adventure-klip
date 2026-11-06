"""Qualify timestamped reactions from the offline multi-reel player.

This command never reads original media, trains a model, edits source files,
deletes video, or promotes subjective reactions into event ground truth.
The only video reads are bounded SHA checks of the local Studio proxy copies.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
from pathlib import Path
import json
import os
import re
import stat

from .highlight_curation_pilot import _safe_dest
from .highlight_feedback_discovery import _safe_input
from .highlight_reel_studio import VERSION as STUDIO_VERSION, _read_local

VERSION="klip-reel-timed-reactions-v1"
_REPORT_VERSION="klip-reel-timed-feedback-qualification-v1"
_TYPES={
    "standout","athletic","camera","story","nature","uncertain",
    "keep","skip","retrim","wow","skill","framing","personal","unclear",
}
_REVIEWER=re.compile(r"^[a-zA-Z0-9_.:-]{4,100}$")
_ID=re.compile(r"^[a-f0-9]{24}$")
_MAX_MEDIA=45*1024*1024


def _verify_private_proxy(root:Path,clip:dict)->bool:
    """Current studio proxy must match the original receipt hash."""
    rid=clip["clipId"]
    if not _ID.fullmatch(rid):
        return False
    if not clip.get("videoUrl"):
        return False
    if clip["videoUrl"]!="media/"+rid+".mp4":
        return False
    media=root/"media"
    path=media/(rid+".mp4")
    expected=clip.get("originalPreviewSha256")
    if (type(expected) is not str or not re.fullmatch(r"[a-f0-9]{64}",expected)
            or media.is_symlink() or path.is_symlink()):
        return False
    fd=None
    try:
        fd=os.open(path,os.O_RDONLY|getattr(os,"O_NOFOLLOW",0))
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 64<=before.st_size<=_MAX_MEDIA:
            return False
        digest=sha256()
        while True:
            buf=os.read(fd,1024*1024)
            if not buf:
                break
            digest.update(buf)
        after=os.fstat(fd)
        same=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        return digest.hexdigest()==expected and same(before)==same(after)
    except OSError:
        return False
    finally:
        if fd is not None:
            os.close(fd)


def qualify_reel_feedback(workspace:Path,studio_dir:Path,annotations_file:Path)->dict:
    root=Path(workspace).expanduser().resolve()
    studio=_safe_input(root,studio_dir)
    manifest,manifest_sha=_read_local(studio/"reel-manifest.json",10_000_000)
    if (manifest.get("version")!=STUDIO_VERSION or
            not isinstance(manifest.get("clips"),list) or
            not isinstance(manifest.get("reels"),list)):
        raise ValueError("Not a frozen interactive Klip Reel Studio manifest")
    annotated,annotations_sha=_read_local(
        Path(annotations_file).expanduser().resolve(),3_000_000)
    if (annotated.get("version")!=VERSION or
            annotated.get("studioManifestSha256")!=manifest_sha or
            not isinstance(annotated.get("notes"),list)
            or len(annotated["notes"])>1500):
        raise ValueError("Reactions are not attached to this exact frozen reel")
    by_id={}
    for clip in manifest["clips"]:
        rid=clip["clipId"]
        if not isinstance(rid,str) or not _ID.fullmatch(rid) or rid in by_id:
            raise ValueError("Malformed or duplicate clip IDs in frozen reel manifest")
        by_id[rid]=clip
    seen_notes=set()
    reviewed=set()
    types=Counter()
    per_clip=defaultdict(list)
    for note in annotated["notes"]:
        if not isinstance(note,dict):
            raise ValueError("Malformed timecoded note")
        required={"id","clipId","annotatorId","kind","startMs","endMs","text"}
        if set(note)!=required:
            raise ValueError("Timed-note schema mismatch")
        nid=note["id"]
        rid=note["clipId"]
        if (type(nid) is not str or not 1<=len(nid)<=100
                or nid in seen_notes or rid not in by_id):
            raise ValueError("Duplicate note or unknown source clip")
        seen_notes.add(nid)
        clip=by_id[rid]
        if (not clip.get("videoUrl")
                or type(note["annotatorId"]) is not str
                or not _REVIEWER.fullmatch(note["annotatorId"])
                or note["kind"] not in _TYPES):
            raise ValueError("Invalid note provenance/role/clip availability")
        a,b=note["startMs"],note["endMs"]
        if (type(a) is not int or type(b) is not int
                or not clip["startMs"]<=a<=b<clip["endMs"]):
            raise ValueError("Timed reaction outside the source interval")
        if (type(note["text"]) is not str or len(note["text"])>1200
                or "\x00" in note["text"]):
            raise ValueError("Invalid note text")
        per_clip[rid].append(dict(note))
        types[note["kind"]]+=1
        reviewed.add((rid,note["annotatorId"]))
    if not seen_notes:
        raise ValueError("No human timing notes to qualify")
    # Validate each used cached local derivative before accepting time links.
    for rid in per_clip:
        if not _verify_private_proxy(studio,by_id[rid]):
            raise ValueError("A reviewed local proxy is missing, changed or unverified")
    decisions=[]
    trims=[]
    positive_markers=defaultdict(list)
    for rid,notes in per_clip.items():
        clip=by_id[rid]
        votes=Counter(n["kind"] for n in notes)
        # Fast reaction markers do not constitute quality or outcome truth.
        proposed=[n for n in notes if n["kind"]=="retrim" and n["endMs"]>n["startMs"]]
        for marker in proposed:
            trims.append({
                "clipId":rid,
                "sourceAssetSha256":clip["sourceAssetSha256"],
                "sourceFamilyId":clip["sourceFamilyId"],
                "startMs":marker["startMs"],
                "endMs":marker["endMs"],
                "annotatorId":marker["annotatorId"],
                "rationale":marker["text"],
                "status":"human_proposed_requires_video_evidence",
                "originalSourceReadAuthorized":False,
            })
        for note in notes:
            if note["kind"] in {
                    "standout","athletic","camera","story","nature","keep",
                    "wow","skill","framing","personal"}:
                source_duration=clip.get("sourceDurationMsFromCatalog")
                if (type(source_duration) is int and
                        clip["endMs"]<=source_duration<=43_200_000):
                    positive_markers[clip["sourceAssetSha256"]].append({
                        "atMs":(note["startMs"]+note["endMs"])//2,
                        "sourceDurationMs":source_duration,
                        "sourceFamilyId":clip["sourceFamilyId"],
                        "clipId":rid,
                        "kind":note["kind"],
                        "annotatorId":note["annotatorId"],
                    })
        decisions.append({
            "clipId":rid,"sourceAssetSha256":clip["sourceAssetSha256"],
            "sourceFamilyId":clip["sourceFamilyId"],
            "originalInterval":[clip["startMs"],clip["endMs"]],
            "provenance":clip["provenance"],
            "humanApprovedBeforeNewReview":clip["humanApproved"],
            "reactionTypeCounts":dict(sorted(votes.items())),
            "humanNotes":notes,
            "newPreferenceStillRequiresReview":True,
            "eventAchievementVerified":False,
        })
    # Cluster nearby human time markers into bounded *discovery hypotheses*.
    # No automatically verified sporting event, action outcome or wildlife
    # claim is created; the source may not currently be accessible.
    discovery=[]
    for asset,markers in sorted(positive_markers.items()):
        markers.sort(key=lambda n:(n["atMs"],n["clipId"],n["kind"]))
        clusters=[]
        current=[]
        for marker in markers:
            if (current and (
                    marker["atMs"]-current[-1]["atMs"]>10000 or
                    marker["atMs"]-current[0]["atMs"]>36000)):
                clusters.append(current)
                current=[]
            current.append(marker)
        if current:
            clusters.append(current)
        for cluster in clusters:
            source_duration=cluster[0]["sourceDurationMs"]
            center_a=min(x["atMs"] for x in cluster)
            center_b=max(x["atMs"] for x in cluster)
            a=max(0,center_a-9000)
            b=min(source_duration,center_b+13000)
            if b-a>60000:
                b=a+60000
            if b-a<1000:
                continue
            discovery.append({
                "sourceAssetSha256":asset,
                "sourceFamilyId":cluster[0]["sourceFamilyId"],
                "startMs":a,"endMs":b,
                "humanMarkedSourceTimesMs":[x["atMs"] for x in cluster],
                "kinds":sorted({x["kind"] for x in cluster}),
                "clipIds":sorted({x["clipId"] for x in cluster}),
                "annotators":sorted({x["annotatorId"] for x in cluster}),
                "reason":"revisit_time_near_human_interest_marker",
                "evidenceStatus":"human_reaction_requires_video_verification",
                "sourceMediaReadAuthorized":False,
                "newPreviewPrepared":False,
                "humanHighlightApprovedAutomatically":False,
            })
    return {
        "version":_REPORT_VERSION,
        "frozenStudioManifestSha256":manifest_sha,
        "annotationsExportSha256":annotations_sha,
        "annotatedClips":len(per_clip),
        "uniqueClipReviewerPairs":len(reviewed),
        "humanMarkerCount":len(seen_notes),
        "types":dict(sorted(types.items())),
        "decisions":decisions,
        "humanProposedRetrims":trims,
        "humanInterestDiscoveryProposals":discovery,
        "sourceOriginalReads":0,"sourceOriginalDecodes":0,
        "modelInferenceCalls":0,"filesDeleted":0,
        "eventGroundTruthEstablished":False,
        "trainingRightsVerified":False,
        "recommendation":"Use timestamped markers to rescan relevant source context, "
                         "and compare proposed boundaries against independent "
                         "human judgments before producing full-resolution reels.",
    }


def save_reel_feedback(workspace:Path,studio_dir:Path,
                       annotations_file:Path,output_dir:Path)->dict:
    root=Path(workspace).expanduser().resolve()
    dest=_safe_dest(root,output_dir)
    report=qualify_reel_feedback(root,studio_dir,annotations_file)
    dest.mkdir(parents=True,mode=0o700,exist_ok=False)
    for name,obj in (
            ("timed-feedback.json",report),
            ("retrim-proposals.json",{
                "version":_REPORT_VERSION,
                "studioManifestSha256":report["frozenStudioManifestSha256"],
                "proposals":report["humanProposedRetrims"],
                "sourceReadsAuthorized":False,
                "filesModified":False,
            }),
            ("source-interest-proposals.json",{
                "version":_REPORT_VERSION,
                "studioManifestSha256":report["frozenStudioManifestSha256"],
                "proposals":report["humanInterestDiscoveryProposals"],
                "originalReadsAuthorized":False,
                "mediaVerified":False,
            })):
        with (dest/name).open("x",encoding="utf-8") as handle:
            json.dump(obj,handle,sort_keys=True,indent=2,allow_nan=False)
            handle.write("\n")
    return {
        "status":"timed_reel_feedback_qualified",
        "report":str(dest/"timed-feedback.json"),
        "retrims":str(dest/"retrim-proposals.json"),
        "interestWindows":str(dest/"source-interest-proposals.json"),
        "annotatedClips":report["annotatedClips"],
        "annotations":report["humanMarkerCount"],
        "proposedRetrims":len(report["humanProposedRetrims"]),
        "interestWindowProposals":len(report["humanInterestDiscoveryProposals"]),
        "sourceOriginalReads":0,"modelInferenceCalls":0,
        "modelTrainingAuthorized":False,
    }
