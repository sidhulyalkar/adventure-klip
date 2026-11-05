"""Public reaction video URL scouting must never silently become training data."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json

import pytest

from adventure_intelligence.cli import main
from adventure_intelligence.reaction_reference_catalog import (
    audit_reaction_references,
)


@pytest.fixture
def catalog(tmp_path):
    path=Path(__file__).resolve().parents[1]/"research"/"reaction-highlight-reference-v1.json"
    data=json.loads(path.read_text(encoding="utf-8"))
    output=tmp_path/"references.json"
    output.write_text(json.dumps(data),encoding="utf-8")
    return output


def test_real_scout_catalog_never_claims_rights_or_downloads(catalog):
    report=audit_reaction_references(catalog)
    assert report["status"]=="reference_only_not_training_eligible"
    assert report["publicReferenceLinks"]==9
    assert report["trainingEligible"]==0
    assert report["originalsDownloaded"]==0
    assert report["reactionVideosDownloaded"]==0
    assert report["transcriptsDownloaded"]==0
    assert report["modelsTrained"]==0
    assert report["sourceVideoReads"]==0
    assert report["externalUploads"]==0
    assert len(report["sports"])>=4
    assert catalog.is_file()


@pytest.mark.parametrize("field,value",[
    ("modelTrainingAllowed",True),
    ("derivedLabelsAllowed",True),
    ("licensedBytesAvailable",True),
    ("trainingAuthorization","approved"),
    ("platformAccessPermission","approved"),
    ("hostVideoRightsPermission","approved"),
    ("embeddedFootageRightsPermission","approved"),
    ("participantPrivacyOrVoicePermission","approved"),
    ("humanReferenceOnly",False),
    ("sourceContentReviewedInFull",True),
])
def test_reference_only_guard_rejects_training_or_license_claims(
        catalog,field,value):
    doc=json.loads(catalog.read_text())
    doc["references"][0][field]=value
    catalog.write_text(json.dumps(doc))
    with pytest.raises(ValueError,match="reference catalog cannot"):
        audit_reaction_references(catalog)


@pytest.mark.parametrize("field",[
    "mediaDownloaded","framesExtracted","transcriptsDownloaded",
    "modelTrainingPerformed","ownershipOrTrainingRightsVerified",
])
def test_catalog_level_provenance_gate(catalog,field):
    doc=json.loads(catalog.read_text())
    doc[field]=True
    catalog.write_text(json.dumps(doc))
    with pytest.raises(ValueError,match="may not claim"):
        audit_reaction_references(catalog)


def test_fake_or_duplicate_source_urls_are_rejected(catalog):
    doc=json.loads(catalog.read_text())
    doc["references"][0]["url"]="https://malicious.example.com/"+doc["references"][0]["videoId"]
    catalog.write_text(json.dumps(doc))
    with pytest.raises(ValueError,match="canonical public YouTube"):
        audit_reaction_references(catalog)
    doc=json.loads((Path(__file__).resolve().parents[1]/
                    "research"/"reaction-highlight-reference-v1.json").read_text())
    doc["references"].append(deepcopy(doc["references"][0]))
    catalog.write_text(json.dumps(doc))
    with pytest.raises(ValueError,match="duplicated YouTube video"):
        audit_reaction_references(catalog)


def test_cli_scout_audit_is_local_and_does_not_create_media(tmp_path,catalog,capsys):
    workspace=tmp_path/"fresh-empty-workspace"
    code=main(["--workspace",str(workspace),"reaction-reference-audit",
               "--catalog",str(catalog)])
    assert code==0
    result=json.loads(capsys.readouterr().out)
    assert result["publicReferenceLinks"]==9
    assert result["trainingEligible"]==0
    assert result["modelsTrained"]==0
    assert not (workspace/"reports").exists()
