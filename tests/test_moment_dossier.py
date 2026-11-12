"""Moment dossier seeds never manufacture full-video, physics or intent truth."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from adventure_intelligence.ktide_local_judge import validate_judge_proposal
from adventure_intelligence.moment_dossier import (
    VERSION, make_dossier_seed_report, source_dossier_from_judge,
)


def judge_row(aid: str, *, success: bool = True) -> dict:
    frames = [{
        "frameIndex": i,
        "requestedMs": (i+1)*30000,
        "decodedPtsMs": (i+1)*30000,
        "timestampBasis": "decoded_frame_pts",
        "imageSha256": f"{i+2:064x}",
    } for i in range(4)]
    proposal = {
        "subsport": "skiing_moguls",
        "sceneSummary": "Snow and irregular terrain are visible.",
        "sceneContext": "snow_mountain",
        "tags": ["snow", "bumps"],
        "frameObservations": [
            {"frameIndex": 1, "observation": "Irregular snow texture"},
        ],
        "eventHints": [
            {"kind": "changing camera direction",
             "firstFrameIndex": 1, "lastFrameIndex": 2},
        ],
        "featureCandidates": [{
            "dimension": "terrain_complexity",
            "frameIndices": [1, 2],
            "observation": "Uneven terrain appears ahead",
        }],
        "uncertainties": ["Exact resort, knee angles and skill unknown"],
        "cameraQuality": "mixed",
        "confidence": 0.65,
    }
    return {
        "sourceSha256": aid,
        "provisionalGroupId": "provisional_capture-20260309-abcdef12",
        "sourceShaVerifiedForJudge": success,
        "judgeStatus": "model_hypothesis" if success else "failed",
        "judgeProposal": (validate_judge_proposal(proposal, 4)
                          if success else None),
        "observedFrames": frames if success else [],
        "failureType": "ValueError" if not success else None,
    }


def report():
    return {
        "version": "klip-ktide-local-vision-judge-v1",
        "model": "gemma3:4b",
        "frameCountPerVideo": 4,
        "sourceCount": 2,
        "successes": 1,
        "proposals": [
            judge_row("a"*64),
            judge_row("b"*64, success=False),
        ],
    }


def test_dossier_is_grounded_sparse_and_preserves_unknowns():
    doc = make_dossier_seed_report(report())
    assert doc["version"] == VERSION
    assert doc["sourceCount"] == 2
    assert doc["sourceOverviewHypotheses"] == 1
    assert doc["unknownOrFailedSources"] == 1
    assert not doc["eventLevelDescriptionsQualified"]
    assert not doc["newModelInference"]
    dossier = doc["dossiers"][0]
    assert dossier["scope"] == "whole_source_sparse_overview_not_event"
    assert dossier["sourceIntervalMs"] is None
    assert dossier["sampledSpanMs"] == [30000, 120000]
    assert dossier["sceneNarrative"]["overviewHypothesis"] is not None
    assert dossier["sceneNarrative"]["eventArc"] is None
    assert dossier["placeAndOuting"]["verifiedLocation"] is None
    assert dossier["placeAndOuting"]["specificRunOrDiveSite"] is None
    assert dossier["unobservableWithoutIndependentEvidence"]["muscleActivation"] is None
    assert dossier["unobservableWithoutIndependentEvidence"]["animalSpecies"] is None
    assert dossier["unobservableWithoutIndependentEvidence"]["cameraOperatorIntention"] is None
    assert dossier["editorial"]["relativeArchiveQualityPercentile"] is None
    assert dossier["editorial"]["momentworthinessScore"] is None
    assert dossier["editorial"]["suggestedEdits"] == []
    assert not dossier["editorial"]["originalMediaDeletionAuthorized"]
    assert dossier["independentTripVerified"] is False
    assert {c["status"] for c in dossier["claimRecords"]} == {"model_hypothesis"}
    assert all(not c["scientificallyVerified"] for c in dossier["claimRecords"])
    seen = {e["evidenceId"] for e in dossier["frameEvidence"]}
    assert all(set(c["evidenceIds"]) <= seen and c["evidenceIds"]
               for c in dossier["claimRecords"])
    assert not doc["dossiers"][1]["claimRecords"]
    assert doc["dossiers"][1]["sceneNarrative"]["overviewHypothesis"] is None


def test_dossier_refuses_proposal_without_supported_frame_evidence():
    row = judge_row("a"*64)
    row["observedFrames"][1]["imageSha256"] = "not-a-checksum"
    with pytest.raises(ValueError, match="checksum"):
        source_dossier_from_judge(row)
    row = judge_row("a"*64)
    row["judgeProposal"]["featureCandidates"][0]["measuredValue"] = 0.85
    with pytest.raises(ValueError, match="must remain unknown"):
        source_dossier_from_judge(row)
    row = judge_row("a"*64)
    row["judgeProposal"]["sceneSummary"] = "Speculative resort ID with no evidence"
    # Text itself is a model hypothesis, never promoted as verification.
    doc = source_dossier_from_judge(row)
    assert doc["claimRecords"][0]["status"] == "model_hypothesis"
    assert doc["placeAndOuting"]["verifiedLocation"] is None


def test_dossier_report_refuses_duplicate_sources_and_count_tamper():
    bad = report()
    bad["proposals"][1]["sourceSha256"] = bad["proposals"][0]["sourceSha256"]
    with pytest.raises(ValueError, match="Duplicate source"):
        make_dossier_seed_report(bad)
    bad = report()
    bad["successes"] = 2
    with pytest.raises(ValueError, match="success total"):
        make_dossier_seed_report(bad)
    bad = report()
    bad["proposals"][1]["judgeProposal"] = {"fabrication": True}
    with pytest.raises(ValueError, match="Rejected proposal"):
        make_dossier_seed_report(bad)


def test_dossier_cli_creates_new_report_without_touching_source(tmp_path):
    ws = tmp_path / "workspace"
    reports = ws / "reports"
    reports.mkdir(parents=True)
    prior = reports / "ktide-judge-synthetic"
    prior.mkdir()
    input_path = prior / "proposals.json"
    input_path.write_text(json.dumps(report()))
    project = Path(__file__).resolve().parents[1]
    args = [
        sys.executable, str(project / "scripts/klip_moment_dossiers.py"),
        "--workspace", str(ws),
        "--judge-report", str(input_path),
        "--name", "moment-dossier-pilot-test",
    ]
    run = subprocess.run(args, capture_output=True, text=True, cwd=project)
    assert run.returncode == 0, run.stderr
    output = reports / "moment-dossier-pilot-test" / "dossiers.json"
    doc = json.loads(output.read_text())
    assert doc["sourceOverviewHypotheses"] == 1
    assert input_path.read_text() == json.dumps(report())
    repeated = subprocess.run(args, capture_output=True, text=True, cwd=project)
    assert repeated.returncode != 0
    assert "Refusing to overwrite" in repeated.stderr
