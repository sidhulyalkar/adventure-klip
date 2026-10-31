"""Focused Review & Library regression tests: security, virtual folders, and asset safety."""
from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from adventure_intelligence.catalog import connect
from adventure_intelligence.indexing import index_folder
from adventure_intelligence.studio import make_handler, set_asset_region, studio_overview


@pytest.fixture
def focused_server(tmp_path, monkeypatch):
    source=tmp_path/"source footage"
    source.mkdir()
    original=source/"DJI_TEST.MP4"
    original.write_bytes(b"preserve original video" * 100)
    monkeypatch.setattr("adventure_intelligence.indexing.probe",
                        lambda p: {"duration":30,"audio":False})
    workspace=tmp_path/"workspace"
    asset_id=index_folder(source,workspace,activity="ski")["asset_ids"][0]
    app=ThreadingHTTPServer(("127.0.0.1",0),make_handler(workspace,"focus-test-token"))
    app.daemon_threads=True
    thread=threading.Thread(target=app.serve_forever,daemon=True)
    thread.start()
    try:
        yield (app,workspace,asset_id,original)
    finally:
        app.shutdown()
        app.server_close()
        thread.join(timeout=3)


def http(server, method, path, data=None, authorized=False):
    app,_,_,_=server
    conn=http.client.HTTPConnection("127.0.0.1",app.server_port,timeout=10)
    headers={"Host":"127.0.0.1:"+str(app.server_port)}
    if authorized:
        headers["X-Klip-Token"]="focus-test-token"
    if data is not None:
        headers["Content-Type"]="application/json"
        data=json.dumps(data).encode()
    conn.request(method,path,body=data,headers=headers)
    response=conn.getresponse()
    result=response.read()
    code=response.status
    conn.close()
    return code,result


def test_focus_is_default_while_legacy_editor_remains(focused_server):
    code,body=http(focused_server,"GET","/")
    assert code==200
    assert b"KLIP FIELD STUDIO" in body
    assert b"focus-test-token" in body
    assert b'id="reviewPage"' in body and b'id="libraryPage"' in body
    assert b"/focus.js" in body and b"/focus.css" in body
    code,old=http(focused_server,"GET","/classic")
    assert code==200 and b'id="queueWorkspace"' in old
    for path,needle in (("/focus.js",b"/api/reviews"),
                        ("/focus.css",b".video-wrap")):
        code,body=http(focused_server,"GET",path)
        assert code==200 and needle in body
    assert b"cdn." not in body


def test_region_label_is_virtual_and_persists_without_touching_source(focused_server):
    app,workspace,asset_id,original=focused_server
    before=original.read_bytes()
    rows=studio_overview(workspace)["assets"]
    assert rows[0]["region"]==""
    assert rows[0]["folder"]=="source footage"
    code,body=http(focused_server,"POST","/api/region",
                   {"assetId":asset_id,"region":"Lake Tahoe"},authorized=True)
    assert code==200
    assert json.loads(body)["region"]=="Lake Tahoe"
    assert studio_overview(workspace)["assets"][0]["region"]=="Lake Tahoe"
    assert original.read_bytes()==before
    with connect(workspace) as db:
        assert db.execute("SELECT region FROM asset_regions WHERE asset_id=?",
                          (asset_id,)).fetchone()["region"]=="Lake Tahoe"
    code,_=http(focused_server,"POST","/api/region",
                {"assetId":asset_id,"region":""},authorized=True)
    assert code==200
    assert studio_overview(workspace)["assets"][0]["region"]==""
    assert original.read_bytes()==before


def test_region_cannot_be_changed_without_token_or_with_bad_asset(focused_server):
    _,workspace,asset_id,_=focused_server
    code,_=http(focused_server,"POST","/api/region",
                {"assetId":asset_id,"region":"Private"},authorized=False)
    assert code==403
    for region in ("x"*81,"line\nbreak","ok\x00bad"):
        code,_=http(focused_server,"POST","/api/region",
                    {"assetId":asset_id,"region":region},authorized=True)
        assert code==400
    code,_=http(focused_server,"POST","/api/region",
                {"assetId":"x"*64,"region":"Hidden"},authorized=True)
    assert code==400
    assert studio_overview(workspace)["assets"][0]["region"]==""


def test_review_is_separate_from_label_verification(focused_server):
    _,workspace,asset_id,_=focused_server
    code,body=http(focused_server,"POST","/api/reviews",{
        "assetId":asset_id,"startSeconds":1,"endSeconds":4,
        "label":"ski_turn","decision":"uncertain","highlight":"keep","note":"Great view"
    },authorized=True)
    assert code==200,body
    with connect(workspace) as db:
        row=db.execute("SELECT decision,highlight FROM example_revisions").fetchone()
        assert (row["decision"],row["highlight"])==("uncertain","keep")
    assert studio_overview(workspace)["assets"][0]["reviews"]==1


def test_set_region_validation():
    with pytest.raises(ValueError,match="Invalid asset id"):
        set_asset_region(__import__("pathlib").Path("/tmp/unused"),"../videos","Hidden")
