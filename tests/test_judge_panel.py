"""Fact judge panel with a fake transport. No model, network or private media."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from adventure_intelligence import judge_panel as panel


def reply(sport, **overrides):
    answers = {k: "no" for k in panel.questions(sport)}
    answers.update(overrides)
    return {"message": {"content": json.dumps(
        {"answers": answers, "description": "frames of a slope"})}}


def judge(model, sport="ski", **overrides):
    return panel.ask(model, sport, ["aGk="], [0], transport=lambda body: reply(sport, **overrides))


def test_rubric_asks_for_facts_and_never_for_a_highlight_verdict():
    for sport in panel.RUBRICS:
        text = panel.prompt(sport, [0, 500]).lower()
        assert "highlight" not in text and "worth" not in text and "best" not in text
        assert set(panel.schema(sport)["properties"]["answers"]["required"]) == set(
            panel.questions(sport))


def test_frame_plan_bursts_around_events_and_stays_in_bounds():
    times = panel.frame_plan(5_000, 40_000, [11_846, 23_714], max_frames=16)
    assert len(times) <= 16 and times == sorted(set(times))
    assert all(5_000 <= t < 40_000 for t in times)
    for event in (11_846, 23_714):  # a frame before and a frame after each candidate
        assert any(event - 1000 <= t < event for t in times)
        assert any(event < t <= event + 2000 for t in times)
    assert len(panel.frame_plan(0, 600_000, list(range(10_000, 590_000, 20_000)))) == 16
    assert len(panel.frame_plan(0, 20_000)) == 16


def test_family_groups_model_variants():
    assert panel.family("qwen3-vl:32b") == panel.family("qwen2.5vl:7b") == "qwen"
    assert panel.family("gemma3:27b") == "gemma" != panel.family("qwen3-vl:8b")


def test_two_families_agreeing_is_a_fact_and_disagreement_goes_to_the_owner():
    a = judge("qwen3-vl:32b", airborne_visible="yes", landing_visible="yes")
    b = judge("gemma3:27b", airborne_visible="yes", landing_visible="no")
    result = panel.combine([a, b])
    assert result["facts"]["airborne_visible"]["outcome"] == "agreed_yes"
    assert result["facts"]["landing_visible"]["outcome"] == "disputed"
    assert result["needsOwner"] == ["landing_visible"]
    assert result["isModelObservationNotOwnerTruth"] is True


def test_same_family_twice_is_not_independent_agreement():
    result = panel.combine([judge("qwen3-vl:32b", airborne_visible="yes"),
                            judge("qwen3-vl:8b", airborne_visible="yes")])
    assert result["facts"]["airborne_visible"]["outcome"] == "insufficient_independent_judges"


def test_judge_that_says_yes_to_a_control_is_excluded():
    eager = judge("gemma3:27b", control_fish="yes", airborne_visible="yes")
    assert eager["controlFailures"] == ["control_fish"]
    result = panel.combine([judge("qwen3-vl:32b", airborne_visible="yes"), eager])
    assert result["failedControls"] == ["gemma3:27b"] and result["usableJudges"] == 1
    assert "control_fish" not in result["facts"]


@pytest.mark.parametrize("bad", [
    {"message": {"content": "not json"}},
    {"message": {"content": json.dumps({"answers": {}, "description": ""})}},
    {"message": {"content": json.dumps({"answers": {"airborne_visible": "maybe"},
                                        "description": ""})}},
    {},
])
def test_malformed_reply_is_an_abstention_not_a_no(bad):
    result = panel.ask("qwen3-vl:32b", "ski", ["aGk="], [0], transport=lambda body: bad)
    assert result["status"] == "abstained" and result["notANegative"] is True
    combined = panel.combine([result, judge("gemma3:27b")])
    assert combined["abstained"] == ["qwen3-vl:32b"]
    assert combined["facts"]["airborne_visible"]["outcome"] == "insufficient_independent_judges"


def test_unavailable_ollama_is_an_abstention():
    def down(body):
        raise RuntimeError("Local Ollama unavailable")
    assert panel.ask("qwen3-vl:32b", "ski", ["aGk="], [0], transport=down)["status"] == "abstained"


def test_calibration_separates_false_yes_from_false_no():
    panels = [panel.combine([judge("qwen3-vl:32b", "snorkeling", animal_clearly_visible="yes"),
                             judge("gemma3:27b", "snorkeling", animal_clearly_visible="no")]),
              panel.combine([judge("qwen3-vl:32b", "snorkeling", animal_clearly_visible="yes"),
                             judge("gemma3:27b", "snorkeling", animal_clearly_visible="no")])]
    report = panel.calibrate(panels, [
        {"index": 0, "answers": {"animal_clearly_visible": "no"}},
        {"index": 1, "answers": {"animal_clearly_visible": "yes"}}])
    rows = {r["model"]: r for r in report["byModelQuestion"]}
    assert (rows["qwen3-vl:32b"]["falseYes"], rows["qwen3-vl:32b"]["falseNo"]) == (1, 0)
    assert (rows["gemma3:27b"]["falseYes"], rows["gemma3:27b"]["falseNo"]) == (0, 1)
    assert report["smallSampleCaution"] is True


def test_cli_samples_frames_from_a_synthetic_clip_and_requires_two_families(tmp_path, capsys):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg not installed")
    clip = tmp_path / "clip.mp4"
    subprocess.run([ffmpeg, "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc=duration=6:size=320x180:rate=10", "-pix_fmt", "yuv420p",
                    str(clip)], check=True)
    spec = importlib.util.spec_from_file_location(
        "klip_judge_panel", Path(__file__).parents[1] / "scripts" / "klip_judge_panel.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    frames = cli.sample_frames(clip, panel.frame_plan(0, 6000, [3000], max_frames=8))
    assert len(frames) == 8 and all(len(f) > 100 for f in frames)
    assert cli.main(["--proxy", str(clip), "--sport", "ski",
                     "--models", "qwen3-vl:32b", "qwen3-vl:8b"]) == 2
    assert "two different families" in capsys.readouterr().err


def test_ollama_http_error_reason_is_kept_on_the_abstention(monkeypatch):
    import io
    from urllib.error import HTTPError
    from adventure_intelligence import adaptive_judge_v2, judge_panel as jp

    def refuse(req, timeout):
        raise HTTPError(req.full_url, 500, "x", {},
                        io.BytesIO(b'{"error":"unknown model architecture: \\u0027qwen3vl\\u0027"}'))

    monkeypatch.setattr(adaptive_judge_v2, "urlopen", refuse)
    out = jp.ask("qwen3-vl:32b", "ski", ["aGk="], [0])
    assert out["status"] == "abstained"
    assert out["failureDetail"] == "Local Ollama HTTP 500: unknown model architecture: 'qwen3vl'"


def test_context_refusal_is_retried_once_with_a_window_that_fits(monkeypatch):
    monkeypatch.setattr(panel, "_CTX_BY_MODEL", {})
    sent = []

    def ollama(body):
        sent.append(body["options"]["num_ctx"])
        if body["options"]["num_ctx"] < 16921:
            raise RuntimeError('Local Ollama HTTP 400: {"error":{"message":"request (16921 '
                               'tokens) exceeds the available context size (8192 tokens)",'
                               '"type":"exceed_context_size_error","n_prompt_tokens":16921}}')
        return reply("ski")

    first = panel.ask("qwen3-vl:32b", "ski", ["aGk="], [0], transport=ollama)
    assert first["status"] == "answered" and first["options"]["num_ctx"] == 20480
    panel.ask("qwen3-vl:32b", "ski", ["aGk="], [0], transport=ollama)
    assert sent == [8192, 20480, 20480]  # the model's window is remembered


def test_context_beyond_the_bound_is_an_abstention(monkeypatch):
    monkeypatch.setattr(panel, "_CTX_BY_MODEL", {})

    def ollama(body):
        raise RuntimeError('exceed_context_size_error "n_prompt_tokens":90000')

    out = panel.ask("qwen3-vl:32b", "ski", ["aGk="], [0], transport=ollama)
    assert out["status"] == "abstained" and "90000" in out["failureDetail"]
