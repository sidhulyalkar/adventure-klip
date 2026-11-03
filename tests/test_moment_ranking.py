"""Edge-case qualification for evidence-aware ranking and marine cross-label review."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from adventure_intelligence.event_context import _context_windows
from adventure_intelligence.indexing import index_folder
from adventure_intelligence.cli import main
from adventure_intelligence.moment_ranking import (
    validate_moment, score_moment, rank_moments, select_review_queue,
    from_event_context, create_rank_report,
)


AID="a"*64
BID="b"*64
CID="c"*64


def moment(name, activity="snorkeling",aid=AID, start=0, **updates):
    row={
        "momentId":name,"activity":activity,
        "assetId":aid,"startMs":start,"endMs":start+15000,
        "subjectGroup":"unknown","humanReview":"unreviewed",
        "taxonomyStatus":"unverified",
        "signals":{
            "eventSignificance":.5,
            "visualNovelty":.5,
            "subjectVisibility":.65,
            "imageQuality":.6,
            "narrativeCompleteness":.65,
            "learningValue":.5,
            "investigationPotential":.65,
            "uncertainty":.5,
        },
    }
    patch=updates.pop("signals",{})
    row["signals"].update(patch)
    row.update(updates)
    return row


def test_murky_exceptional_wildlife_beats_crystal_clear_ordinary_scene():
    clear=moment("clear-sand",start=0,signals={
        "eventSignificance":.12,
        "visualNovelty":.18,"subjectVisibility":.98,
        "imageQuality":.99,"narrativeCompleteness":.70,
        "investigationPotential":.15,"uncertainty":.20})
    murky=moment("murky-confirmed-ray",start=15000,
        taxonomyStatus="verified_species",subjectGroup="ray",
        verifiedTaxonEvidence={"taxonId":"curated-reference-id-17",
                              "basis":"expert_review",
                              "source":"independent-review-2026-11-03"},
        signals={
            "eventSignificance":.95,"visualNovelty":.93,
            "subjectVisibility":.57,"imageQuality":.30,
            "narrativeCompleteness":.76,
            "verifiedTaxonRarity":.95,
            "investigationPotential":.91,"uncertainty":.30})
    a,b=score_moment(clear),score_moment(murky)
    assert b["scores"]["share"]>a["scores"]["share"]
    assert b["scores"]["discovery"]>a["scores"]["discovery"]
    assert b["reviewEligibility"]["share"] is True
    assert b["reviewEligibility"]["autoPublish"] is False
    assert a["reviewEligibility"]["autoPublish"] is False


def test_rare_but_nearly_invisible_remains_investigable_not_share_eligible():
    faint=moment("faint-exception",signals={
        "eventSignificance":.99,"visualNovelty":.95,
        "subjectVisibility":.12,"imageQuality":.14,
        "narrativeCompleteness":.6,
        "investigationPotential":1.0,"uncertainty":.85},
        taxonomyStatus="verified_species",
        verifiedTaxonEvidence={"taxonId":"curated-reference-id-17",
                              "basis":"expert_review",
                              "source":"independent-review-2026-11-03"})
    # Exercise a verified rarity assessment, not a detector hypothesis.
    faint["signals"]["verifiedTaxonRarity"]=.97
    scored=score_moment(faint)
    assert scored["reviewEligibility"]["discovery"] is True
    assert scored["scores"]["discovery"]>.85
    assert scored["reviewEligibility"]["share"] is False
    assert "low_subject_visibility_manual_review_needed" in scored["reasonCodes"]


def test_unsupported_rarity_never_inflates_score():
    candidate=moment("ray-hypothesis",signals={"verifiedTaxonRarity":1.0})
    baseline=moment("ray-control",start=20000,signals={"verifiedTaxonRarity":None})
    a=score_moment(candidate)
    b=score_moment(baseline)
    assert a["scores"]["share"]==b["scores"]["share"]
    assert a["signalsUsed"]["verifiedTaxonRarity"] is None
    assert "no_species_rarity_bonus_without_verification" in a["reasonCodes"]


def test_no_missing_signal_turns_into_false_negative_or_fake_probability():
    unknown=moment("uncertain",signals={
        "subjectVisibility":None,"imageQuality":None,
        "narrativeCompleteness":None,
        "investigationPotential":None,"uncertainty":None,
        "learningValue":None,
    })
    scored=score_moment(unknown)
    assert scored["scores"]=={"discovery":None,"share":None,"learning":None}
    assert scored["signalsUsed"]["subjectVisibility"] is None
    assert scored["reviewEligibility"]["autoPublish"] is False
    ranked=rank_moments([unknown])
    assert all(v==[] for v in ranked["queues"].values())
    assert ranked["rankedCount"]==1


def test_sports_balance_prevents_one_high_motion_sport_from_crowding_others():
    entries=[moment(f"ski-{i}","ski",AID,start=i*20000,signals={
                        "eventSignificance":.95,"visualNovelty":.92,
                        "imageQuality":.94,"subjectVisibility":.96}) for i in range(7)]
    entries += [moment("snorkel-rare","snorkeling",BID,start=0),
                moment("bike-rock","bike",CID,start=0)]
    result=rank_moments(entries,limit=3)
    picks=[next(x for x in result["moments"] if x["momentId"]==mid)
           for mid in result["queues"]["share"]]
    assert {x["activity"] for x in picks}=={"ski","snorkeling","bike"}
    assert result["balancedSportExposure"] is True


def test_overlap_redundancy_suppressed_only_in_shortlist_and_keep_respected():
    a=moment("fish-proposal",start=0)
    b=moment("mammal-proposal",start=1000)
    c=moment("later-distinct",start=30000)
    out=rank_moments([a,b,c],limit=5)
    assert len(out["moments"])==3
    assert len(out["queues"]["share"])==2
    skipped=moment("rejected",start=60000,humanReview="skip")
    out=rank_moments([skipped,a])
    assert "rejected" not in out["queues"]["share"]
    assert "rejected" in out["queues"]["discovery"]


def test_corrupt_scores_and_invalid_ids_are_rejected():
    with pytest.raises(ValueError,match="finite"):
        validate_moment(moment("broken",signals={"imageQuality":float("nan")}))
    with pytest.raises(ValueError,match="finite"):
        validate_moment(moment("boolean-score",signals={"imageQuality":True}))
    with pytest.raises(ValueError,match="positive"):
        validate_moment(moment("bad-ms",start=-100))
    with pytest.raises(ValueError,match="Duplicate"):
        rank_moments([moment("same"),moment("same",start=25000)])
    with pytest.raises(ValueError,match="evidenceNotes"):
        validate_moment(moment("bad-note",evidenceNotes=["z"*800]))


def _proposal(idx,group,start=9000,end=45000,boxes=((12000,10,10,180,160),
                                                    (23000,12,12,176,158))):
    return {
        "id":f"ecology-owlv2:track-{idx}",
        "provider":"ecology-owlv2",
        "label":group,
        "biologicalGroup":group,
        "startMs":start,"endMs":end,
        "reviewPriority":.8,
        "evidenceRequestedTimesMs":[x[0] for x in boxes],
        "sampledBoxEvidence":[{"requestedMs":t,"bbox":list(coords),
                               "bboxFormat":"xyxy"} for t,*coords in boxes],
    }


def test_same_ray_area_fish_and_mammal_labels_merge_as_conflict_not_two_animals():
    fish=_proposal(1,"fish")
    mammal=_proposal(2,"marine_mammal")
    combined=_context_windows([fish,mammal],80000,max_events=4)
    assert len(combined)==1
    group=combined[0]
    assert group["crossLabelConflict"] is True
    assert group["group"]=="conflicting_labels_unverified"
    assert group["biologicalGroupHypotheses"]==["fish","marine_mammal"]
    assert group["mayContainMultipleSubjects"] is True
    assert len(group["sourceProposalIds"])==2


def test_coincident_but_spatially_separate_animals_keep_distinct_review_cards():
    fish=_proposal(1,"fish")
    mammal=_proposal(2,"marine_mammal",boxes=(
        (12000,230,230,350,350),(23000,235,232,356,354)))
    separated=_context_windows([fish,mammal],80000,max_events=4)
    assert len(separated)==2
    assert all(not c["crossLabelConflict"] for c in separated)


def test_manifest_adapter_never_invents_species_rarity_or_quality():
    doc={"version":"event-context-embedding-v1","assetId":AID,
         "events":[{
            "id":"event-context-01","group":"conflicting_labels_unverified",
            "startMs":9000,"endMs":45000,
            "phases":[{"phase":"during","requestedMs":23000}],
            "speciesVerified":False,
         }]}
    adapted=from_event_context(doc)
    assert len(adapted)==1
    scoring=score_moment(adapted[0])
    assert scoring["scores"]["share"] is None
    assert scoring["scores"]["discovery"] is None
    assert scoring["reviewEligibility"]["autoPublish"] is False


def test_cli_event_manifest_is_unscored_until_explicit_review(tmp_path,monkeypatch,capsys):
    camera=tmp_path/"camera"
    camera.mkdir()
    raw=b"camera-data"*2000
    src=camera/"DJI_TEST.MP4"
    src.write_bytes(raw)
    monkeypatch.setattr("adventure_intelligence.indexing.probe",
                        lambda path:{"duration":60.0,"audio":False,"capture_time":None})
    ws=tmp_path/"workspace"
    aid=index_folder(camera,ws,activity="snorkeling")["asset_ids"][0]
    manifest=ws/"reports"/"evidence"
    manifest.mkdir(parents=True,exist_ok=True)
    data={
        "version":"event-context-embedding-v1","assetId":aid,
        "events":[{
            "id":"event-context-01","group":"conflicting_labels_unverified",
            "startMs":9000,"endMs":45000,
            "phases":[],
        }]
    }
    (manifest/"manifest.json").write_text(json.dumps(data))
    result=ws/"reports"/"ranked"
    assert main(["--workspace",str(ws),"rank-moments",
                 "--input",str(manifest/"manifest.json"),
                 "--output-dir",str(result)])==0
    parsed=json.loads(capsys.readouterr().out)
    assert parsed["momentCount"]==1
    assert parsed["sourceVideosDecoded"]==0
    saved=json.loads((result/"ranking.json").read_text())
    assert saved["queues"]["share"]==[]
    assert (result/"review.html").is_file()
    # Exact moment ID can be annotated later with independent human review.
    source_id=f'{aid}:event-context-01'
    reviews=[{
        "momentId":source_id,
        "humanReview":"keep","taxonomyStatus":"unverified",
        "signals":{
            "eventSignificance":.95,"subjectVisibility":.74,
            "imageQuality":.50,"narrativeCompleteness":.83,
            "visualNovelty":.82,"learningValue":.7,
            "investigationPotential":.8,"uncertainty":.4,
        },
    }]
    override=ws/"reports"/"reviews.json"
    override.write_text(json.dumps(reviews))
    second=ws/"reports"/"ranked-reviewed"
    output=create_rank_report(ws,manifest/"manifest.json",second,
                               review_file=override,limit=3)
    assert output["queues"]["share"]==[source_id]
    ranked=json.loads((second/"ranking.json").read_text())
    assert ranked["moments"][0]["taxonomyStatus"]=="unverified"
    assert ranked["moments"][0]["reviewEligibility"]["autoPublish"] is False
    assert src.read_bytes()==raw



def test_self_asserted_verified_species_without_provenance_never_uses_rarity():
    unsupported=moment("claimed-rare",taxonomyStatus="verified_species",
                       signals={"verifiedTaxonRarity":1.0})
    scored=score_moment(unsupported)
    assert scored["signalsUsed"]["verifiedTaxonRarity"] is None
    assert scored["taxonReviewProvenanceProvided"] is False
    assert "self_asserted_taxon_missing_review_provenance" in scored["reasonCodes"]

