"""Authenticated Focus Studio ranked-review API and unscored exploration.

Mock originals only, no media decoder, no cloud, no new ranking labels.
"""
from __future__ import annotations

import http.client
from http.server import ThreadingHTTPServer
import json
import threading

import pytest

from adventure_intelligence.catalog import connect
from adventure_intelligence.examples import review_example
from adventure_intelligence.indexing import index_folder
from adventure_intelligence.studio import make_handler


@pytest.fixture
def ranked_studio(tmp_path, monkeypatch):
    source=tmp_path/"unopened-source"
    source.mkdir()
    a=source/"DJI_A.MP4"
    b=source/"DJI_B.MP4"
    a.write_bytes(b"OriginalVideoA"*100)
    b.write_bytes(b"OriginalVideoB"*100)
    before=(a.read_bytes(),b.read_bytes())
    monkeypatch.setattr(
        "adventure_intelligence.indexing.probe",
        lambda path: {
            "duration":30,"capture_time":"2026-01-14T12:00:00Z",
            "width":3840,"height":2160,"codec":"hevc",
            "avg_frame_rate":"50/1","audio":False,
        })
    work=tmp_path/"workspace"
    ids=index_folder(source,work,activity="unknown")["asset_ids"]
    assert len(ids)==2
    # New workspaces may have a usable catalog but no reports directory.
    assert not (work/"reports").exists()
    with connect(work) as db:
        db.execute(
            "INSERT INTO episodes(id,asset_id,kind,start_ms,end_ms,score,"
            "event_ids_json,evidence_json,origin,version)"
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("synthetic-episode",ids[0],"jump_chain",0,18000,.85,
             "[]","{}","derived","synthetic-v1"))
    server=ThreadingHTTPServer(("127.0.0.1",0),
                               make_handler(work,"ranked-test-token"))
    server.daemon_threads=True
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        yield server,work,ids,(a,b),before
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def request(fixture,path,token=None):
    server,*_=fixture
    conn=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=20)
    headers={"Host":f"127.0.0.1:{server.server_port}"}
    if token is not None:
        headers["X-Klip-Token"]=token
    conn.request("GET",path,headers=headers)
    response=conn.getresponse()
    raw=response.read()
    status=response.status
    conn.close()
    return status,raw


def test_ranked_review_token_protected_and_no_video_reads(ranked_studio):
    _,ws,ids,sources,before=ranked_studio
    assert request(ranked_studio,"/api/ranked-clips")[0]==403
    stamp=(ws/"catalog.sqlite3").stat().st_mtime_ns
    code,raw=request(ranked_studio,
        "/api/ranked-clips?limit=4&goal=auto&exploreFraction=0.50",
        token="ranked-test-token")
    assert code==200,raw
    report=json.loads(raw)
    assert report["sourceCatalogCount"]==2
    assert report["rankedCount"]>=1
    assert report["explorationCount"]>=1
    assert report["sourceVideoRead"] is False
    assert report["workspaceCatalogModified"] is False
    assert report["automaticDeleteExportPublishAuthorized"] is False
    assert not (ws/"reports").exists()
    assert (ws/"catalog.sqlite3").stat().st_mtime_ns==stamp
    assert all(path.read_bytes()==saved
               for path,saved in zip(sources,before))
    assert "/unopened-source/" not in raw.decode()
    assert set(x["sourceSha256"] for x in report["rankedClips"]+
               report["explorationClips"])<=set(ids)


def test_ranked_review_rejects_bad_policy_and_bounds(ranked_studio):
    for suffix in (
        "?limit=9999","?limit=0","?limit=-1",
        "?exploreFraction=NaN","?exploreFraction=-1",
        "?goal=not_a_goal",
    ):
        code,_=request(ranked_studio,"/api/ranked-clips"+suffix,
                       token="ranked-test-token")
        assert code==400,suffix


def test_saved_keep_does_not_reappear_as_new_ranked_candidate(ranked_studio):
    _,ws,ids,originals,before=ranked_studio
    request(ranked_studio,"/api/ranked-clips?limit=4",
            token="ranked-test-token")
    review_example(
        ws,asset_id=ids[0],start_seconds=0,end_seconds=18,
        label="jump_chain",decision="uncertain",highlight="keep")
    code,raw=request(ranked_studio,"/api/ranked-clips?limit=4",
                     token="ranked-test-token")
    assert code==200,raw
    result=json.loads(raw)
    assert result["candidateWindowsExcludedBecauseAlreadyReviewed"]>=1
    assert result["rankedCount"]==0
    assert all(x["sourceSha256"]!=ids[0] for x in result["explorationClips"])
    assert all(path.read_bytes()==saved
               for path,saved in zip(originals,before))


def test_focus_ui_opt_in_controls_exist_without_changing_default_queue(ranked_studio):
    code,html=request(ranked_studio,"/")
    assert code==200
    assert b'id="reviewOrder"' in html
    assert b'id="rankGoal"' in html
    assert b'<option value="queue">Current suggestions</option>' in html
    assert b'<option value="ranked">Ranked + explore</option>' in html
    code,script=request(ranked_studio,"/focus.js")
    assert code==200
    assert b"reviewOrder:'queue'" in script
    assert b"/api/ranked-clips?limit=24" in script
    assert b"rankedItem" in script
    assert b"unscored_archive_coverage_probe" not in script or \
           b"unscored_exploration" in script
    assert b"/api/process" not in script
