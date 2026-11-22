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
    for s in segs:
        _make(rec / "proxies" / f'{s["startMs"]}-{s["endMs"]}.mp4', 20)
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
        return {"message": {"content": json.dumps(DESC)}}
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
    assert first["anchor"] == "observation 1" and first["anchorSeconds"] == 14.5
    assert (first["proxyInSeconds"], first["proxyOutSeconds"]) == (10.0, 20.0)
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
