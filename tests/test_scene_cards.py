"""Scene cards: model observations with controls, abstentions and resumability. No real model."""
from __future__ import annotations

import json

from adventure_intelligence import scene_cards as sc
from adventure_intelligence import value_features as features


def answer(**over):
    facts = {k: "no" for k in sc.QUESTIONS}
    facts.update(over.pop("answers", {}))
    body = {"caption": "A sea turtle swims below the snorkeler.", "activity": "snorkeling",
            "people": "0", "animal": "Sea turtle", "answers": facts, **over}
    return lambda request: {"message": {"content": json.dumps(body)}}


def test_card_is_an_observation_with_controls_and_abstentions():
    card = sc.ask("qwen3-vl:8b", ["img"], [0], transport=answer(
        answers={"underwater": "yes", "animal_visible": "yes", "face_visible": "unclear"}))
    assert card["status"] == "answered" and card["animal"] == "sea turtle"
    assert card["ownerVerified"] is False and card["controlFailures"] == []
    assert set(card["facts"]) == set(sc.FACTS)  # controls are not facts
    feats = sc.features([card])
    assert feats["card:underwater"] == 1.0 and feats["card:camera_steady"] == 0.0
    assert "card:face_visible" not in feats  # unclear is missing, not 0.5
    assert feats["card:people"] == 0.0

    broken = sc.ask("qwen3-vl:8b", ["img"], [0],
                    transport=lambda r: {"message": {"content": '{"caption": 1}'}})
    assert broken["status"] == "abstained" and broken["notANegative"]
    liar = sc.ask("gemma3:27b", ["img"], [0], transport=answer(
        answers={"control_office": "yes", "underwater": "no"}))
    assert liar["controlFailures"] == ["control_office"]
    # A model that fails a control does not count; one that abstained neither.
    assert sc.features([card, liar, broken])["card:underwater"] == 1.0
    request = {}
    sc.ask("qwen3-vl:8b", ["a", "b"], [0, 500], transport=lambda r: request.update(r) or
           answer()(r))
    assert request["options"]["temperature"] == 0 and request["format"] == sc.schema()
    assert "worth keeping" in request["messages"][0]["content"]  # told not to judge it


def test_targets_cover_labels_once_and_add_random_controls_on_labelled_days():
    j = [{"sourceSha256": "a" * 64, "startMs": 0, "endMs": 8000},
         {"sourceSha256": "a" * 64, "startMs": 0, "endMs": 8000}]
    pairs = [{"left": {"sourceSha256": "a" * 64, "startMs": 0, "endMs": 8000},
              "right": {"sourceSha256": "b" * 64, "startMs": 1000, "endMs": 9000}}]
    scanned = {"a" * 64: {"durationMs": 60_000}, "b" * 64: {"durationMs": 60_000},
               "c" * 64: {"durationMs": 60_000}}
    day = {"a" * 64: "d1", "b" * 64: "d1", "c" * 64: "d2"}.get
    made = sc.targets(j, pairs, scanned, day, controls_per_day=3, seed=1)
    labelled = [t for t in made if t["role"] == "owner_labelled"]
    controls = [t for t in made if t["role"] == "random_control"]
    assert len(labelled) == 2 and 1 <= len(controls) <= 3
    assert all(day(t["sourceSha256"]) == "d1" for t in controls)  # only labelled days
    assert all(t["endMs"] - t["startMs"] == 8000 for t in controls)
    assert made == sc.targets(j, pairs, scanned, day, controls_per_day=3, seed=1)


def test_describe_is_resumable_and_feeds_evaluation_items(tmp_path):
    segs = [{"sourceSha256": "a" * 64, "startMs": 0, "endMs": 8000, "role": "owner_labelled"},
            {"sourceSha256": "b" * 64, "startMs": 0, "endMs": 8000, "role": "random_control"}]
    calls = []

    def transport(request):
        calls.append(request["model"])
        return answer(answers={"underwater": "yes"})(request)

    frames = lambda path, times: ["x"] * len(times)  # noqa: E731
    path_of = {"a" * 64: tmp_path / "a.mp4", "b" * 64: None}.get
    totals = sc.describe(segs, path_of, tmp_path / "cards", ["qwen3-vl:8b"], frames=frames,
                         transport=transport)
    assert totals["answered"] == 1 and calls == ["qwen3-vl:8b"]
    again = sc.describe(segs, path_of, tmp_path / "cards", ["qwen3-vl:8b"], frames=frames,
                        transport=transport)
    assert again["alreadyDone"] == 1 and calls == ["qwen3-vl:8b"]
    # A second model family is asked only for itself.
    sc.describe(segs, path_of, tmp_path / "cards", ["qwen3-vl:8b", "gemma3:12b"],
                frames=frames, transport=transport)
    assert calls == ["qwen3-vl:8b", "gemma3:12b"]
    stored = sc.load(tmp_path / "cards")
    assert set(stored) == {("a" * 64, 0, 8000)}
    assert stored[("a" * 64, 0, 8000)]["role"] == "owner_labelled"
    items = {"x": {"sourceSha256": "a" * 64, "startMs": 0, "endMs": 8000, "concepts": {"snow": 0.1}},
             "y": {"sourceSha256": "b" * 64, "startMs": 0, "endMs": 8000, "concepts": None}}
    assert features.attach_cards(items, stored) == 1
    assert items["x"]["concepts"]["card:underwater"] == 1.0 and items["x"]["concepts"]["snow"] == 0.1
    assert items["y"]["concepts"] is None
