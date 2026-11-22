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


SCAN = ("verify", "motion", "audio", "segments", "proxies")


def test_index_batch_reports_its_judge_backlog_instead_of_zeros(setup):
    totals = go(setup, stages=SCAN)["totals"]
    assert totals["judgeStageRequested"] is False and totals["judgedSegments"] == 0
    assert totals["judgeCallsExpected"] == totals["segmentsForReview"] * 2 > 0
    assert totals["judgeCallsLeft"] == totals["judgeCallsExpected"]


def test_judge_only_pass_never_opens_originals_and_keeps_old_report(setup):
    go(setup, stages=SCAN)
    setup["clip"].unlink()  # the drive is gone: judging must still work from proxies
    totals = go(setup, stages=("judge",))["totals"]
    assert totals["failedSources"] == 0 and totals["judgeCallsLeft"] == 0
    assert totals["judgePanelsComplete"] == totals["judgedSegments"] > 0
    assert len(list((setup["out"] / "report-history").glob("report-*.json"))) == 1
    report = json.loads((setup["out"] / "report.json").read_text())
    assert "motion" in report["sources"][0]["timingSeconds"]  # scan timings kept


def test_judge_only_pass_refuses_to_hash_an_unscanned_recording(setup):
    report = go(setup, stages=("judge",))
    assert "never scanned" in report["sources"][0]["failure"]


def test_judging_is_model_major_budgeted_and_resumable(setup):
    go(setup, stages=SCAN)
    order = []

    def transport(body):
        order.append(body["model"])
        assert body["options"]["num_ctx"] >= 8192
        return answer(body)

    first = go(setup, stages=("judge",), transport=transport, judge_budget=[1])["totals"]
    assert len(order) == 1 and first["judgeCallsAnswered"] == 1
    assert first["judgePanelsComplete"] == 0
    go(setup, stages=("judge",), transport=transport)
    n = len(order)
    # every segment's first model before any segment's second model
    assert order == sorted(order, key=lambda m: experiment.DEFAULTS["models"].index(m))
    assert n == go(setup, stages=SCAN)["totals"]["judgeCallsExpected"]


def test_judge_batches_cli_dry_run_then_pilot(setup, capsys, monkeypatch):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "klip_judge_batches", Path(__file__).parents[1] / "scripts" / "klip_judge_batches.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    reports = setup["workspace"] / "reports"
    folder = reports / "discovery-001"
    folder.mkdir(parents=True)
    shutil.copy(setup["sources"], folder / "sources.json")
    go(setup, out=folder, stages=SCAN)
    argv = ["--reports", str(reports), "--trusted-source-root", str(setup["footage"])]
    assert cli.main(argv + ["--dry-run"]) == 0
    backlog = json.loads(capsys.readouterr().out)
    assert backlog["judgeCallsLeft"] == backlog["judgeCallsExpected"] > 0
    monkeypatch.setattr(experiment, "missing_models", lambda models: [])
    real_run = experiment.run
    monkeypatch.setattr(experiment, "run",
                        lambda *a, **k: real_run(*a, **{**k, "transport": answer}))
    assert cli.main(argv + ["--max-calls", "1"]) == 0
    assert '"judgeCallsAnswered": 1' in capsys.readouterr().out


def test_judge_batches_cli_takes_sport_from_roster_without_editing_sources(setup, capsys,
                                                                            monkeypatch):
    import importlib.util
    from pathlib import Path
    from adventure_intelligence import outing_roster as roster
    spec = importlib.util.spec_from_file_location(
        "klip_judge_batches", Path(__file__).parents[1] / "scripts" / "klip_judge_batches.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    reports = setup["workspace"] / "reports"
    folder = reports / "discovery-001"
    folder.mkdir(parents=True)
    unknown = [{**json.loads(setup["sources"].read_text())[0], "sport": "unknown"}]
    (folder / "sources.json").write_text(json.dumps(unknown))
    frozen = (folder / "sources.json").read_bytes()
    go(setup, out=folder, stages=SCAN)
    argv = ["--reports", str(reports), "--trusted-source-root", str(setup["footage"])]
    assert cli.main(argv + ["--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["recordingsStillUnknown"] == 1
    roster_file = setup["workspace"] / "roster.json"
    roster_file.write_text(json.dumps({
        "version": roster.VERSION, "status": next(iter(roster.STATUSES)),
        "outings": [{"outing": "day1", "sources": [
            {"sourceSha256": setup["sha"], "activity": "ski"}]}]}))
    assert cli.main(argv + ["--roster", str(roster_file), "--dry-run"]) == 0
    backlog = json.loads(capsys.readouterr().out)
    assert backlog["recordingsRelabelledFromRoster"] == 1 and backlog["bySport"]["ski"]
    monkeypatch.setattr(experiment, "missing_models", lambda models: [])
    real_run = experiment.run
    monkeypatch.setattr(experiment, "run",
                        lambda *a, **k: real_run(*a, **{**k, "transport": answer}))
    assert cli.main(argv + ["--roster", str(roster_file)]) == 0
    capsys.readouterr()
    assert (folder / "sources.json").read_bytes() == frozen
    prov = json.loads((folder / "judge-sources.provenance.json").read_text())
    assert prov["sportFrom"][setup["sha"]] == "roster"
    report = json.loads((folder / "report.json").read_text())
    assert report["totals"]["judgeCallsLeft"] == 0 and report["sources"][0]["sport"] == "ski"
    assert cli.main(argv + ["--dry-run"]) == 0  # resumes with the relabelled list
    assert json.loads(capsys.readouterr().out)["recordingsStillUnknown"] == 0


def _guess_cli():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "klip_guess_activities", Path(__file__).parents[1] / "scripts" / "klip_guess_activities.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeStore:
    def __init__(self, votes):
        self.votes = votes

    def activity(self, sha):
        v = self.votes.get(sha)
        return v and {"proposal": v[0], "share": v[1], "votes": {v[0]: v[1]}}


def test_activity_guesses_are_tagged_conservative_and_refused_by_evaluation():
    from adventure_intelligence import outing_roster as roster
    g = _guess_cli()
    sha = lambda c: c * 64
    doc = {"version": roster.VERSION, "status": "provisional", "outings": [{
        "outing": "d", "sources": [
            {"sourceSha256": sha("a"), "activity": "ski"},   # owner tag, same day
            {"sourceSha256": sha("b")}, {"sourceSha256": sha("c")}, {"sourceSha256": sha("d")},
            {"sourceSha256": sha("e")}, {"sourceSha256": sha("f")}]}]}
    names = {sha(x): f"DJI_20260301{i}_D.MP4" for i, x in enumerate("abcde")}
    names[sha("f")] = "other.mp4"
    store = _FakeStore({sha("b"): ("ski", 0.9), sha("c"): ("snorkeling", 0.4),
                        sha("d"): ("accidental", 0.8), sha("f"): ("bike", 0.7)})
    rec = g.guess(doc, names, store)
    assert rec[sha("b")]["confidence"] == "high"
    assert rec[sha("c")]["guess"] == "ski" and rec[sha("c")]["confidence"] == "low"
    assert rec[sha("d")]["possibleAccidental"] and rec[sha("d")]["guess"] == "ski"
    assert rec[sha("e")]["reason"].startswith("day only")
    assert rec[sha("f")]["guess"] == "bike" and rec[sha("f")]["confidence"] == "medium"
    before = json.dumps(doc)
    out = g.apply(doc, rec)
    assert json.dumps(doc) == before  # input untouched
    assert roster.guessed_sources(out) == {sha(x) for x in "bcdef"}
    assert all(r.get("activity") != "accidental" for o in out["outings"] for r in o["sources"])
    with pytest.raises(ValueError, match="model-guessed"):
        roster.refuse_guesses(out)
    assert roster.refuse_guesses(doc) is doc
    assert g.guess(out, names, store) == {}  # guesses are not re-guessed


def test_judge_models_filter_runs_one_model_over_everything_first(setup):
    go(setup, stages=SCAN)
    order = []

    def transport(body):
        order.append(body["model"])
        assert body["keep_alive"] == "30m"
        return answer(body)

    first, second = experiment.DEFAULTS["models"]
    t = go(setup, stages=("judge",), transport=transport, judge_models=[second])["totals"]
    assert set(order) == {second} and t["judgePanelsComplete"] == 0
    t = go(setup, stages=("judge",), transport=transport, judge_models=[first])["totals"]
    assert t["judgeCallsLeft"] == 0 and order.count(first) == order.count(second)


def test_clip_without_video_is_marked_unjudgeable_and_spares_the_recording(setup):
    import subprocess
    go(setup, stages=SCAN)
    proxies = sorted((setup["out"] / setup["sha"] / "proxies").glob("*.mp4"))
    assert len(proxies) >= 2
    bad = proxies[0]
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=frequency=300:sample_rate=48000", "-t", "2", "-c:a", "aac",
                    "-y", str(bad)], check=True)                    # audio only: no picture
    t = go(setup, stages=("judge",))["totals"]
    assert t["failedSources"] == 0 and t["segmentsUnjudgeable"] == 1
    assert t["judgeCallsLeft"] == 0 and t["judgedSegments"] == len(proxies) - 1
    marker = bad.parent.parent / "unjudgeable" / (bad.stem + ".json")
    assert json.loads(marker.read_text())["videoMs"] == 0


def test_undecodable_frames_mark_one_segment_not_the_recording(setup):
    go(setup, stages=SCAN)
    calls = []

    def frames(proxy, times):
        calls.append(proxy.name)
        if len(calls) == 1:
            raise ValueError("Could not decode a frame at 43 ms")
        return ["aGk="] * len(times)

    t = go(setup, stages=("judge",), frames=frames)["totals"]
    assert t["failedSources"] == 0 and t["segmentsUnjudgeable"] == 1 and t["judgeCallsLeft"] == 0


def test_accidental_micro_clip_is_not_judged(setup):
    import subprocess
    go(setup, stages=SCAN)
    bad = sorted((setup["out"] / setup["sha"] / "proxies").glob("*.mp4"))[0]
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=320x180:rate=30", "-t", "0.2", "-pix_fmt", "yuv420p",
                    "-y", str(bad)], check=True)                      # a 0.2 s button press
    t = go(setup, stages=("judge",))["totals"]
    assert t["failedSources"] == 0 and t["segmentsUnjudgeable"] == 1 and t["judgeCallsLeft"] == 0
    marker = json.loads((bad.parent.parent / "unjudgeable" / (bad.stem + ".json")).read_text())
    assert 150 <= marker["videoMs"] <= 250 and "under 2000 ms" in marker["reason"]
