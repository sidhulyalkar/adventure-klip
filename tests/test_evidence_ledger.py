"""Owner evidence ledger: synthetic exports only, no media or catalog access."""
from __future__ import annotations

import re
import shutil
import subprocess

import pytest

from adventure_intelligence import evidence_ledger as ledger
from adventure_intelligence import marine_encounter_review as marine
from adventure_intelligence import ski_run_context as ski

SHA_A, SHA_B, DIGEST = "a" * 64, "b" * 64, "d" * 64


def clip(suffix, sha, start=1000, end=21000):
    return {"id": suffix * 24, "origin": "autoscout_seven_stills", "activity": "snorkeling",
            "sourceSha256": sha, "sourceStartMs": start, "sourceEndMs": end}


def label(c, decision="keep", event=(None, None), **extra):
    return {"recordId": c["id"], "sourceSha256": c["sourceSha256"], "decision": decision,
            "animalPresence": "absent", "animalType": "none", "behavior": "none",
            "visibility": 4, "framing": 5, "technicalQuality": 4,
            "narrativeCompleteness": 5, "behaviorInterest": 3,
            "eventStartMs": event[0], "eventEndMs": event[1], **extra}


def narrative(text):
    return {"sceneDescription": text, "whyKeep": "the light", "sceneType": "scenic_underwater",
            "storyRole": "atmosphere", "filmingIntent": "intentional",
            "personalImportance": "high", "sceneInterest": 5}


def marine_export(clips, labels, version=marine.LABEL_VERSION):
    m = marine.manifest(clips, "1" * 64, "2" * 64, "../pilot/media")
    return m, {"version": version, "sampleId": m["sampleId"], "reviews": labels}


def span(sha, start, end, decision, selection="owner_marked_span", goal=None, rid="r"):
    return ledger.record(source_sha=sha, start_ms=start, end_ms=end, decision=decision,
                         selection=selection, schema="test", export_digest=DIGEST,
                         record_id=rid, goal=goal)


def test_marine_v2_keeps_owner_words_and_separates_marked_event():
    c = clip("1", SHA_A)
    m, doc = marine_export([c], [label(c, event=(4000, 9000), **narrative("reef reveal"))])
    window, event = ledger.from_marine(m, doc, DIGEST)
    assert (window["startMs"], window["endMs"]) == (1000, 21000)
    assert window["selection"] == "pipeline_selected_window"
    assert window["detail"]["sceneDescription"] == "reef reveal"
    assert window["outing"] == ledger.UNASSIGNED and window["goal"] == "unspecified"
    assert (event["startMs"], event["endMs"], event["selection"]) == (4000, 9000, "owner_marked_span")


def test_legacy_marine_v1_imports_without_invented_narrative():
    c = clip("1", SHA_A)
    m, doc = marine_export([c], [label(c, "skip")], version=marine.LEGACY_LABEL_VERSION)
    (row,) = ledger.from_marine(m, doc, DIGEST)
    assert row["schema"] == marine.LEGACY_LABEL_VERSION and row["decision"] == "skip"
    assert "sceneDescription" not in row["detail"]


def test_review_for_another_sample_or_source_fails_closed():
    c = clip("1", SHA_A)
    m, doc = marine_export([c], [label(c)])
    with pytest.raises(ValueError):
        ledger.from_marine(m, {**doc, "sampleId": "0" * 64}, DIGEST)
    doc["reviews"][0]["sourceSha256"] = SHA_B
    with pytest.raises(ValueError):
        ledger.from_marine(m, doc, DIGEST)


def test_ski_run_is_owner_marked_and_keeps_feature_markers():
    plan = {"sourceAssetSha256": SHA_A, "recordId": "anchor",
            "contextSourceStartMs": 0, "contextSourceEndMs": 48128}
    doc = {**ski.owner_annotation_template(plan), "runStartMs": 5558, "runEndMs": 39270,
           "runKind": "terrain_park", "preference": "keep",
           "reviewerVerifiedContinuousRun": True,
           "featureMarkers": [{"kind": "jump", "sourceMs": t}
                              for t in (11846, 17960, 23714, 37639)]}
    (row,) = ledger.from_ski_run(plan, doc, DIGEST)
    assert (row["startMs"], row["endMs"], row["selection"]) == (5558, 39270, "owner_marked_span")
    assert len(row["detail"]["featureMarkers"]) == 4
    (unbounded,) = ledger.from_ski_run(plan, ski.owner_annotation_template(plan), DIGEST)
    assert unbounded["selection"] == "pipeline_selected_window"


def test_append_is_idempotent_and_never_rewrites_existing_lines(tmp_path):
    path = tmp_path / "ledger.jsonl"
    first = [span(SHA_A, 0, 5000, "keep")]
    assert ledger.append(path, first) == {"added": 1, "alreadyPresent": 0}
    before = path.read_bytes()
    assert ledger.append(path, first) == {"added": 0, "alreadyPresent": 1}
    assert path.read_bytes() == before
    ledger.append(path, first + [span(SHA_A, 6000, 9000, "skip")])
    assert path.read_bytes().startswith(before) and len(ledger.load(path)) == 2


def test_outing_assignment_does_not_change_evidence_identity():
    row = span(SHA_A, 0, 5000, "keep")
    (tagged,) = ledger.with_outings([row], {SHA_A: "2026-02-park-day"})
    assert tagged["outing"] == "2026-02-park-day" and tagged["evidenceId"] == row["evidenceId"]
    assert ledger.with_outings([row], {SHA_B: "x"})[0]["outing"] == ledger.UNASSIGNED


def test_lookup_never_crosses_sources_and_ranks_by_overlap():
    rows = [span(SHA_A, 0, 20000, "keep", rid="wide"), span(SHA_A, 18000, 30000, "skip", rid="edge"),
            span(SHA_B, 0, 20000, "keep", rid="other")]
    hits = ledger.overlapping(rows, SHA_A, 2000, 12000)
    assert [h["recordId"] for h in hits] == ["wide"] and hits[0]["overlapFraction"] == 1.0
    assert ledger.overlapping(rows, "c" * 64, 0, 20000) == []


def test_conflicts_need_same_source_goal_and_interval_kind():
    keep = span(SHA_A, 0, 10000, "keep", rid="k")
    disagree = span(SHA_A, 2000, 11000, "skip", rid="s")
    assert len(ledger.conflicts([keep, disagree])) == 1
    assert ledger.conflicts([keep, disagree])[0]["resolution"] == "needs_owner_decision"
    assert ledger.conflicts([keep, span(SHA_B, 2000, 11000, "skip")]) == []
    assert ledger.conflicts([keep, span(SHA_A, 2000, 11000, "skip", goal="family_reel")]) == []
    assert ledger.conflicts([keep, span(SHA_A, 9500, 30000, "skip")]) == []
    # Skipping a 20 s window does not contradict keeping a moment inside it.
    window = span(SHA_A, 0, 20000, "skip", selection="pipeline_selected_window")
    assert ledger.conflicts([keep, window]) == []


def test_summary_counts_only_owner_marked_spans_as_recall_evidence():
    rows = [span(SHA_A, 0, 5000, "keep"),
            span(SHA_A, 0, 20000, "keep", selection="pipeline_selected_window")]
    report = ledger.summary(rows)
    assert report["usableForRecallMeasurement"] == 1 and report["records"] == 2


@pytest.mark.parametrize("bad", [
    dict(source_sha="abc"), dict(start_ms=5, end_ms=5), dict(decision="maybe"),
    dict(selection="model_guess"),
])
def test_malformed_evidence_is_rejected(bad):
    args = dict(source_sha=SHA_A, start_ms=0, end_ms=10, decision="keep",
                selection="owner_marked_span", schema="t", export_digest=DIGEST, record_id="r")
    with pytest.raises(ValueError):
        ledger.record(**{**args, **bad})


def test_marine_page_persists_drafts_and_warns_before_unload(tmp_path):
    html = marine.blind_review_html(marine_export([clip("1", SHA_A)], [])[0])
    assert "localStorage.setItem(draftKey" in html and "beforeunload" in html
    assert 'aria-live="polite"' in html
    node = shutil.which("node")
    if node:
        script = tmp_path / "page.js"
        script.write_text(re.search(r"<script>(.*)</script>", html, re.S).group(1))
        assert subprocess.run([node, "--check", str(script)]).returncode == 0
