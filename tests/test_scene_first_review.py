"""Scene-first owner review, legacy compatibility and evidence isolation tests.

Only synthetic preview/owner inputs. No camera source, network, or original media.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from adventure_intelligence import complete_highlight_rerank as ranking
from adventure_intelligence import marine_encounter_review as marine
from adventure_intelligence import scene_review_narrative as scene


def candidate(id_suffix: str = "1", *, tags=("marine_ray",)):
    return {
        "id":id_suffix*24,
        "origin":"autoscout_seven_stills",
        "activity":"snorkeling",
        "sourceSha256":id_suffix*64,
        "sourceStartMs":1000,
        "sourceEndMs":21000,
        "categoryProposals":list(tags),
        "qualityFlagProposals":[],
        "ownerReviewed":False,
        "humanKeepConfirmed":False,
        "heuristic":{"reviewOpportunityScore":2.0},
        "candidateTitle":"Potential snorkeling scene",
    }


def document(sample, item, *, version=marine.LABEL_VERSION):
    return {
        "version":version,
        "sampleId":sample["sampleId"],
        "reviews":[item],
    }


def basic_label(c, *, decision="keep", presence="absent", animal="none"):
    return {
        "recordId":c["id"],"sourceSha256":c["sourceSha256"],
        "decision":decision,"animalPresence":presence,
        "animalType":animal,"behavior":"none" if presence=="absent" else "swimming",
        "visibility":4,"framing":5,"technicalQuality":4,
        "narrativeCompleteness":5,"behaviorInterest":4,
        "eventStartMs":None,"eventEndMs":None,
    }


def test_scenic_no_animal_can_be_human_kept_with_why_and_story_role():
    c=candidate()
    m=marine.manifest([c],"a"*64,"b"*64,"../pilot/media")
    annotation={
        **basic_label(c),
        **scene.empty_narrative(),
        "sceneDescription":"POV reef exploration with colorful water and the start of a reveal.",
        "whyKeep":"Previously enjoyed the scenery; a good establishing scene even without an animal.",
        "sceneType":"scenic_underwater",
        "storyRole":"establishing_scene",
        "filmingIntent":"intentional",
        "personalImportance":"high",
        "sceneInterest":5,
    }
    validated=marine.validate_review(m,document(m,annotation))
    assert validated==[annotation]
    enriched,meta=marine.merge_owner([c],m,document(m,annotation))
    assert meta["counts"]["kept_scenes_without_confirmed_animal"]==1
    assert enriched[0]["humanMarineReview"]["sceneDescription"]==annotation["sceneDescription"]
    assert enriched[0]["humanSceneInterpretation"]["whyKeep"]==annotation["whyKeep"]
    assert enriched[0]["humanConfirmedVisibleAnimal"] is False
    assert enriched[0]["humanKeepConfirmed"] is True
    rank=ranking.rank_review_queue(enriched,top=1)
    assert rank["top"][0]["rankClass"]=="human_kept_underwater_scene"


def test_legacy_animal_review_survives_without_invented_personal_narrative():
    c=candidate()
    sample=marine.manifest([c],"a"*64,"b"*64,"../pilot/media")
    old=basic_label(c,presence="present",animal="ray")
    doc=document(sample,old,version=marine.LEGACY_LABEL_VERSION)
    assert marine.validate_review(sample,doc)==[old]
    new,meta=marine.merge_owner([c],sample,doc)
    assert new[0]["humanSceneInterpretation"] is None
    assert new[0]["humanConfirmedVisibleAnimal"] is True
    assert meta["supportsLegacyV1AndSceneFirstV2"] is True
    # Old unannotated v2 field sets stay accepted to avoid breaking older callers.
    assert marine.validate_review(sample,{**doc,"version":marine.LABEL_VERSION})==[old]


@pytest.mark.parametrize("bad_key,bad_value",[
    ("sceneDescription","X"*2401),
    ("sceneDescription",25),
    ("whyKeep","A"*1801),
    ("whyKeep","unsafe\x00note"),
    ("sceneType","alien_species_confirmed"),
    ("storyRole","completed_jump_verified"),
    ("filmingIntent","certainly_meant_to_find_ray"),
    ("personalImportance","most_important_ever"),
    ("sceneInterest",6),
    ("sceneInterest",True),
])
def test_reject_malformed_scene_fields(bad_key,bad_value):
    c=candidate()
    sample=marine.manifest([c],"a"*64,"b"*64,"../pilot/media")
    annotation={**basic_label(c),**scene.empty_narrative()}
    annotation[bad_key]=bad_value
    with pytest.raises(ValueError):
        marine.validate_review(sample,document(sample,annotation))


def test_partial_narrative_is_invalid_and_mismatched_source_still_rejected():
    c=candidate()
    sample=marine.manifest([c],"a"*64,"b"*64,"../pilot/media")
    partial={**basic_label(c),"sceneDescription":"Beautiful reef with no animal."}
    with pytest.raises(ValueError,match="incomplete"):
        marine.validate_review(sample,document(sample,partial))
    complete={**basic_label(c),**scene.empty_narrative()}
    complete["sourceSha256"]="f"*64
    with pytest.raises(ValueError,match="identity"):
        marine.validate_review(sample,document(sample,complete))


def test_owner_text_is_escaped_in_ranked_gallery_and_not_scored_as_model_truth():
    c=candidate()
    m=marine.manifest([c],"a"*64,"b"*64,"../pilot/media")
    annotation={**basic_label(c),**scene.empty_narrative()}
    annotation["sceneDescription"]="<script>alert('xss')</script> Beautiful reef"
    annotation["whyKeep"]="Good transition & calming water"
    annotation["storyRole"]="transition"
    merged,_=marine.merge_owner([c],m,document(m,annotation))
    assert "humanMarineQualityScore" in merged[0]
    assert merged[0]["humanSceneInterpretation"]["sceneInterest"] is None
    top=ranking.rank_review_queue(merged,top=1)["top"]
    top[0].update(mediaFile="01-test.mp4",rankExplanation="Human owner described the scene")
    html=ranking.gallery_html({"topRankedClips":top})
    assert "&lt;script&gt;" in html
    assert "<script>alert(" not in html
    assert "Good transition &amp; calming water" in html
    assert "Story role: transition" in html


def test_scene_first_ui_is_unblinded_neither_to_model_tags_nor_prior_keep():
    c=candidate()
    m=marine.manifest([c],"a"*64,"b"*64,"../pilot/media")
    html=marine.blind_review_html(m)
    assert "What is happening in the scene?" in html
    assert "Why is this worth saving" in html
    assert 'id="sceneDescription"' in html
    assert 'id="whyKeep"' in html
    assert 'id="storyRole"' in html
    assert "Optional wildlife evidence" in html
    assert "v2" in marine.LABEL_VERSION
    assert '"categoryProposals"' not in html
    assert c["id"] in html
    assert "connect-src 'none'" in html
    assert "__VERSION__" not in html
    assert "__LEGACY__" not in html


@pytest.mark.skipif(not shutil.which("node"),reason="Node.js unavailable")
def test_scene_first_browser_javascript_syntax(tmp_path):
    import re
    m=marine.manifest([candidate()],"a"*64,"b"*64,"../pilot/media")
    html=marine.blind_review_html(m)
    match=re.search(r"<script>(.*?)</script>",html,re.S)
    assert match
    file=tmp_path/"scene-first.js"
    file.write_text(match.group(1))
    got=subprocess.run(["node","--check",str(file)],
                       capture_output=True,text=True,timeout=15)
    assert got.returncode==0,got.stderr


def test_existing_frozen_marine_study_sidecar_upgrade(tmp_path):
    """Reuses a prior synthetic report, preserving old HTML and manifest."""
    file=Path(__file__).resolve().parent/"test_complete_highlight_rerank.py"
    spec=importlib.util.spec_from_file_location("klip_previous_rerank_tests",file)
    old_test=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old_test)
    args,_=old_test._fixture(tmp_path,video=False)

    path=Path(__file__).resolve().parents[1]/"scripts"/"klip_prepare_marine_encounter_review.py"
    spec=importlib.util.spec_from_file_location("klip_scene_upgrade",path)
    prep=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prep)
    output=args.workspace/"reports"/"marine-existing"
    setup=argparse.Namespace(workspace=args.workspace,pilot=args.pilot,
                             autoscout=args.autoscout,output=output,
                             run=True,upgrade_review_page=False)
    created=prep.execute(setup)
    assert created["snorkelingPreviews"]==1
    old_manifest=(output/"marine-sample.json").read_bytes()
    old_html=(output/"review.html").read_bytes()
    setup.run=False
    setup.upgrade_review_page=True
    upgraded=prep.execute(setup)
    assert upgraded["sampleUnchanged"] is True
    assert upgraded["status"]=="scene_first_review_page_ready_legacy_page_untouched"
    assert (output/"review-scene-first.html").is_file()
    assert (output/"marine-sample.json").read_bytes()==old_manifest
    assert (output/"review.html").read_bytes()==old_html
    assert prep.execute(setup)["reviewPage"]==upgraded["reviewPage"]
    assert not (output/"private-owner-review.json").exists()
