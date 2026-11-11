#!/usr/bin/env python3
"""Evaluate a local, explicit scene-evidence JSON packet without video reads.

The input is NOT a raw review export. The dimensions must already have
independently supplied, source-linked normalized values; missing evidence
is left absent. Writes only to stdout (pipe manually to a private report).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from adventure_intelligence.scene_relevance import (
    Evidence, VERSION, scene_envelope,
)

INPUT_VERSION = "klip-scene-evidence-input-v0"


def evaluate(document: dict) -> dict:
    if not isinstance(document, dict) or document.get("version") != INPUT_VERSION:
        raise ValueError("Unsupported scene evidence packet version")
    if not isinstance(document.get("episodes"), list):
        raise ValueError("Expected episodes list")
    if len(document["episodes"]) > 1000:
        raise ValueError("Packet exceeds bounded research batch")
    out, seen = [], set()
    for index, item in enumerate(document["episodes"]):
        if (not isinstance(item, dict) or
                set(item) != {"episodeId", "goal", "evidence"}):
            raise ValueError(f"Episode {index} shape invalid")
        ident = item["episodeId"]
        if (not isinstance(ident, str) or len(ident) > 100
                or not ident or ident in seen):
            raise ValueError("Missing, duplicated or oversized episodeId")
        seen.add(ident)
        supplied = item["evidence"]
        if not isinstance(supplied, dict):
            raise ValueError("Episode evidence must be object")
        rows = {key: Evidence.from_dict(raw)
                for key, raw in supplied.items()}
        out.append({"episodeId": ident, **scene_envelope(item["goal"], rows)})
    return {
        "version": VERSION,
        "inputVersion": INPUT_VERSION,
        "evaluatedEpisodes": len(out),
        "results": out,
        "mediaFilesAccessed": 0,
        "inferencePerformed": False,
        "sourceDeletionAuthorized": False,
        "publishAuthorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True,
                        help="Explicit local JSON evidence packet, not raw video")
    args = parser.parse_args()
    path = Path(args.input).expanduser()
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 4_000_000:
        parser.error("Missing, symlinked, or oversized evidence packet")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        result = evaluate(doc)
    except (ValueError, UnicodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
