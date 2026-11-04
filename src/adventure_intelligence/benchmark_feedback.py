"""Local-only reviewed interval ledger and honest candidate/control evaluation.

Append-only human revisions are stored in the existing workspace SQLite.
No original video changes, decode, source hashes, model inference, fabricated
labels or automatic clip acceptance. Read-only summaries distinguish assessed
from unassessed observations and do not claim archive-wide event recall.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing
from hashlib import sha256
from pathlib import Path
import json
import math
import secrets

from .archive_review import CRITERIA,VERSION as BENCHMARK_VERSION
from .archive_pilot import _open_catalog
from .catalog import connect

VERSION="archive-feedback-ledger-v1"
ROLES={"candidate_hypothesis","control_systematic"}
ACTIVITIES={"snorkeling","ski","bike","other"}
DECISIONS={"keep","skip","uncertain"}
MAX_ASSIGNMENTS=1000


def _canonical(data)->str:
    return json.dumps(data,sort_keys=True,separators=(",",":"),allow_nan=False)


def load_benchmark(workspace:Path,filename:Path)->dict:
    root=Path(workspace).expanduser().resolve()
    path=Path(filename).expanduser().resolve()
    if (not path.is_relative_to(root) or path==root or
            path.relative_to(root).parts[0] not in ("reports","exports")
            or path.name!="assignments.json"):
        raise ValueError("Benchmark must be an assignments.json under workspace reports/ or exports/")
    if not path.is_file() or path.stat().st_size>20_000_000:
        raise ValueError("Missing or oversized benchmark assignments")
    raw=path.read_bytes()
    document=json.loads(raw)
    if (not isinstance(document,dict) or
            document.get("version")!=BENCHMARK_VERSION or
            document.get("schemaVersion")!=1 or
            not isinstance(document.get("assignments"),list) or
            len(document["assignments"])>MAX_ASSIGNMENTS):
        raise ValueError("Invalid versioned archive benchmark")
    mapping={}
    for row in document["assignments"]:
        if not isinstance(row,dict):
            raise ValueError("Invalid review assignment")
        ident,aid=row.get("reviewId"),row.get("assetId")
        start,end=row.get("startMs"),row.get("endMs")
        if (not isinstance(ident,str) or len(ident)!=20 or
                any(c not in "0123456789abcdef" for c in ident) or
                ident in mapping or not isinstance(aid,str) or len(aid)!=64 or
                any(c not in "0123456789abcdef" for c in aid) or
                type(start) is not int or type(end) is not int or
                not 0<=start<end or end-start>120_000 or
                row.get("role") not in ROLES or
                row.get("selectionGroup") not in ("snorkeling","ski","bike","unresolved")):
            raise ValueError("Invalid or duplicate benchmark interval")
        expected=sha256((
            f'{BENCHMARK_VERSION}:{aid}:{row["role"]}:{start}:{end}'
        ).encode()).hexdigest()[:20]
        if ident!=expected:
            raise ValueError("Benchmark interval identity differs from source-backed assignment")
        mapping[ident]=row
    if document.get("reviewIntervalCount")!=len(mapping):
        raise ValueError("Review count differs from benchmark manifest")
    return {
        "benchmarkId":sha256(raw).hexdigest(),
        "path":str(path),
        "assignments":mapping,
        "version":VERSION,
    }


def validate_human_review(benchmark:dict,payload:dict)->dict:
    if not isinstance(payload,dict):
        raise ValueError("Human review must be a JSON object")
    rid=payload.get("reviewId")
    assignment=benchmark["assignments"].get(rid)
    if assignment is None:
        raise ValueError("Review ID absent from this frozen benchmark")
    decision=payload.get("reviewDecision")
    if decision not in (None,*DECISIONS):
        raise ValueError("Decision must be keep, skip, uncertain or null")
    actual=payload.get("observedEvent")
    if actual is not None and type(actual) is not bool:
        raise ValueError("Observed event must be true, false or null")
    activity=payload.get("observedActivity")
    if activity is not None and activity not in ACTIVITIES:
        raise ValueError("Unsupported observed activity")
    event_kind=payload.get("eventKind")
    if event_kind is not None and (not isinstance(event_kind,str) or len(event_kind)>200):
        raise ValueError("Event kind must be a short string or null")
    notes=payload.get("notes","")
    if not isinstance(notes,str) or len(notes)>2000:
        raise ValueError("Notes must be a string of at most 2000 characters")
    values=payload.get("ratings",{})
    if not isinstance(values,dict) or set(values)-set(CRITERIA):
        raise ValueError("Invalid review rating fields")
    numeric={}
    for key in CRITERIA:
        val=values.get(key)
        if val is not None and (type(val) not in (float,int)
                                or not math.isfinite(val) or not 0<=val<=1):
            raise ValueError(f"{key} must be null or a finite number between 0 and 1")
        numeric[key]=float(val) if val is not None else None
    if not (decision is not None or actual is not None or
            any(v is not None for v in numeric.values())):
        raise ValueError("An empty review cannot be saved as completed evidence")
    # Bare claims of rare taxa are not verification; keep the benchmark's
    # species field unverified unless a future curated taxonomy workflow
    # supplies independent provenance.
    if payload.get("rareTaxonVerified") not in (None,False):
        raise ValueError("Species verification requires an independent curated review")
    if payload.get("taxonReviewSource") not in (None,""):
        raise ValueError("Taxon review source is unsupported in this review form")
    boundary=payload.get("eventBoundaryAdjustmentMs")
    if boundary is not None and (type(boundary) is not int or abs(boundary)>10000):
        raise ValueError("Boundary adjustment must be null or within +/-10 seconds")
    return {
        "reviewId":rid,
        "reviewDecision":decision,
        "observedActivity":activity,
        "observedEvent":actual,
        "eventKind":event_kind or None,
        "notes":notes,
        "ratings":numeric,
        "rareTaxonVerified":False,
        "taxonReviewSource":None,
        "eventBoundaryAdjustmentMs":boundary,
        "sourceInterval":{"assetId":assignment["assetId"],
                          "startMs":assignment["startMs"],
                          "endMs":assignment["endMs"]},
        "reviewSource":"local_user_direct",
    }


def save_human_review(workspace:Path,benchmark_file:Path,payload:dict)->dict:
    benchmark=load_benchmark(workspace,benchmark_file)
    normalized=validate_human_review(benchmark,payload)
    rid=normalized["reviewId"]
    assignment=benchmark["assignments"][rid]
    with connect(Path(workspace).expanduser().resolve()) as db:
        asset=db.execute("SELECT id,duration FROM assets WHERE id=?",
                         (assignment["assetId"],)).fetchone()
        if asset is None or asset["duration"] is None or \
                assignment["endMs"]>round(float(asset["duration"])*1000)+1:
            raise ValueError("Benchmark interval no longer matches an indexed source")
        last=db.execute(
            "SELECT revision_id,payload_json FROM benchmark_review_revisions "
            "WHERE benchmark_id=? AND review_id=? ORDER BY rowid DESC LIMIT 1",
            (benchmark["benchmarkId"],rid)).fetchone()
        doc=_canonical(normalized)
        if last is not None and last["payload_json"]==doc:
            return {"status":"unchanged","reviewId":rid,
                    "revisionId":last["revision_id"],
                    "benchmarkId":benchmark["benchmarkId"]}
        rev=secrets.token_hex(16)
        db.execute(
            "INSERT INTO benchmark_review_revisions "
            "(revision_id,benchmark_id,review_id,asset_id,start_ms,end_ms,"
            "role,payload_json) VALUES (?,?,?,?,?,?,?,?)",
            (rev,benchmark["benchmarkId"],rid,assignment["assetId"],
             assignment["startMs"],assignment["endMs"],assignment["role"],doc))
    return {"status":"saved","reviewId":rid,
            "revisionId":rev,"benchmarkId":benchmark["benchmarkId"],
            "appendOnly":True,"sourceMediaModified":False}


def benchmark_feedback_status(workspace:Path,benchmark_file:Path)->dict:
    benchmark=load_benchmark(workspace,benchmark_file)
    ids=benchmark["assignments"]
    latest={}
    with closing(_open_catalog(Path(workspace).expanduser().resolve())) as db:
        exists=db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='benchmark_review_revisions'").fetchone()
        if exists:
            for row in db.execute(
                "SELECT review_id,revision_id,payload_json,created_at FROM "
                "benchmark_review_revisions WHERE benchmark_id=? ORDER BY rowid",
                (benchmark["benchmarkId"],)):
                if row["review_id"] in ids:
                    latest[row["review_id"]]={
                        "revisionId":row["revision_id"],
                        "updatedAt":row["created_at"],
                        **json.loads(row["payload_json"])}
    groups=defaultdict(lambda:defaultdict(lambda:{
        "assigned":0,"eventYes":0,"eventNo":0,"eventUnknown":0,
        "keep":0,"skip":0,"undecided":0,"rated":0}))
    potential_misses=[]
    criterion_totals=defaultdict(lambda:{"sum":0.,"count":0})
    for rid,a in ids.items():
        group=groups[a["selectionGroup"]][a["role"]]
        group["assigned"]+=1
        r=latest.get(rid)
        if r is None:
            group["undecided"]+=1
            group["eventUnknown"]+=1
            continue
        if r["observedEvent"] is True:
            group["eventYes"]+=1
        elif r["observedEvent"] is False:
            group["eventNo"]+=1
        else:
            group["eventUnknown"]+=1
        if r["reviewDecision"]=="keep":
            group["keep"]+=1
        elif r["reviewDecision"]=="skip":
            group["skip"]+=1
        else:
            group["undecided"]+=1
        if any(v is not None for v in r["ratings"].values()):
            group["rated"]+=1
        for name,value in r["ratings"].items():
            if value is not None:
                criterion_totals[name]["sum"]+=value
                criterion_totals[name]["count"]+=1
        if (a["role"]=="control_systematic" and
                (r["observedEvent"] is True or r["reviewDecision"]=="keep")):
            potential_misses.append({
                "reviewId":rid,"assetId":a["assetId"],
                "filename":a.get("basename"),
                "startMs":a["startMs"],"endMs":a["endMs"],
                "selectionGroup":a["selectionGroup"],
                "observedEvent":r["observedEvent"],
                "decision":r["reviewDecision"],
            })
    summaries={}
    for sport,roles in sorted(groups.items()):
        summaries[sport]={}
        for role,counts in sorted(roles.items()):
            assessed=counts["eventYes"]+counts["eventNo"]
            decisions=counts["keep"]+counts["skip"]
            summaries[sport][role]={
                **counts,
                "observedEventRate":round(counts["eventYes"]/assessed,4)
                                  if assessed else None,
                "keepRate":round(counts["keep"]/decisions,4)
                           if decisions else None,
                "eventAssessedCount":assessed,
                "decisionAssessedCount":decisions,
            }
    means={
        name:{"assessed":v["count"],
              "mean":round(v["sum"]/v["count"],4) if v["count"] else None}
        for name,v in sorted(criterion_totals.items())
    }
    return {
        "schemaVersion":1,"version":VERSION,
        "benchmarkId":benchmark["benchmarkId"],
        "benchmarkPath":benchmark["path"],
        "assignments":len(ids),"reviewedAssignments":len(latest),
        "unreviewedAssignments":len(ids)-len(latest),
        "bySportAndRole":summaries,
        "criterionMeans":means,
        "potentiallyMissedControlMoments":potential_misses,
        "reviewEntries":latest,
        "estimatedWholeArchiveRecall":None,
        "estimatedWholeArchivePrecision":None,
        "limitations":[
            "Candidate and systematic control samples are deterministic and non-random.",
            "Candidate/control event rates cannot estimate whole-library recall.",
            "A reviewed control with a useful event is a potential blind spot, not a measured false negative.",
            "Missing judgments are abstentions, never true negatives.",
            "Keep/skip is personal preference, not biological identity or verified action truth.",
            "No numeric rating is created by the system from histogram evidence.",
            "Sessions/days must be grouped before training or held-out generalization claims.",
        ],
        "sourceVideosDecoded":0,
        "newModelInferenceCalls":0,
    }


def write_benchmark_feedback(workspace:Path,benchmark_file:Path,output_dir:Path)->dict:
    root=Path(workspace).expanduser().resolve()
    dest=Path(output_dir).expanduser().resolve()
    if (dest==root or not dest.is_relative_to(root) or
        dest.relative_to(root).parts[0] not in ("reports","exports") or dest.exists()):
        raise ValueError("Output must be a new reports/ or exports/ folder")
    report=benchmark_feedback_status(root,benchmark_file)
    dest.mkdir(parents=True,mode=0o700,exist_ok=False)
    with (dest/"evaluation.json").open("x",encoding="utf-8") as f:
        f.write(json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+"\n")
    html=[
        '<!doctype html><html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<title>Klip · Archive benchmark evaluation</title>',
        '<style>body{font:14px system-ui;background:#101923;color:#eff2f5;',
        'max-width:1100px;margin:36px auto;padding:0 20px;line-height:1.5}',
        'h1{font-size:1.6rem}h2{font-size:1.1rem;color:#f1bcad}',
        'table{border-collapse:collapse;width:100%}td,th{padding:12px;',
        'border-bottom:1px solid #344654;text-align:left}',
        'p{color:#b8c6ce}small{color:#b0bbc4}</style></head><body>',
        '<h1>Benchmark review · evidence first</h1>',
        f'<p>{report["reviewedAssignments"]} of {report["assignments"]} assignments reviewed. ',
        'Unassessed is not a negative; no full-library recall can be computed.</p>',
        '<table><thead><tr><th>Sport</th><th>Review type</th>',
        '<th>Event observed</th><th>Keep</th><th>Review coverage</th></tr></thead><tbody>',
    ]
    from html import escape
    for sport,roles in report["bySportAndRole"].items():
        for role,c in roles.items():
            html.append(
                '<tr><td>'+escape(sport)+'</td><td>'+escape(role)+'</td>'
                f'<td>{c["eventYes"]}/{c["eventAssessedCount"]}</td>'
                f'<td>{c["keep"]}/{c["decisionAssessedCount"]}</td>'
                f'<td>{c["assigned"]-c["eventUnknown"]}/{c["assigned"]}'
                '</td></tr>')
    html.append('</tbody></table><h2>Unexpectedly valuable controls</h2>')
    html.append('<p>These were nominated for review by a systematic coverage '
                'probe, rather than the candidate selection policy.</p>')
    for item in report["potentiallyMissedControlMoments"]:
        html.append('<p>'+escape(item.get("filename") or item["assetId"])+
                    f' · {item["startMs"]/1000:.1f}–{item["endMs"]/1000:.1f}s</p>')
    html.append('</body></html>')
    with (dest/"index.html").open("x",encoding="utf-8") as f:
        f.write("".join(html))
    return {"status":"evaluated_human_reviews","reviewed":report["reviewedAssignments"],
            "assignments":report["assignments"],
            "interestingControls":len(report["potentiallyMissedControlMoments"]),
            "json":str(dest/"evaluation.json"),"html":str(dest/"index.html"),
            "sourceVideosDecoded":0}
