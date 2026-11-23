from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from adventure_intelligence import story_harness as h

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")

SHA = "ab" * 32
NAME = "DJI_20260301101134_0204_D.MP4"  # captured 10:11 -> morning
SOURCES = {"0-10000": "testsrc2=size=320x180:rate=10",
           "10000-20000": "color=c=black:size=320x180:rate=10",
           "20000-30000": "testsrc2=size=320x180:rate=10,gblur=sigma=14"}


def _clip(dest: Path, source: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", source,
                    "-t", "10", "-pix_fmt", "yuv420p", "-y", str(dest)], check=True)


@pytest.fixture(scope="module")
def reports(tmp_path_factory):
    root = tmp_path_factory.mktemp("ws") / "reports"
    folder = root / "discovery-001"
    rec = folder / SHA
    (rec / "proxies").mkdir(parents=True)
    (rec / "judgments").mkdir()
    (folder / "sources.json").write_text(json.dumps(
        [{"sourceSha256": SHA, "path": f"/Volumes/x/{NAME}", "sport": "ski"}]))
    (rec / "source.json").write_text(json.dumps({"durationMs": 30000, "sport": "ski"}))
    segs = []
    for key, src in SOURCES.items():
        a, b = map(int, key.split("-"))
        segs.append({"startMs": a, "endMs": b, "role": "selected", "eventCandidatesMs": [],
                     "peakChange": 0.1, "distinctiveness": 0.2})
        _clip(rec / "proxies" / f"{key}.mp4", src)
    (rec / "segments.json").write_text(json.dumps({"segments": segs}))
    (rec / "motion.json").write_text(json.dumps(
        {"series": [{"t_ms": t, "change": 0.1 + 0.01 * (t % 3)} for t in range(0, 30000, 500)]}))
    (rec / "judgments" / "0-10000.json").write_text(json.dumps({
        "frameTimesMs": [500, 2500, 4500, 6500, 8500],
        "panel": {"facts": {"airborne_visible": {"outcome": "agreed_yes"},
                            "landing_visible": {"outcome": "disputed"},
                            "camera_steady": {"outcome": "agreed_yes"}}}}))
    return root


def _cli():
    spec = importlib.util.spec_from_file_location(
        "klip_outing_stories", Path(__file__).parents[1] / "scripts" / "klip_outing_stories.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _quality(reports, tmp_path):
    cli = _cli()
    items = cli.collect(reports, {})
    cli.run_quality(items, tmp_path, lambda m: None)
    return {it["key"]: json.loads((tmp_path / "segments" / SHA / f'{it["key"]}.quality.json')
                                  .read_text()) for it in items}


def test_quality_gate_separates_sharp_black_and_blurred_scenes(reports, tmp_path):
    q = _quality(reports, tmp_path)
    assert q["0-10000"]["verdict"] == "usable"
    assert q["10000-20000"]["verdict"] == "unusable"
    assert {"dark", "blank_or_covered"} <= set(q["10000-20000"]["flags"])
    assert q["20000-30000"]["verdict"] == "degraded" and "soft" in q["20000-30000"]["flags"]
    assert q["0-10000"]["frameTimesMs"] == [500, 2500, 4500, 6500, 8500]  # the judged frames


def _packet(quality=None):
    quality = quality or {"0-10000": {"verdict": "usable", "flags": []},
                          "10000-20000": {"verdict": "unusable", "flags": ["dark"]},
                          "20000-30000": {"verdict": "degraded", "flags": ["soft"]}}
    from datetime import datetime
    cap = datetime(2026, 3, 1, 10, 11, 34)
    facts = {"airborne_visible": {"outcome": "agreed_yes"},
             "landing_visible": {"outcome": "disputed"}}
    desc = {"summary": "A skier carves down a groomed slope and leaves the snow off a "
                       "small jump.", "subjects": ["skier"], "setting": "ski slope",
            "cameraMotion": "moving_smoothly",
            "observations": [{"atSeconds": 2.5, "text": "skier in the air above the snow"}]}
    scenes = []
    for key, qv in quality.items():
        a, b = map(int, key.split("-"))
        scenes.append({"file": NAME, "sourceSha256": SHA, "startMs": a, "endMs": b,
                       "capture": cap, "quality": qv,
                       "facts": facts if a == 0 else None,
                       "description": desc if a == 0 else None,
                       "signals": {"motionPeak": 0.1}})
    return h.build_packet("day / ski", "ski", scenes, footage_ms=30000)


GOOD = {"beats": [{"text": "In the morning the skier launches off a small jump.",
                   "scenes": ["S01"]},
                  {"text": "Later the light fades and the camera goes soft.",
                   "scenes": ["S02", "S03"]}],
        "moments": [{"scene": "S01", "what": "Skier in the air off a jump",
                     "whyItMatters": "The only airborne moment of the reviewed footage.",
                     "evidence": ["fact:airborne_visible", "obs:1"], "confidence": "high"}],
        "shortlist": ["S01"],
        "excluded": [{"scene": "S02", "reason": "too dark"}]}


def test_grounded_story_passes_and_scenes_are_in_clock_order():
    p = _packet()
    assert [s["id"] for s in p["scenes"]] == ["S01", "S02", "S03"]
    assert p["scenes"][0]["clockStart"] == "10:11:34" and p["scenes"][0]["partOfDay"] == "morning"
    assert h.check_story(GOOD, p, 3) == []


@pytest.mark.parametrize("change,expected", [
    (lambda s: s["moments"][0].update(what="A sea turtle glides past"), "mentions turtle"),
    (lambda s: s["beats"][0].update(text="An evening jump."), "says 'evening'"),
    (lambda s: s.update(shortlist=["S02"]), "S02, which is unusable"),
    (lambda s: s.update(shortlist=["S01", "S03"]), "without a moment"),
    (lambda s: s["beats"].reverse(), "out of clock order"),
    (lambda s: s["moments"][0].update(evidence=["fact:landing_visible"]), "disputed fact"),
    (lambda s: s["moments"][0].update(evidence=["obs:7"]), "not in S01's evidence"),
    (lambda s: s["beats"][1].update(text="The last run ends in a crash.", scenes=["S02"]),
     "says 'last'"),
    (lambda s: s["beats"][1].update(text="A crash on the slope.", scenes=["S02"]),
     "mentions crash"),
])
def test_each_ungrounded_claim_is_caught(change, expected):
    story = json.loads(json.dumps(GOOD))
    change(story)
    assert any(expected in v for v in h.check_story(story, _packet(), 3)), \
        h.check_story(story, _packet(), 3)


def test_degraded_scene_needs_its_flaw_acknowledged():
    story = json.loads(json.dumps(GOOD))
    story["moments"].append({"scene": "S03", "what": "A blurry carve", "whyItMatters": "x",
                             "evidence": ["signal:motionPeak"], "confidence": "low"})
    story["shortlist"].append("S03")
    assert any("degraded S03" in v for v in h.check_story(story, _packet(), 3))
    story["moments"][-1]["evidence"].append("quality:soft")
    assert h.check_story(story, _packet(), 3) == []


def _replies(*stories):
    queue = list(stories)

    def transport(body):
        assert body["options"]["temperature"] == 0 and body["format"]["type"] == "object"
        return {"message": {"content": json.dumps(queue.pop(0))}}
    return transport


def test_one_repair_round_then_accept():
    bad = json.loads(json.dumps(GOOD))
    bad["shortlist"] = ["S02"]
    rec = h.tell(_packet(), "gemma3:27b", transport=_replies(bad, GOOD))
    assert rec["harnessStatus"] == "repaired" and rec["attempts"] == 2
    assert rec["story"]["shortlist"] == ["S01"] and rec["removedByHarness"] == []


def test_still_violating_after_repair_is_pruned_never_kept_silently():
    bad = json.loads(json.dumps(GOOD))
    bad["moments"].append({"scene": "S02", "what": "A shark in the dark", "whyItMatters": "rare",
                           "evidence": ["obs:1"], "confidence": "high"})
    bad["shortlist"] = ["S02", "S01"]
    rec = h.tell(_packet(), "gemma3:27b", transport=_replies(bad, bad))
    assert rec["harnessStatus"] == "violating" and rec["violationsBeforePruning"]
    assert rec["story"]["shortlist"] == ["S01"]
    assert all(m["scene"] != "S02" for m in rec["story"]["moments"])
    assert any("shark" in r for r in rec["removedByHarness"])
    assert h.check_story(rec["story"], _packet(), 3) == []
    md = h.markdown(rec, _packet())
    assert "Removed by the harness" in md and "10:11:34" in md


def test_unusable_scene_is_excluded_by_the_harness_if_the_model_forgets():
    story = json.loads(json.dumps(GOOD))
    story["excluded"] = []
    rec = h.tell(_packet(), "gemma3:27b", transport=_replies(story))
    assert any(e["scene"] == "S02" and e["reason"].startswith("harness") for e in rec["story"]["excluded"])


def test_description_times_outside_the_clip_are_sent_back():
    bad = {"observations": [{"atSeconds": 42.0, "text": "a jump"}], "summary": "a jump",
           "subjects": [], "setting": "slope", "cameraMotion": "steady",
           "filming": {"inFocus": "yes", "exposureOk": "yes", "lensClear": "yes"}}
    good = {**bad, "observations": [{"atSeconds": 2.5, "text": "a jump"}]}
    out = h.call_json("qwen3-vl:32b", [{"role": "user", "content": "x"}],
                      schema=h.describe_schema(), transport=_replies(bad, good),
                      check=lambda d: h.check_description(d, 10000, [500, 2500, 4500]))
    assert out["status"] == "repaired" and out["result"]["observations"][0]["atSeconds"] == 2.5


def test_cli_end_to_end_with_fake_models(reports, tmp_path, capsys):
    cli = _cli()
    desc = {"observations": [{"atSeconds": 2.5, "text": "skier in the air above the snow"}],
            "summary": "A skier leaves the snow off a small jump.", "subjects": ["skier"],
            "setting": "ski slope", "cameraMotion": "moving_smoothly",
            "filming": {"inFocus": "yes", "exposureOk": "yes", "lensClear": "yes"}}
    calls = []

    def transport(body):
        calls.append(body["model"])
        if "images" in body["messages"][0]:
            return {"message": {"content": json.dumps(desc)}}
        return {"message": {"content": json.dumps(GOOD)}}

    out = tmp_path / "stories-out"
    argv = ["--reports", str(reports), "--output", str(out)]
    assert cli.main(argv + ["--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["descriptionsToMake"] == 3
    assert cli.main(argv, transport=transport) == 0
    capsys.readouterr()
    assert calls == ["qwen3-vl:32b"] * 2 + ["gemma3:27b"]  # the black clip is not described
    story = json.loads((out / "stories" / "day-2026-03-01--ski.json").read_text())
    assert story["record"]["harnessStatus"] == "accepted"
    assert story["record"]["story"]["shortlist"] == ["S01"]
    assert (out / "stories" / "day-2026-03-01--ski.md").read_text().startswith("# day-2026-03-01")
    assert cli.main(argv, transport=transport) == 0  # resumable: nothing asked again
    assert len(calls) == 3


def test_clip_without_video_is_unusable_and_not_described(reports, tmp_path):
    cli = _cli()
    bad = reports / "discovery-001" / SHA / "proxies" / "20000-30000.mp4"
    backup = bad.read_bytes()
    try:
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "sine=frequency=300:sample_rate=48000", "-t", "2", "-c:a", "aac",
                        "-y", str(bad)], check=True)
        items = cli.collect(reports, {})
        cli.run_quality(items, tmp_path, lambda m: None)
        q = json.loads((tmp_path / "segments" / SHA / "20000-30000.quality.json").read_text())
        assert q["verdict"] == "unusable" and q["flags"] == ["no_usable_video"]
        asked = []
        desc = {"observations": [{"atSeconds": 2.5, "text": "x"}], "summary": "x",
                "subjects": [], "setting": "x", "cameraMotion": "steady",
                "filming": {"inFocus": "yes", "exposureOk": "yes", "lensClear": "yes"}}

        def transport(body):
            asked.append(1)
            return {"message": {"content": json.dumps(desc)}}
        cli.run_describe(items, tmp_path, "qwen3-vl:32b", None, 60, lambda m: None, transport)
        assert len(asked) == 1   # the black clip and the no-video clip are both skipped
    finally:
        bad.write_bytes(backup)


def test_reliability_settled_fact_is_citable_and_marked():
    from datetime import datetime
    scene = {"file": NAME, "sourceSha256": SHA, "startMs": 0, "endMs": 10000,
             "capture": datetime(2026, 3, 1, 10, 0), "quality": {"verdict": "usable", "flags": []},
             "facts": {"crash_or_fall_visible": {"outcome": "resolved_yes", "pYes": 0.93}},
             "description": {"summary": "The skier falls in deep snow.", "subjects": ["skier"],
                             "setting": "slope", "cameraMotion": "shaky", "observations": []},
             "signals": {}}
    p = h.build_packet("o", "ski", [scene], footage_ms=10000)
    s = p["scenes"][0]
    assert s["factsYes"] == ["crash_or_fall_visible"] and s["factsResolved"] == ["crash_or_fall_visible"]
    assert "settled by measured judge reliability" in h.story_prompt(p, 3)
    story = {"beats": [{"text": "A fall in deep snow.", "scenes": ["S01"]}],
             "moments": [{"scene": "S01", "what": "The skier falls", "whyItMatters": "a real crash",
                          "evidence": ["fact:crash_or_fall_visible"], "confidence": "medium"}],
             "shortlist": ["S01"], "excluded": []}
    assert h.check_story(story, p, 3) == []
def test_judges_lens_view_degrades_but_never_drops_a_scene():
    frames = [{"sharpness": 200.0, "meanLuma": 120.0, "lumaStd": 40.0, "darkShare": 0.0,
               "clippedShare": 0.0}] * 4
    q = h.segment_quality(frames, 200.0, 1.0, {"lens_obstructed": {"outcome": "agreed_yes"}})
    assert q["verdict"] == "degraded" and q["flags"] == ["lens_obstructed_by_judges"]


def test_story_and_description_calls_ask_for_the_answer_without_reasoning():
    bodies = []

    def transport(body):
        bodies.append(body)
        return {"message": {"content": json.dumps(GOOD)}, "eval_count": 120,
                "prompt_eval_count": 900}
    rec = h.tell(_packet(), "gemma3:27b", transport=transport)
    assert bodies[0]["think"] is False and rec["think"] is False
    assert rec["usage"]["outputTokens"] == 120
