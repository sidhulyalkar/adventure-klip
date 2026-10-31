"""Provenance for human clip boundary corrections is atomic and immutable."""
from __future__ import annotations

import pytest

from adventure_intelligence.indexing import index_folder
from adventure_intelligence.catalog import connect
from adventure_intelligence.examples import review_example


@pytest.fixture
def local_video(tmp_path,monkeypatch):
    src=tmp_path/"original";src.mkdir()
    path=src/"DJI_TEST.MP4";path.write_bytes(b"immutable camera sample"*50)
    monkeypatch.setattr("adventure_intelligence.indexing.probe",
                        lambda _:{"duration":45,"audio":False})
    workspace=tmp_path/"klip-work"
    aid=index_folder(src,workspace,activity="ski")["asset_ids"][0]
    return workspace,aid,path


def proposal(**changes):
    return {"source":"queue","id":"ep-1:10000:16000","label":"jump_chain",
            "startMs":10000,"endMs":16000,
            "policyVersion":"trim-policy-v1",
            "suggestedStartMs":8000,"suggestedEndMs":18800,**changes}


def test_human_edit_keeps_original_and_suggested_bounds_without_touching_video(local_video):
    workspace,aid,video=local_video
    before=video.read_bytes()
    result=review_example(workspace,asset_id=aid,start_seconds=8.4,
                          end_seconds=18.3,label="ski_jump",decision="uncertain",
                          highlight="keep",proposal=proposal())
    assert result["status"]=="reviewed"
    with connect(workspace) as db:
        row=db.execute("""SELECT l.*,r.start_ms AS final_start,r.end_ms AS final_end,
                                 r.highlight,r.decision
          FROM review_proposal_links l JOIN example_revisions r ON r.revision_id=l.revision_id
          WHERE l.revision_id=?""",(result["revisionId"],)).fetchone()
    assert row["proposal_source"]=="queue"
    assert row["proposed_start_ms"]==10000 and row["proposed_end_ms"]==16000
    assert (row["suggested_start_ms"],row["suggested_end_ms"])==(8000,18800)
    assert (row["final_start"],row["final_end"])==(8400,18300)
    assert (row["highlight"],row["decision"])==("keep","uncertain")
    assert video.read_bytes()==before


def test_duplicate_unchanged_review_does_not_duplicate_proposal_history(local_video):
    workspace,aid,_=local_video
    kw=dict(asset_id=aid,start_seconds=8,end_seconds=18,label="jump_chain",
            decision="uncertain",highlight="keep",proposal=proposal())
    first=review_example(workspace,**kw)
    second=review_example(workspace,**kw)
    assert second["status"]=="unchanged"
    assert first["revisionId"]==second["revisionId"]
    with connect(workspace) as db:
        assert db.execute("SELECT COUNT(*) FROM review_proposal_links").fetchone()[0]==1
        assert db.execute("SELECT COUNT(*) FROM example_revisions").fetchone()[0]==1


def test_manual_review_without_model_proposal_preserves_legacy_behavior(local_video):
    workspace,aid,_=local_video
    saved=review_example(workspace,asset_id=aid,start_seconds=2,
                         end_seconds=6,label="moment",decision="uncertain",
                         highlight="neutral")
    assert saved["status"]=="reviewed"
    with connect(workspace) as db:
        assert db.execute("SELECT COUNT(*) FROM review_proposal_links").fetchone()[0]==0


@pytest.mark.parametrize("bad",[
    {"startMs":-1},{"startMs":True},{"endMs":10000},{"endMs":100000},
    {"source":"external"},{"source":["queue"]},{"id":""},{"id":"x"*181},
    {"label":"jump chain"},{"suggestedStartMs":-1},{"suggestedEndMs":None},
    {"policyVersion":"x"*81},
])
def test_invalid_proposal_rolls_back_entire_review(local_video,bad):
    workspace,aid,_=local_video
    with pytest.raises((ValueError,TypeError)):
        review_example(workspace,asset_id=aid,start_seconds=8,
                       end_seconds=18,label="jump_chain",decision="uncertain",
                       highlight="keep",proposal=proposal(**bad))
    with connect(workspace) as db:
        assert db.execute("SELECT COUNT(*) FROM example_revisions").fetchone()[0]==0
        assert db.execute("SELECT COUNT(*) FROM review_proposal_links").fetchone()[0]==0


def test_corrected_bounds_keep_same_proposal_id_for_future_evaluation(local_video):
    workspace,aid,_=local_video
    opts=dict(asset_id=aid,label="jump_chain",decision="uncertain",
              highlight="keep",proposal=proposal())
    one=review_example(workspace,start_seconds=8,end_seconds=18,**opts)
    two=review_example(workspace,start_seconds=8.4,end_seconds=17.6,**opts)
    assert one["revisionId"]!=two["revisionId"]
    with connect(workspace) as db:
        matches=list(db.execute("SELECT proposal_id,proposed_start_ms,proposed_end_ms "
                                "FROM review_proposal_links ORDER BY created_at,revision_id"))
    assert len(matches)==2
    assert len({r["proposal_id"] for r in matches})==1
