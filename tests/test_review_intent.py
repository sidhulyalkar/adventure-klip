"""Privacy-preserving offline review intent tests, synthetic records only."""
from hashlib import sha256
import json

import pytest

from adventure_intelligence.review_intent import analyze_export, REVIEW_VERSION


def packet():
    rid = "a" * 24
    manifest = {"records": [{"recordId": rid, "event": {
        "startMs": 1000, "endMs": 30000}}]}
    blob = (json.dumps(manifest, sort_keys=True) + "\n").encode()
    review = {
        "recordId": rid, "annotatorId": "test-reviewer",
        "role": "athlete", "goal": "athletic_skill",
        "choice": "keep", "outcome": "failed", "outcomeVisible": "yes",
        "technicalQuality": 4, "aestheticQuality": None,
        "eventReadability": 3,
        "eventPeakMs": 10000, "resolutionMs": 12000,
        "rationale": "This fall is useful to keep to analyze technique.",
    }
    export = {"version": REVIEW_VERSION,
              "manifestSha256": sha256(blob).hexdigest(),
              "reviews": [review]}
    return export, blob


def test_routes_failure_out_of_highlights_and_verifies_manifest_digest():
    export, manifest = packet()
    result = analyze_export(export, manifest)
    assert result["manifestDigestVerified"]
    assert result["reviewCount"] == 1
    assert result["destinations"] == {"technique_analysis": 1}
    assert result["ratedFieldCounts"] == {
        "eventReadability": 1, "technicalQuality": 1}
    assert not result["trainingPerformed"]
    assert result["sourceVideoReads"] == 0
    assert not result["autoSourceDeleteOrPublication"]


def test_without_manifest_makes_no_unverified_claims():
    export, _ = packet()
    result = analyze_export(export)
    assert not result["manifestDigestVerified"]
    assert not result["previewVerificationPerformed"]
    assert not result["records"][0]["sourceEventsIndependentlyVerified"]


def test_mismatched_digest_and_unknown_record_rejected():
    export, manifest = packet()
    with pytest.raises(ValueError, match="digest mismatch"):
        analyze_export(export, manifest + b" ")
    export["reviews"][0]["recordId"] = "b" * 24
    with pytest.raises(ValueError, match="not present"):
        analyze_export(export, manifest)


def test_out_of_bounds_time_and_unseen_failure_rejected():
    export, manifest = packet()
    export["reviews"][0]["eventPeakMs"] = 40000
    with pytest.raises(ValueError, match="event marker"):
        analyze_export(export, manifest)
    export["reviews"][0]["eventPeakMs"] = 10000
    export["reviews"][0]["outcomeVisible"] = "ambiguous"
    with pytest.raises(ValueError, match="Outcome requires"):
        analyze_export(export, manifest)


def test_duplicate_review_ids_are_rejected():
    export, _ = packet()
    export["reviews"].append(dict(export["reviews"][0]))
    with pytest.raises(ValueError, match="Duplicate"):
        analyze_export(export)


def test_rejected_media_deletion_requests_are_only_flags():
    export, _ = packet()
    review = export["reviews"][0]
    review.update(choice="skip", outcome="unknown", outcomeVisible="ambiguous",
                  rationale="Bad accidental video, delete this recording")
    result = analyze_export(export)
    route = result["records"][0]["intent"]
    assert route["deleteAuthorized"] is False
    assert "manual_only_discard_request" in route["editActions"]
    assert result["autoSourceDeleteOrPublication"] is False


def test_aesthetic_and_family_memory_not_athletic_by_default():
    export, _ = packet()
    review = export["reviews"][0]
    review.update(choice="keep", outcome="unknown", outcomeVisible="ambiguous",
                  goal="athletic_skill",
                  rationale="Scenic lake view is great as a transition or intro")
    result = analyze_export(export)
    assert result["destinations"] == {"scenic_transition": 1}
    review.update(goal="personal_memory",
                  rationale="My first kayak trip with my family")
    result = analyze_export(export)
    assert result["destinations"] == {"personal_memory": 1}


def test_cli_can_read_synthetic_export_without_video(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    export, manifest = packet()
    root = Path(__file__).resolve().parents[1]
    review_path = tmp_path / "reviews.json"
    manifest_path = tmp_path / "manifest.json"
    review_path.write_text(json.dumps(export))
    manifest_path.write_bytes(manifest)
    p = subprocess.run(
        [sys.executable, str(root / "scripts" / "klip_review_intent.py"),
         "--reviews", str(review_path), "--manifest", str(manifest_path)],
        capture_output=True, text=True, cwd=root, timeout=20,
    )
    assert p.returncode == 0, p.stderr
    data = json.loads(p.stdout)
    assert data["manifestDigestVerified"]
    assert data["sourceVideoReads"] == 0
