from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import numpy as np

from adventure_intelligence import (dataset_profile, holdout, owner_examples, owner_labels,
                                    run_records)

ROOT = Path(__file__).parents[1]


def _script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_run_record_is_written_redacted_and_never_for_dry_runs(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    calls = []

    def main(argv):
        calls.append(argv)
        return 0
    assert run_records.main_recorded(main, "x", ["run", "--reports", str(reports),
                                                 "--trusted-source-root", "/Volumes/SSD"]) == 0
    assert run_records.main_recorded(main, "x", ["run", "--reports", str(reports), "--dry-run"]) == 0
    assert run_records.main_recorded(main, "x", ["status", "--reports", str(reports)]) == 0
    recs = run_records.load(reports)
    assert len(recs) == 1 and len(calls) == 3
    r = recs[0]
    assert r["exitCode"] == 0 and r["argv"][2] == "~/reports" and r["argv"][4] == "<volume>/SSD"
    assert r["code"]["commit"] and "story_harness" in r["modules"]
    md = run_records.export_markdown(recs)
    assert str(tmp_path) not in md and "| x | run |" in md


def test_run_record_keeps_the_error_and_reraises(tmp_path):
    reports = tmp_path / "r"

    def boom(argv):
        raise RuntimeError("kaput")
    try:
        run_records.main_recorded(boom, "y", ["go", "--reports", str(reports)])
    except RuntimeError:
        pass
    rec = run_records.load(reports)[0]
    assert rec["error"] == "RuntimeError: kaput" and rec["exitCode"] == 2


def _world(tmp_path):
    reports = tmp_path / "reports"
    folder = reports / "discovery-001"
    rows = []
    for i, (day, sport, ms) in enumerate([("20260309", "ski", 60000), ("20260309", "ski", 300),
                                          ("20241226", "snorkeling", 90000)]):
        sha = f"{i}".ljust(64, "c")
        (folder / sha / "judgments").mkdir(parents=True)
        (folder / sha / "source.json").write_text(json.dumps({"durationMs": ms}))
        (folder / sha / "segments.json").write_text(json.dumps(
            {"segments": [{"role": "selected", "startMs": 0, "endMs": 10000}]}))
        (folder / sha / "judgments" / "0-10000.json").write_text(json.dumps({"complete": True}))
        rows.append({"sourceSha256": sha, "path": f"/ssd/DJI_{day}10000{i}_0001_D.MP4", "sport": sport})
    (folder / "sources.json").write_text(json.dumps(rows))
    return reports, rows


def test_dataset_profile_counts_per_day_and_sport(tmp_path):
    reports, rows = _world(tmp_path)
    holdout.freeze(reports, [("2024-12-26", "snorkeling")])
    doc = dataset_profile.profile(reports, [{"sourceSha256": rows[0]["sourceSha256"], "decision": "keep"}])
    by = {(r["day"], r["sport"]): r for r in doc["days"]}
    ski = by[("2026-03-09", "ski")]
    assert ski["recordings"] == 2 and ski["microRecordings"] == 1 and ski["judgedComplete"] == 2
    assert ski["ownerLedger:keep"] == 1 and not ski["heldOut"]
    assert by[("2024-12-26", "snorkeling")]["heldOut"] and doc["heldOutDays"] == 1
    assert "| 2026-03-09 | ski | 2 |" in dataset_profile.markdown(doc)


def test_owner_examples_never_leak_and_balance_keeps():
    scenes = [{"sourceSha256": "s" * 64, "startMs": 0, "endMs": 1000, "file": "DJI_20260309100000_1_D.MP4"}]
    vec = {"s" * 64: [1.0, 0.0]}
    rows, names = [], {}
    for i in range(10):
        sha = f"{i}".ljust(64, "e")
        vec[sha] = [1.0, 0.0] if i < 8 else [0.0, 1.0]
        names[sha] = f"DJI_2026031{i % 2}100000_1_D.MP4"
        rows.append({"sourceSha256": sha, "startMs": 0, "endMs": 1000,
                     "decision": "keep" if i < 7 else "skip",
                     "detail": {"sceneDescription": f"scene {i}", "whyKeep": "the light"}})
    same_day = "d".ljust(64, "d")
    vec[same_day] = [1.0, 0.0]
    names[same_day] = "DJI_20260309110000_2_D.MP4"
    rows.append({"sourceSha256": same_day, "startMs": 0, "endMs": 1000, "decision": "keep",
                 "detail": {"sceneDescription": "same day"}})
    embed = lambda sha, a, b: np.array(vec[sha], np.float32)
    held = {f"0".ljust(64, "e")}
    out = owner_examples.select(scenes, rows, embed=embed, names=names, held_out=held)
    shas = {e["sourceSha256"] for e in out}
    assert same_day not in shas and not (held & shas) and "s" * 64 not in shas
    assert sum(e["decision"] == "keep" for e in out) == 5 and len(out) == 8
    assert out[0]["reason"] == "the light"
    assert owner_examples.select(scenes, rows, embed=None, names=names, held_out=set()) == []


def test_regression_set_build_score_and_detect_a_regression(tmp_path, capsys):
    from adventure_intelligence import judge_panel as panel
    reg = _script("klip_regression_set")
    reports = tmp_path / "reports"
    j = reports / "discovery-001" / ("a" * 64) / "judgments"
    j.mkdir(parents=True)
    (j / "0-10000.json").write_text(json.dumps({"frameTimesMs": [500, 1500]}))
    owner_labels.append(reports, "fact", deck="d", answers_sha256="x", items=[
        {"item": f"p|q{i}", "answer": "yes", "sport": "ski", "question": "airborne_visible",
         "panel": f"discovery-001/{'a' * 64}/0-10000", "sourceSha256": "a" * 64,
         "frameTimesMs": [500]} for i in range(3)])
    assert reg.main(["build", "--reports", str(reports)]) == 0
    assert reg.main(["build", "--reports", str(reports)]) == 2          # frozen
    capsys.readouterr()

    def answer(value):
        def transport(body):
            ans = {k: "no" for k in {**panel.RUBRICS["ski"], **panel.SHARED, **panel.CONTROLS["ski"]}}
            ans["airborne_visible"] = value
            return {"message": {"content": json.dumps({"answers": ans, "description": "x"})}}
        return transport
    frames = lambda proxy, times: ["aGk="] * len(times)
    base = ["score", "--reports", str(reports), "--model", "gemma3:27b"]
    assert reg.main(base, transport=answer("yes"), frames=frames) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["accuracy"] == 1.0 and first["right"] == 3
    # a prompt change (new fingerprint) that gets it wrong is flagged as a regression
    old_fp = panel.fingerprint
    panel.fingerprint = lambda m, s: "changed-prompt"
    try:
        assert reg.main(base, transport=answer("no"), frames=frames) == 1
    finally:
        panel.fingerprint = old_fp
    second = json.loads(capsys.readouterr().out)
    assert second["comparedWith"]["regression"] and second["comparedWith"]["delta"] == -1.0


def test_pipeline_profile_and_runs_commands(tmp_path, capsys):
    pipe = _script("klip_pipeline")
    reports, _ = _world(tmp_path)
    assert pipe.main(["profile", "--reports", str(reports)]) == 0
    assert (reports / "dataset-profile.md").is_file()
    run_records.main_recorded(lambda a: 0, "klip_pipeline", ["reels", "--reports", str(reports)])
    out = tmp_path / "log.md"
    assert pipe.main(["runs", "--reports", str(reports), "--export", str(out)]) == 0
    assert "| klip_pipeline | reels |" in out.read_text()
