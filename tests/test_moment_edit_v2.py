"""Klip V2 contracts, fully synthetic and independent of personal footage."""
from __future__ import annotations
from copy import deepcopy
import pytest

from adventure_intelligence.moment_edit_v2 import (
    annotation_template, build_plan, compose, make_moments, validate_annotations,
)

MANIFEST = "a" * 64


def row(i, sport="ski", start=10000, end=30000, *, pref=None, rating=None):
    return {
        "recordId": f"{i:024x}", "sourceAssetSha256": f"{i:064x}",
        "sourceStartMs": start, "sourceEndMs": end, "sport": sport,
        "verifiedProxy": True, "proxySha256": f"{i+10:064x}",
        "selectionRole": "uniform_timeline_control",
        "humanPreference": pref,
        "humanRatings": {"eventReadability": rating, "technicalQuality": None,
                         "aestheticQuality": None} if rating else None,
        "humanCurationV2": None,
    }


def annotated(rows):
    return annotation_template(rows, MANIFEST)


def test_unknown_labels_never_invent_actions_or_trims():
    rows = [row(1), row(2, "snorkeling"), row(3, "bike")]
    plan = build_plan(rows, None, MANIFEST)
    assert plan["ownerAnnotatedMomentCount"] == 0
    assert plan["knownCategoryTaggedMoments"] == 0
    assert not plan["collections"]["ski_park"]["selected"]
    assert not plan["collections"]["snorkel_scenic"]["selected"]
    assert len(plan["collections"]["ski_best"]["selected"]) == 1
    for moment in plan["moments"]:
        assert moment["categoryTags"] == []
        for edit in moment["editVariants"].values():
            assert edit["sourceStartMs"] == moment["sourceStartMs"]
            assert edit["sourceEndMs"] == moment["sourceEndMs"]
            assert not edit["qualityCorrectionApplied"]


def test_multi_label_routes_to_specialist_and_best():
    rows = [row(1), row(2, "snorkeling")]
    owner = annotated(rows)
    owner["moments"][0]["tags"] = ["ski_jump", "ski_trees"]
    owner["moments"][0]["eventStartMs"] = 15000
    owner["moments"][0]["eventEndMs"] = 22000
    owner["moments"][1]["tags"] = ["marine_ray", "marine_reef"]
    result = build_plan(rows, owner, MANIFEST)
    for name in ("ski_park", "ski_trees", "ski_best",
                 "snorkel_wildlife", "snorkel_habitats", "snorkel_best"):
        assert result["collections"][name]["selectedCount"] == 1
    ski = next(m for m in result["moments"] if m["sportHint"] == "ski")
    assert ski["editVariants"]["highlight"]["sourceStartMs"] == 12500
    assert ski["editVariants"]["highlight"]["sourceEndMs"] == 23800
    assert ski["editVariants"]["immersive"]["sourceStartMs"] == 10000
    assert ski["editVariants"]["immersive"]["sourceEndMs"] == 30000


def test_owner_noncore_defects_trim_edges_without_losing_event():
    rows = [row(1, "snorkeling")]
    a = annotated(rows)
    r = a["moments"][0]
    r["tags"] = ["marine_wildlife"]
    r["eventStartMs"], r["eventEndMs"] = 16000, 22000
    r["qualitySpans"] = [
        {"kind": "waterline", "startMs": 10000, "endMs": 14000},
        {"kind": "shake", "startMs": 22500, "endMs": 27000},
    ]
    m = build_plan(rows, a, MANIFEST)["moments"][0]
    cut = m["editVariants"]["highlight"]
    assert cut["sourceStartMs"] == 14000
    assert cut["sourceEndMs"] == 22500
    assert cut["sourceStartMs"] <= 16000 < 22000 <= cut["sourceEndMs"]
    assert any(s.startswith("removed_leading") for s in cut["appliedOperations"])
    assert any(s.startswith("removed_trailing") for s in cut["appliedOperations"])


def test_unavoidable_interior_defect_is_flagged():
    rows = [row(1)]
    a = annotated(rows)
    c = a["moments"][0]
    c["eventStartMs"], c["eventEndMs"] = 15000, 22000
    c["qualitySpans"] = [{"kind": "blur", "startMs": 17000, "endMs": 18500}]
    cut = build_plan(rows, a, MANIFEST)["moments"][0]["editVariants"]["highlight"]
    assert "remaining_quality_defect_inside_cut" in cut["reviewRequired"]
    assert cut["sourceStartMs"] < 17000 < 18500 < cut["sourceEndMs"]


@pytest.mark.parametrize("mutation,match", [
    (lambda doc: doc.update(manifestSha256="f"*64), "manifest"),
    (lambda doc: doc["moments"][0].update(tags=["marine_ray"]), "cross-sport"),
    (lambda doc: doc["moments"][0].update(eventStartMs=8000,eventEndMs=20000), "exceeds"),
    (lambda doc: doc["moments"][0].update(eventStartMs=12000,eventEndMs=None), "as a pair"),
    (lambda doc: doc["moments"][0].update(qualitySpans=[
        {"kind": "shake", "startMs": 12000, "endMs": 50000}]), "exceeds"),
    (lambda doc: doc["moments"][0].update(regionLabel="x\nbad"), "Region"),
])
def test_reject_invalid_owner_annotations(mutation, match):
    rows = [row(1)]
    doc = annotated(rows)
    mutation(doc)
    with pytest.raises(ValueError, match=match):
        validate_annotations(doc, rows, MANIFEST)


def test_source_identity_and_duplicates_are_rejected():
    rows = [row(1)]
    doc = annotated(rows)
    doc["moments"][0]["sourceAssetSha256"] = "f"*64
    with pytest.raises(ValueError, match="source identity"):
        validate_annotations(doc, rows, MANIFEST)
    doc = annotated(rows)
    doc["moments"].append(deepcopy(doc["moments"][0]))
    with pytest.raises(ValueError, match="duplicate"):
        validate_annotations(doc, rows, MANIFEST)


def test_only_owner_keep_enters_reviewed_reels():
    rows = [row(1, pref="keep"), row(2), row(3, pref="skip")]
    owner = annotated(rows)
    owner["moments"][0]["tags"] = ["ski_jump"]
    plan = build_plan(rows, owner, MANIFEST, mode="reviewed")
    assert [x["momentId"] for x in plan["collections"]["ski_best"]["selected"]] == [rows[0]["recordId"]]
    assert plan["collections"]["ski_park"]["selectedCount"] == 1


def test_deterministic_source_diversity_and_reel_budget():
    rows = [row(i, "ski", 0, 20000, pref="keep") for i in range(1, 5)]
    rows.append({**row(9, "ski", 1000, 19000, pref="keep"),
                 "sourceAssetSha256": rows[0]["sourceAssetSha256"]})
    moments = make_moments(rows, {})
    a = compose(moments, mode="reviewed", seconds=45, max_clips=8, per_source=1)
    b = compose(moments, mode="reviewed", seconds=45, max_clips=8, per_source=1)
    assert a == b
    chosen = a["ski_best"]["selected"]
    assert len(chosen) == 2
    assert len({x["sourceAssetSha256"] for x in chosen}) == 2
    assert sum(x["variant"]["durationMs"] for x in chosen) <= 45000


def test_unknown_sport_not_misclassified_and_invalid_proxy_rejected():
    unknown = row(1, sport="unknown")
    assert all(x["selectedCount"] == 0 for x in build_plan(
        [unknown], None, MANIFEST)["collections"].values())
    bad = row(1)
    bad["verifiedProxy"] = False
    with pytest.raises(ValueError, match="Unqualified"):
        make_moments([bad], {})


def test_exploration_does_not_include_explicit_skips():
    rows = [row(1, pref="skip"), row(2)]
    plan = build_plan(rows, None, MANIFEST, mode="exploratory")
    chosen = plan["collections"]["ski_best"]["selected"]
    assert len(chosen) == 1 and chosen[0]["momentId"] == rows[1]["recordId"]


def test_owner_region_only_creates_matching_local_region_reel():
    rows = [row(1, "snorkeling"), row(2, "snorkeling")]
    doc = annotated(rows)
    doc["moments"][0]["regionLabel"] = "Verified snorkel outing area"
    doc["moments"][1]["tags"] = ["marine_scenic"]
    plan = build_plan(rows, doc, MANIFEST)
    region = [k for k in plan["collections"] if k.startswith("snorkel_region_")]
    assert len(region) == 1
    reel = plan["collections"][region[0]]
    assert reel["regionLabel"] == "Verified snorkel outing area"
    assert reel["selectedCount"] == 1
    assert plan["collections"]["snorkel_scenic"]["selectedCount"] == 1


def test_owner_label_is_not_independent_event_verification():
    rows = [row(1)]
    doc = annotated(rows)
    doc["moments"][0]["tags"] = ["ski_rail"]
    moment = build_plan(rows, doc, MANIFEST)["moments"][0]
    assert moment["eventTypeOwnerLabeled"] is True
    assert moment["categoryEvidence"] == "owner_supplied_unindependently_adjudicated"
