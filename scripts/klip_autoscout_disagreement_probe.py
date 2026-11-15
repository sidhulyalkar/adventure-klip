#!/usr/bin/env python3
"""Inspect only failed Auto-Scout category outputs in a NEW private report.

The old experiment and its 108 successful receipts remain immutable.
Dry run is default. --run repeats only failed preview inference locally.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

from adventure_intelligence import auto_scout_v2 as scout
from adventure_intelligence import autoscout_disagreement as diag
from adventure_intelligence.studio_preview_judge import valid_local_model


def _json_file(path: Path, max_bytes: int = 1_000_000) -> dict:
    if path.is_symlink() or not path.is_file() or not 2 <= path.stat().st_size <= max_bytes:
        raise ValueError("Missing or unsafe JSON file")
    body = json.loads(path.read_bytes())
    if not isinstance(body, dict):
        raise ValueError("Expected JSON object")
    return body


def run(args) -> dict:
    ws = args.workspace.expanduser().resolve(strict=True)
    pilot = scout._bounded_workspace_path(ws, args.pilot, existing=True)
    original = scout._bounded_workspace_path(ws, args.experiment, existing=True)
    output = scout._bounded_workspace_path(ws, args.output, existing=False)
    if output == original or output.is_relative_to(original) or output == pilot:
        raise ValueError("Diagnostic output must be a separate private experiment")
    rows, plan_sha = scout.load_pilot(ws, pilot)
    if not valid_local_model(args.model):
        raise ValueError("Invalid local model identifier")
    state = _json_file(original/"experiment-state.json")
    if (state.get("version") != scout.VERSION
            or state.get("pilot") != str(pilot)
            or state.get("inputPlanSha256") != plan_sha
            or state.get("model") != args.model
            or state.get("policyFingerprint") != scout.fingerprint(args.model)):
        raise ValueError("Original run identity/model fingerprint mismatch")

    receipts = scout.read_receipts(original, rows, plan_sha, state["policyFingerprint"])
    if len(receipts) != len(rows):
        raise ValueError("Original run has unfinished candidates; finish it first")
    chosen = [
        row for row in rows
        if receipts[row["recordId"]]["status"] == "failed"
        and receipts[row["recordId"]].get("failureStage", "model_schema") == "model_schema"
    ]
    chosen.sort(key=lambda row: row["recordId"])
    if len(chosen) != sum(r["status"] == "failed" for r in receipts.values()):
        raise ValueError("Some failures have other causes; this tool only diagnoses model schema")
    if not chosen:
        return {
            "status": "nothing_to_diagnose",
            "originalSuccessfulReceipts": len(rows), "failed": 0,
        }
    if not 1 <= args.max_items <= 200:
        raise ValueError("max-items must be 1..200")
    selected = chosen[:args.max_items]
    if not args.run:
        return {
            "status": "validated_dry_run",
            "sourcePlanSha256": plan_sha,
            "originalSuccessfulReceipts": len(rows)-len(chosen),
            "originalSchemaFailures": len(chosen),
            "willRerunOnlyFailed": len(selected),
            "destination": str(output),
            "originalMediaReads": 0,
        }

    metadata = {
        "version": diag.VERSION,
        "originalExperiment": str(original),
        "pilot": str(pilot),
        "inputPlanSha256": plan_sha,
        "model": args.model,
        "originalModelPolicyFingerprint": state["policyFingerprint"],
        "purpose": "reobserve_category_contradictions_without_editing_original_receipts",
    }
    if args.resume:
        if not output.is_dir() or output.is_symlink():
            raise ValueError("Cannot resume missing/unsafe output")
        if _json_file(output/"experiment-state.json") != metadata:
            raise ValueError("Resume policy/original run mismatch")
    else:
        if output.exists() or output.is_symlink():
            raise ValueError("Choose a new output folder or use --resume")
        output.mkdir(parents=True, mode=0o700, exist_ok=False)
        (output/"diagnoses").mkdir(mode=0o700)
        scout.save_json(output/"experiment-state.json", metadata)

    saved = []
    for row in selected:
        rid = row["recordId"]
        path = output/"diagnoses"/(rid+".json")
        if path.is_symlink():
            raise ValueError("Symlinked diagnosis receipt refused")
        if path.exists():
            record = _json_file(path, 25_000)
            if (record.get("version") != diag.VERSION or record.get("recordId") != rid
                    or record.get("sourceSha256") != row["sourceSha256"]
                    or record.get("proxySha256") != row["proxySha256"]
                    or record.get("originalModelPolicyFingerprint") != state["policyFingerprint"]):
                raise ValueError("Existing diagnosis does not match source or policy")
            saved.append(record)
            continue

        # stage only verified proxy hardlink for local review; no DJI original I/O.
        scout._safe_preview_link(output, row)
        record = {
            "version": diag.VERSION,
            "recordId": rid, "sourceSha256": row["sourceSha256"],
            "proxySha256": row["proxySha256"],
            "originalModelPolicyFingerprint": state["policyFingerprint"],
            "originalFailureCode": receipts[rid].get("failureCode", "legacy_unknown"),
        }
        try:
            observation = diag.rerun_failed_preview(
                row["video"], model=args.model
            )
            record.update(status="done", diagnosis=observation)
        except (ValueError, RuntimeError, OSError, TimeoutError) as exc:
            record.update(
                status="failed", stage="repeat_decode_or_model_failure",
                errorType=type(exc).__name__,
            )
        scout.save_json(path, record)
        saved.append(record)
        print(f"[diagnostic] {len(saved)}/{len(selected)} {rid[:12]} {record['status']}",
              file=sys.stderr, flush=True)

    # Include all matching receipts on resume/expansion, not just this invocation.
    complete = []
    known = {x["recordId"]: x for x in chosen}
    for path in sorted((output/"diagnoses").glob("*.json")):
        record = _json_file(path, 25_000)
        rid = record.get("recordId")
        if (rid not in known or path.stem != rid
                or record.get("version") != diag.VERSION
                or record.get("sourceSha256") != known[rid]["sourceSha256"]
                or record.get("proxySha256") != known[rid]["proxySha256"]
                or record.get("originalModelPolicyFingerprint") != state["policyFingerprint"]
                or record.get("status") not in ("done", "failed")):
            raise ValueError("Unrecognized stored diagnosis")
        complete.append(record)
    report = diag.summary_report(
        complete, failed_population=len(chosen), model=args.model,
        source_plan_sha=plan_sha, completed_source_receipts=len(rows)-len(chosen)
    )
    scout.save_json(output/"summary.json", report)
    (output/"review.html").write_text(diag.offline_html(report), encoding="utf-8")
    return {
        "status": "diagnostics_written",
        "originalSuccessesKept": len(rows)-len(chosen),
        "totalOriginalFailed": len(chosen),
        "diagnosed": report["attemptedReruns"],
        "repeatConsistency": report["responseConsistency"],
        "output": str(output), "review": str(output/"review.html"),
        "originalReceiptsModified": 0, "modelsLabelsApproved": 0,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workspace", required=True, type=Path)
    p.add_argument("--pilot", required=True, type=Path)
    p.add_argument("--experiment", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--model", default="gemma3:4b")
    p.add_argument("--max-items", type=int, default=12)
    p.add_argument("--run", action="store_true")
    p.add_argument("--resume", action="store_true")
    a = p.parse_args()
    if a.resume and not a.run:
        p.error("--resume requires --run")
    try:
        print(json.dumps(run(a), indent=2, sort_keys=True, allow_nan=False))
        return 0
    except (ValueError, TypeError, KeyError, OSError) as exc:
        print("Schema disagreement probe stopped safely: "+str(exc), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
