from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from adventure_intelligence import holdout, judge_reliability as jr, owner_labels

FAST, SLOW = "gemma3:27b", "qwen3-vl:32b"


def _panels(reports: Path, n: int = 30):
    """Ski panels. airborne_visible: judges always agree. crash_or_fall_visible: when the
    fast judge says yes the slow one says no (a dispute); when it says no they agree."""
    folder = reports / "discovery-017"
    rows = []
    for i in range(n):
        sha = f"{i:02d}".ljust(64, "a")
        crash_fast = "yes" if i % 3 == 0 else "no"
        facts = {"airborne_visible": {"outcome": "agreed_yes" if i % 2 else "agreed_no",
                                      "votes": {FAST: "yes" if i % 2 else "no",
                                                SLOW: "yes" if i % 2 else "no"}},
                 "crash_or_fall_visible": {"outcome": "disputed" if crash_fast == "yes" else "agreed_no",
                                           "votes": {FAST: crash_fast, SLOW: "no"}}}
        j = folder / sha / "judgments"
        j.mkdir(parents=True)
        (j / "0-10000.json").write_text(json.dumps({
            "frameTimesMs": [500, 2500, 4500, 6500], "judgments": [
                {"model": FAST, "status": "answered"}, {"model": SLOW, "status": "answered"}],
            "panel": {"sport": "ski", "facts": facts}}))
        rows.append({"sourceSha256": sha, "path": f"/ssd/DJI_20260309{i:06d}_0001_D.MP4",
                     "sport": "ski"})
    (folder / "sources.json").write_text(json.dumps(rows))
    return rows


def test_agreement_audit_and_cascade_simulation(tmp_path):
    _panels(tmp_path)
    ps = jr.panels(tmp_path)
    by = {r["question"]: r for r in jr.agreement(ps)["byQuestion"]}
    assert by["airborne_visible"]["agreement"] == 1.0 and by["airborne_visible"]["kappa"] == 1.0
    assert by["crash_or_fall_visible"]["disputedShare"] == round(10 / 30, 3)
    sim = jr.simulate_cascade(ps, fast=FAST, slow=SLOW, threshold=0.9, min_n=5)
    assert sim["slowCallsSaved"] == 20 and sim["disputesThatWouldGoUnseen"] == 0
    assert "ski:crash_or_fall_visible=yes" not in sim["trustedFastAnswers"]


def test_deck_skips_held_out_and_labelled_items_and_mixes_controls(tmp_path):
    rows = _panels(tmp_path)
    holdout.freeze(tmp_path, [("2026-03-09", "ski")])
    ps = jr.panels(tmp_path)
    assert jr.deck_items(ps, held_out=holdout.held_out(tmp_path), already=set()) == []
    items = jr.deck_items(ps, held_out=set(), already={f"{ps[0]['id']}|crash_or_fall_visible"},
                          size=12, control_share=0.25)
    assert len(items) == 12 and sum(i["outcome"] != "disputed" for i in items) == 3
    assert all(i["item"] != f"{ps[0]['id']}|crash_or_fall_visible" for i in items)


def _labels(n_each: int):
    """The slow judge is right about crashes; the fast one over-claims them."""
    out = []
    for i in range(n_each):
        out.append({"sport": "ski", "question": "crash_or_fall_visible", "answer": "no",
                    "votes": {FAST: "yes", SLOW: "no"}})
        out.append({"sport": "ski", "question": "crash_or_fall_visible", "answer": "yes",
                    "votes": {FAST: "yes", SLOW: "yes"}})
    return out


def test_reliability_settles_disputes_only_with_enough_evidence():
    rel = jr.reliability(_labels(10))
    cell = {r["model"]: r for r in rel["byModelQuestion"]}
    assert cell[FAST]["falseYes"] == 10 and cell[SLOW]["accuracy"] == 1.0
    fact = {"outcome": "disputed", "votes": {FAST: "yes", SLOW: "no"}}
    settled = jr.apply({"crash_or_fall_visible": fact}, "ski", rel)["crash_or_fall_visible"]
    assert settled["outcome"] == "resolved_no" and settled["pYes"] < 0.15
    assert settled["resolvedBy"].startswith("owner-calibrated")
    thin = jr.reliability(_labels(3))                       # under MIN_LABELS: stays disputed
    assert jr.apply({"crash_or_fall_visible": fact}, "ski", thin)["crash_or_fall_visible"]["outcome"] == "disputed"
    assert jr.apply({"crash_or_fall_visible": fact}, "ski", None)["crash_or_fall_visible"] is fact


def test_cli_deck_import_reliability(tmp_path, capsys):
    spec = importlib.util.spec_from_file_location(
        "klip_fact_check", Path(__file__).parents[1] / "scripts" / "klip_fact_check.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    _panels(tmp_path)
    fake = lambda proxy, times: ["/9j/4AAQSkZJRg=="] * len(times)   # tiny jpeg stub
    base = ["--reports", str(tmp_path)]
    assert cli.main(["deck", *base, "--size", "8"], sampler=fake) == 0
    deck = json.loads(capsys.readouterr().out)
    folder = tmp_path / "fact-check" / deck["deck"]
    key = json.loads((folder / "key.json").read_text())
    page = (folder / "index.html").read_text()
    assert deck["items"] == 8 and "disputed" not in page and "agreed" not in page   # blind
    answers = {"deck": deck["deck"], "answers": {i: "no" for i in key["items"]}}
    f = tmp_path / "a.json"
    f.write_text(json.dumps(answers))
    assert cli.main(["import", *base, "--answers", str(f)]) == 0
    capsys.readouterr()
    assert len(owner_labels.load(tmp_path, "fact")) == 8
    assert cli.main(["reliability", *base]) == 0
    assert json.loads((tmp_path / "judge-reliability.json").read_text())["labels"] == 8
    assert cli.main(["agreement", *base]) == 0 and cli.main(["cascade", *base]) == 0
