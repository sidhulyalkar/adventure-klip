"""Local Field Studio source safety, media ranges, API auth and review history."""
from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

import pytest

from adventure_intelligence.catalog import connect
from adventure_intelligence.indexing import index_folder
from adventure_intelligence.studio import make_handler, studio_asset, studio_overview
from adventure_intelligence.comparisons import compare_clips,comparison_summary
from adventure_intelligence.examples import review_inventory


@pytest.fixture
def fixture(tmp_path,monkeypatch):
    root=tmp_path/"original DJIs"
    root.mkdir()
    a=root/"DJI_SKIA.MP4"
    b=root/"DJI_SKIB.MP4"
    a.write_bytes(b"A" * 2048 + b"OriginalVideoA")
    b.write_bytes(b"B" * 2048 + b"OriginalVideoB")
    monkeypatch.setattr("adventure_intelligence.indexing.probe",
                        lambda p:{"duration":30,"audio":False})
    work=tmp_path/"workspace"
    aids=index_folder(root,work,activity="ski")["asset_ids"]
    with connect(work) as db:
        for i,aid in enumerate(aids):
            db.execute("""INSERT INTO episodes
             (id,asset_id,kind,start_ms,end_ms,score,event_ids_json,evidence_json,origin,version)
             VALUES (?,?,?,?,?,?,?,?,?,?)""",
             ("ep"+str(i),aid,"sustained_motion_candidate",0,18000,.94,
              "[]","{}","derived","test"))
    return work,aids,(a,b)


@pytest.fixture
def server(fixture):
    from http.server import ThreadingHTTPServer
    work,_,_=fixture
    token="unit-test-session-token"
    app=ThreadingHTTPServer(("127.0.0.1",0),make_handler(work,token))
    app.daemon_threads=True
    thread=threading.Thread(target=app.serve_forever,daemon=True)
    thread.start()
    try:
        yield app,token
    finally:
        app.shutdown();app.server_close();thread.join(timeout=3)


def request(server,method,path,body=None,headers=None):
    app,token=server
    conn=http.client.HTTPConnection("127.0.0.1",app.server_port,timeout=15)
    h={"Host":"127.0.0.1:"+str(app.server_port)}
    h.update(headers or {})
    if isinstance(body,dict):
        body=json.dumps(body).encode()
        h["Content-Type"]="application/json"
    conn.request(method,path,body=body,headers=h)
    response=conn.getresponse()
    data=response.read()
    status=response.status
    result=dict(response.getheaders())
    conn.close()
    return status,data,result


def test_studio_package_assets_include_useful_static_ui(server):
    status,raw,headers=request(server,"GET","/")
    assert status==200 and b"klip" in raw
    assert b"FIELD STUDIO" in raw
    assert b"unit-test-session-token" in raw
    assert headers["Content-Security-Policy"].startswith("default-src 'none'")
    for path,needle in (("/studio.css",b".comparison-grid"),
                        ("/studio.js",b"/api/reviews")):
        status,body,headers=request(server,"GET",path)
        assert status==200 and needle in body
    assert b"cdn." not in raw
    assert b'focus.js' in raw and b'focus_sequence.js' in raw


def test_no_unauthorized_asset_info_or_media(server,fixture):
    _,aids,_=fixture
    for path in ("/api/overview","/api/asset/"+aids[0],
                 "/api/media/"+aids[0]):
        code,_,_=request(server,"GET",path)
        assert code==403
    code,_,_=request(server,"GET","/api/overview",headers={"Host":"evil.test"})
    assert code==403
    code,_,_=request(server,"POST","/api/reviews",{},{
        "Origin":"http://evil.example","X-Klip-Token":server[1]})
    assert code==403
    code,_,_=request(server,"GET","/api/media/../../etc/passwd?token="+server[1])
    assert code in (403,404)


def test_streams_original_read_only_and_supports_byte_range(server,fixture):
    work,aids,files=fixture
    original=files[0].read_bytes()
    token=server[1]
    code,body,headers=request(server,"GET",
        "/api/media/"+aids[0]+"?token="+token,headers={"Range":"bytes=100-199"})
    assert code==206
    assert body==original[100:200]
    assert headers["Content-Range"]=="bytes 100-199/"+str(len(original))
    assert headers["Accept-Ranges"]=="bytes"
    code,body,_=request(server,"GET","/api/media/"+aids[0]+"?token="+token,
                        headers={"Range":"bytes=-16"})
    assert code==206 and body==original[-16:]
    code,body,_=request(server,"GET","/api/media/"+aids[0]+"?token="+token,
                        headers={"Range":"bytes=900000-999999"})
    assert code==416
    assert files[0].read_bytes()==original
    assert set(files[0].parent.iterdir())==set(files)


def test_refuses_changed_original_after_first_stream(server,fixture):
    work,aids,files=fixture
    url="/api/media/"+aids[0]+"?token="+server[1]
    assert request(server,"GET",url,headers={"Range":"bytes=0-3"})[0]==206
    files[0].write_bytes(b"TAMPERED")
    assert request(server,"GET",url,headers={"Range":"bytes=0-3"})[0]==404
    # Studio itself never altered media; test changed input deliberately.


def test_review_and_comparison_api_write_workspace_only(server,fixture):
    work,aids,videos=fixture
    before=[p.read_bytes() for p in videos]
    auth={"X-Klip-Token":server[1]}
    code,raw,_=request(server,"GET","/api/overview",headers=auth)
    overview=json.loads(raw)
    assert code==200 and len(overview["assets"])==2
    code,raw,_=request(server,"GET","/api/asset/"+aids[0],headers=auth)
    assert code==200 and json.loads(raw)["suggestions"]
    review={"assetId":aids[0],"startSeconds":2,"endSeconds":8,
            "label":"ski_lift","decision":"confirmed","highlight":"skip",
            "note":"Chairlift, not a skiing highlight"}
    code,raw,_=request(server,"POST","/api/reviews",review,auth)
    assert code==200 and json.loads(raw)["status"]=="reviewed"
    review["note"]="Re-reviewed this scene, still a lift"
    code,raw,_=request(server,"POST","/api/reviews",review,auth)
    assert code==200
    examples=review_inventory(work)
    assert examples["reviewed"]==1 and examples["revisions"]==2
    pair={"leftAssetId":aids[0],"leftStart":0,"leftEnd":9,
          "rightAssetId":aids[1],"rightStart":0,"rightEnd":9,
          "preference":"right","reason":"action","note":"More interesting turns"}
    code,raw,_=request(server,"POST","/api/compare",pair,auth)
    assert code==200 and json.loads(raw)["status"]=="reviewed"
    pair["preference"]="tie"
    assert request(server,"POST","/api/compare",pair,auth)[0]==200
    pairs=comparison_summary(work)
    assert pairs["current"]==1 and pairs["revisions"]==2
    assert all(p.read_bytes()==data for p,data in zip(videos,before))


def test_rejects_malformed_reviews_and_bad_comparisons(server,fixture):
    _,aids,_=fixture
    auth={"X-Klip-Token":server[1]}
    invalid=[
        {"assetId":aids[0],"startSeconds":-2,"endSeconds":5,
         "label":"ski_lift","decision":"confirmed"},
        {"assetId":aids[0],"startSeconds":1,"endSeconds":9999,
         "label":"ski_lift","decision":"confirmed"},
        {"assetId":aids[0],"startSeconds":1,"endSeconds":2,
         "label":"bad<script>","decision":"confirmed"},
    ]
    for body in invalid:
        assert request(server,"POST","/api/reviews",body,auth)[0]==400
    same={"leftAssetId":aids[0],"leftStart":2,"leftEnd":5,
          "rightAssetId":aids[0],"rightStart":2,"rightEnd":5,
          "preference":"left"}
    assert request(server,"POST","/api/compare",same,auth)[0]==400
    assert request(server,"POST","/api/compare",{"nonsense":True},auth)[0]==400
    assert request(server,"POST","/api/compare",b"hello",auth)[0]==415


def test_studio_api_does_not_know_unindexed_paths(fixture):
    workspace,aids,_=fixture
    with pytest.raises(ValueError,match="Invalid asset id"):
        studio_asset(workspace,"../../etc/passwd")
    with pytest.raises(KeyError,match="Asset not found"):
        studio_asset(workspace,"f"*64)
    overview=studio_overview(workspace)
    assert "stored_path" not in json.dumps(overview)
    assert overview["localOnly"]
