"""Studio ranked-review integration: private receipts, bounded access and feedback."""
from __future__ import annotations

import http.client
import json
from pathlib import Path
import threading
from http.server import ThreadingHTTPServer

import pytest

from adventure_intelligence.examples import review_example
from adventure_intelligence.indexing import index_folder
from adventure_intelligence.moment_clip_ranking import VERSION
from adventure_intelligence.studio import make_handler
from adventure_intelligence.studio_ranked_review import (
    REPORT_FILENAME, studio_ranked_queue,
)


@pytest.fixture
def ranked_workspace(tmp_path,monkeypatch):
    source=tmp_path/"offline-source"
    source.mkdir()
    a=source/"POV_ONE.MP4"
    b=source/"POV_TWO.MP4"
    a.write_bytes(b"source-one-original"*100)
    b.write_bytes(b"source-two-original"*100)
    monkeypatch.setattr(
        "adventure_intelligence.indexing.probe",
        lambda path: {"duration":30,"audio":False},
    )
    workspace=tmp_path/"workspace"
    aids=index_folder(source,workspace,activity="unknown")["asset_ids"]
    (workspace/"reports").mkdir(exist_ok=True)
    report={
        "version":VERSION,
        "rankedCount":1,
        "explorationCount":1,
        "rankedClips":[{
            "clipId":"clip-"+"a"*24,
            "sourceSha256":aids[0],
            "startMs":5000,"endMs":12500,
            "eventKindProposal":"active_sequence",
            "sportHint":"ski",
            "priorityLower":46.1,
            "priorityUpper":81.1,
            "observableCueCoverage":.65,
            "missingCueNames":["scene_context"],
        }],
        "explorationClips":[{
            "sourceSha256":aids[1],
            "startMs":0,"endMs":20000,
            "selectionType":"unscored_archive_coverage_probe",
            "sportHint":"unknown",
        }],
    }
    (workspace/"reports"/REPORT_FILENAME).write_text(json.dumps(report))
    return workspace,aids,(a,b),report


def test_private_ranked_feed_has_explicit_unscored_probes(ranked_workspace):
    workspace,aids,originals,_=ranked_workspace
    before=[p.read_bytes() for p in originals]
    result=studio_ranked_queue(workspace)
    assert len(result["moments"])==2
    rank,probe=result["moments"]
    assert rank["assetId"]==aids[0]
    assert rank["selectionType"]=="ranked"
    assert rank["priorityRange"]=={
        "lower":46.1,"upper":81.1,
        "cueCoverage":.65,"isCalibrated":False,
    }
    assert rank["requiresReview"] is True
    assert rank["trimSuggestion"] is None
    assert rank["editorialScore"] is None
    assert probe["assetId"]==aids[1]
    assert probe["selectionType"]=="exploration"
    assert probe["priorityRange"] is None
    assert probe["label"]=="unverified_scene"
    assert result["counts"]=={"ranked":1,"exploration":1}
    assert result["rawSourceMediaRead"] is False
    assert result["modelInferencePerformed"] is False
    assert result["automaticDeletionOrPublishing"] is False
    assert "/Volumes/" not in json.dumps(result)
    assert [p.read_bytes() for p in originals]==before


def test_explicit_keep_skip_filters_only_reviewed_proposals(ranked_workspace):
    workspace,aids,_,_=ranked_workspace
    review_example(
        workspace,asset_id=aids[0],
        start_seconds=5,end_seconds=12.5,
        label="ski_turn",decision="uncertain",highlight="keep",
    )
    result=studio_ranked_queue(workspace)
    assert result["counts"]=={"ranked":0,"exploration":1}
    assert result["skipped"]=={"already_kept_or_skipped":1}
    # Later is not a rejection, and should remain eligible.
    review_example(
        workspace,asset_id=aids[0],
        start_seconds=5,end_seconds=12.5,
        label="ski_turn",decision="uncertain",highlight="neutral",
    )
    assert studio_ranked_queue(workspace)["counts"]=={"ranked":1,"exploration":1}


def test_missing_stale_and_malformed_packets_fail_closed(ranked_workspace):
    workspace,aids,_,report=ranked_workspace
    p=workspace/"reports"/REPORT_FILENAME
    p.unlink()
    with pytest.raises(ValueError,match="run scripts/klip_rank_clips.py"):
        studio_ranked_queue(workspace)
    p.write_text(json.dumps(report))
    report["rankedClips"][0]["endMs"]=30001
    p.write_text(json.dumps(report))
    result=studio_ranked_queue(workspace)
    assert result["counts"]=={"ranked":0,"exploration":1}
    assert result["skipped"]["unavailable_source_or_changed_duration"]==1
    report["rankedClips"][0]["endMs"]=12500
    report["rankedClips"][0]["priorityUpper"]=999
    p.write_text(json.dumps(report))
    with pytest.raises(ValueError,match="Malformed ranked"):
        studio_ranked_queue(workspace)
    p.unlink()
    other=workspace/"secret-report"
    other.write_text(json.dumps(report))
    p.symlink_to(other)
    with pytest.raises(ValueError,match="Ranking report unavailable"):
        studio_ranked_queue(workspace)


def test_ranked_api_requires_existing_studio_token(ranked_workspace):
    workspace,aids,originals,_=ranked_workspace
    app=ThreadingHTTPServer(
        ("127.0.0.1",0),make_handler(workspace,"rank-test-token"))
    app.daemon_threads=True
    thread=threading.Thread(target=app.serve_forever,daemon=True)
    thread.start()
    def fetch(path,token=False):
        c=http.client.HTTPConnection("127.0.0.1",app.server_port,timeout=10)
        headers={"Host":"127.0.0.1:"+str(app.server_port)}
        if token:
            headers["X-Klip-Token"]="rank-test-token"
        c.request("GET",path,headers=headers)
        r=c.getresponse()
        status,body=r.status,r.read()
        c.close()
        return status,body
    before=[p.read_bytes() for p in originals]
    try:
        assert fetch("/api/ranked-queue")[0]==403
        code,body=fetch("/api/ranked-queue",token=True)
        assert code==200
        assert json.loads(body)["counts"]=={"ranked":1,"exploration":1}
        code,html=fetch("/")
        assert code==200 and b'id="reviewMode"' in html
        code,js=fetch("/focus.js")
        assert code==200 and b"/api/ranked-queue" in js
        assert b"Unscored coverage probe" not in js or b"unscored" in js
    finally:
        app.shutdown()
        app.server_close()
        thread.join(timeout=3)
    assert [p.read_bytes() for p in originals]==before
