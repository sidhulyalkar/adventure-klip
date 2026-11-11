#!/usr/bin/env python3
"""Offline KSRE V1: inspect or score user-adjustable sub-sport profiles.

No video reads, media writes, cloud inference, source deletion or publishing.
Only explicitly provided normalized source-linked evidence can narrow bounds.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from adventure_intelligence.scene_relevance import Evidence, GOAL_WEIGHTS
from adventure_intelligence.scene_profiles import (
    VERSION, DIMENSIONS, SUBSPORT_PRIORS, profiled_scene_envelope,
    temporal_span_metrics,
)

INPUT_VERSION = "klip-scene-profile-input-v1"


def _profile_config(profiles: object, subsport: str, goal: str) -> dict:
    if not isinstance(profiles, dict):
        raise ValueError("profiles must be an object")
    if set(profiles) - set(SUBSPORT_PRIORS):
        raise ValueError("Unknown subsport in profiles")
    by_goal = profiles.get(subsport, {})
    if not isinstance(by_goal, dict) or set(by_goal) - set(GOAL_WEIGHTS):
        raise ValueError("Invalid goal-specific profile definition")
    candidate = by_goal.get(goal, {})
    if not isinstance(candidate, dict):
        raise ValueError("Goal profile customization must be object")
    return candidate


def _combine(global_conf: dict, local_conf: dict) -> dict:
    if not isinstance(global_conf, dict) or not isinstance(local_conf, dict):
        raise ValueError("Profile customization must be object")
    keys = {"weightOverrides", "weightMultipliers", "parameterOverrides"}
    if set(global_conf) - keys or set(local_conf) - keys:
        raise ValueError("Unexpected customization fields")
    combined = {}
    for name in keys:
        a, b = global_conf.get(name, {}), local_conf.get(name, {})
        if not isinstance(a, dict) or not isinstance(b, dict):
            raise ValueError(name + " must be an object")
        combined[name] = {**a, **b}
    return combined


def evaluate(document: dict) -> dict:
    if (not isinstance(document, dict)
            or document.get("version") != INPUT_VERSION
            or set(document) - {"version", "episodes", "profiles"}):
        raise ValueError("Unsupported input version or extra fields")
    episodes, profiles = document.get("episodes"), document.get("profiles", {})
    if not isinstance(episodes, list) or len(episodes) > 2000:
        raise ValueError("Invalid or excessive number of episodes")
    if not isinstance(profiles, dict) or set(profiles) - set(SUBSPORT_PRIORS):
        raise ValueError("Invalid global sub-sport profiles")
    results, seen = [], set()
    for i, episode in enumerate(episodes):
        allowed = {
            "episodeId", "goal", "subsport", "evidence", "customization",
            "sourceDurationMs", "temporalSpans",
        }
        if (not isinstance(episode, dict)
                or set(episode) - allowed
                or not {"episodeId", "goal", "subsport", "evidence"} <= set(episode)):
            raise ValueError(f"Invalid episode {i} fields")
        ident = episode["episodeId"]
        if (not isinstance(ident, str) or not 1 <= len(ident) <= 120
                or ident in seen):
            raise ValueError("Invalid or repeated episodeId")
        seen.add(ident)
        if not isinstance(episode["evidence"], dict):
            raise ValueError("Evidence must be an object")
        parsed = {
            key: Evidence.from_dict(value)
            for key, value in episode["evidence"].items()
        }
        goal, sport = episode["goal"], episode["subsport"]
        custom = _combine(
            _profile_config(profiles, sport, goal),
            episode.get("customization", {}),
        )
        score = profiled_scene_envelope(goal, sport, parsed, custom)
        row = {"episodeId": ident, **score}
        has_time = "temporalSpans" in episode or "sourceDurationMs" in episode
        if has_time:
            if not {"sourceDurationMs", "temporalSpans"} <= set(episode):
                raise ValueError("Both sourceDurationMs and temporalSpans are required")
            row["temporalEvidence"] = temporal_span_metrics(
                episode["sourceDurationMs"], episode["temporalSpans"])
        results.append(row)
    return {
        "version": VERSION, "inputVersion": INPUT_VERSION,
        "evaluatedEpisodes": len(results),
        "availableSubsports": sorted(SUBSPORT_PRIORS),
        "availableGoals": sorted(GOAL_WEIGHTS),
        "dimensionCount": len(DIMENSIONS), "results": results,
        "mediaFilesAccessed": 0, "trainingPerformed": False,
        "modelInferencePerformed": False,
        "autoDeleteOrPublish": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--input", help="Local JSON of already measured evidence")
    group.add_argument("--list-profiles", action="store_true",
                       help="List known sub-sports, goals and normalized dimensions")
    args = parser.parse_args()
    if args.list_profiles:
        print(json.dumps({
            "version": VERSION,
            "goals": sorted(GOAL_WEIGHTS),
            "subsports": sorted(SUBSPORT_PRIORS),
            "dimensions": list(DIMENSIONS),
        }, indent=2, sort_keys=True))
        return 0
    path = Path(args.input).expanduser()
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 4_000_000:
        parser.error("Input must be a local regular non-symlink JSON <= 4 MB")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        result = evaluate(document)
    except (UnicodeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
