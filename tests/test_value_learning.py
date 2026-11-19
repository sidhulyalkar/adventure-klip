"""Within-outing value learning on synthetic discovery output. No video, no models.

A generator writes discovery-experiment folders whose owner "keeps" are driven
by a planted signal. The tests check that each stage carries that signal
through, that a held-out outing never reaches its own model, and that the
report refuses to claim anything when the data cannot support a claim.
"""
from __future__ import annotations

from hashlib import sha256
import json
import sqlite3

import numpy as np
import pytest

from adventure_intelligence import evidence_ledger as ledger
from adventure_intelligence import outing_roster as roster
from adventure_intelligence import value_eval
from adventure_intelligence import value_features as features
from adventure_intelligence import value_head as head
from adventure_intelligence import value_pairs as pairs_mod

SEG_MS, SEGS = 20_000, 10
DIM = 16


def sha(text: str) -> str:
    return sha256(text.encode()).hexdigest()


def write_experiment(folder, *, outings=6, per_outing=2, seed=0, value_in="audio",
                     proxies=True):
    """Each source: ten 20 s segments. A third are 'valuable'.

    value_in="audio": valuable segments carry a loud second.
    value_in="embedding": valuable segments point along one embedding axis and
    are otherwise indistinguishable; the embedding is returned for attaching.
    Motion is a level unrelated to value, so it is a distractor.
    """
    rng = np.random.default_rng(seed)
    folder.mkdir(parents=True)
    truth, embeddings, shas, mapping = {}, {}, [], {}
    for o in range(outings):
        for s in range(per_outing):
            source = sha(f"{seed}-{o}-{s}")
            shas.append(source)
            mapping[source] = f"outing-{o}"
            base = folder / source
            (base / "proxies").mkdir(parents=True)
            duration = SEG_MS * SEGS
            (base / "source.json").write_text(json.dumps(
                {"sourceSha256": source, "sport": "ski", "durationMs": duration}))
            valuable = rng.random(SEGS) < (0.5 if value_in == "embedding" else 0.35)
            valuable[rng.integers(SEGS)] = True
            motion, audio, segments, frames, times = [], [], [], [], []
            for k in range(SEGS):
                start, level = k * SEG_MS, rng.uniform(0.5, 2.0)
                for t in range(start, start + SEG_MS, 500):
                    motion.append({"t_ms": t, "change": float(level + rng.normal(0, 0.05))})
                loud = start + 7000 if (valuable[k] and value_in == "audio") else None  # "none": no signal
                for t in range(start, start + SEG_MS, 1000):
                    rms = 0.6 if t == loud else 0.05 + abs(rng.normal(0, 0.01))
                    audio.append({"t_ms": t, "rms": rms, "spectralFlux": rms / 2})
                for t in range(start, start + SEG_MS, 4000):
                    v = rng.normal(0, 1, DIM)
                    if value_in == "none":
                        v[0] = rng.normal(0, 0.3)
                    elif value_in == "embedding":
                        # Two equally common looks: valuable footage is not rarer,
                        # so novelty cannot find it and only the content can.
                        v[0] = 4.0 if valuable[k] else -4.0
                    else:
                        v[0] = rng.normal(0, 0.3)
                    frames.append(v)
                    times.append(t)
                segments.append({"startMs": start, "endMs": start + SEG_MS,
                                 "role": "ranked_proposal" if k % 2 else "unranked_control",
                                 "eventCandidatesMs": []})
                truth[(source, start, start + SEG_MS)] = bool(valuable[k])
                if proxies:
                    (base / "proxies" / f"{start}-{start + SEG_MS}.mp4").write_bytes(b"x")
            (base / "motion.json").write_text(json.dumps({"series": motion}))
            (base / "audio.json").write_text(json.dumps({"series": audio}))
            (base / "segments.json").write_text(json.dumps({"segments": segments}))
            matrix = np.array(frames, dtype=np.float32)
            embeddings[source] = (np.array(times), matrix / np.linalg.norm(matrix, axis=1,
                                                                           keepdims=True))
    (folder / "experiment.json").write_text(json.dumps({"sources": shas}))
    return {"truth": truth, "embeddings": embeddings, "mapping": mapping, "shas": shas}


def owner_ledger(truth, *, marked=True, skip_every=1):
    """Owner Keep for every valuable segment, Skip for the rest."""
    rows = []
    for i, ((source, start, end), keep) in enumerate(sorted(truth.items())):
        if not keep and i % skip_every:
            continue
        rows.append(ledger.record(
            source_sha=source, start_ms=start, end_ms=end,
            decision="keep" if keep else "skip",
            selection="owner_marked_span" if marked else "pipeline_selected_window",
            schema="synthetic", export_digest=sha("export"), record_id=f"r{i}",
            goal="outing_highlight"))
    return rows


def build(folder, made, *, embeddings=True, intervals=()):
    sources = features.load_experiment(folder)
    if embeddings:
        for source, (times, matrix) in made["embeddings"].items():
            sources[source].update(frameTimes=times, frames=matrix, embeddingKey="k")

    def group_of(s):
        return roster.group_of(made["mapping"], s)

    pool = features.segment_intervals(sources)
    items = features.build_items(sources, pool + list(intervals), group_of)
    return sources, items, {features.item_id(*p) for p in pool}, group_of


# ---- roster ---------------------------------------------------------------------

def test_roster_splits_on_capture_gaps_and_isolates_undated_recordings():
    rows = [{"sourceSha256": sha("a"), "captureTime": "2026-01-02T09:00:00"},
            {"sourceSha256": sha("b"), "captureTime": "2026-01-02T11:30:00"},
            {"sourceSha256": sha("c"), "captureTime": "2026-01-02T20:00:00"},
            {"sourceSha256": sha("d"), "captureTime": None}]
    doc = roster.propose(rows, gap_hours=6)
    assert [len(o["sources"]) for o in doc["outings"]] == [2, 1, 1]
    assert doc["status"] == "provisional" and doc["undatedSources"] == 1
    mapping = roster.outing_by_source(doc)
    assert mapping[sha("a")] == mapping[sha("b")] != mapping[sha("c")]
    assert roster.group_of(mapping, sha("zzz")).startswith("source:")


def test_roster_rejects_a_recording_in_two_outings():
    row = {"sourceSha256": sha("a")}
    doc = {"version": roster.VERSION, "status": "owner_confirmed",
           "outings": [{"outing": "x", "sources": [row]}, {"outing": "y", "sources": [row]}]}
    with pytest.raises(ValueError, match="two outings"):
        roster.validate(doc)


def test_roster_reads_the_catalog_read_only(tmp_path):
    database = tmp_path / "catalog.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE assets (id TEXT, capture_time TEXT, duration REAL, activity TEXT)")
        db.execute("INSERT INTO assets VALUES (?,?,?,?)", (sha("a"), "2026-01-02T09:00:00", 60, "ski"))
    before = database.read_bytes()
    rows = roster.catalog_rows(tmp_path)
    assert rows[0]["sourceSha256"] == sha("a") and database.read_bytes() == before


# ---- features -------------------------------------------------------------------

def test_features_keep_missing_evidence_as_none_and_measure_planted_audio(tmp_path):
    made = write_experiment(tmp_path / "exp", outings=2)
    (tmp_path / "exp" / made["shas"][0] / "audio.json").unlink()
    _, items, pool_ids, _ = build(tmp_path / "exp", made, embeddings=False)
    assert len(pool_ids) == 2 * 2 * SEGS
    first = [i for i in items.values() if i["sourceSha256"] == made["shas"][0]]
    assert all(i["signals"]["audio_peak_rel"] is None for i in first)
    assert all(i["signals"]["novelty_outing"] is None for i in items.values())
    other = [i for i in items.values() if i["sourceSha256"] == made["shas"][1]]
    loud = [i["signals"]["audio_peak_rel"] for i in other
            if made["truth"][(i["sourceSha256"], i["startMs"], i["endMs"])]]
    quiet = [i["signals"]["audio_peak_rel"] for i in other
             if not made["truth"][(i["sourceSha256"], i["startMs"], i["endMs"])]]
    assert min(loud) > max(quiet)


def test_outing_novelty_ignores_the_interval_and_its_neighbours(tmp_path):
    made = write_experiment(tmp_path / "exp", outings=1, per_outing=1)
    sources = features.load_experiment(tmp_path / "exp")
    source = made["shas"][0]
    times, matrix = made["embeddings"][source]
    # One distinctive segment; its own frames must not count as context.
    rng = np.random.default_rng(5)
    matrix = np.eye(DIM, dtype=np.float32)[0] + 0.2 * rng.normal(size=matrix.shape)
    odd = (times >= 100_000) & (times < 120_000)
    matrix[odd] = np.eye(DIM, dtype=np.float32)[DIM - 1]
    matrix = (matrix / np.linalg.norm(matrix, axis=1, keepdims=True)).astype(np.float32)
    # With a 30 s guard, a 200 s recording still leaves plenty of context frames.
    sources[source].update(frameTimes=times, frames=matrix, embeddingKey="k")
    items = features.build_items(sources, features.segment_intervals(sources),
                                 lambda s: "one")
    target = items[features.item_id(source, 100_000, 120_000)]
    others = [i["signals"]["novelty_outing"] for i in items.values() if i is not target]
    assert target["signals"]["novelty_outing"] > max(others)


def test_catalog_and_described_vectors_are_never_mixed(tmp_path):
    # Real failure: catalog SigLIP (768) for most recordings, description pass (1152)
    # for a few. Novelty and the head must use one space; the rest are set aside.
    made = write_experiment(tmp_path / "exp", outings=2, per_outing=2)
    sources = features.load_experiment(tmp_path / "exp")
    shas = made["shas"]
    rng = np.random.default_rng(1)
    for i, s in enumerate(shas):
        times, _ = made["embeddings"][s]
        width, key = (1152, "described:siglip2") if i == 0 else (768, "catalog:siglip")
        m = rng.normal(size=(len(times), width)).astype(np.float32)
        sources[s].update(frameTimes=times, frames=m / np.linalg.norm(m, axis=1, keepdims=True),
                          embeddingKey=key)
    items = features.build_items(sources, features.segment_intervals(sources),
                                 lambda s: roster.group_of(made["mapping"], s))
    dims = {len(i["embedding"]) for i in items.values() if i["embedding"] is not None}
    assert dims == {768}
    assert all(i["embedding"] is None for i in items.values() if i["sourceSha256"] == shas[0])
    report = features.use_one_embedding_space(sources)
    assert report == {"space": "catalog:siglip", "recordings": 3,
                      "setAside": {"described:siglip2": 1}}
    assert sources[shas[0]]["frames"] is None
    # Once the description pass covers as many recordings, it wins the tie.
    for s in shas[1:2]:
        sources[s].update(frameTimes=made["embeddings"][s][0],
                          frames=np.ones((len(made["embeddings"][s][0]), 4), np.float32) / 2,
                          embeddingKey="described:siglip2")
    sources[shas[0]].update(frameTimes=made["embeddings"][shas[0]][0],
                            frames=np.ones((len(made["embeddings"][shas[0]][0]), 4), np.float32) / 2,
                            embeddingKey="described:siglip2")
    assert features.embedding_space(sources)["space"] == "described:siglip2"


def test_attach_embeddings_keeps_one_model_revision(tmp_path, monkeypatch):
    made = write_experiment(tmp_path / "exp", outings=1, per_outing=2)
    sources = features.load_experiment(tmp_path / "exp")
    (tmp_path / "catalog.sqlite3").touch()
    a, b = made["shas"]

    def frames(db, workspace, asset, provider):
        times, matrix = made["embeddings"][asset]
        rows = [{"tMs": int(t), "embedding": v.tolist()} for t, v in zip(times, matrix, strict=True)]
        return rows, ("model-1" if asset == a else "model-2")

    from adventure_intelligence import scene_atlas
    monkeypatch.setattr(scene_atlas, "frames_for_asset", frames)
    summary = features.attach_embeddings(sources, tmp_path)
    assert summary["sourcesWithEmbeddings"] == 1 and summary["sourcesSkippedOtherModel"] == 1
    assert [sources[x]["frames"] is None for x in (a, b)].count(True) == 1


# ---- pairs and taps -------------------------------------------------------------

def test_ledger_pairs_stay_within_an_outing_and_skip_overlapping_intervals():
    a, b = sha("a"), sha("b")
    rows = [ledger.record(source_sha=a, start_ms=0, end_ms=10_000, decision="skip",
                          selection="pipeline_selected_window", schema="s",
                          export_digest=sha("e"), record_id="1"),
            ledger.record(source_sha=a, start_ms=2_000, end_ms=5_000, decision="keep",
                          selection="owner_marked_span", schema="s",
                          export_digest=sha("e"), record_id="2"),
            ledger.record(source_sha=a, start_ms=20_000, end_ms=30_000, decision="skip",
                          selection="pipeline_selected_window", schema="s",
                          export_digest=sha("e"), record_id="3"),
            ledger.record(source_sha=b, start_ms=0, end_ms=10_000, decision="skip",
                          selection="pipeline_selected_window", schema="s",
                          export_digest=sha("e"), record_id="4")]
    made = pairs_mod.from_ledger(rows, lambda s: "day" if s == a else "other")
    assert len(made) == 1
    assert made[0]["right"]["startMs"] == 20_000 and made[0]["outcome"] == 1.0


def test_deck_is_blind_within_outing_and_repeats_are_swapped(tmp_path):
    made = write_experiment(tmp_path / "exp", outings=3)
    _, items, _, _ = build(tmp_path / "exp", made)
    deck = pairs_mod.build_deck(items, experiment="exp", pairs=40, repeats=8, seed=3)
    assert deck["uniquePairs"] == 40 and len(deck["pairs"]) == 48
    for p in deck["pairs"]:
        assert made["mapping"][p["left"]["sourceSha256"]] == made["mapping"][p["right"]["sourceSha256"]]
        if p["repeatOf"] is not None:
            origin = deck["pairs"][p["repeatOf"]]
            assert (p["left"], p["right"]) == (origin["right"], origin["left"])
            assert p["index"] > origin["index"]
    page = pairs_mod.deck_html(deck)
    for word in ("ranked_proposal", "unranked_control", "score", "sampling"):
        assert word not in page
    again = pairs_mod.build_deck(items, experiment="exp", pairs=40, repeats=8, seed=3)
    assert pairs_mod.deck_digest(again) == pairs_mod.deck_digest(deck)


def test_tap_import_is_bound_to_its_deck_idempotent_and_measures_consistency(tmp_path):
    made = write_experiment(tmp_path / "exp", outings=2)
    _, items, _, group_of = build(tmp_path / "exp", made)
    deck = pairs_mod.build_deck(items, experiment="exp", pairs=20, repeats=5, seed=1)
    flip = {"left": "right", "right": "left"}
    answers = []
    for p in deck["pairs"]:
        choice = "left" if p["index"] % 2 else "right"
        if p["repeatOf"] is not None:
            first = next(a for a in answers if a["index"] == p["repeatOf"])
            choice = flip[first["choice"]]  # perfectly consistent owner
        answers.append({"index": p["index"], "choice": choice})
    doc = {"version": pairs_mod.ANSWERS_VERSION, "deckSha256": pairs_mod.deck_digest(deck),
           "answers": answers}
    rows = pairs_mod.tap_records(deck, doc, sha("export"))
    path = tmp_path / "pairs.jsonl"
    assert pairs_mod.append(path, rows)["added"] == len(rows)
    assert pairs_mod.append(path, rows)["added"] == 0
    consistency = pairs_mod.self_consistency(pairs_mod.load(path))
    assert consistency["repeatedPairs"] == 5 and consistency["sameAnswer"] == 1.0
    assert len(pairs_mod.from_taps(pairs_mod.load(path), group_of)) == 20  # repeats merged
    other = {**deck, "seed": 99}
    with pytest.raises(ValueError, match="do not belong"):
        pairs_mod.tap_records(other, doc, sha("export"))


def test_ties_train_at_half_and_cannot_tell_is_dropped():
    side = {"sourceSha256": sha("a"), "startMs": 0, "endMs": 1}
    base = {"deckSha256": "d", "left": side, "right": {**side, "startMs": 5, "endMs": 6},
            "sampling": "uncertain", "repeatOf": None, "pairEvidenceId": "x"}
    rows = [{**base, "index": 0, "choice": "tie"}, {**base, "index": 1, "choice": "cannot_tell"}]
    made = pairs_mod.from_taps(rows, lambda s: "day")
    assert [p["outcome"] for p in made] == [0.5]
    assert made[0]["evalEligible"] is False  # uncertainty-sampled pairs never score a model


def test_an_inconsistent_repeat_becomes_a_tie():
    side = {"sourceSha256": sha("a"), "startMs": 0, "endMs": 1}
    other = {**side, "startMs": 5, "endMs": 6}
    base = {"deckSha256": "d", "sampling": "random", "pairEvidenceId": "x"}
    rows = [{**base, "index": 0, "left": side, "right": other, "choice": "left", "repeatOf": None},
            {**base, "index": 1, "left": other, "right": side, "choice": "left", "repeatOf": 0}]
    (pair,) = pairs_mod.from_taps(rows, lambda s: "day")
    assert pair["outcome"] == 0.5
    rows[1]["choice"] = "right"  # same preference, sides swapped
    (pair,) = pairs_mod.from_taps(rows, lambda s: "day")
    assert pair["outcome"] == 1.0


# ---- head and evaluation ------------------------------------------------------------

def test_head_learns_a_planted_audio_preference(tmp_path):
    made = write_experiment(tmp_path / "exp", outings=6)
    _, items, _, group_of = build(tmp_path / "exp", made)
    pairs = pairs_mod.from_ledger(owner_ledger(made["truth"]), group_of)
    model = head.fit(pairs, items, variant="signals")
    top = head.export(model)["weights"][0]["evidence"]
    assert top in ("audio_peak_rel", "audio_flux_peak")
    assert head.accuracy(model, pairs, items) > 0.9


def test_full_head_finds_value_only_visible_in_the_embedding(tmp_path):
    made = write_experiment(tmp_path / "exp", outings=6, value_in="embedding")
    _, items, pool_ids, group_of = build(tmp_path / "exp", made)
    rows = owner_ledger(made["truth"])
    pairs = pairs_mod.from_ledger(rows, group_of)
    report = value_eval.evaluate(items, pairs, rows, pool_ids=pool_ids, roster_confirmed=True)
    acc = report["pairAccuracy"]
    assert acc["head_full"]["macro"] > 0.85
    assert acc["head_full"]["macro"] > acc["head_signals"]["macro"] + 0.2
    assert acc["novelty_outing"]["macro"] < 0.7 and acc["audio_peak_rel"]["macro"] < 0.7


def test_each_outing_is_scored_by_a_head_that_never_saw_it(tmp_path, monkeypatch):
    made = write_experiment(tmp_path / "exp", outings=5)
    _, items, pool_ids, group_of = build(tmp_path / "exp", made)
    rows = owner_ledger(made["truth"])
    pairs = pairs_mod.from_ledger(rows, group_of)
    seen = []
    real = head.fit

    def spy(train, items_, **kwargs):
        seen.append({p["group"] for p in train})
        return real(train, items_, **kwargs)

    monkeypatch.setattr(head, "fit", spy)
    original_score = head.score

    def checked(model, item):
        assert item["group"] not in model["_groups"], "held-out outing reached its own model"
        return original_score(model, item)

    def tagging(train, items_, **kwargs):
        model = spy(train, items_, **kwargs)
        if model is not None:
            model["_groups"] = {p["group"] for p in train}
        return model

    monkeypatch.setattr(head, "fit", tagging)
    monkeypatch.setattr(head, "score", checked)
    value_eval.evaluate(items, pairs, rows, pool_ids=pool_ids, roster_confirmed=True)
    assert seen and all(len(groups) < 5 for groups in seen)


def test_scaling_is_fitted_on_training_items_only(tmp_path):
    made = write_experiment(tmp_path / "exp", outings=3)
    _, items, _, group_of = build(tmp_path / "exp", made)
    pairs = pairs_mod.from_ledger(owner_ledger(made["truth"]), group_of)
    train = [p for p in pairs if p["group"] != "outing-0"]
    before = head.fit(train, items, variant="full")
    for item in items.values():
        if item["group"] == "outing-0":
            item["signals"]["motion_mean"] = 1e6  # wild held-out values
            item["embedding"] = -item["embedding"] if item["embedding"] is not None else None
    after = head.fit(train, items, variant="full")
    assert before["spec"]["signals"] == after["spec"]["signals"]
    assert np.allclose(before["weights"], after["weights"])


def test_recall_uses_only_owner_marked_spans_and_beats_chance_on_planted_audio(tmp_path):
    made = write_experiment(tmp_path / "exp", outings=6)
    _, items, pool_ids, group_of = build(tmp_path / "exp", made)
    rows = owner_ledger(made["truth"])
    report = value_eval.evaluate(items, pairs_mod.from_ledger(rows, group_of), rows,
                                 pool_ids=pool_ids, roster_confirmed=True)
    recall = report["recallAtBudget"]["0.25"]
    assert recall["audio_peak_rel"]["macro"] > recall["chance"]["macro"] + 0.3
    assert report["coverage"]["ownerKeptSpansInPool"] == sum(made["truth"].values())
    unmarked = owner_ledger(made["truth"], marked=False)
    report = value_eval.evaluate(items, pairs_mod.from_ledger(unmarked, group_of), unmarked,
                                 pool_ids=pool_ids, roster_confirmed=True)
    assert report["recallAtBudget"]["0.25"]["chance"]["perOuting"] == {}


def test_gate_makes_no_claim_on_small_or_unconfirmed_data(tmp_path):
    made = write_experiment(tmp_path / "exp", outings=3)
    _, items, pool_ids, group_of = build(tmp_path / "exp", made)
    rows = owner_ledger(made["truth"])
    report = value_eval.evaluate(items, pairs_mod.from_ledger(rows, group_of), rows,
                                 pool_ids=pool_ids, roster_confirmed=False)
    assert report["gate"]["status"] == "pilot_no_claim"
    assert any("roster" in r for r in report["gate"]["reasons"])
    assert any("outings" in r for r in report["gate"]["reasons"])


def test_gate_credits_a_head_only_when_it_clearly_beats_the_best_baseline(tmp_path):
    made = write_experiment(tmp_path / "exp", outings=8, value_in="embedding")
    _, items, pool_ids, group_of = build(tmp_path / "exp", made)
    rows = owner_ledger(made["truth"])
    report = value_eval.evaluate(items, pairs_mod.from_ledger(rows, group_of), rows,
                                 pool_ids=pool_ids, roster_confirmed=True)
    assert report["gate"]["status"] == "head_beats_best_baseline"
    made = write_experiment(tmp_path / "exp2", outings=8, value_in="audio")
    _, items, pool_ids, group_of = build(tmp_path / "exp2", made)
    rows = owner_ledger(made["truth"])
    report = value_eval.evaluate(items, pairs_mod.from_ledger(rows, group_of), rows,
                                 pool_ids=pool_ids, roster_confirmed=True)
    # Audio alone already explains every keep, so the head has nothing to add.
    assert report["gate"]["status"] == "head_not_shown_better"


def test_review_queue_explains_each_score(tmp_path):
    made = write_experiment(tmp_path / "exp", outings=4)
    _, items, pool_ids, group_of = build(tmp_path / "exp", made)
    pairs = pairs_mod.from_ledger(owner_ledger(made["truth"]), group_of)
    queue = value_eval.review_queue(items, pairs, pool_ids, per_outing=5)
    some = next(iter(queue["outings"].values()))
    assert len(some["segments"]) == 5 and some["inSample"] is True
    assert some["segments"][0]["why"] and some["segments"][0]["isModelRankingNotOwnerJudgment"]
    assert some["segments"][0]["score"] >= some["segments"][-1]["score"]


# ---- command line ---------------------------------------------------------------

def test_cli_deck_import_and_evaluate_end_to_end(tmp_path, capsys):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "cli", Path(__file__).parents[1] / "scripts" / "klip_value_experiment.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)

    made = write_experiment(tmp_path / "exp", outings=6)
    confirmed = {"version": roster.VERSION, "status": "owner_confirmed", "outings": [
        {"outing": o, "sources": [{"sourceSha256": s} for s, g in made["mapping"].items() if g == o]}
        for o in sorted(set(made["mapping"].values()))]}
    (tmp_path / "roster.json").write_text(json.dumps(confirmed))
    ledger.append(tmp_path / "evidence.jsonl", owner_ledger(made["truth"], skip_every=2))

    assert cli.main(["deck", "--format", "pairs", "--experiment", str(tmp_path / "exp"),
                     "--roster", str(tmp_path / "roster.json"), "--pairs", "30",
                     "--repeats", "5"]) == 0
    written = json.loads(capsys.readouterr().out)
    deck = json.loads(Path(written["deck"]).read_text())
    assert Path(written["page"]).is_file() and written["rosterConfirmed"]

    def louder(side):
        return made["truth"][(side["sourceSha256"], side["startMs"], side["endMs"])]
    answers = [{"index": p["index"], "choice": "left" if louder(p["left"]) >= louder(p["right"])
                else "right"} for p in deck["pairs"]]
    (tmp_path / "answers.json").write_text(json.dumps({
        "version": pairs_mod.ANSWERS_VERSION, "deckSha256": pairs_mod.deck_digest(deck),
        "answers": answers}))
    assert cli.main(["import-taps", "--deck", written["deck"],
                     "--answers", str(tmp_path / "answers.json"),
                     "--pairs-ledger", str(tmp_path / "pairs.jsonl")]) == 0
    capsys.readouterr()

    args = ["evaluate", "--experiment", str(tmp_path / "exp"), "--roster",
            str(tmp_path / "roster.json"), "--ledger", str(tmp_path / "evidence.jsonl"),
            "--pairs-ledger", str(tmp_path / "pairs.jsonl"), "--output", str(tmp_path / "out")]
    assert cli.main(args) == 0
    report = json.loads((tmp_path / "out" / "report.json").read_text())
    assert report["coverage"]["pairsByTier"]["owner_tap"] > 0
    assert report["pairAccuracy"]["audio_peak_rel"]["macro"] > 0.8
    assert report["inputs"]["rosterConfirmed"] is True
    assert (tmp_path / "out" / "queue.json").is_file()
    assert cli.main(args) == 2  # never overwrites an earlier evaluation


def test_same_day_rule_pairs_keeps_filed_under_other_goals():
    # Real data: 12 keeps under personal memory / nature had no same-goal skip, so the
    # default rule gave them no pairs at all.
    def row(i, sha, decision, goal):
        return {"sourceSha256": sha, "startMs": i * 10_000, "endMs": i * 10_000 + 5000,
                "decision": decision, "goal": goal, "evidenceId": f"e{i}"}
    rows = [row(0, "a" * 64, "keep", "curation_personal_memory"),
            row(1, "a" * 64, "skip", "curation_athletic_skill"),
            row(2, "b" * 64, "skip", "curation_athletic_skill"),
            row(3, "c" * 64, "keep", "curation_nature_encounter")]
    day = {"a" * 64: "d1", "b" * 64: "d1", "c" * 64: "d2"}.get
    assert pairs_mod.from_ledger(rows, day) == []
    made = pairs_mod.from_ledger(rows, day, rule="same_day")
    assert len(made) == 2 and {p["group"] for p in made} == {"d1"}
    assert all(p["pairRule"] == "same_day" and p["outcome"] == 1.0 for p in made)
    assert made[0]["goal"] == "curation_athletic_skill|curation_personal_memory"
    with pytest.raises(ValueError):
        pairs_mod.from_ledger(rows, day, rule="anything")
