"""The highlights library: owner judgments from every surface gathered into moments."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from adventure_intelligence import highlight_library as lib
from adventure_intelligence import highlight_review as review
from adventure_intelligence import owner_labels

A, B = "a" * 64, "b" * 64


def clip(sha, start, end):
    return {"sourceSha256": sha, "startMs": start, "endMs": end}


def screen(deck, index, clips, keep, best=None, cannot=False, n=0):
    return {"version": "klip-owner-grid-ledger-v1", "deckSha256": deck, "index": index,
            "clips": clips, "keep": keep, "best": best, "cannotJudge": cannot,
            "repeatOf": None, "pairEvidenceId": f"{deck}{index}{n}"}


def test_grid_latest_answer_wins_and_unticked_clips_are_skips():
    clips = [clip(A, 0, 10000), clip(A, 20000, 30000), clip(B, 0, 8000)]
    rows = [screen("d", 0, clips, keep=[0], best=0, n=1),
            screen("d", 0, clips, keep=[1, 2], best=2, n=2),     # re-answered
            screen("d", 1, clips, keep=[0], cannot=True)]
    ev = lib.grid_evidence(rows)
    assert [e["judgment"] for e in ev] == ["skip", "keep", "best"]


def test_overlapping_windows_are_one_moment_and_tiers_follow_the_strongest_owner_word():
    ev = [lib._ev(A, 0, 10000, "keep", "grid", "g1"),
          lib._ev(A, 2000, 9000, "best", "grid", "g2"),
          lib._ev(A, 50000, 60000, "skip", "grid", "g3"),
          lib._ev(A, 80000, 90000, "keep", "grid", "g4"),
          lib._ev(A, 81000, 89000, "skip", "grid", "g5"),
          lib._ev(B, 0, 5000, "proposed", "search", "c1"),
          lib._ev(B, 40000, 45000, "preferred", "tap", "t1")]
    got = lib.build(ev, {A: {"sport": "ski", "outing": "o1"}, B: {"sport": "snorkeling"}})
    tiers = [(m["sourceSha256"][0], m["startMs"], m["tier"], m["conflict"]) for m in got["moments"]]
    assert tiers == [("a", 2000, "owner_best", False), ("a", 80000, "owner_keep", True),
                     ("b", 0, "to_review", False)]
    assert got["counts"]["excludedSkipped"] == 2       # the skipped one and the tap-only one
    assert got["counts"]["ownerMoments"] == 2 and got["counts"]["conflicts"] == 1
    assert got["moments"][0]["windowBasis"] == "owner_judged"   # the tighter owner window


def test_an_owner_trim_sets_the_window():
    ev = [lib._ev(A, 0, 30000, "keep", "grid", "g1"),
          lib._ev(A, 4000, 12000, "keep", "trim", "b1", trimmed=True)]
    [m] = lib.build(ev, {})["moments"]
    assert (m["startMs"], m["endMs"], m["windowBasis"]) == (4000, 12000, "owner_trim")


def test_ratings_on_the_review_page_set_the_tier_and_order():
    labels = [{"item": "x", "sourceSha256": A, "startMs": 0, "endMs": 9000, "decision": "maybe",
               "stars": 4, "what": ["speed"]},
              {"item": "y", "sourceSha256": B, "startMs": 0, "endMs": 9000, "decision": "keep",
               "stars": 5, "note": "best jump of the trip"},
              {"item": "z", "sourceSha256": A, "startMs": 50000, "endMs": 59000,
               "decision": "keep", "stars": 2}]
    got = lib.build(lib.review_evidence(labels), {})
    order = [(m["sourceSha256"][0], m["tier"], m["stars"]) for m in got["moments"]]
    assert order == [("b", "owner_best", 5.0), ("a", "owner_keep", 4.0), ("a", "owner_keep", 2.0)]
    assert got["moments"][0]["notes"] == ["best jump of the trip"]


def test_gallery_trims_keep_and_reject():
    labels = [{"item": "c1", "verdict": "adjusted", "sourceSha256": A,
               "owner": {"originalInMs": 3000, "originalOutMs": 9000}},
              {"item": "c2", "verdict": "reject", "sourceSha256": A,
               "owner": {"originalInMs": 40000, "originalOutMs": 46000}}]
    got = lib.build(lib.boundary_evidence(labels), {})
    assert [m["windowBasis"] for m in got["moments"]] == ["owner_trim"]
    assert got["counts"]["excludedSkipped"] == 1


def test_library_deck_groups_by_outing_and_plays_the_window_inside_a_longer_clip(tmp_path):
    ev = [lib._ev(A, 12000, 18000, "best", "grid", "g"), lib._ev(B, 0, 4000, "keep", "grid", "h")]
    library = lib.build(ev, {A: {"sport": "ski", "outing": "o2"}, B: {"sport": "ski", "outing": "o1"}})
    proxy = tmp_path / "p.mp4"
    deck = lib.deck(library, lambda m: (proxy, 10000, 40000) if m["sourceSha256"] == A else None)
    assert [deck["items"][i]["outing"] for i in deck["order"]] == ["o1", "o2"]
    page = review.page(deck, tmp_path, lambda i: Path(deck["media"][i]) if i in deck["media"] else None)
    assert 'src="p.mp4#t=2.00,8.00"' in page and 'data-start="10000"' in page
    assert "How good, 1 to 5" in page and "No clip yet" in page


def _cli():
    spec = importlib.util.spec_from_file_location(
        "klip_highlight_library", Path(__file__).parent.parent / "scripts" / "klip_highlight_library.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cli_build_page_rate_and_rebuild(tmp_path, capsys):
    cli = _cli()
    reports = tmp_path / "reports"
    disc = reports / "discovery-001"
    (disc / A / "proxies").mkdir(parents=True)
    (disc / A / "proxies" / "0-30000.mp4").write_bytes(b"x")
    (disc / "sources.json").write_text(json.dumps(
        [{"sourceSha256": A, "path": "/x/DJI_20260201100000_0001_D.MP4", "sport": "ski"}]))
    ledgers = reports / "owner-ledgers"
    ledgers.mkdir()
    rows = [screen("d", 0, [clip(A, 0, 30000), clip(A, 40000, 60000)], keep=[0], best=0)]
    (ledgers / "owner-pairs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

    assert cli.main(["build", "--reports", str(reports)]) == 0
    built = json.loads(capsys.readouterr().out)
    assert built["counts"]["byTier"] == {"owner_best": 1}
    assert built["counts"]["excludedSkipped"] == 1

    assert cli.main(["page", "--reports", str(reports)]) == 0
    page = json.loads(capsys.readouterr().out)
    assert page["moments"] == 1 and page["withoutClip"] == 0
    html = Path(page["page"]).read_text()
    assert "../../../discovery-001/" in html

    [item_id] = json.loads((Path(page["page"]).parent / "deck.json").read_text())["order"]
    answers = {"version": review.ANSWERS_VERSION, "deck": page["deck"], "answers": {
        item_id: {"decision": "keep", "stars": 3, "what": ["speed"], "note": "fun line",
                  "bestStartMs": 5000, "bestEndMs": 14000}}}
    f = tmp_path / "answers.json"
    f.write_text(json.dumps(answers))
    assert cli.main(["import-answers", "--reports", str(reports), "--answers", str(f)]) == 0
    assert json.loads(capsys.readouterr().out)["added"] == 1
    assert owner_labels.load(reports, "highlight")[0]["stars"] == 3

    assert cli.main(["build", "--reports", str(reports)]) == 0
    capsys.readouterr()
    library = json.loads((reports / "highlight-library-v1" / "library.json").read_text())
    [m] = library["moments"]
    assert (m["startMs"], m["endMs"], m["windowBasis"]) == (5000, 14000, "owner_trim")
    assert m["tier"] == "owner_best" and m["stars"] == 3.0 and m["what"] == ["speed"]
    assert cli.main(["top", "--reports", str(reports)]) == 0
    assert "owner_best/highlight ★3.0 ski" in capsys.readouterr().out


def test_cli_import_refuses_answers_from_another_page(tmp_path, capsys):
    cli = _cli()
    f = tmp_path / "a.json"
    f.write_text(json.dumps({"version": review.ANSWERS_VERSION, "deck": "nope", "answers": {}}))
    assert cli.main(["import-answers", "--reports", str(tmp_path), "--answers", str(f)]) == 3
    assert "No library page" in capsys.readouterr().err
