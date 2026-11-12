"""Evidence-first scene-dossier seeds from already completed local judge reports.

No video decoding, model inference, source access, scoring, private
geolocation, human-intent inference, editing or publishing. The source
judge's descriptions remain model hypotheses, never physical ground truth.
A seed is for enrichment by future dense video and verified context.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json

from .encoder_dataset import SHA

VERSION = "klip-moment-dossier-seed-v1"
JUDGE_VERSION = "klip-ktide-local-vision-judge-v1"


def _expect(condition: bool, explanation: str):
    if not condition:
        raise ValueError(explanation)


def _reference_frames(frames: list[dict], indices: list[int]) -> list[str]:
    _expect(isinstance(indices, list) and
            all(type(i) is int and 0 <= i < len(frames) for i in indices),
            "Model description has invalid sampled frame references")
    return [f"frame-{i}" for i in dict.fromkeys(indices)]


def source_dossier_from_judge(row: dict) -> dict:
    """Conservatively translate a source-level judgment to an enrichment seed.

    No inferred clip boundaries: a source-level four-frame judgment is not
    an identified event, complete ski run, dive, or moment.
    """
    _expect(isinstance(row, dict), "Expected local judge source record")
    aid = row.get("sourceSha256")
    _expect(isinstance(aid, str) and SHA.fullmatch(aid),
            "Invalid source identity in judge report")
    frames = row.get("observedFrames")
    _expect(isinstance(frames, list) and len(frames) <= 8,
            "Invalid sampled-frame provenance")
    evidence = []
    for i, frame in enumerate(frames):
        _expect(isinstance(frame, dict) and frame.get("frameIndex") == i,
                "Invalid or reordered frame provenance")
        t, img = frame.get("requestedMs"), frame.get("imageSha256")
        _expect(type(t) is int and t >= 0 and
                isinstance(img, str) and SHA.fullmatch(img),
                "Invalid frame-time or frame-checksum receipt")
        if evidence:
            _expect(t > evidence[-1]["requestedMs"],
                    "Frame timestamps must strictly increase")
        evidence.append({
            "evidenceId": f"frame-{i}",
            "sourceSha256": aid,
            "requestedMs": t,
            "decodedPtsMs": frame.get("decodedPtsMs"),
            "timestampBasis": frame.get("timestampBasis"),
            "imageSha256": img,
            "kind": "original_decoded_frame_receipt",
        })
    status = row.get("judgeStatus")
    _expect(status in {"model_hypothesis","failed","not_run"},
            "Unrecognized source judging status")
    proposal = row.get("judgeProposal")
    if status != "model_hypothesis":
        _expect(proposal is None, "Rejected proposal cannot become scene claim")
    else:
        from .ktide_local_judge import validate_judge_proposal
        _expect(row.get("sourceShaVerifiedForJudge") is True and
                2 <= len(frames) <= 8, "Valid source/frame receipts required")
        _expect(isinstance(proposal, dict),
                "Missing normalized local judge response")
        # Normalized proposals append evidenceSource/measuredValue for
        # feature candidates. Verify they were never turned into scores.
        raw = dict(proposal)
        features = raw.get("featureCandidates")
        _expect(isinstance(features, list), "Invalid feature candidate list")
        raw["featureCandidates"] = []
        for feature in features:
            _expect(isinstance(feature, dict) and
                    set(feature) == {
                        "dimension","frameIndices","observation",
                        "evidenceSource","measuredValue"
                    } and feature["evidenceSource"] == "model_hypothesis"
                    and feature["measuredValue"] is None,
                    "Previously unmeasured model feature must remain unknown")
            raw["featureCandidates"].append({
                k: feature[k] for k in
                ("dimension","frameIndices","observation")})
        normalized = validate_judge_proposal(raw, len(frames))
        _expect(normalized == proposal,
                "Judge proposal normalization/identity mismatch")

    claims = []
    def claim(kind: str, text: str, indices: list[int]):
        ids = _reference_frames(evidence, indices)
        _expect(bool(ids), "A model claim requires sampled-frame evidence")
        claims.append({
            "claimId": f"claim-{len(claims)}",
            "kind": kind,
            "statement": text,
            "evidenceIds": ids,
            "status": "model_hypothesis",
            "scientificallyVerified": False,
        })

    if status == "model_hypothesis":
        all_indices = list(range(len(evidence)))
        claim("overall_scene", proposal["sceneSummary"], all_indices)
        claim("subsport", proposal["subsport"], all_indices)
        claim("scene_context", proposal["sceneContext"], all_indices)
        claim("camera_quality", proposal["cameraQuality"], all_indices)
        for item in proposal["frameObservations"]:
            claim("frame_observation_hypothesis", item["observation"],
                  [item["frameIndex"]])
        for item in proposal["featureCandidates"]:
            claim("ksre_" + item["dimension"], item["observation"],
                  item["frameIndices"])
        for item in proposal["eventHints"]:
            claim("sparse_event_hint:" + item["kind"], item["kind"],
                  list(range(item["firstFrameIndex"],
                             item["lastFrameIndex"]+1)))

    overview = proposal["sceneSummary"] if status == "model_hypothesis" else None
    return {
        "version": VERSION,
        "dossierId": "source-overview-" + sha256(aid.encode()).hexdigest()[:24],
        "sourceSha256": aid,
        "scope": "whole_source_sparse_overview_not_event",
        "judgingStatus": status,
        "provisionalCaptureGroupId": row.get("provisionalGroupId"),
        "independentTripVerified": False,
        "sourceIntervalMs": None,
        "sampledFrameCount": len(evidence),
        "sampledSpanMs": ([evidence[0]["requestedMs"],
                           evidence[-1]["requestedMs"]]
                          if evidence else None),
        "frameEvidence": evidence,
        "claimRecords": claims,
        "sceneNarrative": {
            "overviewHypothesis": overview,
            "eventArc": None,
            "terrainAndEnvironment": None,
            "visibleTechnique": None,
            "visualStyle": None,
            "possibleMomentMeaning": None,
            "uncertainties": (proposal["uncertainties"]
                              if status == "model_hypothesis" else []),
        },
        "placeAndOuting": {
            "verifiedLocation": None,
            "specificRunOrDiveSite": None,
            "relatedSourceEvidence": [],
            "locationEvidence": [],
        },
        "unobservableWithoutIndependentEvidence": {
            "skiEdgeAngleDegrees": None,
            "lowerLimbJointAngles": None,
            "muscleActivation": None,
            "animalSpecies": None,
            "cameraOperatorIntention": None,
        },
        "editorial": {
            "ownerProvidedMeaning": None,
            "filmingIntentHypothesis": None,
            "accidentalCaptureHypothesis": None,
            "relativeArchiveQualityPercentile": None,
            "matchedQualityCohortSize": None,
            "momentworthinessScore": None,
            "eventBoundariesMs": None,
            "suggestedEdits": [],
            "approvedForPublication": False,
            "originalMediaDeletionAuthorized": False,
        },
        "provenance": {
            "allNarrativeClaimsAreModelHypotheses": True,
            "modelRunPerformedForDossierSeed": False,
            "sourceVideoReadForDossierSeed": False,
            "sourceVideoEdited": False,
        },
    }


def make_dossier_seed_report(judge_report: dict) -> dict:
    _expect(isinstance(judge_report, dict) and
            judge_report.get("version") == JUDGE_VERSION and
            isinstance(judge_report.get("proposals"), list),
            "Expected original local vision judge proposals JSON")
    rows = judge_report["proposals"]
    _expect(bool(rows) and len(rows) <= 32 and
            judge_report.get("sourceCount") == len(rows),
            "Invalid judge source roster")
    seen, dossiers = set(), []
    for row in rows:
        dossier = source_dossier_from_judge(row)
        aid = dossier["sourceSha256"]
        _expect(aid not in seen, "Duplicate source in judge proposals")
        seen.add(aid)
        dossiers.append(dossier)
    success = sum(d["judgingStatus"] == "model_hypothesis" for d in dossiers)
    _expect(success == judge_report.get("successes"),
            "Judge success total does not match individual receipts")
    coverage = Counter(d["judgingStatus"] for d in dossiers)
    return {
        "version": VERSION,
        "sourceCount": len(dossiers),
        "sourceOverviewHypotheses": success,
        "unknownOrFailedSources": len(dossiers) - success,
        "sourceStatusCounts": dict(sorted(coverage.items())),
        "sourceVideosRead": False,
        "newModelInference": False,
        "editOrDeletionPerformed": False,
        "eventLevelDescriptionsQualified": False,
        "physicalOrGeographicClaimsVerified": False,
        "dossiers": dossiers,
    }
