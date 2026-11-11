#!/usr/bin/env python3
"""Prepare, audit or evaluate a blind, trip-held-out K-TIDE retrieval study.

No source videos or preview proxies are opened, no training/inference is run,
and no annotation is inferred from a model. Output is JSON on stdout only.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from adventure_intelligence.encoder_dataset import load_manifest
from adventure_intelligence.ktide_study_contract import (
    build_blind_deck, load_json, validate_deck, validate_training_lineage,
    review_worksheet,
)
from adventure_intelligence.ktide_study_metrics import evaluate_retrieval


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("plan", "worksheet", "audit", "evaluate"), required=True)
    parser.add_argument("--manifest", required=True,
                        help="Exact frozen K-TIDE dataset manifest JSON")
    parser.add_argument("--training-report", required=True,
                        help="K-TIDE training-report.json with frozen split assignments")
    parser.add_argument("--study", help="Private human-adjudicated blind study JSON")
    parser.add_argument("--embeddings", help="K-TIDE + frozen baseline report JSON")
    parser.add_argument("--max-queries", type=int, default=60)
    parser.add_argument("--candidates-per-query", type=int, default=10)
    parser.add_argument("--seed", type=int, default=31)
    parser.add_argument("--task", default="scene_situation",
                        choices=("scene_situation", "temporal_event", "editorial_context"))
    args = parser.parse_args()
    if args.mode != "plan" and not args.study:
        parser.error("--study required for worksheet/audit/evaluate")
    if args.mode == "evaluate" and not args.embeddings:
        parser.error("--embeddings required for evaluate")
    try:
        manifest, manifest_sha = load_manifest(Path(args.manifest).expanduser())
        training, _ = load_json(Path(args.training_report).expanduser(),
                                max_bytes=8_000_000)
        validate_training_lineage(training, manifest, manifest_sha)
        if args.mode == "plan":
            result = build_blind_deck(
                manifest, manifest_sha, training,
                max_queries=args.max_queries,
                candidates_per_query=args.candidates_per_query,
                seed=args.seed, task=args.task)
        else:
            deck, study_sha = load_json(Path(args.study).expanduser(),
                                        max_bytes=16_000_000)
            if args.mode == "worksheet":
                result = review_worksheet(deck, manifest, manifest_sha, training)
            elif args.mode == "audit":
                result = {
                    "version": "klip-ktide-study-audit-v1",
                    **validate_deck(deck, manifest, manifest_sha, training,
                                    require_complete=False),
                    "studyFileSha256": study_sha,
                    "rawMediaRead": False,
                    "modelPredictionsNotShownToReviewer": True,
                }
            else:
                representations, projection_sha = load_json(
                    Path(args.embeddings).expanduser(), max_bytes=64_000_000)
                result = evaluate_retrieval(
                    deck, manifest, manifest_sha, training, representations,
                    bootstrap_seed=args.seed)
                result["studyFileSha256"] = study_sha
                result["embeddingReportSha256"] = projection_sha
    except (ValueError, OSError, UnicodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
