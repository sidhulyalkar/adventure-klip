#!/usr/bin/env python3
"""Read-only SigLIP2 exemplar retrieval from Klip's existing human reviews.

No video decode, no Ollama call, no training, no cloud service. Requires
the frozen V2 pilot and existing verified semantic artifacts in the catalog.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
from tempfile import NamedTemporaryFile
from urllib.parse import quote

from adventure_intelligence.auto_scout_v2 import load_pilot, _bounded_workspace_path
from adventure_intelligence.examples import latest_examples
from adventure_intelligence.scene_atlas import clip_feature, frames_for_asset
from adventure_intelligence.exemplar_retrieval_baseline import compare_to_examples


def collect_readonly(workspace: Path, pilot: Path) -> dict:
    """Create a report using checked preview IDs and cache-verified embeddings."""
    rows, plan_sha = load_pilot(workspace, pilot)
    db_file = workspace / "catalog.sqlite3"
    if db_file.is_symlink() or not db_file.is_file():
        raise ValueError("Catalog absent or unsafe; no data has been changed")
    # query_only avoids catalog.connect(), which performs schema/WAL initialization.
    uri = "file:" + quote(str(db_file), safe="/") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    cache = {}
    def get(aid):
        if aid not in cache:
            cache[aid] = frames_for_asset(conn, workspace, aid, "siglip2")
        return cache[aid]

    try:
        if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='example_revisions'").fetchone() is None:
            raise ValueError("Human exemplar revision table is missing")
        reviewed = latest_examples(conn)
        vetted = []
        for ex in reviewed:
            if ex.get("decision") != "confirmed" or ex.get("highlight") not in ("keep", "skip"):
                continue
            feat = clip_feature(*get(ex["asset_id"]), ex["start_ms"], ex["end_ms"])
            if not feat.get("available"):
                continue
            vetted.append({
                "sourceSha256": ex["asset_id"], "modelKey": feat["modelKey"],
                "vector": feat["vector"], "decision": "confirmed",
                "highlight": ex["highlight"], "label": ex["label"],
                "exampleId": ex["example_id"],
            })
        candidates, coverage = [], {"withoutCachedFeatures": 0, "availableFeatures": 0}
        for row in rows:
            feat = clip_feature(
                *get(row["sourceSha256"]), row["sourceStartMs"], row["sourceEndMs"]
            )
            if feat.get("available"):
                coverage["availableFeatures"] += 1
            else:
                coverage["withoutCachedFeatures"] += 1
            candidates.append({
                "recordId": row["recordId"],
                "sourceSha256": row["sourceSha256"],
                "startMs": row["sourceStartMs"], "endMs": row["sourceEndMs"],
                "modelKey": feat.get("modelKey"), "vector": feat.get("vector"),
                "sampleCount": feat.get("sampleCount", 0),
            })
    finally:
        conn.close()
    report = compare_to_examples(candidates, vetted)
    report["inputPlanSha256"] = plan_sha
    report["frozenPilot"] = str(pilot)
    report["cachedFeatureCoverage"] = coverage
    report["originalVideoReads"] = 0
    report["originalVideoWrites"] = 0
    report["newEmbeddingsComputed"] = 0
    report["databaseMutations"] = 0
    report["benchmarkAccuracyMeasured"] = False
    report["nextStep"] = (
        "Collect human-reviewed Keep/Skip exemplar labels before comparing ranks"
        if report["positiveExampleCount"] == 0
        else "Blind-review returned cross-source matches, grouped by independent outing"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--pilot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        ws = args.workspace.expanduser().resolve(strict=True)
        pilot = _bounded_workspace_path(ws, args.pilot, existing=True)
        output = Path(os.path.abspath(os.path.expanduser(str(args.output))))
        if (not output.is_relative_to(ws) or output.relative_to(ws).parts[0] != "reports"
                or output.exists() or output.is_symlink()
                or output.parent.is_symlink() or not output.parent.is_dir()):
            raise ValueError("Choose a new private workspace reports/ output filename")
        report = collect_readonly(ws, pilot)
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                prefix=".klip-exemplar-", delete=False) as file:
            temp = Path(file.name)
            json.dump(report, file, indent=2, sort_keys=True, allow_nan=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        try:
            os.replace(temp, output)
        finally:
            temp.unlink(missing_ok=True)
        print(json.dumps({
            "output": str(output),
            "candidateCount": report["candidateCount"],
            "withCachedFeatures": report["cachedFeatureCoverage"]["availableFeatures"],
            "confirmedExamplesWithFeatures": report["humanConfirmedExampleCount"],
            "positiveExamples": report["positiveExampleCount"],
            "rankedLabels": list(report["rankings"]),
            "status": report["nextStep"],
        }, indent=2))
        return 0
    except (ValueError, OSError, TypeError, KeyError, sqlite3.Error) as exc:
        print("Exemplar baseline blocked safely: " + str(exc), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
