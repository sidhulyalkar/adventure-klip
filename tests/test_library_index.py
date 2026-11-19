"""Library index: one read per file, discovery-compatible batches, resumable. Generated media only."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from adventure_intelligence import discovery_experiment as experiment
from adventure_intelligence import library_index as index
from adventure_intelligence import media
from adventure_intelligence import value_features as features

needs_ffmpeg = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg not installed")


def clip(path, seconds=12, colour="gray"):
    subprocess.run([shutil.which("ffmpeg"), "-loglevel", "error", "-f", "lavfi", "-i",
                    f"color=c={colour}:s=96x54:r=5:d={seconds}", "-pix_fmt", "yuv420p",
                    str(path)], check=True)
    return path


class Recorder(index.Prefetch):
    def __init__(self, events):
        super().__init__()
        self.events = events

    def warm(self, path):
        if path is not None and path not in self._jobs:
            self.events.append(("warm", path.name))
        super().warm(path)

    def ready(self, path):
        super().ready(path)
        self.events.append(("ready", path.name))


def fake_motion(path, fps, seconds):
    series = [{"t_ms": int(i * 1000 / fps), "change": 0.01 + 0.2 * (10 <= i < 20)}
              for i in range(1, int(seconds * fps))]
    return series, "fake-decoder"


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment, "MAX_SOURCES", 3)
    footage, ws = tmp_path / "footage", tmp_path / "ws"
    footage.mkdir()
    (ws / "reports").mkdir(parents=True)
    colours = ["gray", "blue", "red", "green", "white"]
    assets = {}
    for k, colour in enumerate(colours):
        path = clip(footage / f"DJI_{k:04d}.MP4", colour=colour)
        assets[experiment.source_digest(path)] = {"path": str(path), "durationS": 12.0,
                                                  "sport": "unknown"}
    return {"footage": footage, "ws": ws, "reports": ws / "reports", "assets": assets,
            "by_name": {a["path"].rsplit("/", 1)[1]: s for s, a in assets.items()}}


def go(lib, events, **kwargs):
    described = kwargs.pop("described_calls", [])

    def describe_one(sha, path):
        events.append(("describe", path.name))
        described.append(sha)
        return {"frames": 6, "audioWindows": 0, "seconds": {"decode": 0.1}}

    kwargs.setdefault("describe_one", describe_one)
    return index.run(lib["assets"], workspace=lib["ws"], trusted_root=lib["footage"],
                     reports=lib["reports"], described=kwargs.pop("described", set()),
                     motion=fake_motion, prefetch=Recorder(events), **kwargs)


@needs_ffmpeg
def test_index_scans_in_compatible_batches_and_describes_each_file_while_cached(library):
    lib = library
    before = {p.name: p.read_bytes() for p in lib["footage"].iterdir()}
    # An earlier hand-run batch already scanned DJI_0000: it is only described now.
    first = lib["by_name"]["DJI_0000.MP4"]
    old = lib["reports"] / "discovery-007"
    (lib["reports"] / "old-sources.json").write_text(json.dumps(
        [{"sourceSha256": first, "path": lib["assets"][first]["path"], "sport": "unknown"}]))
    experiment.run(lib["reports"] / "old-sources.json", old, workspace=lib["ws"],
                   trusted_root=lib["footage"], stages=("verify", "motion", "audio", "segments"),
                   decode=lambda p, f, s: fake_motion(p, f, s)[0])
    events = []
    labelled = lib["by_name"]["DJI_0004.MP4"]
    totals = go(lib, events, first={labelled: 3.0})

    assert totals["scanned"] == 4 and totals["described"] == 5
    assert totals["scanFailed"] == totals["describeFailed"] == 0
    assert [b["folder"] for b in totals["batches"]] == ["discovery-008", "discovery-009"]
    # Owner-labelled recordings first, then capture order.
    batch = json.loads((lib["reports"] / "discovery-008" / "sources.json").read_text())
    assert [lib["assets"][r["sourceSha256"]]["path"][-12:] for r in batch] == [
        "DJI_0004.MP4", "DJI_0001.MP4", "DJI_0002.MP4"]
    # Each file is read ahead, waited for, then decoded and described; the next one is
    # being read while the current one is worked on.
    for name in ["DJI_0004.MP4", "DJI_0001.MP4", "DJI_0002.MP4", "DJI_0003.MP4", "DJI_0000.MP4"]:
        assert events.index(("ready", name)) < events.index(("describe", name))
    assert events.index(("warm", "DJI_0001.MP4")) < events.index(("describe", "DJI_0004.MP4"))
    # Every existing reader sees the new batches as ordinary discovery output.
    for folder in ("discovery-008", "discovery-009"):
        loaded = features.load_experiment(lib["reports"] / folder)
        assert loaded and all(len(s["motion"]) for s in loaded.values())
        log = [json.loads(line) for line in
               (lib["reports"] / folder / "index.jsonl").read_text().splitlines()]
        assert {r["motionDecoder"] for r in log if "motionDecoder" in r} == {"fake-decoder"}
        assert not any((lib["reports"] / folder / s / "judgments").exists() for s in loaded)
    assert {p.name: p.read_bytes() for p in lib["footage"].iterdir()} == before

    # A second run finds nothing to do and reads no file.
    events.clear()
    again = go(lib, events, described=set(lib["assets"]))
    assert again["scanned"] == again["described"] == 0 and again["batches"] == []
    assert events == []


@needs_ffmpeg
def test_a_stopped_batch_is_finished_not_rescanned_elsewhere(library):
    lib = library
    events, calls = [], {"n": 0}

    def power_cut(*args, **kwargs):
        original = kwargs["after"]

        def after(row, failed):
            original(row, failed)
            calls["n"] += 1
            if calls["n"] == 2:
                raise KeyboardInterrupt
        return experiment.run(*args, **{**kwargs, "after": after})

    with pytest.raises(KeyboardInterrupt):
        go(lib, events, run_batch=power_cut)
    totals = go(lib, events)
    folders = sorted(p.name for p in lib["reports"].glob("discovery-*"))
    assert folders == ["discovery-001", "discovery-002"]
    assert totals["batches"][0]["folder"] == "discovery-001"
    scanned = {s for f in folders for s in features.load_experiment(lib["reports"] / f)}
    assert scanned == set(lib["assets"])


@needs_ffmpeg
def test_failed_verification_is_not_described_and_not_retried_by_default(library):
    lib = library
    bad = lib["by_name"]["DJI_0002.MP4"]
    other = lib["assets"][lib["by_name"]["DJI_0003.MP4"]]["path"]
    lib["assets"][bad]["path"] = other  # catalog points at the wrong file
    events, described = [], []
    totals = go(lib, events, described_calls=described)
    assert totals["scanFailed"] == 1 and bad not in described
    assert ("describe", "DJI_0003.MP4") in events  # the real owner of that file is fine
    lib["assets"][bad]["path"] = str(lib["footage"] / "DJI_0002.MP4")
    assert go(lib, [], described=set(described))["skippedFailedBefore"] == 1
    retried = go(lib, [], described=set(described), retry_failed=True)
    assert retried["scanned"] == 1 and retried["described"] == 1


def test_selection_orders_labelled_first_and_skips_what_is_done():
    assets = {s: {"path": f"/f/{p}.MP4"} for s, p in (("a", "3"), ("b", "1"), ("c", "2"),
                                                     ("d", "0"))}
    done = {"scanned": {"d"}, "attempted": {"d", "c"}}
    chosen = index.select(assets, done, described=set(), first={"a": 1})
    assert chosen == {"scan": ["a", "b"], "describeOnly": ["d"], "skippedFailedBefore": ["c"]}
    assert index.select(assets, done, {"d"}, describe=False)["describeOnly"] == []


def test_motion_decode_can_use_hardware_without_changing_the_signal(monkeypatch):
    seen = {}

    class Stop(Exception):
        pass

    def popen(cmd, **kwargs):
        seen["cmd"] = cmd
        raise Stop

    monkeypatch.setattr(media.subprocess, "Popen", popen)
    with pytest.raises(Stop):
        media.motion_series("x.mp4", hwaccel="videotoolbox")
    cmd = seen["cmd"]
    assert cmd[cmd.index("-hwaccel") + 1] == "videotoolbox" and cmd.index("-hwaccel") < cmd.index("-i")
    with pytest.raises(Stop):
        media.motion_series("x.mp4")
    assert "-hwaccel" not in seen["cmd"]
    # Same sampling and size either way.
    assert "fps=2,scale=64:36,format=gray" in seen["cmd"]


@needs_ffmpeg
def test_index_sees_a_scan_another_process_ran_meanwhile(library):
    lib = library
    events = []
    real_run = experiment.run
    taken = {}

    def other_process_then_run(sources_file, output, **kwargs):
        if not taken:
            # While the index runs its first batch, another scan claims discovery-002
            # and a recording the index planned for its second batch.
            rows = json.loads((output / "sources.json").read_text())
            planned = sorted(set(lib["assets"]) - {r["sourceSha256"] for r in rows})
            taken["sha"] = planned[0]
            other = lib["reports"] / "discovery-002"
            (lib["reports"] / "x.json").write_text(json.dumps([{
                "sourceSha256": planned[0], "path": lib["assets"][planned[0]]["path"],
                "sport": "unknown"}]))
            real_run(lib["reports"] / "x.json", other, workspace=lib["ws"],
                     trusted_root=lib["footage"], stages=("verify", "motion"),
                     decode=lambda p, f, s: fake_motion(p, f, s)[0])
        return real_run(sources_file, output, **kwargs)

    totals = go(lib, events, run_batch=other_process_then_run)
    assert [b["folder"] for b in totals["batches"]] == ["discovery-001", "discovery-003"]
    batch3 = json.loads((lib["reports"] / "discovery-003" / "sources.json").read_text())
    assert taken["sha"] not in {r["sourceSha256"] for r in batch3}
    assert totals["scanned"] == 4
