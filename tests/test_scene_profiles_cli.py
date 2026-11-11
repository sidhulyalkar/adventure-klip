"""CLI validation uses synthetic examples, never private camera files."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from adventure_intelligence.scene_profiles import DIMENSIONS, SUBSPORT_PRIORS
from scripts.klip_scene_profiles import evaluate, INPUT_VERSION


def test_synthetic_profile_cli_offline():
    root = Path(__file__).resolve().parents[1]
    path = root / "examples" / "scene_profiles_synthetic_v1.json"
    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / "klip_scene_profiles.py"),
         "--input", str(path)],
        cwd=root, capture_output=True, text=True, timeout=25,
    )
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(proc.stdout)
    assert doc["evaluatedEpisodes"] == 2
    assert doc["dimensionCount"] == len(DIMENSIONS)
    assert len(doc["availableSubsports"]) == len(SUBSPORT_PRIORS)
    assert doc["mediaFilesAccessed"] == 0
    assert doc["trainingPerformed"] is False
    assert doc["autoDeleteOrPublish"] is False
    assert doc["results"][0]["temporalEvidence"]["coverageFraction"] == .6
    assert doc["results"][1]["dimensions"]["wildlife_behavior_distinctiveness"][
        "status"] == "hypothesis_only"


def test_cli_lists_profiles():
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / "klip_scene_profiles.py"),
         "--list-profiles"],
        cwd=root, capture_output=True, text=True, timeout=25,
    )
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert "skiing_trees" in data["subsports"]
    assert "snorkeling_ray_shark" in data["subsports"]


def test_config_validation_rejects_injected_and_duplicate_data():
    doc = {"version": INPUT_VERSION, "episodes": []}
    assert evaluate(doc)["evaluatedEpisodes"] == 0
    with pytest.raises(ValueError):
        evaluate({**doc, "sourceVideoPath": "/Volumes/unsafe"})
    with pytest.raises(ValueError):
        evaluate({
            **doc,
            "episodes": [
                {"episodeId": "dup", "goal": "sport_action",
                 "subsport": "skiing_speed", "evidence": {}},
                {"episodeId": "dup", "goal": "sport_action",
                 "subsport": "skiing_speed", "evidence": {}},
            ],
        })


def test_global_plus_local_weight_overrides_have_clear_precedence():
    doc = {
        "version": INPUT_VERSION,
        "profiles": {"skiing_speed": {"sport_action": {
            "weightOverrides": {"action_intensity": .4},
            "parameterOverrides": {"contextBeforeSeconds": 6}
        }}},
        "episodes": [{
            "episodeId": "synthetic",
            "goal": "sport_action", "subsport": "skiing_speed",
            "evidence": {},
            "customization": {
                "weightOverrides": {"action_intensity": .8},
                "parameterOverrides": {"contextBeforeSeconds": 10}
            },
        }],
    }
    row = evaluate(doc)["results"][0]
    assert row["profile"]["parameters"]["contextBeforeSeconds"] == 10
    assert row["profile"]["weights"]["action_intensity"] > .35
    assert row["profile"]["customized"] is True
