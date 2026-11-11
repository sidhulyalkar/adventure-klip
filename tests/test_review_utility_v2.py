"""Synthetic-only regressions for human preference routing.

No private review text, identities, source recordings, or locations.
"""
from adventure_intelligence.review_utility_v2 import editorial_route, routing_summary
from adventure_intelligence import campaign_reels as cr


def review(note, *, choice="keep", goal="athletic_skill", outcome="unknown",
           peak=None, resolution=None):
    return {
        "rationale": note, "preference": choice, "highlightGoal": goal,
        "observedOutcome": outcome, "eventPeakMs": peak,
        "resolutionMs": resolution,
    }


def test_separates_use_from_quality_and_outcome():
    assert editorial_route(review(
        "This fall is worth keeping to analyze technique", outcome="failed"
    ))["destination"] == "technique_analysis"
    assert editorial_route(review(
        "First day snorkeling with my family, preserve only as personal memories",
        goal="personal_memory"))["destination"] == "personal_memory"
    assert editorial_route(review(
        "Nice lake panorama for an intro transition"
    ))["destination"] == "scenic_transition"
    assert editorial_route(review(
        "Great school of fish seen close to the coral reef"
    ))["destination"] == "nature_encounter"
    assert editorial_route(review(
        "Fast controlled bike line through technical terrain"
    ))["destination"] == "sport_action"


def test_separates_human_keep_from_sport_reel_eligibility():
    for note, expected in [
        ("Great tree ski line and smooth turn", True),
        ("Keep this crash for technique analysis only", False),
        ("First paddle with my family, this is a personal memory", False),
        ("Not worth keeping, not highlighting", False),
    ]:
        assert editorial_route(review(note))[
            "eligibleForSportHighlightDraft"] is expected


def test_false_positive_delete_and_uncertainty_never_destructive():
    for choice in ("skip", "neither", "cannot_tell"):
        route = editorial_route(review(
            "Delete this boring clip", choice=choice))
        assert not route["deleteAuthorized"]
        assert not route["publicationAuthorized"]
        assert not route["eligibleForSportHighlightDraft"]
    route = editorial_route(review("Delete this clip", choice="skip"))
    assert "manual_only_discard_request" in route["editActions"]


def test_time_markers_are_not_trim_endpoints():
    route = editorial_route(review(
        "Trim the recording around this encounter", peak=35000,
        resolution=42000))
    assert route["humanTimeAnchorsMs"] == {
        "eventPeak": 35000, "observedResolution": 42000}
    assert not route["autoTrimAuthorized"]
    assert "retrim_with_verified_source_times" in route["editActions"]


def test_context_and_recovery_flags_do_not_automatically_edit():
    route = editorial_route(review(
        "Include next 10-20 seconds and entire jump line after this clip"))
    assert "inspect_adjacent_source_context" in route["editActions"]
    assert not route["autoTrimAuthorized"]
    assert "compare_alternates_before_final_selection" in editorial_route(
        review("There are many moments like this, compare alternatives")
    )["editActions"]
    assert "isolate_verified_underwater_spans" in editorial_route(
        review("Great reef, but camera leaves the water")
    )["editActions"]


def test_human_high_priority_and_sparse_ratings():
    top = editorial_route(review(
        "Must highlight the complete verified subject pass"))
    assert top["editorialPriority"] == "explicit_top_pick"
    assert editorial_route(review(
        "Nice ordinary scene"))["editorialPriority"] == "ordinary_keep_or_unknown"


def test_ambiguous_keep_does_not_become_reel():
    route = editorial_route(review(
        "Run with friends not worth keeping, not highlighting"))
    assert route["destination"] == "needs_human_routing"
    assert "resolve_conflicting_review_choice" in route["editActions"]
    assert not route["eligibleForSportHighlightDraft"]


def test_multiple_goals_are_not_collapsed_into_default_goal():
    route = editorial_route(review(
        "Difficult snorkel, an athletic achievement with beautiful coral",
        goal="personal_memory"))
    assert route["destination"] == "personal_memory"
    assert "sport_action_for_human_review" in route["additionalHumanReviewUses"]


def test_summary_does_not_claim_learning_or_video_examination():
    report = routing_summary([
        review("Nice fast ski action"),
        review("Great reef encounter"),
        review("Uninformative clip", choice="skip"),
    ])
    assert report["reviewCount"] == 3
    assert report["destinations"]["not_selected"] == 1
    assert report["userDataTrained"] is False


def test_reviewed_sport_selection_excludes_other_destinations():
    base = {
        "sport": "ski", "selectionRole": "motion_change_hypothesis",
        "humanPreference": "keep", "sourceStartMs": 10000,
        "sourceEndMs": 20000, "humanPreferenceTieBreaker": None,
    }
    rows = []
    for i, note in enumerate((
        "This fall is worth keeping to analyze my technique",
        "Amazing ski jump with complete approach and landing",
        "This first trip with my family is a personal memory",
    )):
        rows.append({
            **base, "recordId": str(i), "sourceAssetSha256": str(i) * 64,
            "humanCurationV2": editorial_route(review(note)),
        })
    selected = cr._choose(rows, "ski", "reviewed", 60, 4)
    assert len(selected) == 1
    assert selected[0]["recordId"] == "1"
