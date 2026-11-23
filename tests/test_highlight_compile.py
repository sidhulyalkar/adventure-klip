from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from adventure_intelligence import highlight_compile as hc

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")

SHA = "cd" * 32
NAME = "DJI_20241226143814_0056_D.MP4"


def _make(dest: Path, seconds: int, video: str = "testsrc2=size=640x360:rate=30") -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", video,
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                    "-t", str(seconds), "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                    "-y", str(dest)], check=True)


def _duration(path: Path) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                 "-of", "default=nw=1:nk=1", str(path)],
                                capture_output=True, text=True, check=True).stdout)


@pytest.fixture()
def world(tmp_path):
    ssd = tmp_path / "ssd"
    ssd.mkdir()
    original = ssd / NAME
    _make(original, 40)
    reports = tmp_path / "ws" / "reports"
    rec = reports / "discovery-001" / SHA
    (rec / "proxies").mkdir(parents=True)
    (reports / "discovery-001" / "sources.json").write_text(json.dumps(
        [{"sourceSha256": SHA, "path": str(original), "sport": "snorkeling"}]))
    (rec / "source.json").write_text(json.dumps(
        {"durationMs": 40000, "sizeBytes": original.stat().st_size, "sport": "snorkeling"}))
    segs = [{"startMs": 0, "endMs": 20000, "role": "selected", "eventCandidatesMs": [],
             "peakChange": 0.3, "distinctiveness": 0.1},
            {"startMs": 20000, "endMs": 40000, "role": "selected", "eventCandidatesMs": [31000],
             "peakChange": 0.2, "distinctiveness": 0.1}]
    (rec / "segments.json").write_text(json.dumps({"segments": segs}))
    (rec / "motion.json").write_text(json.dumps(
        {"series": [{"t_ms": t, "change": 1.0 if t == 12000 else 0.1} for t in range(0, 40000, 500)]}))
    looks = ["testsrc2=size=640x360:rate=30",
             # a blue-green "underwater" scene: red absorbed
             "smptebars=size=640x360:rate=30,colorchannelmixer=rr=0.25:gg=0.9:bb=1.0"]
    for s, look in zip(segs, looks):
        _make(rec / "proxies" / f'{s["startMs"]}-{s["endMs"]}.mp4', 20, look)
    return {"reports": reports, "ssd": ssd, "original": original}


DESC = {"observations": [{"atSeconds": 14.5, "text": "a turtle swims over the reef"}],
        "summary": "A turtle swims over coral reef in clear water.", "subjects": ["turtle"],
        "setting": "reef", "cameraMotion": "moving_smoothly",
        "filming": {"inFocus": "yes", "exposureOk": "yes", "lensClear": "yes"}}
STORY = {"beats": [{"text": "Over the reef a turtle swims past.", "scenes": ["S01"]},
                   {"text": "The swim continues along the reef.", "scenes": ["S02"]}],
         "moments": [{"scene": "S01", "what": "A turtle swims over the reef",
                      "whyItMatters": "The clearest animal encounter of the swim.",
                      "evidence": ["obs:1", "subject:turtle"], "confidence": "high"},
                     {"scene": "S02", "what": "Swimming along the reef",
                      "whyItMatters": "Shows the reef setting.",
                      "evidence": ["obs:1"], "confidence": "medium"}],
         "shortlist": ["S01", "S02"], "excluded": []}


def _fake(body):
    if "images" in body["messages"][0]:
        import re
        # like a real describer, report what it saw at a frame time it was shown
        times = [float(t) for t in re.findall(r"([0-9]+\.[0-9])s",
                                              body["messages"][0]["content"].split("taken at:")[1])]
        at = min(times, key=lambda t: abs(t - 14.5))
        desc = {**DESC, "observations": [{**DESC["observations"][0], "atSeconds": at}]}
        return {"message": {"content": json.dumps(desc)}}
    return {"message": {"content": json.dumps(STORY)}}


def _pipeline():
    spec = importlib.util.spec_from_file_location(
        "klip_pipeline", Path(__file__).parents[1] / "scripts" / "klip_pipeline.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_anchor_prefers_cited_observation_then_event_then_motion_peak():
    scene = {"startMs": 20000, "endMs": 40000, "observations": ["3.0s: a ray"]}
    assert hc.anchor({"evidence": ["obs:1"]}, scene, None, []) == (3.0, "observation 1")
    assert hc.anchor({"evidence": ["obs:9"]}, scene, {"eventCandidatesMs": [31000]}, []) == \
        (11.0, "event candidate")
    assert hc.anchor(None, scene, None, [{"t_ms": 25000, "change": 2}, {"t_ms": 30000, "change": 1}]) \
        == (5.0, "motion peak")
    assert hc.anchor(None, scene, None, []) == (10.0, "middle")


def test_window_is_sport_shaped_and_clamped():
    assert hc.window(10.0, 20.0, "ski") == (7.0, 15.0)
    assert hc.window(0.5, 20.0, "snorkeling") == (0.0, 10.0)      # clamped at the start
    assert hc.window(19.5, 20.0, "snorkeling") == (10.0, 20.0)    # and at the end
    assert hc.window(3.0, 5.0, "ski") == (0.0, 5.0)               # short scene: all of it


def test_end_to_end_stories_to_preview_reel_and_gallery(world):
    pipe = _pipeline()
    argv = ["run", "--reports", str(world["reports"]), "--roster", "/dev/null", "--skip-judge"]
    assert pipe.main(argv) == 2  # /dev/null is not a roster: refused, nothing written
    assert not (world["reports"] / "outing-stories-v1").exists()
    roster = world["reports"].parent / "roster.json"
    from adventure_intelligence import outing_roster as r
    roster.write_text(json.dumps({"version": r.VERSION, "status": "provisional", "outings": [
        {"outing": "tahiti-day3", "sources": [{"sourceSha256": SHA, "activity": "snorkeling"}]}]}))
    argv[4] = str(roster)
    assert pipe.main(argv, transport=_fake) == 0
    hl = world["reports"] / "highlights-v1"
    recipe = json.loads(next(hl.glob("*.recipe.json")).read_text())
    assert [c["scene"] for c in recipe["clips"]] == ["S01", "S02"]          # clock order
    first = recipe["clips"][0]
    assert first["anchor"] == "observation 1" and 13 < first["anchorSeconds"] < 16
    at = first["anchorSeconds"]                      # snorkeling: 4 s lead-in, 6 s after
    assert (first["proxyInSeconds"], first["proxyOutSeconds"]) == hc.window(at, 20.0, "snorkeling")
    assert first["proxyOutSeconds"] - first["proxyInSeconds"] == 10.0
    assert recipe["isDraft"] and not recipe["publicationApproved"]
    preview = next(hl.glob("*.preview.mp4"))
    meta = json.loads(next(hl.glob("*.preview.json")).read_text())
    assert meta["audio"] and meta["originalVideoReads"] == 0
    assert abs(_duration(preview) - meta["estimatedSeconds"]) < 0.6       # 2 x 10 s - fade
    assert "tahiti-day3" in (hl / "index.html").read_text()
    st = pipe.status(world["reports"])
    assert st["reels"] == {"recipes": 1, "previews": 1, "approved": 0, "masters": 0}
    assert st["stories"]["outingStories"] == 1


def test_master_only_for_an_approved_unchanged_recipe_from_verified_originals(world):
    pipe = _pipeline()
    roster = world["reports"].parent / "roster.json"
    from adventure_intelligence import outing_roster as r
    roster.write_text(json.dumps({"version": r.VERSION, "status": "provisional", "outings": [
        {"outing": "tahiti-day3", "sources": [{"sourceSha256": SHA, "activity": "snorkeling"}]}]}))
    assert pipe.main(["run", "--reports", str(world["reports"]), "--roster", str(roster),
                      "--skip-judge"], transport=_fake) == 0
    hl = world["reports"] / "highlights-v1"
    recipe = json.loads(next(hl.glob("*.recipe.json")).read_text())
    before = world["original"].read_bytes()
    common = ["reels", "--reports", str(world["reports"]), "--trusted-source-root", str(world["ssd"])]
    assert pipe.main(common + ["--master"]) == 0
    assert not list(hl.glob("*.master.mp4"))                                 # not approved
    with pytest.raises(PermissionError):
        hc.render_master(recipe, hl / "x.mp4", originals={}, trusted_root=world["ssd"], approved=set())
    tampered = {**recipe, "targetSeconds": 5}
    with pytest.raises(ValueError, match="changed after"):
        hc.render_master(tampered, hl / "x.mp4", originals={}, trusted_root=world["ssd"],
                         approved={recipe["recipeSha256"]})
    assert pipe.main(common + ["--approve", recipe["recipeSha256"], "--master"]) == 0
    master = next(hl.glob("*.master.mp4"))
    meta = json.loads(next(hl.glob("*.master.json")).read_text())
    assert meta["originalVideoReads"] == 2 and abs(_duration(master) - meta["estimatedSeconds"]) < 0.6
    assert world["original"].read_bytes() == before                          # original untouched
    approvals = json.loads((hl / "approvals.json").read_text())["approved"]
    assert approvals[0]["recipeSha256"] == recipe["recipeSha256"] and approvals[0]["at"]
    elsewhere = world["ssd"].parent / "elsewhere"
    elsewhere.mkdir()
    with pytest.raises(ValueError, match="trusted source root"):
        hc.render_master(recipe, hl / "y.mp4", trusted_root=elsewhere,
                         approved={recipe["recipeSha256"]},
                         originals={SHA: {"path": str(world["original"]),
                                          "sizeBytes": world["original"].stat().st_size}})
    with pytest.raises(ValueError, match="changed since"):
        hc.render_master(recipe, hl / "y.mp4", trusted_root=world["ssd"],
                         approved={recipe["recipeSha256"]},
                         originals={SHA: {"path": str(world["original"]), "sizeBytes": 1}})


def test_identical_scenes_are_dropped_as_near_duplicates(world):
    rec = world["reports"] / "discovery-001" / SHA / "proxies"
    shutil.copy(rec / "0-20000.mp4", rec / "20000-40000.mp4")   # the same footage twice
    pipe = _pipeline()
    roster = world["reports"].parent / "roster.json"
    from adventure_intelligence import outing_roster as r
    roster.write_text(json.dumps({"version": r.VERSION, "status": "provisional", "outings": [
        {"outing": "d", "sources": [{"sourceSha256": SHA, "activity": "snorkeling"}]}]}))
    assert pipe.main(["run", "--reports", str(world["reports"]), "--roster", str(roster),
                      "--skip-judge"], transport=_fake) == 0
    recipe = json.loads(next((world["reports"] / "highlights-v1").glob("*.recipe.json")).read_text())
    assert [c["scene"] for c in recipe["clips"]] == ["S01"]
    assert recipe["dropped"][0]["reason"].startswith("near-duplicate of S01 (thumbnail cosine")
    assert recipe["variety"]["methods"] == ["thumbnail"]


def test_stored_embeddings_win_and_same_recording_overlap_is_a_duplicate():
    import numpy as np
    scenes = [{"id": f"S0{i}", "file": NAME, "sourceSha256": SHA, "startMs": a, "endMs": b,
               "clockStart": None, "quality": {"verdict": "usable", "flags": []},
               "observations": [], "factsYes": [], "factsNo": []}
              for i, (a, b) in enumerate([(0, 10000), (10200, 18000), (40000, 60000)], 1)]
    doc = {"record": {"model": "m", "harnessStatus": "accepted", "packetSha256": "x",
                      "story": {"shortlist": ["S01", "S02", "S03"], "moments": []}},
           "packet": {"outing": "o", "sport": "ski", "scenes": scenes}}
    locate = lambda sha, a, b: {"proxy": Path("/nonexistent.mp4"), "segment": None, "motion": []}
    vec = {0: np.array([1.0, 0]), 40000: np.array([0.0, 1])}
    rec = hc.plan_reel(doc, locate, embed=lambda sha, a, b: vec.get(a - a % 40000))
    reasons = [d["reason"] for d in rec["dropped"]]
    assert [c["scene"] for c in rec["clips"]] == ["S01", "S03"]
    assert reasons == ["overlaps S01 in the same recording"]
    same = lambda sha, a, b: np.array([1.0, 0])
    rec = hc.plan_reel(doc, locate, embed=same)
    assert [c["scene"] for c in rec["clips"]] == ["S01"] and "siglip2 cosine 1.000" in rec["dropped"][1]["reason"]


def _rgb(r, g, b, n=2):
    import numpy as np
    rng = np.random.default_rng(0)
    base = np.stack([np.full((180, 320), c, np.float32) for c in (r, g, b)], -1)
    return [np.clip(base + rng.normal(0, 18, base.shape), 0, 255).astype(np.uint8) for _ in range(n)]


def test_colour_correction_applies_only_to_underwater_snorkeling_clips():
    blue = _rgb(40, 140, 170)
    scene = {"factsYes": [], "factsNo": []}
    p = hc.color_proposal(blue, scene, "snorkeling")
    assert p["applies"] and p["why"].startswith("measured red deficit") and p["params"]["redFromGreen"] > 0.2
    assert not hc.color_proposal(blue, scene, "ski")["applies"]                       # never on snow
    assert not hc.color_proposal(blue, {"factsYes": [], "factsNo": ["underwater"]}, "snorkeling")["applies"]
    neutral = _rgb(120, 125, 130)
    assert not hc.color_proposal(neutral, scene, "snorkeling")["applies"]             # surface shot
    assert hc.color_proposal(neutral, {"factsYes": ["underwater"], "factsNo": []},
                             "snorkeling")["why"] == "judges agreed: underwater"


def _ratio(path: Path) -> tuple[float, float]:
    frames = hc.sample_rgb(path, 0.5, 4.5)
    import numpy as np
    rgb = np.concatenate([f.reshape(-1, 3) for f in frames]).astype(float)
    return rgb[:, 0].mean() / ((rgb[:, 1].mean() + rgb[:, 2].mean()) / 2), float((rgb >= 254).mean())


def test_underwater_mode_restores_red_in_preview_and_changes_the_recipe(world):
    pipe = _pipeline()
    roster = world["reports"].parent / "roster.json"
    from adventure_intelligence import outing_roster as r
    roster.write_text(json.dumps({"version": r.VERSION, "status": "provisional", "outings": [
        {"outing": "d", "sources": [{"sourceSha256": SHA, "activity": "snorkeling"}]}]}))
    base = ["--reports", str(world["reports"])]
    assert pipe.main(["run", *base, "--roster", str(roster), "--skip-judge"], transport=_fake) == 0
    hl = world["reports"] / "highlights-v1"
    plain = json.loads(next(hl.glob("*.recipe.json")).read_text())
    blue = next(c for c in plain["clips"] if c["scene"] == "S02")
    assert blue["color"]["applies"] and not next(c for c in plain["clips"] if c["scene"] == "S01")["color"]["applies"]
    single = {**plain, "clips": [blue]}
    before, _ = _ratio(hc.render_preview({**single, "colorMode": "none"}, hl / "a.mp4") and hl / "a.mp4")
    after, clipped = _ratio(hc.render_preview({**single, "colorMode": "underwater-v1"}, hl / "b.mp4") and hl / "b.mp4")
    assert after > before + 0.25 and clipped < 0.05
    assert pipe.main(["reels", *base, "--color", "underwater-v1"]) == 0
    corrected = json.loads(next(hl.glob("*.recipe.json")).read_text())
    assert corrected["colorMode"] == "underwater-v1"
    assert corrected["recipeSha256"] != plain["recipeSha256"]   # an approval binds the look
    assert list((hl / "history").glob("*.recipe.json"))         # the plain recipe is kept


def test_colour_ab_deck_is_blind_and_scored_with_a_sign_test(world, tmp_path):
    pipe = _pipeline()
    roster = world["reports"].parent / "roster.json"
    from adventure_intelligence import outing_roster as r
    roster.write_text(json.dumps({"version": r.VERSION, "status": "provisional", "outings": [
        {"outing": "d", "sources": [{"sourceSha256": SHA, "activity": "snorkeling"}]}]}))
    base = ["--reports", str(world["reports"])]
    assert pipe.main(["run", *base, "--roster", str(roster), "--skip-judge"], transport=_fake) == 0
    assert pipe.main(["reels", *base, "--ab-color"]) == 0
    ab = world["reports"] / "highlights-v1" / "color-ab"
    key = json.loads((ab / "key.json").read_text())
    page = (ab / "index.html").read_text()
    assert len(key["pairs"]) == 1 and "corrected" not in page and "underwater" not in page
    assert len(list(ab.glob("clip-*.mp4"))) == 2
    truth = key["pairs"]["pair-001"]["variant"]
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"deck": key["deck"], "answers": {"pair-001": truth}}))
    assert pipe.main(["ab-score", *base, "--answers", str(answers)]) == 0
    result = json.loads(next(ab.glob("result-*.json")).read_text())
    assert result["preferCorrected"] == 1 and result["verdict"].startswith("too few")
    answers.write_text(json.dumps({"deck": "other", "answers": {}}))
    assert pipe.main(["ab-score", *base, "--answers", str(answers)]) == 2
    many = {"deck": "d", "variant": "corrected", "baseline": "original",
            "pairs": {f"p{i}": {"variant": "left"} for i in range(16)}}
    picks = {"deck": "d", "answers": {f"p{i}": ("left" if i < 13 else "right") for i in range(16)}}
    s = hc.color_ab_score(many, picks)
    assert (s["preferCorrected"], s["preferOriginal"]) == (13, 3) and s["signTestP"] < 0.05
    assert s["verdict"].startswith("correction preferred")


def test_trim_handles_import_labels_and_score_boundaries(world, tmp_path):
    import os
    from adventure_intelligence import holdout, owner_labels
    pipe = _pipeline()
    roster = world["reports"].parent / "roster.json"
    from adventure_intelligence import outing_roster as r
    roster.write_text(json.dumps({"version": r.VERSION, "status": "provisional", "outings": [
        {"outing": "d", "sources": [{"sourceSha256": SHA, "activity": "snorkeling"}]}]}))
    base = ["--reports", str(world["reports"])]
    assert pipe.main(["run", *base, "--roster", str(roster), "--skip-judge"], transport=_fake) == 0
    hl = world["reports"] / "highlights-v1"
    page = (hl / "index.html").read_text()
    key = json.loads((hl / "boundary-deck.json").read_text())
    ids = sorted(key["clips"])
    assert len(ids) == 2 and all(f'data-id="{i}"' in page for i in ids)
    rel = os.path.relpath(key["clips"][ids[0]]["proxy"], hl)
    assert f'src="{rel}"' in page and "/Volumes" not in page      # relative paths only
    c0, c1 = key["clips"][ids[0]], key["clips"][ids[1]]
    answers = {"deck": key["deck"], "boundaries": {
        ids[0]: {"in": c0["proposedIn"], "out": c0["proposedOut"] + 1.5, "verdict": "adjusted"},
        ids[1]: {"in": c1["proposedIn"], "out": c1["proposedOut"], "verdict": "ok"}}}
    f = tmp_path / "trims.json"
    f.write_text(json.dumps(answers))
    assert pipe.main(["import-trims", *base, "--answers", str(f)]) == 0
    assert pipe.main(["import-trims", *base, "--answers", str(f)]) == 0   # idempotent
    labels = owner_labels.load(world["reports"], "boundary")
    assert len(labels) == 2 and {l["reviewer"] for l in labels} == {"owner"}
    adj = next(l for l in labels if l["verdict"] == "adjusted")
    assert adj["owner"]["originalOutMs"] - adj["owner"]["originalInMs"] == \
        int(round((c0["proposedOut"] + 1.5 - c0["proposedIn"]) * 1000))
    assert pipe.main(["eval-trims", *base]) == 0
    res = json.loads(next((world["reports"] / "evaluations").glob("trims-*.json")).read_text())
    dev = res["methods"]["fixed"]["development"]
    assert dev["clips"] == 2 and dev["truncationRate"] == 0.5 and dev["slackRate"] == 0
    assert res["methods"]["fixed"]["heldOut"]["clips"] == 0
    holdout.freeze(world["reports"], [("2024-12-26", "snorkeling")])
    assert pipe.main(["eval-trims", *base]) == 0
    res = json.loads(sorted((world["reports"] / "evaluations").glob("trims-*.json"))[-1].read_text())
    assert res["methods"]["fixed"]["heldOut"]["clips"] == 2
    bad = tmp_path / "other.json"
    bad.write_text(json.dumps({"deck": "nope", "boundaries": {}}))
    assert pipe.main(["import-trims", *base, "--answers", str(bad)]) == 2


def test_story_vs_baseline_reels_blind_deck(world, tmp_path):
    from adventure_intelligence import holdout
    pipe = _pipeline()
    roster = world["reports"].parent / "roster.json"
    from adventure_intelligence import outing_roster as r
    roster.write_text(json.dumps({"version": r.VERSION, "status": "provisional", "outings": [
        {"outing": "d", "sources": [{"sourceSha256": SHA, "activity": "snorkeling"}]}]}))
    base = ["--reports", str(world["reports"])]
    assert pipe.main(["run", *base, "--roster", str(roster), "--skip-judge"], transport=_fake) == 0
    assert pipe.main(["reels", *base, "--ab-reels", "motionPeak"]) == 0
    ab = world["reports"] / "highlights-v1" / "reel-ab"
    key = json.loads((ab / "key.json").read_text())
    page = (ab / "index.html").read_text()
    assert key["variant"] == "story" and key["baseline"] == "baseline:motionPeak"
    assert len(key["pairs"]) == 1
    assert "preview" not in page and "baseline" not in page            # nothing names a side
    assert len(list(ab.glob("reel-*.mp4"))) == 2
    baseline = json.loads(next(ab.glob("reel-*.recipe.json")).read_text())
    assert baseline["story"]["model"] == "baseline:motionPeak"
    assert all(c["anchor"] != "observation 1" for c in baseline["clips"])  # no story evidence
    truth = key["pairs"]["outing-001"]["variant"]
    f = tmp_path / "a.json"
    f.write_text(json.dumps({"deck": key["deck"], "answers": {"outing-001": truth}}))
    assert pipe.main(["ab-score", *base, "--deck", "reel-ab", "--answers", str(f)]) == 0
    holdout.freeze(world["reports"], [("2024-12-26", "snorkeling")])
    import shutil as _s
    _s.rmtree(ab)
    assert pipe.main(["reels", *base, "--ab-reels", "motionPeak"]) == 0
    assert not (ab / "key.json").exists()                               # held-out: no deck
