"""Reveal independent local scene narratives only for *already voted* blind pairs.

No story appears in the blind review page, and an unvoted pair never gets
narrative details through this report. The experiment audits where narrative
hypotheses differ from the human's preferences; it never retrains a model.
"""
from __future__ import annotations

from hashlib import sha256
from html import escape
from pathlib import Path
import json

from .social_action_lens import _json, _new_folder
from .social_blind_pairs import VERSION as PAIR_VERSION, evaluate_pair_reviews
from .social_scene_stories import VERSION as STORY_VERSION


def _identity(row:dict)->tuple:
    return row.get("assetId"),row.get("startMs"),row.get("endMs")


def _safe_text(value)->str:
    return escape(str(value)) if value is not None else "Unknown"


def _render_story(story:dict|None,frames:list|None,
                  signals:dict|None=None,hypotheses:dict|None=None)->str:
    if not isinstance(story,dict):
        return "<p>No independent story evidence available for this clip.</p>"
    if story.get("status")!="provisional_story_hypotheses":
        return "<p>Story data was not validated.</p>"
    timeline=[]
    for row in story.get("timeline",[]):
        stamps=[]
        for number in row["frameIndices"]:
            found=next((f for f in frames or [] if f.get("frameIndex")==number),None)
            stamps.append(
                f'{found["relativeTimeMs"]/1000:.1f}s' if found else f'frame {number}')
        timeline.append(
            "<li><b>"+_safe_text(row["phase"])+
            " · "+_safe_text(", ".join(stamps))+
            "</b><p>"+_safe_text(row["visibleObservation"])+
            "</p><p class='muted'>Possible meaning: "+
            _safe_text(row["possibleInterpretation"])+
            " ("+_safe_text(row["evidenceStatus"])+")</p></li>")
    arc=story["narrativeArc"]
    def list_items(key):
        return "<ul>"+ "".join("<li>"+_safe_text(x)+"</li>"
            for x in story[key])+"</ul>"
    return (
        "<p><strong>Visible:</strong> "+_safe_text(story["visibleScene"])+"</p>"
        "<p>"+_safe_text(story["sceneNarrative"])+"</p>"
        "<h4>Frame-grounded chronology</h4><ol>"+ "".join(timeline)+"</ol>"
        "<h4>Possible story structure</h4>"
        "<p><b>Opening:</b> "+_safe_text(arc["setup"])+
        "</p><p><b>Development:</b> "+_safe_text(arc["development"])+
        "</p><p><b>Possible key moment:</b> "+_safe_text(arc["possiblePeak"])+
        "</p><p><b>Outcome:</b> "+_safe_text(arc["outcome"])+"</p>"
        "<h4>Why it might matter</h4>"+list_items("potentialSignificance")+
        "<h4>Alternative explanations</h4>"+list_items("alternativeInterpretations")+
        "<h4>What is not established</h4>"+list_items("uncertainties")+
        "<h4>Questions for human playback</h4>"+list_items("questionsForHuman")+
        "<details><summary>Inspect input evidence and provenance</summary>"
        "<p class='muted'>Numeric cues describe pixels and cached signals, not actions. "
        "Text-image and object proposals are unverified and model-dependent.</p>"
        "<pre>"+_safe_text(json.dumps({
            "measuredSignals":signals,
            "unverifiedSemanticHypotheses":hypotheses,
        },sort_keys=True,indent=2,ensure_ascii=True))+"</pre></details>"
        "<p class='muted'>Provisional local model interpretation; seven stills "
        "and limited temporal measurements, not observed continuous behavior.</p>"
    )


def story_counter_audit(workspace:Path,pair_file:Path,reviews_file:Path,
                        story_file:Path,*,additional_stories:tuple[Path,...]=())->dict:
    root=Path(workspace).expanduser().resolve()
    story_paths=(story_file,*additional_stories)
    if len(story_paths)>30:
        raise ValueError("At most 30 independent bounded story batches supported")
    for file in (pair_file,*story_paths):
        path=Path(file).expanduser().resolve()
        if not path.is_relative_to(root) or (
                path.relative_to(root).parts[0] not in ("reports","exports")):
            raise ValueError("Pair and narrative reports must be inside private workspace")
    pair_path=Path(pair_file).expanduser().resolve()
    audited=evaluate_pair_reviews(pair_path,reviews_file)
    frozen=_json(pair_path)
    if frozen.get("version")!=PAIR_VERSION:
        raise ValueError("Expected frozen pair study")
    by_id={}
    report_shas=[]
    evidence_fingerprint=None
    model_name=None
    for file in story_paths:
        path=Path(file).expanduser().resolve()
        document=_json(path)
        if (document.get("version")!=STORY_VERSION or
                document.get("mode")!="local_ollama"
                or not isinstance(document.get("stories"),list)):
            raise ValueError("Expected completed local model story batch")
        provenance=document.get("inputProvenance") or {}
        batch_key=provenance.get("featureTemplateSha256")
        temporal_key=provenance.get("temporalEvidenceSha256")
        if not isinstance(batch_key,str) or not isinstance(temporal_key,str):
            raise ValueError("Story batch missing input provenance")
        if evidence_fingerprint is None:
            evidence_fingerprint=(batch_key,temporal_key)
            model_name=document.get("model")
        elif (batch_key,temporal_key)!=evidence_fingerprint or (
                document.get("model")!=model_name):
            raise ValueError("Cannot combine stories from different input evidence or models")
        report_shas.append(sha256(path.read_bytes()).hexdigest())
        for r in document["stories"]:
            if not isinstance(r,dict) or not isinstance(r.get("assetId"),str):
                continue
            key=_identity(r)
            if key in by_id:
                raise ValueError("Duplicate story candidate across batches")
            if r.get("story") is not None:
                if (r["story"].get("status")!="provisional_story_hypotheses"
                        or not r["story"].get("frameReferencesValidated")):
                    raise ValueError("Unvalidated story content cannot be revealed")
            by_id[key]=r
    reviews=_json(Path(reviews_file).expanduser().resolve(),limit=250000)
    votes={r["pairId"]:r for r in reviews["reviews"]}
    output=[]
    for pair in frozen["pairs"]:
        vote=votes.get(pair["pairId"])
        if vote is None:
            continue  # Strict: no story exposure for a still-unvoted pair.
        sides={}
        for side in ("a","b"):
            clip=pair[side]
            scene=by_id.get(_identity(clip))
            sides[side]={
                "assetId":clip["assetId"],
                "startMs":clip["startMs"],"endMs":clip["endMs"],
                "sceneStory":scene.get("story") if scene else None,
                "frameEvidence":scene.get("frameEvidence") if scene else None,
                "observedSignals":scene.get("observedSignals") if scene else None,
                "auxiliaryHypotheses":scene.get("auxiliaryHypotheses") if scene else None,
                "modelEvidenceStatus":scene.get("status") if scene else "not_analyzed",
            }
        output.append({
            "pairId":pair["pairId"],"sport":pair["sport"],
            "humanChoice":vote["choice"],"humanReasons":vote["reasons"],
            "humanNote":vote["notes"],
            "higherKlipSide":pair["higherKlipSide"],
            "a":sides["a"],"b":sides["b"],
        })
    available=sum(1 for p in output for s in ("a","b") if p[s]["sceneStory"] is not None)
    return {
        "version":"social-story-counter-audit-v1",
        "blindPairSha256":sha256(pair_path.read_bytes()).hexdigest(),
        "storySourcesSha256":report_shas,
        "votedPairsOnly":True,
        "votedPairsInReport":len(output),"narrativesAvailable":available,
        "narrativesMissing":len(output)*2-available,
        "pairResults":{
            "pairsAssessed":audited["pairsAssessed"],
            "pairsUnreviewed":audited["pairsUnreviewed"],
            "agreementWithKlipOnDecisivePairs":audited[
                "agreementWithKlipOnDecisivePairs"],
            "outcomeCounts":audited["outcomeCounts"],
        },
        "pairs":output,"externalUploads":0,"humanReviewsModified":False,
        "modelTrainingPerformed":False,
        "interpretation":[
            "Only previously voted pairs may display model storytelling",
            "Narratives are unverified alternate interpretations, not reasons to overwrite human vote",
            "The seven stills cannot prove continuous animal behavior or successful sports maneuvers",
            "Model disagreement alone is neither clip quality nor predictive social engagement",
            "The report is not generalizable to unreviewed or out-of-archive footage",
        ],
    }


def _report_html(report:dict)->str:
    blocks=[]
    for item in report["pairs"]:
        sides=[]
        for side in ("a","b"):
            entry=item[side]
            sides.append(
                "<article><h3>"+side.upper()+" · "+
                _safe_text(entry["modelEvidenceStatus"])+
                "</h3>"+_render_story(
                    entry["sceneStory"],entry["frameEvidence"],
                    entry.get("observedSignals"),entry.get("auxiliaryHypotheses"))+
                "</article>")
        blocks.append(
            "<section><h2>"+_safe_text(item["sport"])+
            " · Pair "+_safe_text(item["pairId"][:10])+"</h2>"
            "<p>Human preference: <b>"+_safe_text(item["humanChoice"])+
            "</b> · Klip higher-ranked side: <b>"+
            _safe_text(item["higherKlipSide"])+
            "</b></p><p>Human reasons: "+
            _safe_text(", ".join(item["humanReasons"]))+
            "</p><p>Human note: "+_safe_text(item["humanNote"])+
            "</p><div class='columns'>"+ "".join(sides)+"</div></section>")
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Klip · Post-vote narrative audit</title><style>'
        'body{max-width:1260px;margin:22px auto;padding:0 20px;'
        'background:#111a27;color:#f0f3f6;font:14px system-ui;line-height:1.55}'
        'h1{font-size:1.7rem}h2{color:#edb1a0}.muted{color:#b8c8d4}'
        '.columns{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}'
        'section{border-bottom:1px solid #425264;padding:12px 0 28px}'
        'article{background:#1a293c;border:1px solid #36485c;'
        'border-radius:12px;padding:14px;overflow-wrap:anywhere}'
        'pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}'
        'li{margin-bottom:9px}p{overflow-wrap:anywhere}'
        '@media(max-width:750px){.columns{grid-template-columns:1fr}}'
        '</style></head><body><h1>Scene understanding · after human vote</h1>'
        '<p class="muted">Independent local narrative hypotheses are revealed '
        'only for pairs already reviewed. They are NOT validated story truth '
        'and never update the saved A/B preference.</p>'
        '<p>Voted pairs: '+str(report["votedPairsInReport"])+
        ' · Available narratives: '+str(report["narrativesAvailable"])+
        ' · Missing narratives: '+str(report["narrativesMissing"])+
        '</p>'+ "".join(blocks)+"</body></html>"
    )


def save_counter_audit(workspace:Path,pair_file:Path,reviews_file:Path,
                       story_file:Path,output_dir:Path,*,
                       additional_stories:tuple[Path,...]=())->dict:
    root=Path(workspace).expanduser().resolve()
    destination=_new_folder(root,output_dir)
    report=story_counter_audit(root,pair_file,reviews_file,story_file,
                               additional_stories=additional_stories)
    destination.mkdir(parents=True,mode=0o700,exist_ok=False)
    with (destination/"counter-audit.json").open("x",encoding="utf-8") as f:
        json.dump(report,f,sort_keys=True,indent=2,allow_nan=False)
        f.write("\n")
    with (destination/"index.html").open("x",encoding="utf-8") as f:
        f.write(_report_html(report))
    return {
        "status":"post_vote_scene_counter_audit_written",
        "json":str(destination/"counter-audit.json"),
        "html":str(destination/"index.html"),
        "votedPairs":report["votedPairsInReport"],
        "availableStories":report["narrativesAvailable"],
        "missingStories":report["narrativesMissing"],
        "reviewsModified":False,"externalUploads":0,
    }
