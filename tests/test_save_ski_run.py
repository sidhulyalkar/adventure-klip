"""Source-linked reviewed-run storage, synthetic evidence only."""
from __future__ import annotations
from hashlib import sha256
import argparse
import importlib.util
import json
from pathlib import Path

import pytest

from adventure_intelligence import ski_run_context as context
from adventure_intelligence.auto_scout_v2 import save_json


def runner():
    file=Path(__file__).resolve().parents[1]/"scripts"/"klip_save_ski_run.py"
    spec=importlib.util.spec_from_file_location("klip_ski_run_save",file)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture(tmp_path):
    ws=tmp_path/"workspace"
    folder=ws/"reports"/"source-run-context"
    (folder/"media").mkdir(parents=True)
    anchor={
        "recordId":"c"*24,"sourceSha256":"a"*64,"proxySha256":"b"*64,
        "sourceStartMs":16000,"sourceEndMs":36000,
    }
    plan=context.plan_context(anchor,230000)
    preview=folder/"media"/"run-context.mp4"
    preview.write_bytes(b"frozen-test-preview-bytes"*100)
    plan["previewRender"]={
        "sourceMediaChanged":False,
        "videoSha256":sha256(preview.read_bytes()).hexdigest(),
    }
    save_json(folder/"run-context-plan.json",plan)
    return ws,folder,plan


def test_reviewed_full_run_preserves_third_jump_past_old_cut(tmp_path):
    ws,folder,plan=fixture(tmp_path)
    owner=context.owner_annotation_template(plan)
    owner.update(
        runStartMs=5000,runEndMs=190000,
        runKind="terrain_park",preference="keep",
        reviewerVerifiedContinuousRun=True,
        featureMarkers=[
            {"kind":"jump","sourceMs":28000},
            {"kind":"box","sourceMs":68000},
            {"kind":"jump","sourceMs":150000},
        ],
    )
    exported=ws/"reports"/"owner.json"
    exported.write_text(json.dumps(owner))
    out=ws/"reports"/"saved-run"
    args=argparse.Namespace(workspace=ws,context_dir=folder,
                            review_json=exported,output_dir=out)
    got=runner().save(args)
    assert got["positiveRunSelectionAllowed"] is True
    assert got["featureCountInsideRun"]==3
    data=json.loads((out/"run-record.json").read_text())
    assert data["runSourceBoundsMs"]==[5000,190000]
    assert data["featureMarkersInsideRun"][-1]["sourceMs"]==150000
    assert data["featureMarkersInsideRun"][-1]["sourceMs"]>plan["anchorSourceEndMs"]
    assert data["publicationAuthorized"] is False
    assert data["sourceMediaRead"] is False
    with pytest.raises(ValueError,match="new private"):
        runner().save(args)


def test_transit_skip_does_not_get_positive_run_label(tmp_path):
    ws,folder,plan=fixture(tmp_path)
    owner=context.owner_annotation_template(plan)
    owner.update(runKind="transit",preference="skip")
    exported=ws/"reports"/"owner-skip.json"
    exported.write_text(json.dumps(owner))
    args=argparse.Namespace(workspace=ws,context_dir=folder,
                            review_json=exported,
                            output_dir=ws/"reports"/"skip-review")
    result=runner().save(args)
    assert result["positiveRunSelectionAllowed"] is False
    assert result["runStartEndMs"] is None


def test_preview_sha_mutation_stops_review_storage(tmp_path):
    ws,folder,plan=fixture(tmp_path)
    owner=context.owner_annotation_template(plan)
    exported=ws/"reports"/"owner.json"
    exported.write_text(json.dumps(owner))
    (folder/"media"/"run-context.mp4").write_bytes(b"tampered")
    args=argparse.Namespace(workspace=ws,context_dir=folder,
                            review_json=exported,
                            output_dir=ws/"reports"/"blocked")
    with pytest.raises(ValueError,match="preview"):
        runner().save(args)
    assert not args.output_dir.exists()


def test_unverified_boundary_not_promoted_to_saved_run(tmp_path):
    ws,folder,plan=fixture(tmp_path)
    owner=context.owner_annotation_template(plan)
    owner.update(
        runStartMs=0,runEndMs=200000,
        preference="keep",runKind="terrain_park",
        reviewerVerifiedContinuousRun=False,
    )
    exported=ws/"reports"/"owner.json"
    exported.write_text(json.dumps(owner))
    args=argparse.Namespace(workspace=ws,context_dir=folder,
                            review_json=exported,
                            output_dir=ws/"reports"/"unverified")
    got=runner().save(args)
    assert got["positiveRunSelectionAllowed"] is False
