"""Every owner label reaches the learner once, in its latest form, without leaking."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from adventure_intelligence import catalog
from adventure_intelligence import outing_roster as roster
from adventure_intelligence import owner_evidence
from adventure_intelligence import value_eval
from adventure_intelligence import value_pairs as pairs_mod

from test_value_learning import build, owner_ledger, sha, write_experiment

A, B, C = sha("a"), sha("b"), sha("c")


def _cli():
    spec = importlib.util.spec_from_file_location(
        "cli", Path(__file__).parents[1] / "scripts" / "klip_value_experiment.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    return cli


@pytest.fixture
def workspace(tmp_path):
    with catalog.connect(tmp_path) as db:
        for i, (s, activity, when) in enumerate(((A, "ski", "2026-01-02T09:00:00"),
                                                 (B, "ski", "2026-01-02T10:00:00"),
                                                 (C, "snorkeling", "2026-02-02T10:00:00"))):
            db.execute("INSERT INTO assets (id,sha256,stored_path,size_bytes,suffix,duration,"
                       "capture_time,activity) VALUES (?,?,?,?,?,?,?,?)",
                       (s, s, f"/media/{i}.mp4", 1, ".mp4", 600.0, when, activity))
        ex = ("INSERT INTO example_revisions (revision_id,example_id,previous_revision_id,"
              "asset_id,start_ms,end_ms,label,decision,highlight,created_at) "
              "VALUES (?,?,?,?,?,?,?,?,?,?)")
        db.execute(ex, ("r1", "e1", None, A, 1000, 9000, "jump", "confirmed", "skip", "2026-01-01"))
        db.execute(ex, ("r2", "e1", "r1", A, 1000, 9000, "jump", "confirmed", "keep", "2026-01-02"))
        db.execute(ex, ("r3", "e2", None, A, 20000, 30000, "lift", "confirmed", "skip", "2026-01-01"))
        db.execute("INSERT INTO review_proposal_links (revision_id,asset_id,proposal_source,"
                   "proposal_id,proposal_label,proposed_start_ms,proposed_end_ms) "
                   "VALUES (?,?,?,?,?,?,?)", ("r3", A, "queue", "p", "lift", 20000, 30000))
        db.execute("INSERT INTO pairwise_revisions (revision_id,comparison_id,left_asset_id,"
                   "right_asset_id,left_start_ms,left_end_ms,right_start_ms,right_end_ms,"
                   "preference,reason,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   ("p1", "c1", A, C, 0, 5000, 0, 5000, "right", "fish", "2026-01-01"))
        db.execute("INSERT INTO pairwise_revisions (revision_id,comparison_id,left_asset_id,"
                   "right_asset_id,left_start_ms,left_end_ms,right_start_ms,right_end_ms,"
                   "preference,reason,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   ("p2", "c2", A, B, 0, 5000, 0, 5000, "uncertain", "?", "2026-01-01"))
    return tmp_path


def test_catalog_reads_latest_revision_and_owner_boundaries(workspace):
    before = (workspace / "catalog.sqlite3").read_bytes()
    found = owner_evidence.from_catalog(workspace)
    by_start = {j["startMs"]: j for j in found["judgments"]}
    assert by_start[1000]["decision"] == "keep"  # r2 superseded r1
    assert by_start[1000]["selection"] == "owner_marked_span"  # no proposal: owner drew it
    assert by_start[20000]["selection"] == "pipeline_selected_window"  # untouched proposal
    assert len(found["pairs"]) == 1 and found["pairs"][0]["outcome"] == 0.0  # uncertain dropped
    assert (workspace / "catalog.sqlite3").read_bytes() == before


def test_review_files_are_found_matched_and_unused_ones_reported(workspace):
    reports = workspace / "reports" / "study"
    reports.mkdir(parents=True)
    manifest = {"version": "social-blind-pairs-v1", "pairs": [
        {"pairId": "x", "sport": "ski",
         "a": {"assetId": A, "startMs": 0, "endMs": 4000, "sport": "ski"},
         "b": {"assetId": B, "startMs": 0, "endMs": 4000, "sport": "ski"}}]}
    raw = json.dumps(manifest).encode()
    (reports / "manifest.json").write_bytes(raw)
    from hashlib import sha256
    (reports / "reviews.json").write_text(json.dumps({
        "version": "social-blind-pair-reviews-v1", "manifestSha256": sha256(raw).hexdigest(),
        "reviews": [{"pairId": "x", "choice": "a", "reasons": [], "notes": ""}]}))
    (reports / "orphan.json").write_text(json.dumps({
        "version": "social-blind-pair-reviews-v1", "manifestSha256": "0" * 64, "reviews": []}))
    (reports / "moments.json").write_text(json.dumps({"version": "klip-owner-moment-annotations-v2"}))
    (reports / "marine.json").write_text(json.dumps({"version": "klip-marine-owner-labels-v2"}))
    found = owner_evidence.from_files(workspace, set())
    assert len(found["pairs"]) == 1 and found["pairs"][0]["outcome"] == 1.0
    unused = {Path(f["file"]).name: f["reason"] for f in found["files"] if not f["used"]}
    assert set(unused) == {"orphan.json", "moments.json", "marine.json"}
    assert "manifest not found" in unused["orphan.json"]
    assert "ledger" in unused["marine.json"]


def test_contradictions_are_set_aside_and_duplicates_merged():
    def row(decision, origin, marked):
        return owner_evidence.judgment(source=A, start=0, end=5000, decision=decision,
                                       selection="owner_marked_span" if marked else
                                       "pipeline_selected_window", goal="g", origin=origin,
                                       provenance=origin + decision)
    collected = owner_evidence.collect(None, [])
    assert collected["judgments"] == []
    rows = [row("keep", "x", False), row("keep", "y", True)]
    out = _collect_from(rows)
    assert len(out["judgments"]) == 1 and out["judgments"][0]["origin"] == "y"
    out = _collect_from(rows + [row("skip", "z", False)])
    assert out["judgments"] == [] and out["counts"]["contradictionsExcluded"] == 1


def _collect_from(judgments):
    original = owner_evidence.from_ledger
    owner_evidence.from_ledger = lambda rows: list(judgments)
    try:
        return owner_evidence.collect(None, [])
    finally:
        owner_evidence.from_ledger = original


def test_cross_outing_pairs_train_but_never_score_and_respect_holdout(tmp_path, monkeypatch):
    made = write_experiment(tmp_path / "exp", outings=5)
    _, items, pool_ids, group_of = build(tmp_path / "exp", made)
    s0, s1 = made["shas"][0], made["shas"][2]  # two different outings
    direct = [{"left": {"sourceSha256": s0, "startMs": 0, "endMs": 20000},
               "right": {"sourceSha256": s1, "startMs": 0, "endMs": 20000},
               "outcome": 1.0, "origin": "studio-pairwise", "goal": "g", "evidenceId": "e"}]
    (pair,) = pairs_mod.from_direct(direct, group_of)
    assert pair["evalEligible"] is False and len(pair["groups"]) == 2
    rows = owner_ledger(made["truth"])
    pairs = pairs_mod.from_ledger(rows, group_of) + [pair]
    from adventure_intelligence import value_head as head
    real = head.fit
    trained = []

    def spy(train, items_, **kwargs):
        trained.append(train)
        return real(train, items_, **kwargs)

    monkeypatch.setattr(head, "fit", spy)
    value_eval.evaluate(items, pairs, rows, pool_ids=pool_ids, roster_confirmed=True)
    held_runs = [t for t in trained if not any(p["group"] == group_of(s0) for p in t)]
    assert held_runs and all(pair not in t for t in held_runs)


def test_ratings_order_kept_clips_within_one_session():
    def row(start, ratings, decision="keep", session="s"):
        j = owner_evidence.judgment(source=A, start=start, end=start + 1000, decision=decision,
                                    selection="pipeline_selected_window", goal="g",
                                    origin="m", provenance=str(start), session=session,
                                    ratings=ratings)
        return j
    rows = [row(0, {"framing": 5, "visibility": 5}), row(2000, {"framing": 2, "visibility": 3}),
            row(4000, {"framing": 5, "visibility": 4.5}),
            row(6000, {"framing": 1, "visibility": 1}, session="other"),
            row(8000, {"framing": 1, "visibility": 1}, decision="skip")]
    made = pairs_mod.from_ratings(rows, lambda s: "day")
    assert [(p["left"]["startMs"], p["right"]["startMs"]) for p in made] == [(0, 2000), (4000, 2000)]
    assert all(p["weight"] == 0.5 and not p["evalEligible"] for p in made)


def _grid_answers(deck, truth, flip_repeat=False):
    answers = []
    for s in deck["screens"]:
        keep = [k for k, c in enumerate(s["clips"])
                if truth[(c["sourceSha256"], c["startMs"], c["endMs"])]]
        if flip_repeat and s["repeatOf"] is not None:
            keep = [k for k in range(len(s["clips"])) if k not in keep]
        answers.append({"index": s["index"], "keep": keep, "best": keep[0] if keep else None})
    return {"version": pairs_mod.GRID_ANSWERS_VERSION, "deckSha256": pairs_mod.deck_digest(deck),
            "answers": answers}


def test_grid_screens_are_one_outing_and_give_labels_pairs_and_consistency(tmp_path):
    made = write_experiment(tmp_path / "exp", outings=3)
    _, items, _, group_of = build(tmp_path / "exp", made)
    deck = pairs_mod.build_grid(items, experiment="exp", screens=12, repeats=3, seed=2)
    assert len(deck["screens"]) == 15
    for s in deck["screens"]:
        assert len({made["mapping"][c["sourceSha256"]] for c in s["clips"]}) == 1
        assert len({json.dumps(c) for c in s["clips"]}) == 4
        if s["repeatOf"] is not None:
            origin = deck["screens"][s["repeatOf"]]
            assert sorted(map(json.dumps, s["clips"])) == sorted(map(json.dumps, origin["clips"]))
            assert s["clips"] != origin["clips"]
    page = pairs_mod.grid_html(deck)
    for word in ("ranked_proposal", "unranked_control", "sampling", "roles"):
        assert word not in page
    rows = pairs_mod.grid_records(deck, _grid_answers(deck, made["truth"]), sha("x"))
    assert pairs_mod.grid_consistency(rows)["sameKeepDecision"] == 1.0
    labels = pairs_mod.grid_judgments(rows)
    assert all(j["selection"] == "random_segment_sample" for j in labels)
    assert all(made["truth"][(j["sourceSha256"], j["startMs"], j["endMs"])] == (j["decision"] == "keep")
               for j in labels)
    pairs = pairs_mod.from_grid(rows, group_of)
    assert pairs and all(made["truth"][(p["left"]["sourceSha256"], p["left"]["startMs"],
                                        p["left"]["endMs"])] for p in pairs)
    assert {p["tier"] for p in pairs} <= {"owner_grid_keep", "owner_grid_best"}


def test_grid_contradictions_drop_the_clips_and_stars_involved(tmp_path):
    made = write_experiment(tmp_path / "exp", outings=2)
    _, items, _, group_of = build(tmp_path / "exp", made)
    deck = pairs_mod.build_grid(items, experiment="exp", screens=6, repeats=2, seed=4)
    rows = pairs_mod.grid_records(deck, _grid_answers(deck, made["truth"], flip_repeat=True), sha("x"))
    assert pairs_mod.grid_consistency(rows)["sameKeepDecision"] == 0.0
    repeated = {json.dumps(c) for s in deck["screens"] if s["repeatOf"] is not None
                for c in s["clips"]}
    for p in pairs_mod.from_grid(rows, group_of):
        assert json.dumps(p["left"]) not in repeated and json.dumps(p["right"]) not in repeated
    labelled = {json.dumps({k: j[k] for k in ("sourceSha256", "startMs", "endMs")})
                for j in pairs_mod.grid_judgments(rows)}
    assert not labelled & repeated


def test_grid_answers_must_star_a_kept_clip(tmp_path):
    made = write_experiment(tmp_path / "exp", outings=1)
    _, items, _, _ = build(tmp_path / "exp", made)
    deck = pairs_mod.build_grid(items, experiment="exp", screens=2, repeats=0)
    bad = {"version": pairs_mod.GRID_ANSWERS_VERSION, "deckSha256": pairs_mod.deck_digest(deck),
           "answers": [{"index": 0, "keep": [1], "best": 2}]}
    with pytest.raises(ValueError, match="star a ticked"):
        pairs_mod.grid_records(deck, bad, sha("x"))


def test_sampled_keep_recall_is_measured_from_grid_labels(tmp_path):
    made = write_experiment(tmp_path / "exp", outings=6)
    _, items, pool_ids, group_of = build(tmp_path / "exp", made)
    deck = pairs_mod.build_grid(items, experiment="exp", screens=36, repeats=0)
    rows = pairs_mod.grid_records(deck, _grid_answers(deck, made["truth"]), sha("x"))
    report = value_eval.evaluate(items, pairs_mod.from_grid(rows, group_of),
                                 pairs_mod.grid_judgments(rows), pool_ids=pool_ids,
                                 roster_confirmed=True)
    recall = report["sampledKeepRecallAtBudget"]["0.25"]
    # Over a third of segments are kept, so even a perfect ranking recovers at most
    # ~0.68 of keeps within a 25% budget; twice chance is near that ceiling.
    assert recall["audio_peak_rel"]["macro"] > 2 * recall["chance"]["macro"]
    assert recall["motion_mean"]["macro"] < recall["audio_peak_rel"]["macro"]
    assert report["coverage"]["randomlySampledJudgedSegments"] > 0
    assert all(0 < r < 1 for r in report["sampledKeepRate"].values())


def test_plan_spreads_across_outings_and_skips_scanned_or_unknown_sports(workspace, tmp_path):
    ledger_rows = []
    from adventure_intelligence import evidence_ledger as ledger
    for i, s in enumerate((A, A, A, B, C)):
        ledger_rows.append(ledger.record(source_sha=s, start_ms=i * 1000, end_ms=i * 1000 + 500,
                                         decision="keep", selection="owner_marked_span",
                                         schema="t", export_digest=sha("e"), record_id=str(i)))
    ledger.append(tmp_path / "ev.jsonl", ledger_rows)
    doc = roster.propose(roster.catalog_rows(workspace))
    (tmp_path / "roster.json").write_text(json.dumps(doc))
    cli = _cli()
    args = ["plan", "--workspace", str(workspace), "--ledger", str(tmp_path / "ev.jsonl"),
            "--roster", str(tmp_path / "roster.json"), "--max", "2"]
    import io
    from contextlib import redirect_stdout
    out = io.StringIO()
    with redirect_stdout(out):
        assert cli.main(args) == 0
    picked = json.loads(out.getvalue())
    # A and B share an outing; C is another. Two picks cover both outings.
    assert {p["sourceSha256"] for p in picked} == {A, C}
    assert picked[0]["path"] == "/media/0.mp4" and picked[1]["sport"] == "snorkeling"
    out = io.StringIO()
    with redirect_stdout(out):
        assert cli.main(args[:-2] + ["--max", "3", "--sport", "ski"]) == 0
    assert [p["sourceSha256"] for p in json.loads(out.getvalue())] == [A, B]


def test_harvest_reports_every_origin_and_unused_file(workspace, tmp_path):
    (workspace / "reports").mkdir()
    (workspace / "reports" / "reel.json").write_text(json.dumps({"version": "klip-reel-timed-reactions-v1"}))
    cli = _cli()
    import io
    from contextlib import redirect_stdout
    out = io.StringIO()
    with redirect_stdout(out):
        assert cli.main(["harvest", "--workspace", str(workspace)]) == 0
    report = json.loads(out.getvalue())
    assert report["judgmentsByOrigin"]["studio-example"]["keep"] == 1
    assert report["pairsByTier"]["owner_studio_pairwise"]["scoreable"] == 0  # A vs C: two outings
    assert report["reviewFiles"]["notConsumed"][0]["version"] == "klip-reel-timed-reactions-v1"


def test_cli_grid_round_trip(tmp_path, capsys):
    made = write_experiment(tmp_path / "exp", outings=6)
    confirmed = {"version": roster.VERSION, "status": "owner_confirmed", "outings": [
        {"outing": o, "sources": [{"sourceSha256": s} for s, g in made["mapping"].items() if g == o]}
        for o in sorted(set(made["mapping"].values()))]}
    (tmp_path / "roster.json").write_text(json.dumps(confirmed))
    cli = _cli()
    assert cli.main(["deck", "--experiment", str(tmp_path / "exp"), "--roster",
                     str(tmp_path / "roster.json"), "--screens", "36"]) == 0
    written = json.loads(capsys.readouterr().out)
    deck = json.loads(Path(written["deck"]).read_text())
    assert deck["version"] == pairs_mod.GRID_DECK_VERSION
    (tmp_path / "answers.json").write_text(json.dumps(_grid_answers(deck, made["truth"])))
    assert cli.main(["import-answers", "--deck", written["deck"], "--answers",
                     str(tmp_path / "answers.json"), "--pairs-ledger",
                     str(tmp_path / "pairs.jsonl")]) == 0
    imported = json.loads(capsys.readouterr().out)
    assert imported["gridConsistency"]["sameKeepDecision"] == 1.0
    assert cli.main(["evaluate", "--experiment", str(tmp_path / "exp"), "--roster",
                     str(tmp_path / "roster.json"), "--pairs-ledger", str(tmp_path / "pairs.jsonl"),
                     "--output", str(tmp_path / "out")]) == 0
    report = json.loads((tmp_path / "out" / "report.json").read_text())
    assert report["coverage"]["pairsByTier"]["owner_grid_keep"] > 0
    assert report["pairAccuracy"]["audio_peak_rel"]["macro"] > 0.8


def test_locate_finds_catalogs_and_review_files_anywhere(workspace, tmp_path):
    elsewhere = tmp_path / "repo" / "reports" / "marine"
    elsewhere.mkdir(parents=True)
    (elsewhere / "labels.json").write_text(json.dumps({"version": "klip-marine-owner-labels-v2"}))
    found = owner_evidence.locate([tmp_path])
    (cat,) = found["catalogs"]
    assert cat["example_revisions"] == 3 and cat["pairwise_revisions"] == 2
    assert found["reviewFilesByFolder"] == {str(elsewhere): {"klip-marine-owner-labels-v2": 1}}
    report = owner_evidence.from_files(workspace, set(), [tmp_path / "repo" / "reports"])
    assert report["files"][0]["file"] == "repo/reports/marine/labels.json"
    assert "ledger" in report["files"][0]["reason"]
