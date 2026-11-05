"""Only synthetic records: prove curation preflight refuses false labels and leakage."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json

import pytest

from adventure_intelligence.highlight_curation_validate import (
    audit_highlight_curation_manifest,
)
from adventure_intelligence.cli import main


def _item(asset:str="a"*64,outing:str="outing-001",
          family:str="sourcefamily-001",partition:str="unassigned") -> dict:
    return {
        "version":"klip-highlight-curation-record-v1",
        "recordId":"synthetic-record-001",
        "source":{
            "assetSha256":asset,"sourceFamilyId":family,
            "outingGroupId":outing,"sourceDurationMs":20000,
            "cameraView":"helmet_pov","rawOrEdited":"raw",
        },
        "event":{
            "startMs":1000,"endMs":15000,"peakMs":7500,
            "eventClass":"example synthetic jump","observedOutcome":"unknown",
            "outcomeVisible":"no",
            "phases":[{"phase":"action_peak","startMs":6000,
                       "endMs":9000,"visibility":"partially_visible",
                       "evidenceKind":"unknown"}],
        },
        "sportContext":{"sport":"ski","executionEvidenceStatus":"unknown"},
        "filming":{"technicalQuality":None,"aestheticQuality":None,
                   "eventReadability":None,
                   "cameraMotionInterpretation":"not_assessed"},
        "observerJudgments":[],
        "reactions":[],
        "rights":{
            "rightsStatus":"pending",
            "sourceAccessAuthorized":False,
            "sourceTrainingAuthorized":False,
            "embeddedMediaTrainingAuthorized":False,
            "audioTrainingAuthorized":False,
            "speakerVoiceConsent":False,
            "sourcePermissionRecordId":None,
            "revocationState":"unknown",
        },
        "split":{"partition":partition,
                 "sourceFamilyId":family,"outingGroupId":outing,
                 "frozenSplitVersion":None},
        "evidenceStatus":{
            "sourceSha256Reverified":False,
            "humanEventAnnotationFrozen":False,
            "humanRatingsFrozen":False,
            "modelHypothesesStoredSeparately":True,
        },
    }


def _save(tmp_path,records):
    f=tmp_path/"curation-manifest.json"
    f.write_text(json.dumps({"version":"klip-highlight-curation-manifest-v1",
                             "records":records}))
    return f


def test_preflight_reports_unknown_and_never_asserts_legal_rights(tmp_path,capsys):
    p=_save(tmp_path,[_item()])
    before=p.read_bytes()
    result=audit_highlight_curation_manifest(p)
    assert result["records"]==1
    assert result["unknownOutcomeRecords"]==1
    assert result["rightsStatuses"]=={"pending":1}
    assert result["structurallyEligibleButRightsNotIndependentlyVerified"]==0
    assert result["legalPermissionIndependentlyVerified"] is False
    assert result["trainingIsAuthorizedByThisTool"] is False
    assert result["mediaReadCount"]==0
    assert result["modelsTrained"]==0
    assert main(["highlight-curation-preflight","--manifest",str(p)])==0
    assert json.loads(capsys.readouterr().out)["rightsStatuses"]=={"pending":1}
    assert before==p.read_bytes()


def test_leakage_detected_across_same_source_family(tmp_path):
    a=_item(partition="train")
    b=_item(asset="b"*64,partition="test")
    b["recordId"]="synthetic-record-002"
    p=_save(tmp_path,[a,b])
    with pytest.raises(ValueError,match="source family"):
        audit_highlight_curation_manifest(p)


def test_leakage_detected_across_same_outing_even_if_source_differs(tmp_path):
    a=_item(partition="train")
    b=_item(asset="b"*64,family="sourcefamily-002",partition="validation")
    b["recordId"]="synthetic-record-002"
    p=_save(tmp_path,[a,b])
    with pytest.raises(ValueError,match="outing"):
        audit_highlight_curation_manifest(p)


def test_definite_outcome_requires_visible_resolution(tmp_path):
    a=_item()
    a["event"]["observedOutcome"]="completed"
    p=_save(tmp_path,[a])
    with pytest.raises(ValueError,match="invisible/ambiguous resolution"):
        audit_highlight_curation_manifest(p)


def test_missing_rights_cannot_be_promoted_by_a_flag(tmp_path):
    a=_item(partition="train")
    a["rights"]["rightsStatus"]="approved_for_training"
    p=_save(tmp_path,[a])
    with pytest.raises(ValueError,match="Training permission fields incomplete"):
        audit_highlight_curation_manifest(p)
    a["rights"].update({
        "sourceAccessAuthorized":True,"sourceTrainingAuthorized":True,
        "embeddedMediaTrainingAuthorized":True,"audioTrainingAuthorized":True,
        "speakerVoiceConsent":True,
        "sourcePermissionRecordId":"synthetic-grant-001",
        "revocationState":"active",
    })
    p=_save(tmp_path,[a])
    with pytest.raises(ValueError,match="evidence/split gates incomplete"):
        audit_highlight_curation_manifest(p)
    a["evidenceStatus"].update({
        "sourceSha256Reverified":True,
        "humanEventAnnotationFrozen":True,
        "humanRatingsFrozen":True,
    })
    p=_save(tmp_path,[a])
    result=audit_highlight_curation_manifest(p)
    assert result["structurallyEligibleButRightsNotIndependentlyVerified"]==1
    assert result["legalPermissionIndependentlyVerified"] is False
    assert result["trainingIsAuthorizedByThisTool"] is False


def test_bad_reaction_timeline_and_phase_are_rejected(tmp_path):
    a=_item()
    a["reactions"]=[{
        "reactionId":"synthetic-reaction","observerId":"volunteer-001",
        "reactionStartMs":11000,"reactionEndMs":13000,
        "sourceStartMs":14000,"sourceEndMs":19000,
        "mappingStatus":"human_verified",
    }]
    p=_save(tmp_path,[a])
    with pytest.raises(ValueError,match="Reaction mapping outside event"):
        audit_highlight_curation_manifest(p)
    a["reactions"]=[]
    a["event"]["phases"][0]["endMs"]=19000
    p=_save(tmp_path,[a])
    with pytest.raises(ValueError,match="Phase outside event"):
        audit_highlight_curation_manifest(p)


def test_reviewer_role_not_equivalent_to_training_truth(tmp_path):
    a=_item()
    a["observerJudgments"]=[{
        "annotatorId":"reviewer-001",
        "role":"expert_coach",
        "blindActionOnly":True,
        "reviewStatus":"model_hypothesis",
    }]
    p=_save(tmp_path,[a])
    result=audit_highlight_curation_manifest(p)
    assert result["observerRoles"]=={"expert_coach":1}
    assert result["humanVerifiedOpinionRecords"]==0
