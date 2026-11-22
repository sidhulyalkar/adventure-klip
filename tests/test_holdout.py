from __future__ import annotations

import json
from pathlib import Path

import pytest

from adventure_intelligence import holdout


def _world(tmp_path):
    reports = tmp_path / "reports"
    plan = {"days": {"011": [("20241223", "snorkeling", 12)],
                     "013": [("20241226", "snorkeling", 9), ("20241227", "snorkeling", 3)],
                     "017": [("20260309", "ski", 11), ("20260311", "ski", 20)]}}
    for n, days in plan["days"].items():
        folder = reports / f"discovery-{n}"
        rows = []
        for i, (day, sport, segs) in enumerate(days):
            sha = (n + day + str(i)).ljust(64, "0")[:64]
            (folder / sha).mkdir(parents=True)
            (folder / sha / "segments.json").write_text(json.dumps(
                {"segments": [{"role": "selected"}] * segs + [{"role": "not_reviewed"}] * 4}))
            rows.append({"sourceSha256": sha, "path": f"/ssd/DJI_{day}101010_0001_D.MP4",
                         "sport": sport})
        (folder / "sources.json").write_text(json.dumps(rows))
    return reports


def test_propose_picks_the_biggest_day_per_sport_outside_pilot_folders(tmp_path):
    reports = _world(tmp_path)
    picks = {p["sport"]: p for p in holdout.propose(reports)}
    assert picks["snorkeling"]["day"] == "2024-12-26"   # 2024-12-23 is in the pilot folder
    assert picks["ski"]["day"] == "2026-03-11" and picks["ski"]["reviewSegments"] == 20
    assert not (reports / holdout.FILE).exists()        # proposing writes nothing


def test_freeze_once_replace_with_reason_and_history_and_exclusion(tmp_path):
    reports = _world(tmp_path)
    doc = holdout.freeze(reports, [("2026-03-11", "ski")])
    assert doc["frozenBy"] == "owner" and len(doc["recordings"]) == 1
    with pytest.raises(ValueError, match="already frozen"):
        holdout.freeze(reports, [("2024-12-26", "snorkeling")])
    with pytest.raises(ValueError, match="needs a --reason"):
        holdout.freeze(reports, [("2024-12-26", "snorkeling")], replace=True)
    with pytest.raises(ValueError, match="No indexed ski recordings on 2020-01-01"):
        holdout.freeze(reports, [("2020-01-01", "ski")], replace=True, reason="x")
    holdout.freeze(reports, [("2024-12-26", "snorkeling")], replace=True, reason="more data")
    assert len(list((reports / "holdout-history").glob("*.json"))) == 1
    banned = holdout.held_out(reports)
    items = [{"sourceSha256": s} for s in banned] + [{"sourceSha256": "f" * 64}]
    kept, removed = holdout.exclude(items, reports)
    assert removed == len(banned) and kept == [{"sourceSha256": "f" * 64}]


def test_cli_freeze_proposed(tmp_path, capsys):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "klip_holdout", Path(__file__).parents[1] / "scripts" / "klip_holdout.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    reports = _world(tmp_path)
    assert cli.main(["freeze", "--reports", str(reports), "--proposed"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert {u["day"] for u in out["units"]} == {"2024-12-26", "2026-03-11"}
    assert cli.main(["freeze", "--reports", str(reports), "--day", "bad"]) == 2
