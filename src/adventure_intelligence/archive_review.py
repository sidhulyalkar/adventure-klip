"""Review-first benchmark seeding from frozen archive plans and cached evidence.

Every interval is a *review assignment*, not a detected clip. Include
candidate-like and systematic control intervals, including failed and
unreviewed footage, to measure what Klip misses. No model calls, frame
decoding, source video opening, source hashing, or edits to the catalog.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
from hashlib import sha256
from html import escape
from pathlib import Path
from urllib.parse import quote
import json

from .archive_pilot import _open_catalog
from .archive_status import inspect_archive_campaign, _read_json
from .adaptive_sampling import validate_plan

VERSION="archive-review-benchmark-v1"
CRITERIA={
    "eventSignificance":"How meaningful was the actual witnessed action or encounter?",
    "subjectVisibility":"Could a viewer follow the key subject throughout the moment?",
    "ambientVisibility":"Scene clarity, including water turbidity, weather or darkness. Distinct from subject visibility.",
    "technicalExecution":"Quality of observable sport execution, not inferred from camera shake or terrain alone.",
    "outcomeEvidence":"Does continuous footage show completion, recovery, or meaningful aftermath?",
    "narrativeCompleteness":"Does the window preserve approach, main event and outcome?",
    "visualQuality":"Sharpness, exposure, composition and stability, apart from event importance.",
    "archiveNovelty":"Is this visually or behaviorally different from this person's other recordings?",
    "behavioralNovelty":"Unusual observable animal behavior or athletic action, not a species-rarity claim.",
    "learningValue":"Would reviewing this help improve technique, observation or filming?",
    "sharingValue":"Would another person understand and enjoy the complete event?",
    "personalMeaning":"Personal significance independent of audience preferences.",
}
SPORT_QUESTIONS={
    "snorkeling":[
        "Is the camera above water, submerged above sand, near reef, or transitioning?",
        "What organism or habitat is observable, without guessing species?",
        "Does local subject contrast/visibility outweigh murky ambient water?",
        "Do approach and departure add information beyond a still image?",
        "Is the encounter novel in behavior, composition or viewpoint?",
    ],
    "ski":[
        "Is this a lift, traverse, descent, turn sequence or stationary view?",
        "Which movements were actually completed rather than merely suggested by terrain?",
        "Are the line entry, turn rhythm, obstacle and exit visible?",
        "Does the moment demonstrate reviewable technique, a learning opportunity or a scenic highlight?",
    ],
    "bike":[
        "Is this climbing, transfer, flowing trail, technical feature or stationary?",
        "Is there visible approach, feature execution and recovery?",
        "Does camera movement correspond to rider motion or vibration?",
        "Is this sequence distinct from repeated trail footage?",
    ],
    "unresolved":[
        "Which activity and scene actually appear in this source interval?",
        "Why might the earlier activity model have abstained?",
        "Is there any meaningful event that the sampler overlooked?",
    ],
}
NONCANDIDATE={"coverage_probe","deterministic_exploration_probe"}


def _window(t:int,duration:int,half_ms:int)->tuple[int,int]:
    # Clamp boundaries and preserve a useful window even for short recordings.
    start=max(0,t-half_ms)
    end=min(duration,t+half_ms)
    if end<=start:
        end=min(duration,start+1000)
    return start,end


def _choose_windows(asset:dict,*,half_ms:int=7000)->list[dict]:
    frames=asset["frames"]
    duration=asset["durationMs"]
    if type(duration) is not int or duration<=0 or not frames:
        return []
    scored=[x for x in frames if set(x["reasons"])-NONCANDIDATE]
    controls=[x for x in frames if "coverage_probe" in x["reasons"]]
    # Candidate-like timestamps are a detector/scheduler hypothesis, not a
    # human-reviewed event. If none exist, use a center-ish systematic frame,
    # but mark the candidate as an unverified *exploration assignment*.
    candidate=(sorted(scored,key=lambda x:(-len(set(x["reasons"])-NONCANDIDATE),
                                            x["tMs"]))[0]
               if scored else min(frames,key=lambda x:(abs(x["tMs"]-duration//2),x["tMs"])))
    control_pool=controls if controls else frames
    control=max(control_pool,key=lambda x:(abs(x["tMs"]-candidate["tMs"]),-x["tMs"]))
    out=[]
    for role,item in (("candidate_hypothesis",candidate),("control_systematic",control)):
        start,end=_window(item["tMs"],duration,half_ms)
        if role=="control_systematic" and out and \
                max(0,min(out[0]["endMs"],end)-max(out[0]["startMs"],start))>0:
            # Closely overlapping segments in a very short video cannot be
            # independent positive-vs-negative examples; keep only one.
            continue
        out.append({"role":role,"anchorMs":item["tMs"],
                    "startMs":start,"endMs":end,"schedulerReasons":item["reasons"],
                    "notGroundTruth":True})
    return out


def _html(report:dict)->str:
    """Offline, user-editable benchmark; JSON export remains entirely local."""
    groups=[]
    scale=[(None,"Not assessed"),(0,"0 · Very low"),(.25,"0.25 · Low"),
           (.5,"0.50 · Moderate"),(.75,"0.75 · High"),(1,"1.00 · Exceptional")]
    for sport in ("snorkeling","ski","bike","unresolved"):
        entries=[x for x in report["assignments"] if x["selectionGroup"]==sport]
        if not entries:
            continue
        blocks=[]
        for a in entries:
            url=a.get("sourceFileUrl")
            link=(f'<a href="{escape(url,quote=True)}" rel="noopener">Open source video</a>'
                  if url else '<span>Original file unavailable</span>')
            questions=''.join('<li>'+escape(q)+'</li>'
                              for q in SPORT_QUESTIONS[sport])
            rating_fields=[]
            for key,desc in CRITERIA.items():
                options=''.join('<option value="'+('' if val is None else str(val))+'">'+
                                escape(label)+'</option>'
                                for val,label in scale)
                rating_fields.append(
                    '<label class="rating"><span>'+escape(key)+
                    f'<small>{escape(desc)}</small></span>'
                    f'<select data-criterion="{escape(key,quote=True)}">'
                    +options+'</select></label>')
            blocks.append(
                '<article class="review" data-review-id="'+escape(a["reviewId"],quote=True)+'">'
                '<strong>'+escape(a["basename"])+'</strong>'
                f'<p>{escape(a["role"])} · {a["startMs"]/1000:.1f}–'
                f'{a["endMs"]/1000:.1f}s · {escape(a["analysisCoverage"])}</p>'
                +link+
                '<p>Watch the full interval. A candidate is not necessarily '
                'good; a control is not necessarily uninteresting.</p>'
                '<label>Decision <select data-field="reviewDecision">'
                '<option value="">Not reviewed</option>'
                '<option value="keep">Keep</option>'
                '<option value="skip">Skip</option>'
                '<option value="uncertain">Uncertain</option>'
                '</select></label>'
                '<label>Actual activity <select data-field="observedActivity">'
                '<option value="">Unknown / not assessed</option>'
                '<option value="snorkeling">Snorkeling</option>'
                '<option value="ski">Ski</option>'
                '<option value="bike">Bike</option>'
                '<option value="other">Other / mixed</option>'
                '</select></label>'
                '<label>Was a meaningful event actually observed? '
                '<select data-field="observedEvent">'
                '<option value="">Not assessed</option>'
                '<option value="true">Yes</option>'
                '<option value="false">No</option>'
                '<option value="unknown">Unclear</option>'
                '</select></label>'
                '<label>Event description <input data-field="eventKind" '
                'maxlength="200" placeholder="e.g. ray swims past, completed turn"/></label>'
                '<details><summary>Value criteria (0 to 1, unknown allowed)</summary>'
                +''.join(rating_fields)+'</details>'
                '<details><summary>Sport-specific review prompts</summary><ul>'
                +questions+'</ul></details>'
                '<label>Evidence and improvement notes '
                '<textarea data-field="notes" rows="3" maxlength="2000" '
                'placeholder="What was visible? What would make this better?"></textarea></label>'
                '<small>'+escape(a["reviewId"])+'</small></article>')
        groups.append('<section><h2>'+escape(sport)+'</h2>'+
                      ''.join(blocks)+'</section>')
    script=r"""
    (() => {
      const exportButton=document.getElementById("export");
      exportButton.addEventListener("click", () => {
        const data=Array.from(document.querySelectorAll(".review")).map(card => {
          const field=name=>card.querySelector('[data-field="'+name+'"]').value;
          const event=field("observedEvent");
          const ratings={};
          for (const el of card.querySelectorAll("[data-criterion]")) {
            ratings[el.dataset.criterion]=el.value===""?null:Number(el.value);
          }
          return {
            reviewId:card.dataset.reviewId,
            reviewDecision:field("reviewDecision")||null,
            observedActivity:field("observedActivity")||null,
            observedEvent:event===""||event==="unknown"?null:event==="true",
            eventKind:field("eventKind")||null,
            notes:field("notes"),
            ratings,
            rareTaxonVerified:false,
            taxonReviewSource:null,
            eventBoundaryAdjustmentMs:null
          };
        });
        const content=JSON.stringify(data,null,2)+"\n";
        const blob=new Blob([content],{type:"application/json"});
        const url=URL.createObjectURL(blob);
        const anchor=document.createElement("a");
        anchor.href=url;anchor.download="klip-human-reviews.json";
        anchor.click();
        URL.revokeObjectURL(url);
      });
    })();"""
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Klip · Archive benchmark assignments</title><style>'
        'body{font:14px system-ui;background:#101923;color:#eef2f5;'
        'max-width:1150px;margin:28px auto;padding:0 16px;line-height:1.5}'
        'main{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,'
        '350px),1fr));gap:16px}h1{font-size:1.6rem}h2{font-size:1.1rem;'
        'color:#eebdaf}article{padding:14px;background:#1c2b38;'
        'border:1px solid #354657;border-radius:12px;margin-bottom:12px}'
        'p,small{color:#b4c4d1}small{display:block;overflow-wrap:anywhere}'
        'a{color:#9ad2de}label{display:block;margin:12px 0}'
        'input,textarea,select{font:inherit;border:1px solid #526578;'
        'color:#eef2f5;background:#233644;border-radius:6px;'
        'padding:6px;max-width:100%;display:block;margin-top:4px}'
        'textarea{width:95%}.rating{display:flex;gap:12px;'
        'align-items:center;justify-content:space-between;'
        'border-bottom:1px solid #354657;padding-bottom:8px}'
        '.rating span{max-width:65%}.rating select{max-width:35%}'
        'button{padding:12px 18px;border:0;border-radius:8px;'
        'background:#eebdaf;color:#182431;font-weight:700;cursor:pointer}'
        'button:focus-visible,input:focus-visible,select:focus-visible,'
        'textarea:focus-visible{outline:2px solid #eebdaf}'
        'details{margin:12px 0;padding:8px;border:1px solid #354657;'
        'border-radius:8px}summary{cursor:pointer}</style></head><body>'
        '<h1>Archive event review benchmark</h1>'
        '<p>Candidate and systematic-control intervals. Scores are '
        'human assessments, not classifier confidence. No files are '
        'uploaded; export a local JSON file when you finish.</p>'
        '<p><button id="export" type="button">Export my reviews (JSON)</button></p>'
        '<main>'+''.join(groups)+'</main><script>'+script+'</script></body></html>'
    )

def seed_archive_reviews(workspace:Path,campaign:Path,output_dir:Path,*,
                         max_assets:int=32,window_seconds:int=14)->dict:
    if type(max_assets) is not int or not 1<=max_assets<=128:
        raise ValueError("max-assets must be 1..128")
    if type(window_seconds) is not int or not 4<=window_seconds<=60:
        raise ValueError("window-seconds must be 4..60")
    root=Path(workspace).expanduser().resolve()
    campaign=Path(campaign).expanduser().resolve()
    dest=Path(output_dir).expanduser().resolve()
    if (dest==root or not dest.is_relative_to(root)
        or dest.relative_to(root).parts[0] not in ("reports","exports")
        or dest.exists()):
        raise ValueError("Use a new output directory in workspace reports/ or exports/")
    status=inspect_archive_campaign(root,campaign)
    selected={}
    # Every relevant source gets at least one assignment; both successful
    # and failed recordings are represented, but their coverage is explicit.
    for batch in status["batches"]:
        plan=_read_json(Path(batch["plan"]))
        validate_plan(plan)
        for asset in plan["assets"]:
            aid=asset["assetId"]
            if aid not in selected and len(selected)<max_assets:
                state=next(x for x in batch["assets"] if x["assetId"]==aid)
                selected[aid]=(asset,state)
    with closing(_open_catalog(root)) as db:
        paths={r["id"]:r["stored_path"] for r in db.execute(
            "SELECT id,stored_path FROM assets WHERE id IN ("+
            ",".join("?" for _ in selected)+")",list(selected))} if selected else {}
    assignments=[]
    for aid,(asset,state) in selected.items():
        sport=state["selectionGroup"]
        if sport not in SPORT_QUESTIONS:
            sport="unresolved"
        video=paths.get(aid)
        file_url=Path(video).as_uri() if isinstance(video,str) and Path(video).is_absolute() else None
        for x in _choose_windows(asset,half_ms=window_seconds*500):
            stable=f'{VERSION}:{aid}:{x["role"]}:{x["startMs"]}:{x["endMs"]}'
            item={
                "reviewId":sha256(stable.encode()).hexdigest()[:20],
                "assetId":aid,"basename":state["filename"],
                "selectionGroup":sport,
                "activityConfirmed":bool(state["activityConfirmed"]),
                "analysisCoverage":state["status"],
                "sourceFileUrl":file_url,
                **x,
                "truthStatus":"unreviewed",
                "observedEvent":None,
                "eventKind":None,
                "speciesVerified":False,
                "techniqueVerified":False,
                "reviewComplete":False,
            }
            assignments.append(item)
    form=[{
        "reviewId":a["reviewId"],
        "reviewDecision":None,
        "observedActivity":None,
        "observedEvent":None,
        "eventKind":None,
        "notes":"",
        "ratings":{criterion:None for criterion in CRITERIA},
        "rareTaxonVerified":False,
        "taxonReviewSource":None,
        "eventBoundaryAdjustmentMs":None,
    } for a in assignments]
    result={
        "schemaVersion":1,"version":VERSION,
        "campaign":str(campaign),"assignments":assignments,
        "sourceAssetCount":len(selected),
        "reviewIntervalCount":len(assignments),
        "selectionRoles":dict(sorted(Counter(x["role"] for x in assignments).items())),
        "selectionGroups":dict(sorted(Counter(x["selectionGroup"] for x in assignments).items())),
        "criteria":CRITERIA,"sportQuestions":SPORT_QUESTIONS,
        "sourceFramesDecoded":0,"modelInferenceCalls":0,
        "sourceMediaRead":False,
        "limitations":[
            "Sampled review assignments are not accepted clips or validated events.",
            "A histogram cache is not a semantic wildlife or sport-action classifier.",
            "Systematic controls are not guaranteed event-negative examples.",
            "Failed/incomplete frame samples retain unknown coverage.",
            "Source file links are local only and browser handling may vary.",
            "Human review annotations must be entered separately before training/ranking.",
            "Reviewed intervals within one session are correlated; hold out entire trips for evaluation.",
        ],
    }
    dest.mkdir(parents=True,mode=0o700,exist_ok=False)
    for name,data in (("assignments.json",result),("review-template.json",form)):
        with (dest/name).open("x",encoding="utf-8") as f:
            json.dump(data,f,indent=2,sort_keys=True,ensure_ascii=False,allow_nan=False)
            f.write("\n")
    with (dest/"index.html").open("x",encoding="utf-8") as f:
        f.write(_html(result))
    return {"status":"review_assignments_created",
            "assets":len(selected),"intervals":len(assignments),
            "groups":result["selectionGroups"],
            "assignments":str(dest/"assignments.json"),
            "reviewTemplate":str(dest/"review-template.json"),
            "gallery":str(dest/"index.html"),
            "sourceFramesDecoded":0,"modelInferenceCalls":0}
