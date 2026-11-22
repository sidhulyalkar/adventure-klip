from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from adventure_intelligence import outing_roster as r


def _cli():
    spec = importlib.util.spec_from_file_location(
        "klip_tag_micro_recordings",
        Path(__file__).parents[1] / "scripts" / "klip_tag_micro_recordings.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _world(tmp_path):
    reports = tmp_path / "reports"
    folder = reports / "discovery-014"
    durations = {"a" * 64: 200, "b" * 64: 936, "c" * 64: 45_000, "d" * 64: 500, "e" * 64: 300}
    rows = []
    for sha, ms in durations.items():
        (folder / sha).mkdir(parents=True)
        (folder / sha / "source.json").write_text(json.dumps({"durationMs": ms}))
        rows.append({"sourceSha256": sha, "path": f"/ssd/DJI_{sha[0]}.MP4", "sport": "snorkeling"})
    (folder / "sources.json").write_text(json.dumps(rows))
    doc = {"version": r.VERSION, "status": "provisional", "outings": [{"outing": "d27", "sources": [
        {"sourceSha256": "a" * 64},                                              # untagged
        {"sourceSha256": "b" * 64, "activity": "snorkeling", "tags": [r.GUESS_TAG]},  # a guess
        {"sourceSha256": "c" * 64, "activity": "snorkeling"},                    # long: untouched
        {"sourceSha256": "d" * 64, "activity": "kayak"}]}]}                      # owner label
    path = tmp_path / "roster.json"
    path.write_text(json.dumps(doc))
    return reports, path, doc


def test_micro_recordings_become_accidental_but_owner_labels_are_kept(tmp_path, capsys):
    cli = _cli()
    reports, path, doc = _world(tmp_path)
    argv = ["--reports", str(reports), "--roster", str(path)]
    assert cli.main(argv + ["--dry-run"]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["microRecordings"] == 4 and path.read_text() == json.dumps(doc)   # untouched
    assert cli.main(argv) == 0
    out = json.loads(capsys.readouterr().out)
    roster = r.validate(json.loads(path.read_text()))
    by = {row["sourceSha256"][0]: row for row in roster["outings"][0]["sources"]}
    assert by["a"]["activity"] == r.EXCLUDED and by["a"]["tags"] == ["micro-recording"]
    assert by["b"]["activity"] == r.EXCLUDED and r.GUESS_TAG not in by["b"]["tags"]
    assert by["c"]["activity"] == "snorkeling"
    assert by["d"]["activity"] == "kayak"                       # the owner's own label wins
    assert out["rosters"][0]["tagged"] == 2
    assert [k["ownerActivity"] for k in out["rosters"][0]["ownerLabelKept"]] == ["kayak"]
    assert out["rosters"][0]["notInRoster"] == ["DJI_e.MP4"]
    backups = list(tmp_path.glob("roster.json.bak-*"))
    assert len(backups) == 1 and json.loads(backups[0].read_text()) == doc
    log = json.loads(Path(out["log"]).read_text())
    assert log["decidedBy"] == "owner" and log["rosters"][0]["changes"][0]["durationMs"] == 200
    assert r.guessed_sources(roster) == set()            # the overruled guess is gone
    assert cli.main(argv) == 0                                  # idempotent: nothing new
    assert json.loads(capsys.readouterr().out)["rosters"][0]["tagged"] == 0
    assert len(list(tmp_path.glob("roster.json.bak-*"))) == 1
