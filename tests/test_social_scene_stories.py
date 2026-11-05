"""Evidence-first local scene narratives: blind, cache-safe, no automatic labels."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import pytest

from adventure_intelligence.catalog import connect
from adventure_intelligence import social_scene_stories as narrative
from adventure_intelligence import social_story_counter_audit as audit
from adventure_intelligence import social_blind_pairs as pairs
from adventure_intelligence.social_action_lens import USER_VERSION,FEATURE_VERSION,FEATURES
from adventure_intelligence.social_temporal_evidence import VERSION as TEMPORAL_VERSION
from adventure_intelligence.studio_preview_judge import preview_paths


def story_payload():
    return {
        "visibleScene":"A broad mountain slope and white snow appear behind a ski.",
        "sceneNarrative":"Across sampled images, a snowy landscape and changing trail "
                         "views are visible. The images suggest a moving camera, "
                         "but cannot establish technique or a completed maneuver.",
        "timeline":[
            {"phase":"opening","frameIndices":[0,1],
             "visibleObservation":"Snow and the edge of a trail appear.",
             "possibleInterpretation":"none","evidenceStatus":"visibly_observed"},
            {"phase":"development","frameIndices":[2,3],
             "visibleObservation":"The relative foreground pattern differs.",
             "possibleInterpretation":"This might indicate forward travel.",
             "evidenceStatus":"possible_interpretation"},
            {"phase":"notable_moment","frameIndices":[5,6],
             "visibleObservation":"A broad trail view remains in the sampled images.",
             "possibleInterpretation":"The wider scene could be visually engaging.",
             "evidenceStatus":"possible_interpretation"},
        ],
        "narrativeArc":{
            "setup":"A snow-covered scene opens the shot.",
            "development":"Changing camera viewpoints suggest movement.",
            "possiblePeak":"No clear peak is confirmed by sparse stills.",
            "outcome":"Unknown; the intervening footage and action are not observed.",
        },
        "potentialSignificance":[
            "The landscape could contribute to a coherent skiing sequence.",
            "Potential scenic context for a complete run."
        ],
        "alternativeInterpretations":[
            "Changes may reflect a camera pan, rider movement, or edit."
        ],
        "uncertainties":[
            "Actual skier speed and continuous technique are unverified.",
            "The final outcome is not visible."
        ],
        "questionsForHuman":[
            "Does full playback show sustained, aesthetically pleasant movement?",
            "Would this shot be useful in a longer descent sequence?"
        ],
        "continuousActionVerified":False,
    }


def dataset(tmp_path,*,num=4):
    root=tmp_path/"workspace"
    with connect(root) as db:
        for i in range(num):
            aid=f"{i+1:064x}"
            db.execute(
                "INSERT INTO assets(id,sha256,stored_path,size_bytes,suffix,duration,"
                "activity) VALUES(?,?,?,?,?,?,?)",
                (aid,aid,str(tmp_path/f"never-open-source-{i}.MP4"),1,".MP4",30.0,"ski"))
    feature_rows=[]
    temporal_rows=[]
    scored=[]
    for i in range(num):
        aid=f"{i+1:064x}"
        start,end=1000,21000
        feature_rows.append({
            "assetId":aid,"startMs":start,"endMs":end,"sport":"ski",
            "featureVersion":FEATURE_VERSION,"featureMethod":"unassessed",
            "features":{name:None for name in FEATURES},
        })
        video,receipt=preview_paths(root,aid,start,end)
        video.parent.mkdir(parents=True,exist_ok=True)
        data=(f"synthetic receipt marker {i}".encode())*10
        video.write_bytes(data)
        receipt.write_text(json.dumps({
            "version":"studio-preview-local-judge-v1",
            "assetId":aid,"startMs":start,"endMs":end,
            "previewSha256":sha256(data).hexdigest(),
            "originalSha256Verified":True,
        }))
        temporal_rows.append({
            "assetId":aid,"startMs":start,"endMs":end,"sport":"ski",
            "status":"temporal_visual_metrics_observed",
            "previewVerified":True,
            "visualTemporal":{
                "frameCount":80,"sampledDurationSec":20,
                "frameChangeMedian":.02+i*.01,
                "frameChangeP90":.1+i*.02,
                "changeMedianEarlyMiddleLate":[.02,.04,.01],
            },
            "semanticTemporal":{"status":"uncalibrated_temporal_embedding_evidence",
                                 "modelKey":"frozen-siglip-v2",
                                 "samples":8,"medianAdjacentCosineSimilarity":.87},
            "cachedMotionAcoustics":{"motionSamples":10,"motionChangeMedian":.04},
        })
        scored.append({
            "sourceAssetId":aid,"startMs":start,"endMs":end,
            "activityGroup":"ski","score":[.95,.75,.43,.19][i%4],
        })
    report_folder=root/"reports"/"input"
    report_folder.mkdir(parents=True)
    features=report_folder/"features.json"
    temporal=report_folder/"temporal.json"
    study=report_folder/"study.json"
    features.write_text(json.dumps({
        "version":USER_VERSION,"featureVersion":FEATURE_VERSION,
        "items":feature_rows,
    }))
    temporal.write_text(json.dumps({
        "version":TEMPORAL_VERSION,
        "mode":"local_preview_temporal_extraction",
        "sourceTemplateSha256":sha256(features.read_bytes()).hexdigest(),
        "candidates":temporal_rows,
    }))
    study.write_text(json.dumps({
        "version":"archive-value-study-v1","rankedCandidates":scored,
    }))
    return root,features,temporal,study


def fake_inference(monkeypatch):
    record=[]
    monkeypatch.setattr(narrative,"_samples",
                        lambda path:(["b64_image_fixture"]*7,
                           [1400,4000,6800,10000,13200,16000,18600]))
    def transport(body):
        record.append(body)
        return {"message":{"content":json.dumps(story_payload())}}
    return record,transport


def test_strict_scene_story_requires_frame_grounded_uncertainty():
    validated=narrative.validate_story(story_payload())
    assert validated["frameReferencesValidated"]
    assert validated["sourceVideoAndAudioSemanticallyReviewed"] is False
    assert validated["personalImportanceVerified"] is False
    assert validated["mayModifyHumanKeepOrSkip"] is False
    bad=json.loads(json.dumps(story_payload()))
    bad["continuousActionVerified"]=True
    with pytest.raises(ValueError,match="continuous action"):
        narrative.validate_story(bad)
    bad=json.loads(json.dumps(story_payload()))
    bad["timeline"][1]["frameIndices"]=[6]
    with pytest.raises(ValueError,match="nonchronological"):
        narrative.validate_story(bad)
    bad=json.loads(json.dumps(story_payload()))
    bad["timeline"][0]["possibleInterpretation"]="The skier is definitely okay"
    with pytest.raises(ValueError,match="interpretation"):
        narrative.validate_story(bad)
    bad=json.loads(json.dumps(story_payload()))
    bad["sceneNarrative"]="The skier rode at 100 mph."
    with pytest.raises(ValueError,match="Unsupported"):
        narrative.validate_story(bad)
    bad=json.loads(json.dumps(story_payload()))
    bad["questionsForHuman"]=[]
    with pytest.raises(ValueError):
        narrative.validate_story(bad)


def test_independent_story_judge_never_sees_klip_score_or_human_choice(tmp_path,monkeypatch):
    root,features,temporal,study=dataset(tmp_path,num=4)
    record,transport=fake_inference(monkeypatch)
    monkeypatch.setattr(narrative,"_auxiliary_hypotheses",lambda *a:{
        "sceneContextCacheKey":"siglip-key",
        "sceneFramesSeen":5,
        "unverifiedScenePrompts":[{"label":"powder snow","relativeSimilarity":.31,
                                    "observations":3}],
        "objectCacheKey":None,"unverifiedObjectProposals":[],
        "status":"uncalibrated_model_hypotheses_not_independent_evidence",
    })
    planned=narrative.analyze_scene_stories(root,features,temporal,
                                            run=False,limit=2,transport=transport)
    assert planned["modelRequests"]==0 and not record
    assert planned["outcomes"]=={"eligible_not_run":2}
    result=narrative.analyze_scene_stories(root,features,temporal,
                                           run=True,limit=2,transport=transport)
    assert result["modelRequests"]==2
    assert result["countSucceeded"]==2
    assert result["humanPreferencesModified"] is False
    assert result["originalVideoReads"]==0
    assert result["sourceTrainingLabelsWritten"]==0
    assert len(record)==2
    for request in record:
        assert request["model"]=="gemma3:4b"
        assert request["options"]["temperature"]==0
        assert len(request["messages"][0]["images"])==7
        text=request["messages"][0]["content"]
        assert "powder snow" in text
        assert "signalObservations" in text
        assert "unverified" in text.lower()
        assert '"klipScore"' not in text
        assert "higherKlipSide" not in text
        assert '"humanChoice"' not in text
        assert '"reviews"' not in text
        assert "0.95" not in text
        assert "sourceAssetId" not in text
    assert all(x["story"]["status"]=="provisional_story_hypotheses"
               for x in result["stories"])
    again=narrative.analyze_scene_stories(root,features,temporal,
                                          run=True,limit=2,transport=transport)
    assert again["modelRequests"]==0
    assert again["cachedStoryCount"]==2
    assert len(record)==2  # content-verified cache; model not called again


def test_preview_tampering_and_missing_local_video_abstain(tmp_path,monkeypatch):
    root,features,temporal,_=dataset(tmp_path,num=2)
    called,transport=fake_inference(monkeypatch)
    preview,_=preview_paths(root,f"{1:064x}",1000,21000)
    preview.write_bytes(preview.read_bytes()+b"tamper")
    r=narrative.analyze_scene_stories(root,features,temporal,limit=2,
                                      run=True,transport=transport)
    assert r["outcomes"].get("missing_or_invalid_verified_preview",0)==1
    assert r["modelRequests"]==1
    assert len(called)==1
    assert r["originalVideoReads"]==0


def test_preview_changed_during_sampling_abstains_without_model_call(tmp_path,monkeypatch):
    root,features,temporal,_=dataset(tmp_path,num=2)
    base,_receipt=preview_paths(root,f"{1:064x}",1000,21000)
    model_requests=[]
    def change_preview(path):
        if path==base:
            base.write_bytes(base.read_bytes()+b"changed-after-verification")
        return (["b64_frame"]*7,[1400,4000,6800,10000,13200,16000,18600])
    monkeypatch.setattr(narrative,"_samples",change_preview)
    def should_not_call(body):
        model_requests.append(body)
        return {"message":{"content":json.dumps(story_payload())}}
    result=narrative.analyze_scene_stories(
        root,features,temporal,limit=2,run=True,transport=should_not_call)
    assert result["countFailed"]==1
    assert result["failures"][0]["errorType"]=="RuntimeError"
    assert "changed during frame sampling" in result["failures"][0]["reason"]
    assert result["modelRequests"]==1
    assert len(model_requests)==1
    assert result["originalVideoReads"]==0


def test_preview_changed_during_inference_cannot_be_saved_as_story(tmp_path,monkeypatch):
    root,features,temporal,_=dataset(tmp_path,num=1)
    preview,receipt=preview_paths(root,f"{1:064x}",1000,21000)
    monkeypatch.setattr(narrative,"_samples",
        lambda path:(["b64_frame"]*7,
                     [1400,4000,6800,10000,13200,16000,18600]))
    requests=[]
    def mutate_during_local_model(body):
        requests.append(body)
        preview.write_bytes(preview.read_bytes()+b"racing-preview-change")
        return {"message":{"content":json.dumps(story_payload())}}
    report=narrative.analyze_scene_stories(
        root,features,temporal,limit=1,run=True,
        transport=mutate_during_local_model)
    assert len(requests)==1
    assert report["countFailed"]==1
    assert report["modelRequests"]==0
    assert "changed during narrative inference" in report["failures"][0]["reason"]
    # A failed race must not yield an accepted story or a validated cache.
    assert report["stories"]==[]
    assert list((root/"reports"/"social-story-cache-v1").glob("*.json"))==[]
    assert report["originalVideoReads"]==0


def test_targeted_blind_pairs_and_post_vote_reveal_only(tmp_path,monkeypatch):
    root,features,temporal,study=dataset(tmp_path,num=4)
    pair_dest=root/"reports"/"pairs"
    pair_result=pairs.write_pair_study(
        root,study,features,temporal,pair_dest,max_pairs=2)
    assert pair_result["pairs"]>=2
    frozen=json.loads(Path(pair_result["manifest"]).read_text())
    expected={(
        p[side]["assetId"],p[side]["startMs"],p[side]["endMs"])
        for p in frozen["pairs"] for side in ("a","b")}
    record,transport=fake_inference(monkeypatch)
    story=narrative.analyze_scene_stories(
        root,features,temporal,limit=4,run=True,
        pairs_file=Path(pair_result["manifest"]),transport=transport)
    assert story["inputProvenance"]["pairTargeted"] is True
    assert { (s["assetId"],s["startMs"],s["endMs"])
             for s in story["stories"]}==expected
    stories_file=root/"reports"/"stories.json"
    stories_file.write_text(json.dumps(story))
    reviewed=frozen["pairs"][0]
    review_file=root/"reports"/"export.json"
    review_file.write_text(json.dumps({
        "version":pairs.REVIEW_VERSION,
        "manifestSha256":pair_result["manifestSha256"],
        "reviews":[{"pairId":reviewed["pairId"],"choice":"a",
                    "reasons":["interesting_event"],"notes":"<user hypothesis>"}],
    }))
    counter=audit.story_counter_audit(
        root,Path(pair_result["manifest"]),review_file,stories_file)
    assert counter["votedPairsOnly"] is True
    assert counter["votedPairsInReport"]==1
    assert counter["narrativesAvailable"]==2
    assert counter["pairResults"]["pairsUnreviewed"]>=1
    assert "higherKlipSide" in counter["pairs"][0]
    assert counter["modelTrainingPerformed"] is False
    html=audit._report_html(counter)
    assert "&lt;user hypothesis&gt;" in html
    assert "<user hypothesis>" not in html
    assert "What is not established" in html
    assert "Frame-grounded chronology" in html
    assert "Inspect input evidence and provenance" in html
    assert "measuredSignals" in html
    assert "unverifiedSemanticHypotheses" in html
    output=audit.save_counter_audit(
        root,Path(pair_result["manifest"]),review_file,stories_file,
        root/"reports"/"post-vote-audit")
    assert Path(output["html"]).is_file()
    assert output["votedPairs"]==1
    assert output["verifiedPlayablePreviews"]==2
    assert 'controls preload="none"' in Path(output["html"]).read_text()
    assert output["reviewsModified"] is False
    review_bad=json.loads(review_file.read_text())
    review_bad["manifestSha256"]="0"*64
    review_file.write_text(json.dumps(review_bad))
    with pytest.raises(ValueError,match="fingerprint"):
        audit.story_counter_audit(
            root,Path(pair_result["manifest"]),review_file,stories_file)


def test_bounded_story_batches_advance_and_do_not_repeat(tmp_path,monkeypatch):
    root,features,temporal,_=dataset(tmp_path,num=4)
    _,transport=fake_inference(monkeypatch)
    first=narrative.analyze_scene_stories(
        root,features,temporal,limit=2,offset=0,run=True,transport=transport)
    later=narrative.analyze_scene_stories(
        root,features,temporal,limit=2,offset=2,run=True,transport=transport)
    assert first["countSucceeded"]==2
    assert later["countSucceeded"]==2
    assert first["inputProvenance"]["totalEligibleForBatching"]==4
    assert set(x["assetId"] for x in first["stories"]).isdisjoint(
        x["assetId"] for x in later["stories"])
    again=narrative.analyze_scene_stories(
        root,features,temporal,limit=2,offset=0,run=True,transport=transport)
    assert again["cachedStoryCount"]==2
    assert again["modelRequests"]==0
    empty=narrative.analyze_scene_stories(
        root,features,temporal,limit=2,offset=4,run=False)
    assert empty["countRequested"]==0
    with pytest.raises(ValueError,match="offset"):
        narrative.analyze_scene_stories(root,features,temporal,offset=-1)


def test_post_vote_refuses_modified_report_media(tmp_path,monkeypatch):
    root,features,temporal,study=dataset(tmp_path,num=4)
    pair_info=pairs.write_pair_study(
        root,study,features,temporal,root/"reports"/"pairs-tamper",max_pairs=2)
    manifest=json.loads(Path(pair_info["manifest"]).read_text())
    first=manifest["pairs"][0]
    clip=first["a"]
    original,_=preview_paths(
        root,clip["assetId"],clip["startMs"],clip["endMs"])
    derived=Path(pair_info["manifest"]).parent/"media"/original.name
    derived.unlink()
    derived.write_bytes(b"tampered local derivative")
    assert audit._verified_media_link(
        root,Path(pair_info["manifest"]).parent,
        root/"reports"/"new-audit",clip) is None


def test_combine_story_batches_only_when_same_model_and_evidence(tmp_path,monkeypatch):
    root,features,temporal,study=dataset(tmp_path,num=4)
    _,transport=fake_inference(monkeypatch)
    pair_info=pairs.write_pair_study(
        root,study,features,temporal,root/"reports"/"pair-batch",max_pairs=2)
    frozen=json.loads(Path(pair_info["manifest"]).read_text())
    # Use frozen pair order, so both batch windows cover a real 2-pair study.
    file_one=root/"reports"/"story-batch-one.json"
    file_two=root/"reports"/"story-batch-two.json"
    a=narrative.analyze_scene_stories(root,features,temporal,
        pairs_file=Path(pair_info["manifest"]),offset=0,
        limit=2,run=True,transport=transport)
    b=narrative.analyze_scene_stories(root,features,temporal,
        pairs_file=Path(pair_info["manifest"]),offset=2,
        limit=2,run=True,transport=transport)
    file_one.write_text(json.dumps(a))
    file_two.write_text(json.dumps(b))
    exported=root/"reports"/"finished-votes.json"
    exported.write_text(json.dumps({
        "version":pairs.REVIEW_VERSION,
        "manifestSha256":pair_info["manifestSha256"],
        "reviews":[{"pairId":row["pairId"],"choice":"b",
                    "reasons":[],"notes":""} for row in frozen["pairs"]],
    }))
    result=audit.story_counter_audit(
        root,Path(pair_info["manifest"]),exported,file_one,
        additional_stories=(file_two,))
    assert result["votedPairsInReport"]==len(frozen["pairs"])
    assert result["narrativesAvailable"]==4
    assert len(result["storySourcesSha256"])==2
    with pytest.raises(ValueError,match="Duplicate"):
        audit.story_counter_audit(
            root,Path(pair_info["manifest"]),exported,file_one,
            additional_stories=(file_one,))
    invalid=json.loads(file_two.read_text())
    invalid["model"]="some-different-model"
    file_two.write_text(json.dumps(invalid))
    with pytest.raises(ValueError,match="different input evidence"):
        audit.story_counter_audit(
            root,Path(pair_info["manifest"]),exported,file_one,
            additional_stories=(file_two,))


def test_invalid_model_and_output_paths_do_not_train_or_write(tmp_path):
    root,features,temporal,_=dataset(tmp_path,num=3)
    with pytest.raises(ValueError,match="local"):
        narrative.analyze_scene_stories(
            root,features,temporal,model="https://attacker.invalid/image",
            run=True)
    with pytest.raises(ValueError,match="1..24"):
        narrative.analyze_scene_stories(root,features,temporal,limit=25)
    tampered=json.loads(temporal.read_text())
    tampered["sourceTemplateSha256"]="0"*64
    temporal.write_text(json.dumps(tampered))
    with pytest.raises(ValueError,match="matching"):
        narrative.analyze_scene_stories(root,features,temporal,run=False)
