"""Convert human review exports into a source-safe next-discovery plan.

Critical distinction: keep/skip is a judgement on a *window under a goal*.
It is NEVER a label for the entire source recording, event nonexistence or
legal authorization to delete. Text-matched cues create explicit, reviewable
hypotheses, never trained semantic classifications.

This stage reads only frozen workspace reports and human JSON. It never
stats, opens, hashes, decodes or edits an original camera video.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
from html import escape
from pathlib import Path
import json
import re

from .highlight_curation_pilot import (
    VERSION as PILOT_VERSION, MANIFEST_VERSION, _safe_dest
)
from .highlight_curation_review import (
    REVIEW_VERSION, _read_json, _review
)
from .highlight_curation_validate import audit_highlight_curation_manifest

VERSION="klip-highlight-review-discovery-plan-v1"
_ROUTE={
    "keep":"provisional_highlight",
    "skip":"exclude_this_window",
    "neither":"undecided",
    "cannot_tell":"insufficient_observation",
}
# These are explicit human-text keyword cues. They are not event detectors.
_PRESERVE=re.compile(
    r"\b(worth saving|worth keeping|save (?:the |this )?(?:video|recording)|"
    r"rest of (?:the )?(?:complete )?run|trip summary|family|personal memory)\b",re.I)
_EXTEND=re.compile(
    r"\b(rest of (?:the )?(?:complete )?run|whole run|complete run|"
    r"start of (?:a |the )?(?:steeper|run|ski)|beginning of (?:a |the )?run|"
    r"rest of (?:the |this )?video|after this clip)\b",re.I)
_DELETION=re.compile(r"\b(delete|trash|erase)\b",re.I)
_VISUAL=re.compile(r"\b(blurry|blur|hazy|dark|deep water|bad visibility|"
                   r"poor visibility|too far|out of focus)\b",re.I)


def _safe_input(root:Path,path:Path)->Path:
    p=Path(path).expanduser().resolve()
    if (p==root or not p.is_relative_to(root) or
            p.relative_to(root).parts[0] not in ("reports","exports")):
        raise ValueError("Pilot reports must remain in the Klip workspace")
    return p


def _sha_bytes(path:Path,max_bytes:int=10_000_000)->tuple[dict,str]:
    if not path.is_file() or path.stat().st_size>max_bytes:
        raise ValueError("Frozen review file missing or exceeds size limit")
    b=path.read_bytes()
    try:
        doc=json.loads(b)
    except (UnicodeDecodeError,ValueError) as exc:
        raise ValueError("Invalid frozen JSON artifact") from exc
    if not isinstance(doc,dict):
        raise ValueError("Expected frozen report JSON object")
    return doc,sha256(b).hexdigest()


def _clip_context(source:dict, start:int, end:int, route:str,
                  explicit_full_run:bool,
                  seek_clearer_alternative:bool=False)->list[dict]:
    """Metadata-only bounded nearby intervals, not event detections."""
    duration=source["sourceDurationMs"]
    proposals=[]
    seen={(start,end)}
    def suggest(a,b,why):
        if not 0<=a<b<=duration or b-a<1000 or b-a>60000:
            return
        pair=(a,b)
        if pair in seen:
            return
        seen.add(pair)
        proposals.append({
            "startMs":a,"endMs":b,
            "kind":"candidate_not_verified",
            "reason":why,
            "sourceOriginalReadAuthorized":False,
            "modelObservationPerformed":False,
        })
    # Broaden context for positive events without assuming a correct peak.
    if route=="provisional_highlight":
        extra=12000
        left=max(0,start-extra)
        right=min(duration,end+extra)
        if right-left>60000:
            right=min(duration,left+60000)
        suggest(left,right,"check_approach_peak_and_aftermath")
    # A quality-rejected but explicitly valuable nature/action event may
    # have a better, clearer neighboring view. These are only proposals.
    if seek_clearer_alternative:
        suggest(max(0,start-25000),min(duration,start+4000),
                "search_prior_clearer_event_view")
        suggest(max(0,end-4000),min(duration,end+25000),
                "search_following_clearer_event_view")
    # A start-of-run complaint deserves forward exploration, not training
    # a "boring" label against the rest of that run.
    if explicit_full_run:
        cursor=end
        for _ in range(3):
            a=max(0,cursor-5000)
            b=min(duration,cursor+25000)
            suggest(a,b,"inspect_downstream_run_continuation")
            cursor+=25000
        suggest(max(0,start-30000),min(duration,start+5000),
                "inspect_context_before_run_start")
    return proposals[:5]


def _signals(note:str)->dict:
    return {
        "explicitPreserveLanguage":bool(_PRESERVE.search(note)),
        "explicitRunContinuation":bool(_EXTEND.search(note)),
        "visualLimitationMentioned":bool(_VISUAL.search(note)),
        "deleteLanguagePresent":bool(_DELETION.search(note)),
        "allAreTextCuesNeedingHumanConfirmation":True,
    }


def plan_review_discovery(workspace:Path,pilot_dir:Path,
                          reviews_files:list[Path])->dict:
    root=Path(workspace).expanduser().resolve()
    pilot=_safe_input(root,pilot_dir)
    if not isinstance(reviews_files,list) or not 1<=len(reviews_files)<=25:
        raise ValueError("Provide 1..25 review JSON exports")
    manifest,manifest_sha=_sha_bytes(pilot/"curation-manifest.json")
    if (manifest.get("version")!=MANIFEST_VERSION or
            not isinstance(manifest.get("records"),list)):
        raise ValueError("Expected frozen curation pilot manifest")
    # Structural audit does not independently approve training or labels.
    audited=audit_highlight_curation_manifest(pilot/"curation-manifest.json")
    presentations,_=_sha_bytes(pilot/"presentation.json",5_000_000)
    selection,_=_sha_bytes(pilot/"selection-report.json",5_000_000)
    if (presentations.get("version")!=PILOT_VERSION or
            presentations.get("manifestSha256")!=manifest_sha or
            not isinstance(presentations.get("records"),dict) or
            selection.get("version")!=PILOT_VERSION or
            selection.get("manifestSha256")!=manifest_sha or
            not isinstance(selection.get("selections"),list)):
        raise ValueError("Pilot metadata and frozen manifest fingerprints do not match")
    by_id={row["recordId"]:row for row in manifest["records"]}
    if (len(by_id)!=len(manifest["records"]) or
            set(by_id)!=set(presentations["records"])):
        raise ValueError("Pilot record identities are missing or duplicated")
    selection_keys={row["recordId"] for row in selection["selections"]}
    if selection_keys!=set(by_id):
        raise ValueError("Pilot source-selection roster does not match manifest")
    reviews=defaultdict(list)
    seen=set()
    digest_files=[]
    for source in reviews_files:
        doc,digest=_sha_bytes(Path(source).expanduser().resolve(),2_000_000)
        if (doc.get("version")!=REVIEW_VERSION or
                doc.get("manifestSha256")!=manifest_sha or
                not isinstance(doc.get("reviews"),list) or
                len(doc["reviews"])>len(by_id)):
            raise ValueError("Review export not from this frozen pilot")
        digest_files.append(digest)
        for row in doc["reviews"]:
            rid=row.get("recordId") if isinstance(row,dict) else None
            if rid not in by_id or not presentations["records"][rid].get("verifiedProxyReady"):
                raise ValueError("Review refers to unavailable/nonexistent pilot clip")
            # Read ONLY the report-local derivative, never the source SD card.
            # A stale/modified proxy cannot ground a human review.
            entry=presentations["records"][rid]
            expected=entry.get("proxySha256")
            proxy=pilot/"media"/(rid+".mp4")
            if (not isinstance(expected,str) or
                    not re.fullmatch(r"[a-f0-9]{64}",expected)
                    or not proxy.is_file() or not 64<=proxy.stat().st_size<=45*1024*1024
                    or sha256(proxy.read_bytes()).hexdigest()!=expected):
                raise ValueError(
                    "Frozen reviewer derivative missing/altered: cannot link unverified clip")
            checked=_review(row,by_id[rid],True)
            key=(rid,checked["annotatorId"])
            if key in seen:
                raise ValueError("Duplicate reviewer/interval across export files")
            seen.add(key)
            reviews[rid].append(checked)
    if not seen:
        raise ValueError("No submitted observations in review export")
    decisions=[]
    followups=[]
    choices=Counter()
    routes=Counter()
    preservation=Counter()
    unreviewed=[]
    for item in manifest["records"]:
        rid=item["recordId"]
        submitted=reviews.get(rid,[])
        if not submitted:
            unreviewed.append(rid)
            continue
        votes=Counter(r["preference"] for r in submitted)
        choices.update(votes)
        if len(votes)==1:
            route=_ROUTE[next(iter(votes))]
        else:
            route="human_disagreement"
        comments=[r["rationale"] for r in submitted]
        cues=[_signals(note) for note in comments]
        any_cue=lambda key:any(c[key] for c in cues)
        keep=(route=="provisional_highlight")
        preserve=(keep or any_cue("explicitPreserveLanguage"))
        extended=any_cue("explicitRunContinuation")
        limited=any_cue("visualLimitationMentioned")
        delete=any_cue("deleteLanguagePresent")
        # Never interpret DELETE in comments as authorization to change files.
        action=["human_confirm_highlight_in_context" if keep else
                "review_disagreement" if route=="human_disagreement" else
                "exclude_exact_reviewed_window_only" if
                route=="exclude_this_window" else
                "collect_more_visual_evidence_before_deciding"]
        if preserve and not keep:
            action.append("preserve_original_seek_better_window")
        if limited and not keep:
            action.append("inspect_alternate_clearer_source_window")
        if extended:
            action.append("inspect_longer_same_source_sequence")
        if delete:
            action.append("manual_deletion_request_hold_no_file_operation")
        start,end=item["event"]["startMs"],item["event"]["endMs"]
        source=item["source"]
        context=_clip_context(source,start,end,route,extended,
                              seek_clearer_alternative=(preserve and limited
                                                        and not keep))
        if preserve:
            preservation["source_preservation_suggested"]+=1
        if delete:
            preservation["manual_deletion_language_only"]+=1
        routes[route]+=1
        decisions.append({
            "recordId":rid,
            "sourceAssetSha256":source["assetSha256"],
            "sourceFamilyId":source["sourceFamilyId"],
            "sourceDurationMsFromCatalog":source["sourceDurationMs"],
            "startMs":start,"endMs":end,
            "activityFromCatalog":item["sportContext"]["sport"],
            "route":route,
            "voteCounts":dict(sorted(votes.items())),
            "observerGoals":[r["highlightGoal"] for r in submitted],
            "observerRoles":[r["role"] for r in submitted],
            "ratingsByReviewer":[{
                "annotatorId":r["annotatorId"],
                "technicalQuality":r.get("technicalQuality"),
                "aestheticQuality":r.get("aestheticQuality"),
                "eventReadability":r.get("eventReadability"),
                "observedOutcome":r.get("observedOutcome"),
                "outcomeVisible":r.get("outcomeVisible"),
                "eventPeakMs":r.get("eventPeakMs"),
                "resolutionMs":r.get("resolutionMs"),
                "rationale":r["rationale"],
            } for r in submitted],
            "textCues":cues,
            "preserveSourceSuggestedNotExecuted":preserve,
            "deleteRequestReviewOnlyNeverExecuted":delete,
            "followUpActions":action,
            "contextSearchProposals":context,
            "contextFootageVerified":False,
            "sourceMediaOpened":False,
            "groundTruthEventAdjudicated":False,
            "trainingExampleApproved":False,
        })
        for proposal in context:
            followups.append({
                "parentRecordId":rid,
                "sourceAssetSha256":source["assetSha256"],
                "sourceFamilyId":source["sourceFamilyId"],
                "activityFromCatalog":item["sportContext"]["sport"],
                **proposal,
            })
    assert len(decisions)==len(reviews)
    return {
        "version":VERSION,
        "frozenPilotManifestSha256":manifest_sha,
        "reviewExportSha256":digest_files,
        "reviewedWindows":len(decisions),
        "reviewedSourceCount":len({x["sourceAssetSha256"] for x in decisions}),
        "unreviewedPilotWindows":len(unreviewed),
        "unreviewedRecordIds":unreviewed,
        "choices":dict(sorted(choices.items())),
        "routingCounts":dict(sorted(routes.items())),
        "preservationIndicators":dict(sorted(preservation.items())),
        "provisionalHighlightCount":routes["provisional_highlight"],
        "sourceContextProposals":followups,
        "decisions":decisions,
        "sourceVideosRead":0,
        "newPreviewDecodes":0,
        "modelInferences":0,
        "filesDeleted":0,
        "eventGroundTruthEstablished":False,
        "modelTrainingAuthorized":False,
        "humanReviewSource":"uploaded_independent_observer_exports",
        "pilotPreflightRecords":audited["records"],
        "limits":[
            "One reviewer is a preference signal, not an adjudicated universal ranking.",
            "Review comments are human source descriptions; wildlife species and sport techniques have not been independently verified.",
            "Explicit text pattern cues generate proposals, not demonstrated context or source preservation.",
            "A skip labels only the exact reviewed window and goal, never the full source.",
            "An original-source deletion note is retained as a flag only, not an executable operation.",
            "Context intervals are metadata-only probes with NO current preview or media verification.",
            "Source IDs/timecodes require the frozen local pilot manifest; a detached review export cannot resolve recordings.",
            "Unrepresented archive sources still require a separate source-safe discovery audit.",
        ],
    }


def _html(report:dict)->str:
    groups=defaultdict(list)
    for row in report["decisions"]:
        groups[row["route"]].append(row)
    sections=[]
    for title,code in (("Provisional highlights","provisional_highlight"),
                       ("Conflicting opinions","human_disagreement"),
                       ("Reject this cut, not the source","exclude_this_window"),
                       ("Needs evidence","insufficient_observation"),
                       ("Undecided","undecided")):
        rows=groups.get(code,[])
        if not rows:
            continue
        sections.append("<section><h2>"+escape(title)+"</h2>")
        for item in rows:
            goals=", ".join(item["observerGoals"])
            actions=", ".join(item["followUpActions"])
            comments=" / ".join(escape(x["rationale"][:800])
                               for x in item["ratingsByReviewer"])
            sections.append(
                "<article><strong>"+escape(item["recordId"])+
                "</strong><p>"+escape(item["activityFromCatalog"])+
                " · "+escape(item["sourceAssetSha256"][:12])+
                " · "+str(item["startMs"])+".."+str(item["endMs"])+
                " ms</p><p>Goal: "+escape(goals)+
                "</p><p>Next: "+escape(actions)+
                "</p><blockquote>"+comments+"</blockquote></article>")
        sections.append("</section>")
    html="""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none';style-src 'unsafe-inline';base-uri 'none'">
<title>Klip | Human Highlight Feedback</title><style>
:root{color-scheme:dark}body{background:#111c2a;color:#eef2f6;font:15px/1.55 system-ui;margin:auto;max-width:950px;padding:32px}
small,p{color:#b9c6d4}section{margin-top:30px}article{background:#1a293a;border:1px solid #304257;border-radius:12px;padding:16px;margin:12px 0}
blockquote{border-left:3px solid #efa48e;margin:12px 0;padding-left:14px;color:#f1e3de}
h1{font-size:24px}h2{font-size:17px}.meta{color:#efa48e}</style></head><body>
<h1>Human highlight feedback</h1><p class="meta">No source video read, no file deletion, no model training</p>
<p>Window-level review interpretations only. Source hashes and timestamps are from the frozen local manifest.</p>"""
    return html+"\n".join(sections)+"</body></html>"


def save_review_discovery(workspace:Path,pilot_dir:Path,
                          reviews_files:list[Path],output_dir:Path)->dict:
    root=Path(workspace).expanduser().resolve()
    dest=_safe_dest(root,output_dir)
    report=plan_review_discovery(root,pilot_dir,reviews_files)
    dest.mkdir(parents=True,mode=0o700,exist_ok=False)
    with (dest/"review-discovery-plan.json").open("x",encoding="utf-8") as fp:
        json.dump(report,fp,indent=2,sort_keys=True,allow_nan=False)
        fp.write("\n")
    with (dest/"index.html").open("x",encoding="utf-8") as fp:
        fp.write(_html(report))
    return {
        "status":"review_discovery_plan_created",
        "report":str(dest/"review-discovery-plan.json"),
        "html":str(dest/"index.html"),
        "reviewedWindows":report["reviewedWindows"],
        "provisionalHighlights":report["provisionalHighlightCount"],
        "contextProposals":len(report["sourceContextProposals"]),
        "manualDeletionRequests":report["preservationIndicators"].get(
            "manual_deletion_language_only",0),
        "sourceVideoReads":0,
        "filesDeleted":0,
        "modelTrainingAuthorized":False,
    }
