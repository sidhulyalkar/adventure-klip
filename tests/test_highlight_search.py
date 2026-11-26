"""Highlight-first funnel: search (A), verify (B), owner review (D). Synthetic evidence only."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

from adventure_intelligence import highlight_review as review
from adventure_intelligence import highlight_search as hs
from adventure_intelligence import highlight_verify as hv
from adventure_intelligence import owner_labels

SHA = "a" * 64
SHA2 = "b" * 64
SKI = "c" * 64


class FakeImage:
    """A text encoder where every concept is its own direction (plus one 'scene' direction)."""
    model_id, revision, scale, bias = "fake/siglip", "r1", 10.0, -5.0

    def __init__(self):
        self.concepts = list(dict.fromkeys(f"{g}:{c}" for g, c, _ in hs.query_rows()))
        self.dim = len(self.concepts) + 1

    def axis(self, concept):
        v = np.zeros(self.dim, np.float32)
        v[self.concepts.index(concept)] = 1
        return v

    def texts(self, texts):
        rows = hs.query_rows()
        assert [p for *_, p in rows] == texts
        return np.stack([self.axis(f"{g}:{c}") for g, c, _ in rows])

    def frames(self, n, seed=0, **at):
        """n frames of ordinary scene; at={concept: [frame indices]} adds that concept."""
        rng = np.random.default_rng(seed)
        out = np.zeros((n, self.dim), np.float32)
        out[:, -1] = 1
        out[:, :-1] = rng.normal(0, 0.03, (n, self.dim - 1))
        for concept, idx in at.items():
            for i in idx:
                out[i] += self.axis(concept.replace("__", ":"))
        return out / np.linalg.norm(out, axis=1, keepdims=True)


@pytest.fixture(scope="module")
def image():
    return FakeImage()


@pytest.fixture(scope="module")
def scorer(image):
    doc, vectors = hs.encode_queries(image)
    return hs.Scorer(doc, vectors)


def snorkel(scorer, image, sha=SHA, n=300, seed=0, **at):
    emb = image.frames(n, seed, **at)
    return {"sourceSha256": sha, "sport": "snorkeling", "durationMs": n * 2000,
            "times": np.arange(n) * 2000, "logits": scorer.logits(emb), "file": f"{sha[:4]}.MP4",
            "day": "2026-01-01", "outing": "o1"}


# ---------------------------------------------------------------- stage A: animals

def test_animal_encounter_found_with_a_tight_window(scorer, image):
    rec = snorkel(scorer, image, animal__manta_ray=[150, 151, 152, 153])
    got = hs.search([rec], scorer, set())
    animals = [c for c in got["candidates"] if c["kind"] == "animal"]
    assert len(animals) == 1
    c = animals[0]
    assert c["top"] == "manta_ray" and c["visibleSeconds"] == 8.0
    assert (c["startMs"], c["endMs"]) == (300000 - 3000, 306000 + 2000 + 3000)
    assert 300000 <= c["peakMs"] <= 306000 and c["coreEndMs"] - c["coreStartMs"] == 6000
    assert got["isModelProposalNotOwnerJudgment"] and got["settingsSha256"]


def test_ordinary_reef_proposes_no_animal(scorer, image):
    got = hs.search([snorkel(scorer, image)], scorer, set())
    assert not [c for c in got["candidates"] if c["kind"] == "animal"]


def test_one_ray_scored_as_two_species_is_one_encounter(scorer, image):
    rec = snorkel(scorer, image, animal__stingray=[100, 101], animal__eagle_ray=[101, 102])
    animals = [c for c in hs.search([rec], scorer, set())["candidates"] if c["kind"] == "animal"]
    assert len(animals) == 1
    assert {x["concept"] for x in animals[0]["concepts"]} == {"stingray", "eagle_ray"}


def test_separate_sightings_stay_separate(scorer, image):
    rec = snorkel(scorer, image, animal__sea_turtle=[20, 21, 200, 201])
    animals = [c for c in hs.search([rec], scorer, set())["candidates"] if c["kind"] == "animal"]
    assert [c["top"] for c in animals] == ["sea_turtle", "sea_turtle"]


def test_held_out_recordings_never_set_the_baseline(scorer, image):
    dev = snorkel(scorer, image, SHA, n=200)
    held = snorkel(scorer, image, SHA2, n=200, seed=1, animal__shark=list(range(0, 200)))
    got = hs.search([dev, held], scorer, {SHA2})
    assert got["baseline"]["snorkeling"]["frames"] == 200
    sharks = [c for c in got["candidates"] if c["top"] == "shark"]
    assert sharks and all(c["heldOut"] for c in sharks)


def test_unknown_setting_is_refused(scorer, image):
    with pytest.raises(ValueError, match="Unknown search setting"):
        hs.search([snorkel(scorer, image)], scorer, set(), {"zmin": 3})


def test_query_embeddings_must_match_the_prompts(image):
    doc, vectors = hs.encode_queries(image)
    with pytest.raises(ValueError, match="encode again"):
        hs.Scorer(doc, vectors[:-1])


# ---------------------------------------------------------------- stage A: ski

def ski_recording(scorer, image, *, layout, peak_at=None):
    """layout: [(seconds, state)] with state lift / ride / stop."""
    n = sum(s for s, _ in layout) // 2
    lift, motion, t = [], [], 0
    for seconds, state in layout:
        for k in range(seconds // 2):
            if state == "lift":
                lift.append(t // 2000)
            for half in range(4):     # 2 fps rows inside this 2 s step
                change = {"lift": 0.02, "ride": 0.05, "stop": 0.001}[state]
                if peak_at is not None and abs(t - peak_at) < 6000 and state == "ride":
                    change = 0.2
                motion.append({"t_ms": t + half * 500, "change": change})
            t += 2000
    emb = image.frames(n, 3, ski_context__lift=lift)
    return {"sourceSha256": SKI, "sport": "ski", "durationMs": n * 2000,
            "times": np.arange(n) * 2000, "logits": scorer.logits(emb), "motion": motion,
            "audio": [], "file": "ski.MP4", "day": "2026-02-01", "outing": "s1"}


def test_ski_run_between_lifts_and_its_fastest_moment(scorer, image):
    rec = ski_recording(scorer, image, layout=[(40, "lift"), (160, "ride"), (40, "lift")],
                        peak_at=120000)
    got = hs.search([rec], scorer, set())
    runs = [c for c in got["candidates"] if c["kind"] == "ski_run"]
    assert len(runs) == 1 and runs[0]["run"]["afterLift"] and runs[0]["run"]["beforeLift"]
    assert 38000 <= runs[0]["startMs"] <= 42000 and 198000 <= runs[0]["endMs"] <= 202000
    moments = [c for c in got["candidates"] if c["kind"] in ("ski_action", "ski_jump")]
    assert moments and abs(moments[0]["peakMs"] - 120000) <= 6000
    assert "speed" in moments[0]["reasons"]
    assert all(40000 <= m["peakMs"] < 200000 for m in moments)   # never on the lift
    summary = got["recordings"][0]
    assert summary["runs"] == 1 and summary["seconds"]["lift"] >= 70


def test_a_long_stop_splits_runs_and_a_short_one_does_not(scorer, image):
    long_stop = ski_recording(scorer, image, layout=[(60, "ride"), (60, "stop"), (60, "ride")])
    short_stop = ski_recording(scorer, image, layout=[(60, "ride"), (10, "stop"), (60, "ride")])
    count = lambda rec: sum(c["kind"] == "ski_run"
                            for c in hs.search([rec], scorer, set())["candidates"])
    assert count(long_stop) == 2 and count(short_stop) == 1


# ---------------------------------------------------------------- audit and evaluation

def test_audit_is_reproducible_and_avoids_proposed_moments(scorer, image):
    rec = snorkel(scorer, image, animal__manta_ray=[150, 151])
    a = hs.search([rec], scorer, set(), {"auditRate": 0.2})
    b = hs.search([rec], scorer, set(), {"auditRate": 0.2})
    assert a["audit"] == b["audit"] and a["audit"]
    taken = [(c["startMs"], c["endMs"]) for c in a["candidates"] if c["kind"] in hs.MOMENT_KINDS]
    for w in a["audit"]:
        assert not any(s < w["endMs"] and w["startMs"] < e for s, e in taken)
    eligible = 60 - 2   # 600 s in 10 s windows, minus the two the encounter overlaps
    assert len(a["audit"]) == -(-eligible * 2 // 10)
    other_seed = hs.search([rec], scorer, set(), {"auditRate": 0.2, "seed": 1})["audit"]
    assert other_seed != a["audit"]


def truth(sha, presence, span=None, clip=(0, 600000), kind="ray"):
    return {"sourceSha256": sha, "recordId": f"r-{presence}-{span}", "sampleId": "s",
            "clip": clip, "span": span, "presence": presence,
            "animalType": kind if presence == "present" else "none", "decision": "keep"}


def test_evaluate_recall_kind_false_alarms_and_tightness(scorer, image):
    rec = snorkel(scorer, image, animal__manta_ray=[150, 151, 152])
    found = hs.search([rec], scorer, set())["candidates"]
    t = [truth(SHA, "present", (300000, 304000)),
         truth(SHA, "present", (500000, 510000), kind="turtle"),
         truth(SHA, "absent", clip=(100000, 120000)),
         truth("f" * 64, "present", (0, 1000))]
    ev = hs.evaluate(found, t, searchable={SHA}, held_out=set(),
                     snorkel_hours={"development": 600 / 3600})
    dev = ev["development"]
    assert ev["unsearchable"] == 1
    assert dev["present"] == 2 and dev["found"] == 1 and dev["recall"] == 0.5
    assert dev["rightKind"] == 0.5          # the ray is a ray; the turtle was missed
    assert dev["falseAlarmRate"] == 0.0 and dev["medianSpanCovered"] == 1.0
    assert dev["missed"][0]["animalType"] == "turtle"
    assert dev["candidatesPerHour"] == 6.0
    assert ev["recallIsConditionalOnEarlierScoutSelection"]


def test_marine_truth_reads_a_v1_owner_export():
    from adventure_intelligence import marine_encounter_review as marine
    clip = {"recordId": "c1", "sourceSha256": SHA, "sourceStartMs": 1000,
            "sourceEndMs": 9000, "media": "../p/media/c1.mp4"}
    sample = {"version": marine.VERSION, "planSha256": "p", "receiptsSha256": "r",
              "clips": [clip]}
    sample["sampleId"] = marine._canonical_sha(sample)
    review_row = {"recordId": "c1", "sourceSha256": SHA, "decision": "keep",
                  "animalPresence": "present", "animalType": "ray", "behavior": "swimming",
                  **{k: None for k in marine.RATINGS}, "eventStartMs": 2000, "eventEndMs": 5000}
    export = {"version": marine.LEGACY_LABEL_VERSION, "sampleId": sample["sampleId"],
              "reviews": [review_row]}
    [row] = hs.marine_truth(sample, export)
    assert row["span"] == (2000, 5000) and row["clip"] == (1000, 9000)
    assert row["presence"] == "present" and row["animalType"] == "ray"


def test_operating_point_is_cheapest_setting_reaching_the_recall_target():
    points = [{"zMin": 3, "recall": 0.95, "candidatesPerHour": 40},
              {"zMin": 4, "recall": 0.92, "candidatesPerHour": 12},
              {"zMin": 6, "recall": 0.6, "candidatesPerHour": 3}]
    assert hs.choose_operating_point(points)["zMin"] == 4
    assert hs.choose_operating_point(points, recall_target=0.99)["zMin"] == 3
    assert hs.choose_operating_point([{"recall": None}]) is None


# ---------------------------------------------------------------- stage B

def test_frame_plan_is_dense_around_the_peak_and_inside_the_clip():
    times = hv.frame_plan(20000, 10000)
    assert times == sorted(times) and 0 <= times[0] and times[-1] <= 20000 - hv.MARGIN_MS
    near = [t for t in times if abs(t - 10000) <= 3000]
    assert len(near) >= len(times) / 2
    assert hv.frame_plan(30, 0) == [0]


@pytest.mark.parametrize("answer,expected", [
    ({"animal": "no_animal", "framesWithAnimal": [2], "count": 0}, "no_animal must list no"),
    ({"animal": "manta_ray", "framesWithAnimal": [], "count": 1}, "must list the frames"),
    ({"animal": "manta_ray", "framesWithAnimal": [3], "count": 0}, "count of at least 1"),
    ({"animal": "other_animal", "framesWithAnimal": [1], "count": 1, "otherAnimal": ""},
     "name the animal"),
    ({"animal": "shark", "framesWithAnimal": [9], "count": 1}, "frame numbers 1 to 4"),
])
def test_marine_answer_checks(answer, expected):
    assert any(expected in v for v in hv.check_marine(answer, 4))


def test_ski_answer_checks():
    assert hv.check_ski({"airborne": "yes", "framesAirborne": []}, 4)
    assert hv.check_ski({"airborne": "no", "framesAirborne": [1]}, 4)
    assert not hv.check_ski({"airborne": "yes", "framesAirborne": [2, 3]}, 4)


def _reply(answer):
    def transport(body):
        transport.bodies.append(body)
        return {"message": {"content": json.dumps(answer)}, "done_reason": "stop",
                "prompt_eval_count": 10, "eval_count": 5}
    transport.bodies = []
    return transport


MANTA = {"animal": "manta_ray", "otherAnimal": "", "count": 1, "framesWithAnimal": [2, 3],
         "distance": "close", "clarity": "clear", "snorkelerInFrame": "no"}


def test_verify_is_blind_to_what_the_search_suspected():
    a = {"id": "x", "kind": "animal", "top": "manta_ray", "sourceSha256": SHA,
         "startMs": 0, "endMs": 8000}
    b = {**a, "id": "y", "top": "shark"}
    t = _reply(MANTA)
    ra = hv.ask(a, ["img"] * 4, [0, 1000, 2000, 3000], 8000, model="m", transport=t)
    rb = hv.ask(b, ["img"] * 4, [0, 1000, 2000, 3000], 8000, model="m", transport=t)
    assert t.bodies[0]["messages"] == t.bodies[1]["messages"]
    assert ra["verdict"] == "verified" and ra["searchAgrees"] and not rb["searchAgrees"]
    assert ra["isModelOutputNotOwnerJudgment"] and ra["frameTimesMs"] == [0, 1000, 2000, 3000]


@pytest.mark.parametrize("kind,answer,expected", [
    ("animal", {**MANTA, "clarity": "poor"}, "uncertain"),
    ("animal", {**MANTA, "animal": "no_animal"}, "rejected"),
    ("ski_action", {"activity": "on_chairlift_or_gondola", "clarity": "clear"}, "rejected"),
    ("ski_jump", {"activity": "skiing_downhill", "airborne": "no", "clarity": "clear"},
     "verified_not_airborne"),
    ("ski_jump", {"activity": "skiing_downhill", "airborne": "yes", "clarity": "clear"},
     "verified"),
    ("companion", {"snorkelerInFrame": "yes", "clarity": "clear"}, "verified"),
])
def test_verdicts(kind, answer, expected):
    assert hv.verdict({"kind": kind, "top": "manta_ray"}, answer)["verdict"] == expected


def test_verify_queue_puts_animals_first_and_skips_done():
    cands = [{"id": "r", "kind": "ski_run", "score": 9}, {"id": "a1", "kind": "animal", "score": 2},
             {"id": "a2", "kind": "animal", "score": 5}, {"id": "j", "kind": "ski_jump", "score": 1}]
    assert [c["id"] for c in hv.queue(cands)] == ["a2", "a1", "j", "r"]
    assert [c["id"] for c in hv.queue(cands, done={"a2"})] == ["a1", "j", "r"]


# ---------------------------------------------------------------- stage D

def _deck():
    cands = [{"id": f"m{i}", "kind": "animal", "sport": "snorkeling", "sourceSha256": SHA,
              "startMs": i * 10000, "endMs": i * 10000 + 8000, "peakMs": i * 10000 + 4000,
              "top": "manta_ray", "score": 10 - i, "file": "a.MP4"} for i in range(6)]
    results = {"m0": {"verdict": "verified", "animal": "manta_ray", "model": "m"},
               "m1": {"verdict": "uncertain", "animal": "unclear", "model": "m"},
               **{f"m{i}": {"verdict": "rejected", "animal": "no_animal", "model": "m"}
                  for i in range(2, 6)}}
    audit = [{"id": "au1", "kind": "audit", "sport": "snorkeling", "sourceSha256": SHA,
              "startMs": 100000, "endMs": 110000, "peakMs": 105000, "file": "a.MP4"}]
    return review.build_deck(cands, audit, results)


def test_deck_mixes_proposed_rejected_sample_and_audit_in_a_fixed_order():
    deck = _deck()
    assert deck["counts"] == {"proposed": 2, "rejected_sample": 3, "audit": 1}
    assert deck == _deck() and sorted(deck["order"]) == sorted(deck["items"])


def test_review_page_is_blind_and_accessible(tmp_path):
    deck = _deck()
    page = review.page(deck, tmp_path, lambda i: tmp_path / f"{i}.mp4")
    low = page.lower()
    for leak in ("audit", "rejected", "verified", "uncertain", "searchtop", "score"):
        assert leak not in low, leak
    assert page.count("<fieldset>") == 3 * len(deck["items"]) and 'aria-live="polite"' in page
    assert 'lang="en"' in page and page.count("<legend>") == 3 * len(deck["items"])


def test_answers_become_owner_labels_and_measure_the_funnel(tmp_path):
    deck = _deck()
    answers = {"version": review.ANSWERS_VERSION, "deck": deck["deck"], "answers": {
        "m0": {"decision": "keep", "what": ["manta_ray", "not_a_choice"],
               "bestStartMs": 1000, "bestEndMs": 6000},
        "m1": {"decision": "maybe", "what": ["stingray"]},
        "au1": {"decision": "keep", "what": ["sea_turtle"], "bestStartMs": 1, "bestEndMs": 2},
        "m2": {"decision": "skip", "what": []},
        "zz": {"decision": "keep"}}}
    raw = json.dumps(answers).encode()
    got = review.import_answers(tmp_path, deck, answers, raw)
    assert got["added"] == 4 and got["skipped"] == 1
    assert review.import_answers(tmp_path, deck, answers, raw)["added"] == 0   # idempotent
    labels = {l["item"]: l for l in owner_labels.load(tmp_path, "highlight")}
    assert labels["m0"]["what"] == ["manta_ray"] and labels["m0"]["bestStartMs"] == 1000
    assert labels["au1"]["bestStartMs"] is None          # outside the window: dropped
    ev = review.evaluate(deck, list(labels.values()))["development"]
    assert ev["byRole"]["audit"]["keepRate"] == 1.0
    assert ev["estimatedMissedKeepsPerUnproposedHour"] == 360.0
    assert ev["verifierSpecies"] == {"right": 1, "verifier_unclear_owner_animal": 1,
                                     "verifier_none_owner_none": 1}
    with pytest.raises(ValueError, match="not for deck"):
        review.import_answers(tmp_path, deck, {**answers, "deck": "other"}, raw)


# ---------------------------------------------------------------- the command line

def _cli():
    spec = importlib.util.spec_from_file_location(
        "klip_highlight_search", Path(__file__).parent.parent / "scripts" / "klip_highlight_search.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _workspace(tmp_path, image, scorer):
    reports = tmp_path / "reports"
    disc = reports / "discovery-001"
    rec = disc / SHA
    rec.mkdir(parents=True)
    (disc / "sources.json").write_text(json.dumps(
        [{"sourceSha256": SHA, "path": str(tmp_path / "src" / "DJI_20260101100000_0001_D.MP4"),
          "sport": "snorkeling"}]))
    (rec / "source.json").write_text(json.dumps({"sourceSha256": SHA, "durationMs": 600000,
                                                  "sizeBytes": 10}))
    ev = reports / "evidence"
    (ev / SHA).mkdir(parents=True)
    (ev / "vocabulary.json").write_text(json.dumps({"imageModel": image.model_id}))
    (ev / SHA / "meta.json").write_text(json.dumps({"imageModel": image.model_id}))
    emb = image.frames(300, animal__sea_turtle=[100, 101, 102]).astype(np.float16)
    np.savez(ev / SHA / "frames.npz", times=np.arange(300) * 2000, embeddings=emb)
    home = reports / "highlight-search-v1"
    home.mkdir()
    doc, vectors = hs.encode_queries(image)
    (home / "queries.json").write_text(json.dumps(doc))
    np.savez(home / "queries.npz", prompts=vectors)
    return reports


def test_cli_search_verify_review_end_to_end(tmp_path, image, scorer, monkeypatch, capsys):
    cli = _cli()
    reports = _workspace(tmp_path, image, scorer)
    assert cli.main(["search", "--reports", str(reports)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["animalsFound"] == {"sea_turtle": 1} and out["inventory"]["searchable"] == 1

    cut = []
    monkeypatch.setattr(cli, "cut_clip", lambda sources, c, dest: (
        cut.append(c["id"]), dest.parent.mkdir(parents=True, exist_ok=True), dest.write_bytes(b"x")))
    from adventure_intelligence import panel_frames
    monkeypatch.setattr(panel_frames, "sample_frames", lambda clip, times: ["img"] * len(times))
    monkeypatch.setattr(panel_frames, "video_span", lambda clip: None)
    turtle = {**MANTA, "animal": "sea_turtle"}
    t = _reply(turtle)
    argv = ["verify", "--reports", str(reports), "--kinds", "animal"]
    assert cli.main(argv, transport=t) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["asked"] == 1 and got["byKind"]["animal"] == {"verified": 1}
    assert got["searchToVerifier"] == {"sea_turtle -> sea_turtle": 1}
    assert cli.main(argv, transport=t) == 0          # resumable: nothing asked again
    assert json.loads(capsys.readouterr().out)["asked"] == 0 and len(t.bodies) == 1

    assert cli.main(["review", "--reports", str(reports)]) == 0
    page = json.loads(capsys.readouterr().out)
    assert page["counts"]["proposed"] == 1 and Path(page["page"]).is_file()


def test_cli_refuses_stale_prompt_embeddings(tmp_path, image, scorer, capsys):
    cli = _cli()
    reports = _workspace(tmp_path, image, scorer)
    q = reports / "highlight-search-v1" / "queries.json"
    doc = json.loads(q.read_text())
    q.write_text(json.dumps({**doc, "queriesSha256": "0" * 64}))
    assert cli.main(["search", "--reports", str(reports)]) == 3
    assert "encode-queries again" in capsys.readouterr().err


def test_cli_finds_marine_label_pairs(tmp_path, capsys):
    from adventure_intelligence import marine_encounter_review as marine
    cli = _cli()
    sample = {"version": marine.VERSION, "clips": [], "planSha256": "p", "receiptsSha256": "r"}
    sample["sampleId"] = marine._canonical_sha(sample)
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "marine-sample.json").write_text(json.dumps(sample))
    (tmp_path / "dl").mkdir()
    (tmp_path / "dl" / "marine-labels.json").write_text(json.dumps(
        {"version": marine.LABEL_VERSION, "sampleId": sample["sampleId"], "reviews": []}))
    (tmp_path / "dl" / "orphan.json").write_text(json.dumps(
        {"version": marine.LABEL_VERSION, "sampleId": "nope", "reviews": []}))
    assert cli.main(["find-labels", "--roots", str(tmp_path)]) == 0
    got = json.loads(capsys.readouterr().out)
    assert len(got["pairs"]) == 1 and len(got["exportsWithoutSample"]) == 1
