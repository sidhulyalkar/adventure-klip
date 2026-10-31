"""Focus Studio: filename-date provenance, virtual hierarchy, media immutability."""
from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from adventure_intelligence.catalog import connect
from adventure_intelligence.indexing import index_folder
from adventure_intelligence.library_index import _day,library_index,organize_asset
from adventure_intelligence.studio import make_handler


@pytest.fixture
def library(tmp_path,monkeypatch):
    originals=tmp_path/"DJI-originals";originals.mkdir()
    a=originals/"DJI_20260311141524_0253_D.MP4"
    b=originals/"DJI_20241225093954_0040_D.MP4"
    a.write_bytes(b"ski camera bytes"*41)
    b.write_bytes(b"underwater camera bytes"*33)
    monkeypatch.setattr("adventure_intelligence.indexing.probe",
                        lambda path:{"duration":35,"audio":False})
    work=tmp_path/"workspace"
    result=index_folder(originals,work,activity="unknown")
    before={x.name:x.read_bytes() for x in (a,b)}
    return work,result["asset_ids"],(a,b),before


def test_filename_date_is_only_an_estimate():
    assert _day(None,"DJI_20260311141524_0253_D.MP4")==("2026-03-11","filename_estimate")
    assert _day(None,"DJI_20241225093954_0040_D.MP4")==("2024-12-25","filename_estimate")
    assert _day("2026-01-06T01:05:00","unknown.MP4")==("2026-01-06","camera_metadata")
    assert _day(None,"DJI_20261399141524_0253_D.MP4")== (None,"unknown")
    assert _day(None,"unlabeled.MP4")== (None,"unknown")


def test_grouping_and_activity_correction_are_virtual(library):
    work,ids,videos,before=library
    initial=library_index(work)
    assert initial["total"]==2
    assert {x["day"] for x in initial["items"]}=={"2026-03-11","2024-12-25"}
    assert all(x["dayProvenance"]=="filename_estimate" for x in initial["items"])
    original=initial["items"][0]
    updated=organize_asset(work,asset_id=original["assetId"],
             region="French Polynesia",place="Moorea",
             collection="Best snorkeling",activity="snorkeling",
             note="Underwater footage; labeled ski by old import")
    assert updated["status"]=="saved"
    later=library_index(work)
    chosen=next(x for x in later["items"] if x["assetId"]==original["assetId"])
    assert chosen["activity"]=="snorkeling"
    assert chosen["indexedActivity"]=="unknown"
    assert chosen["activityReviewed"] is True
    assert chosen["region"]=="French Polynesia"
    assert chosen["place"]=="Moorea"
    assert chosen["collection"]=="Best snorkeling"
    assert not any("stored_path" in x for x in later["items"])
    assert {p.name:p.read_bytes() for p in videos}==before


def test_library_rejects_malicious_updates_and_unknown_sources(library):
    work,ids,videos,before=library
    with pytest.raises(ValueError):
        organize_asset(work,asset_id="../bad",region="Unknown")
    with pytest.raises(ValueError):
        organize_asset(work,asset_id=ids[0],region="x"*91)
    with pytest.raises(ValueError):
        organize_asset(work,asset_id=ids[0],region="unsafe\nlabel")
    with pytest.raises(ValueError):
        organize_asset(work,asset_id=ids[0],activity="skiing")
    with pytest.raises(ValueError):
        organize_asset(work,asset_id="f"*64,note="missing")
    with pytest.raises(ValueError):
        organize_asset(work,asset_id=ids[0])
    assert {p.name:p.read_bytes() for p in videos}==before


def test_focused_ui_assets_and_library_api_are_local_authenticated(library):
    work,ids,videos,before=library
    server=ThreadingHTTPServer(("127.0.0.1",0),make_handler(work,"test-local-token"))
    server.daemon_threads=True
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    def req(method,path,payload=None,authorized=True):
        conn=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=15)
        headers={"Host":"127.0.0.1:"+str(server.server_port)}
        if authorized:headers["X-Klip-Token"]="test-local-token"
        body=json.dumps(payload).encode() if payload is not None else None
        if payload is not None:headers["Content-Type"]="application/json"
        conn.request(method,path,body=body,headers=headers)
        response=conn.getresponse()
        data=response.read()
        status=response.status
        conn.close()
        return status,data
    try:
        for url,fragment in (
          ("/",b"One moment at a time"),
          ("/focus.css",b".screen"),
          ("/focus.js",b"/api/queue"),
          ("/focus_sequence.js",b"/api/sequence")):
            status,data=req("GET",url)
            assert status==200 and fragment in data
        assert req("GET","/api/library",authorized=False)[0]==403
        code,raw=req("GET","/api/library")
        assert code==200 and json.loads(raw)["total"]==2
        assert req("POST","/api/library/organize",{
            "assetId":ids[0],"activity":"snorkeling",
            "region":"Hawaii","collection":"Water scenes"},authorized=False)[0]==403
        code,raw=req("POST","/api/library/organize",{
            "assetId":ids[0],"activity":"snorkeling",
            "region":"Hawaii","collection":"Water scenes"})
        assert code==200
        assert json.loads(raw)["status"]=="saved"
        assert req("GET","/api/library")[0]==200
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)
    assert {p.name:p.read_bytes() for p in videos}==before
