"""End-to-end discovery run on generated test video with a fake judge. No private media."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from adventure_intelligence import discovery_experiment as experiment
from adventure_intelligence import evidence_ledger as ledger
from adventure_intelligence import judge_panel as panel

pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg not installed")


def answer(body):
    sport = "ski" if "control_fish" in body["format"]["properties"]["answers"]["required"] \
        and "takeoff_visible" in body["format"]["properties"]["answers"]["required"] else "snorkeling"
    answers = {k: "no" for k in panel.questions(sport)}
    if body["model"].startswith("qwen"):
        answers["camera_steady"] = "yes"  # the two judges disagree on one fact
    return {"message": {"content": json.dumps({"answers": answers, "description": "test"})}}


@pytest.fixture
def setup(tmp_path):
    footage, workspace = tmp_path / "footage", tmp_path / "ws"
    footage.mkdir()
    workspace.mkdir()
    clip = footage / "run.mp4"
    # Still, then moving, then still: gives the segmenter a real level shift.
    subprocess.run([shutil.which("ffmpeg"), "-loglevel", "error", "-f", "lavfi", "-i",
                    "color=c=gray:s=160x90:r=10:d=10", "-f", "lavfi", "-i",
                    "testsrc2=s=160x90:r=10:d=12", "-f", "lavfi", "-i",
                    "color=c=gray:s=160x90:r=10:d=10", "-filter_complex",
                    "[0][1][2]concat=n=3:v=1", "-pix_fmt", "yuv420p", str(clip)], check=True)
    sources = tmp_path / "sources.json"
    sha = experiment.source_digest(clip)
    sources.write_text(json.dumps([{"sourceSha256": sha, "path": str(clip), "sport": "ski"}]))
    return {"clip": clip, "sha": sha, "sources": sources, "footage": footage,
            "workspace": workspace, "out": workspace / "reports" / "exp"}


def go(s, **kwargs):
    kwargs.setdefault("transport", answer)
    return experiment.run(s["sources"], kwargs.pop("out", s["out"]), workspace=s["workspace"],
                          trusted_root=s["footage"], **kwargs)


def test_full_run_produces_segments_proxies_judgments_and_leaves_source_untouched(setup):
    before = setup["clip"].read_bytes()
    report = go(setup)
    folder = setup["out"] / setup["sha"]
    assert report["totals"]["failedSources"] == 0 and report["containsOwnerJudgments"] is False
    (source,) = report["sources"]
    assert source["segments"] >= 2 and source["judgedSegments"] >= 1
    assert len(list((folder / "proxies").glob("*.mp4"))) == source["reviewSegments"]
    assert report["totals"]["segmentsWithDisputedFacts"] == source["judgedSegments"]
    assert report["ownerReviewQueue"][0]["questions"] == ["camera_steady"]
    assert setup["clip"].read_bytes() == before
    assert str(setup["footage"]) not in (setup["out"] / "report.json").read_text()


def test_rerun_resumes_without_decoding_cutting_or_judging_again(setup):
    go(setup)
    calls = {"decode": 0, "cut": 0, "judge": 0}

    def decode(*args):
        calls["decode"] += 1
        return []

    def cut(*args, **kwargs):
        calls["cut"] += 1

    def transport(body):
        calls["judge"] += 1
        return answer(body)

    go(setup, decode=decode, cut=cut, transport=transport)
    assert calls == {"decode": 0, "cut": 0, "judge": 0}


def test_abstained_judges_are_retried_alone_on_the_next_run(setup):
    def qwen_down(body):
        if body["model"].startswith("qwen"):
            raise RuntimeError("Local Ollama unavailable")
        return answer(body)

    first = go(setup, transport=qwen_down)
    assert first["totals"]["judgeAbstentions"] == first["totals"]["judgedSegments"] > 0
    asked = []

    def record(body):
        asked.append(body["model"])
        return answer(body)

    second = go(setup, transport=record)
    assert second["totals"]["judgeAbstentions"] == 0
    assert asked and all(m.startswith("qwen") for m in asked)


def test_changed_settings_need_a_new_output_directory(setup):
    go(setup, stages=("verify", "motion", "segments"))
    with pytest.raises(ValueError, match="new output directory"):
        go(setup, overrides={"budgetFraction": 0.5})


def test_stages_can_stop_before_any_model_call(setup):
    def never(body):
        raise AssertionError("no model call expected")
    report = go(setup, stages=("verify", "motion", "segments"), transport=never)
    assert report["sources"][0]["judgedSegments"] == 0


def test_wrong_sha_fails_that_source_only_and_is_recorded(setup, tmp_path):
    rows = json.loads(setup["sources"].read_text())
    rows.append({"sourceSha256": "f" * 64, "path": rows[0]["path"], "sport": "ski"})
    setup["sources"].write_text(json.dumps(rows))
    report = go(setup)
    assert report["totals"]["failedSources"] == 1
    assert "SHA mismatch" in report["sources"][1]["failure"]
    assert report["sources"][0]["failure"] is None and report["sources"][0]["judgedSegments"] >= 1


def test_output_inside_footage_and_oversized_or_single_family_runs_are_refused(setup):
    with pytest.raises(ValueError, match="outside the footage"):
        go(setup, out=setup["footage"] / "out")
    with pytest.raises(ValueError, match="two different families"):
        go(setup, overrides={"models": ["qwen3-vl:32b", "qwen3-vl:8b"]})
    row = json.loads(setup["sources"].read_text())[0]
    many = [{**row, "sourceSha256": f"{i:064x}"} for i in range(experiment.MAX_SOURCES + 1)]
    setup["sources"].write_text(json.dumps(many))
    with pytest.raises(ValueError, match="1..12"):
        go(setup)


def test_source_outside_trusted_root_is_not_read(setup, tmp_path):
    elsewhere = tmp_path / "elsewhere.mp4"
    shutil.copy(setup["clip"], elsewhere)
    rows = json.loads(setup["sources"].read_text())
    rows[0]["path"] = str(elsewhere)
    setup["sources"].write_text(json.dumps(rows))
    report = go(setup)
    assert "not under --trusted-source-root" in report["sources"][0]["failure"]


def test_owner_spans_in_the_ledger_add_a_baseline_comparison(setup, tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger.append(path, [ledger.record(
        source_sha=setup["sha"], start_ms=11_000, end_ms=21_000, decision="keep",
        selection="owner_marked_span", schema="t", export_digest="d" * 64, record_id="r")])
    report = go(setup, ledger_file=path, stages=("verify", "motion", "segments"))
    assert report["sources"][0]["ownerKeptSpans"] == 1
    assert set(report["sources"][0]["comparison"]) >= {"fixedWindows", "segments"}


def test_preflight_names_models_that_are_not_pulled():
    def tags():
        return {"models": [{"name": "gemma3:27b"}]}
    assert experiment.missing_models(["qwen3-vl:32b", "gemma3:27b"], fetch=tags) == ["qwen3-vl:32b"]

    def down():
        raise RuntimeError("Ollama is not running on this machine")
    with pytest.raises(RuntimeError):
        experiment.missing_models(["gemma3:27b"], fetch=down)


def test_audio_timeline_is_saved_once_and_resumed(setup):
    calls = []

    def audio(path):
        calls.append(path)
        return [{"t_ms": 0, "rms": 0.1, "spectralFlux": None, "zeroCrossingRate": 0.2}]

    go(setup, stages=("verify", "motion", "audio", "segments"), audio=audio)
    saved = json.loads((setup["out"] / setup["sha"] / "audio.json").read_text())
    assert saved["series"] == [{"t_ms": 0, "rms": 0.1, "spectralFlux": None}]
    go(setup, stages=("verify", "motion", "audio", "segments"), audio=audio)
    assert len(calls) == 1


def test_silent_recording_records_an_empty_audio_timeline(setup):
    go(setup, stages=("verify", "motion", "audio"))
    assert json.loads((setup["out"] / setup["sha"] / "audio.json").read_text())["series"] == []


def test_proxy_keeps_sound_when_the_source_has_it(tmp_path):
    clip = tmp_path / "loud.mp4"
    subprocess.run([shutil.which("ffmpeg"), "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=s=160x90:r=10:d=4", "-f", "lavfi", "-i", "sine=f=440:d=4",
                    "-shortest", "-pix_fmt", "yuv420p", str(clip)], check=True)
    before = clip.read_bytes()
    experiment.cut_proxy(clip, tmp_path / "p.mp4", 500, 3500, width=160)
    streams = subprocess.run([shutil.which("ffprobe"), "-v", "error", "-show_entries",
                              "stream=codec_type", "-of", "csv=p=0", str(tmp_path / "p.mp4")],
                             capture_output=True, text=True, check=True).stdout.split()
    assert sorted(streams) == ["audio", "video"] and clip.read_bytes() == before
    experiment.cut_proxy(clip, tmp_path / "q.mp4", 500, 3500, width=160, audio=False)
    streams = subprocess.run([shutil.which("ffprobe"), "-v", "error", "-show_entries",
                              "stream=codec_type", "-of", "csv=p=0", str(tmp_path / "q.mp4")],
                             capture_output=True, text=True, check=True).stdout.split()
    assert streams == ["video"]


def test_untagged_recording_runs_every_stage_but_the_judge(setup):
    rows = json.loads(setup["sources"].read_text())
    rows[0]["sport"] = "unknown"
    setup["sources"].write_text(json.dumps(rows))

    def never(body):
        raise AssertionError("no judge call for an untagged recording")

    report = go(setup, transport=never)
    (source,) = report["sources"]
    assert source["failure"] is None and source["segments"] >= 2 and source["judgedSegments"] == 0
    assert list((setup["out"] / setup["sha"] / "proxies").glob("*.mp4"))


def test_unreadable_recording_fails_alone_and_the_run_continues(setup):
    def broken(path):
        raise subprocess.CalledProcessError(1, ["ffprobe", str(path)])

    report = go(setup, stages=("verify", "motion", "audio", "segments"), audio=broken)
    (source,) = report["sources"]
    assert source["failure"].startswith("CalledProcessError")
    assert report["totals"]["failedSources"] == 1
    assert (setup["out"] / "report.json").is_file()
