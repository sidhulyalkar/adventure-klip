"""Focused Review & Library regression tests: security, virtual folders, and asset safety."""
from __future__ import annotations

import http.client as http_client
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
    conn=http_client.HTTPConnection("127.0.0.1",app.server_port,timeout=10)
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
    assert b"/focus_refine.css" in body
    assert b'id="refreshCatalog"' in body
    assert b'id="emptyNext"' in body
    assert b'id="focusFullscreen"' in body
    code,old=http(focused_server,"GET","/classic")
    assert code==200 and b'id="queueWorkspace"' in old
    for path,needle in (("/focus.js",b"/api/reviews"),
                        ("/focus.css",b".video-wrap"),
                        ("/focus_refine.css",b"--bg:#0d1725")):
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


def test_unverified_keep_is_visible_in_clip_shelf_and_exports_without_false_label(focused_server):
    _,workspace,asset_id,original=focused_server
    from adventure_intelligence.examples import review_example
    from adventure_intelligence.sequence_editor import sequence_state, save_sequence, export_sequence
    before=original.read_bytes()
    saved=review_example(workspace,asset_id=asset_id,start_seconds=2,
        end_seconds=7,label="ski_jump",decision="uncertain",highlight="keep",
        note="Nice shot, action not confirmed")
    bank=sequence_state(workspace)["bank"]
    assert len(bank)==1
    assert bank[0]["exampleId"]==saved["exampleId"]
    assert bank[0]["labelDecision"]=="uncertain"
    revision=save_sequence(workspace,entries=[{"exampleId":saved["exampleId"]}],
                           expected_revision=None)
    result=export_sequence(workspace,expected_revision=revision["revisionId"])
    plan=json.loads(__import__("pathlib").Path(result["path"]).read_text())
    assert plan["segments"][0]["kind"]=="unverified_moment"
    assert plan["segments"][0]["labelDecision"]=="uncertain"
    assert original.read_bytes()==before


def test_explicit_keep_or_skip_is_not_suggested_again_but_later_can_return():
    from adventure_intelligence.curation_queue import _group_candidates
    aid="a"*64
    candidates=[{"episodeId":"ep1","startMs":1000,"endMs":10000,
                 "kind":"sustained_motion_candidate","score":0.7}]
    review={"asset_id":aid,"start_ms":1000,"end_ms":10000,
            "label":"ski_jump","decision":"uncertain","highlight":"keep"}
    assert _group_candidates(candidates,[review],aid,"ski")==[]
    review["highlight"]="skip"
    assert _group_candidates(candidates,[review],aid,"ski")==[]
    review["highlight"]="neutral"
    assert len(_group_candidates(candidates,[review],aid,"ski"))==1


def test_catalog_refresh_route_is_read_only_even_with_unprocessed_recording(focused_server):
    _,workspace,asset_id,original=focused_server
    before=original.read_bytes()
    code,raw=http(focused_server,"GET","/api/overview",authorized=True)
    assert code==200
    overview=json.loads(raw)
    assert overview["assets"][0]["id"]==asset_id
    assert overview["assets"][0]["reviews"]==0
    assert overview["assets"][0]["proposals"]==0
    assert original.read_bytes()==before


def test_quiet_ui_does_not_claim_catalog_count_is_processing_completion(focused_server):
    code,body=http(focused_server,"GET","/focus.js")
    assert code==200
    assert b"indexed" in body
    assert b"async function refreshCatalog" in body
    assert b"skipped>=2" in body
    assert b"visibleLimit:60" in body
    assert b"loadQueue({skipEmpty:false})" in body
    # No inference endpoint or processing job is called during navigation.
    assert b"/api/process" not in body


def test_trim_suggestions_from_catalog_are_review_only_and_source_safe(focused_server):
    from adventure_intelligence.studio import studio_asset
    _,workspace,asset_id,original=focused_server
    before=original.read_bytes()
    with connect(workspace) as db:
        db.execute("""INSERT INTO episodes
         (id,asset_id,kind,start_ms,end_ms,score,event_ids_json,
          evidence_json,origin,version)
         VALUES (?,?,?,?,?,?,?,?,?,?)""",
         ("candidate-for-trim",asset_id,"jump_chain",5000,13000,.85,
          "[]","{}","derived","test"))
    code,raw=http(focused_server,"GET","/api/queue?activity=ski&page=0",authorized=True)
    assert code==200
    data=json.loads(raw)
    assert data["moments"]
    cut=data["moments"][0]["trimSuggestion"]
    assert cut["timestampBasis"]=="source_relative_ms"
    assert cut["requiresReview"] is True
    assert cut["startMs"]<=5000 and cut["endMs"]>=13000
    detail=studio_asset(workspace,asset_id)
    assert detail["suggestions"][0]["trimSuggestion"]["version"]=="trim-policy-v1"
    assert original.read_bytes()==before


def test_explicit_skipped_interval_blocks_trim_expansion(focused_server):
    from adventure_intelligence.clip_rules import suggest_trim, exclusion_spans
    _,workspace,asset_id,original=focused_server
    before=original.read_bytes()
    from adventure_intelligence.examples import review_example
    review_example(workspace,asset_id=asset_id,start_seconds=1,end_seconds=4,
                   label="non_action",decision="uncertain",highlight="skip")
    from adventure_intelligence.examples import latest_examples
    with connect(workspace) as db:
        guards=exclusion_spans(db,asset_id,"ski",latest_examples(db,asset_id=asset_id))
    out=suggest_trim(start_ms=4500,end_ms=9500,duration_ms=30000,
                     kind="jump_chain",activity="ski",excluded=guards)
    assert out["status"]=="suggested"
    assert out["startMs"]>=4300  # explicit skip ends at 4000 + 300ms guard
    assert original.read_bytes()==before
