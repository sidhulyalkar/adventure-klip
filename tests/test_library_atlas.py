"""Library atlas: header facts, shooting behaviour, and what labels can answer. Synthetic data."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from adventure_intelligence import library_atlas as atlas
from adventure_intelligence import outing_roster as roster

needs_ffmpeg = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg not installed")


def probe_json(**over):
    doc = {"format": {"duration": "12.0", "tags": {
        "encoder": "DJI OsmoAction4", "major_brand": "isom",
        "com.apple.quicktime.location.ISO6709": "+17.5-149.8/", "comment": "private note"}},
        "streams": [
            {"codec_type": "video", "codec_name": "hevc", "pix_fmt": "yuv420p10le",
             "width": 2688, "height": 1512, "avg_frame_rate": "50/1",
             "color_transfer": "arib-std-b67", "bit_rate": "83000000"},
            {"codec_type": "audio", "codec_name": "aac", "channels": 2, "sample_rate": "48000"},
            {"codec_type": "data", "codec_tag_string": "djmd",
             "tags": {"handler_name": "DJI meta"}}]}
    doc.update(over)
    return doc


def fake_run(doc):
    class Out:
        stdout = json.dumps(doc)
    return lambda *a, **k: Out()


def test_header_keeps_shooting_facts_and_drops_location_values():
    head = atlas.header("x.mp4", run=fake_run(probe_json()))
    assert head["tags"] == {"encoder": "DJI OsmoAction4", "major_brand": "isom"}
    assert head["hasLocationTag"] is True
    assert "+17.5" not in json.dumps(head) and "private note" not in json.dumps(head)
    assert head["data"] == [{"tag": "djmd", "handler": "DJI meta"}]
    assert atlas.mode(head) == "2688x1512 50fps 10-bit HLG"
    head["video"].update(rotation=-90, fps=120.0, pixFmt="yuv420p", colorTransfer="bt709")
    assert atlas.mode(head) == "1512x2688 120fps 8-bit SDR"


@needs_ffmpeg
def test_header_reads_a_real_file_without_decoding(tmp_path):
    path = tmp_path / "DJI_0001.MP4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=160x90:rate=100:d=1", "-metadata",
                    "location=+17.5-149.8/", "-pix_fmt", "yuv420p", str(path)], check=True)
    head = atlas.header(path)
    assert head["video"]["fps"] == 100.0 and head["hasLocationTag"]
    assert "149.8" not in json.dumps(head)
    cache = tmp_path / "atlas" / "headers.jsonl"
    rec = [{"sha": "a" * 64, "path": str(path)}]
    calls = []

    def counting(p):
        calls.append(p)
        return atlas.header(p)
    atlas.headers(cache, rec, probe=counting)
    atlas.headers(cache, rec, probe=counting)  # cached by SHA and size
    assert len(calls) == 1
    path.write_bytes(path.read_bytes() + b"\0")  # a changed file is read again
    atlas.headers(cache, rec, probe=counting)
    assert len(calls) == 2


def library():
    # Day 1 (ski): a burst of three recordings, one a retake, then one much later.
    # Day 2 (snorkeling): two recordings, one a mis-press marked accidental.
    def rec(sha, when, seconds, activity="unknown"):
        return {"sha": sha * 64, "path": f"/f/DJI_{sha}.MP4", "durationS": seconds,
                "captureTime": when, "activity": activity}
    recordings = [rec("a", "2026-02-28T10:00:00", 60), rec("b", "2026-02-28T10:01:20", 30),
                  rec("c", "2026-02-28T10:02:00", 2), rec("d", "2026-02-28T13:00:00", 300),
                  rec("e", "2026-03-02T09:00:00", 120), rec("f", "2026-03-02T09:30:00", 1)]
    doc = roster.propose([{"sourceSha256": r["sha"], "captureTime": r["captureTime"],
                           "durationSeconds": r["durationS"], "activity": r["activity"]}
                          for r in recordings])
    for outing in doc["outings"]:
        for row in outing["sources"]:
            row["activity"] = "ski" if row["sourceSha256"][0] in "abcd" else "snorkeling"
            if row["sourceSha256"][0] == "f":
                row["activity"] = "accidental"
    return recordings, doc


def test_shooting_behaviour_marks_bursts_retakes_and_order():
    recordings, doc = library()
    outing = roster.outing_by_source(doc)
    shot = atlas.shooting(recordings, lambda s: roster.group_of(outing, s))
    a, b, c, d = (shot[x * 64] for x in "abcd")
    assert a["gapBeforeS"] is None and b["gapBeforeS"] == 20.0 and c["gapBeforeS"] == 10.0
    assert b["retake"] and c["retake"] and not a["retake"]
    assert a["sequence"] == b["sequence"] == c["sequence"] != d["sequence"]
    assert a["sequenceSize"] == 3 and d["sequenceSize"] == 1
    assert [shot[x * 64]["positionInDay"] for x in "abcd"] == [1, 2, 3, 4]


def test_atlas_says_what_labels_asked_and_whether_evaluation_is_possible():
    recordings, doc = library()
    outing = roster.outing_by_source(doc)
    heads = {r["sha"]: {**atlas.header("x", run=fake_run(probe_json())), "sizeBytes": 1}
             for r in recordings}
    judgments = [
        {"sourceSha256": "a" * 64, "decision": "keep", "goal": "athletic_skill",
         "origin": "curation_review"},
        {"sourceSha256": "d" * 64, "decision": "skip", "goal": "athletic_skill",
         "origin": "curation_review"},
        {"sourceSha256": "e" * 64, "decision": "keep", "goal": "marine_life",
         "origin": "marine_review"}]
    side = lambda x: {"sourceSha256": x * 64, "startMs": 0, "endMs": 1000}  # noqa: E731
    day1 = roster.group_of(outing, "a" * 64)
    pairs = [{"left": side("a"), "right": side("d"), "groups": [day1], "goal": "athletic_skill",
              "tier": "owner_keep_over_skip", "evalEligible": True},
             {"left": side("a"), "right": side("b"), "groups": [day1], "goal": "athletic_skill",
              "tier": "owner_keep_over_skip", "evalEligible": True}]
    built = atlas.build(recordings, roster_doc=doc, heads=heads, scanned={"a" * 64, "d" * 64},
                        described={"a" * 64}, judgments=judgments, pairs=pairs)
    s, rows = built["summary"], {r["file"]: r for r in built["rows"]}

    assert s["library"]["recordings"] == 5 and s["library"]["excludedAccidental"] == 1
    assert s["library"]["short"] == 1 and s["library"]["retakes"] == 2
    assert s["library"]["dataTracks"] == {"djmd": 5} and s["library"]["withLocationTag"] == 5
    assert s["byActivity"]["ski"]["recordings"] == 4
    assert s["coverage"] == {"scanned": 2, "described": 1, "labelled": 3, "inPairs": 3, "of": 5}
    asked = s["labelsAskedWhat"]
    assert asked["curation_review"]["athletic_skill"]["activities"] == {"ski": 2}
    assert asked["marine_review"]["marine_life"]["days"] == 1
    ski = s["evaluableByActivity"]["ski"]
    assert ski["pairs"] == 2 and ski["pairsBothScanned"] == 1
    assert ski["daysWithScoredPairs"] == 1 and not ski["tripHoldoutPossible"]
    assert rows["DJI_c.MP4"]["short"] and rows["DJI_f.MP4"]["activity"] == "accidental"
    day = s["trips"][rows["DJI_a.MP4"]["trip"]]["days"][day1]
    assert day["sequences"] == 2 and day["activities"] == {"ski": 4}
    assert "149.8" not in json.dumps(built)


@needs_ffmpeg
def test_cli_atlas_is_read_only_and_rebuildable(tmp_path, capsys):
    import importlib.util
    import sqlite3
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "cli", Path(__file__).parents[1] / "scripts" / "klip_value_experiment.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    footage, ws = tmp_path / "footage", tmp_path / "ws"
    footage.mkdir()
    ws.mkdir()
    rows = []
    for k, rate in enumerate((50, 120)):
        path = footage / f"DJI_{k}.MP4"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                        f"testsrc2=size=160x90:rate={rate}:d=1", "-pix_fmt", "yuv420p",
                        str(path)], check=True)
        rows.append((str(k) * 64, str(k) * 64, str(path), 1.0, f"2026-02-28T10:0{k}:00", "unknown"))
    with sqlite3.connect(ws / "catalog.sqlite3") as db:
        db.execute("CREATE TABLE assets (id TEXT, sha256 TEXT, stored_path TEXT, duration REAL, "
                   "capture_time TEXT, activity TEXT)")
        db.executemany("INSERT INTO assets VALUES (?,?,?,?,?,?)", rows)
    before = {p: p.read_bytes() for p in [*footage.iterdir(), ws / "catalog.sqlite3"]}
    for _ in range(2):  # rerunning replaces the derived files
        assert cli.main(["atlas", "--workspace", str(ws), "--source-root", str(footage)]) == 0
    shown = capsys.readouterr().out
    out = ws / "reports" / "atlas"
    summary = json.loads((out / "atlas.json").read_text())
    assert summary["library"]["recordings"] == 2 and summary["library"]["highFps"] == 1
    assert (out / "recordings.csv").read_text().count("\n") == 3
    assert {p: p.read_bytes() for p in before} == before
    assert '"evaluableByActivity"' in shown and '"trips"' not in shown
