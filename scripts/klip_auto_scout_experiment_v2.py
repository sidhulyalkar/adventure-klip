#!/usr/bin/env python3
"""Checkpointed Klip V2 auto-scout on local verified V2 preview derivatives.

Default --dry-run prints source coverage and stops with zero model calls.
--run opts in to localhost Ollama only. --resume reuses a frozen output
directory and never reprocesses successful, checksum-bound receipts.
"""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
import subprocess
from pathlib import Path
import sys
from urllib.request import urlopen

from adventure_intelligence.auto_scout_v2 import (
    VERSION, _bounded_workspace_path, _safe_preview_link, build_report,
    diversify, fingerprint, infer_proxy, load_pilot, quality_measurements,
    read_receipts, review_html, save_json,
)
from adventure_intelligence.studio_preview_judge import valid_local_model


def _ollama_preflight(model: str) -> None:
    """Talk exclusively to loopback, fail quickly on wrong/missing model."""
    if not valid_local_model(model):
        raise ValueError("Invalid local image model name")
    try:
        with urlopen("http://127.0.0.1:11434/api/tags", timeout=4) as res:
            body = res.read(200_001)
        if len(body) > 200_000:
            raise ValueError("Local Ollama model list exceeds preflight limit")
        doc = json.loads(body)
        if not isinstance(doc.get("models"), list):
            raise ValueError("Invalid installed Ollama model list")
        names = {m.get("name") for m in doc["models"] if isinstance(m,dict)}
        if model not in names:
            raise ValueError("Model "+model+" is not installed locally; check 'ollama list'")
    except (OSError, TimeoutError) as exc:
        raise ValueError("Ollama must be running locally at 127.0.0.1:11434") from exc


def execute(args) -> dict:
    ws = args.workspace.expanduser().resolve(strict=True)
    pilot = _bounded_workspace_path(ws, args.pilot, existing=True)
    destination = _bounded_workspace_path(ws, args.output, existing=False)
    if destination == pilot or destination.is_relative_to(pilot):
        raise ValueError("Output cannot be inside the frozen input pilot")
    all_rows, plan_sha = load_pilot(ws, pilot)
    ordered = diversify(all_rows)
    cap = args.max_items
    if type(cap) is not int or cap < 1 or cap > 1000:
        raise ValueError("max-items must be 1..1000")
    chosen = ordered[:cap]
    policy = fingerprint(args.model)
    if not args.run:
        return {
            "status": "validated_dry_run_no_local_model_calls",
            "version": VERSION, "inputPlanSha256": plan_sha,
            "allCandidates": len(ordered), "requested": len(chosen),
            "representedSources": len({x["sourceSha256"] for x in chosen}),
            "catalogSportHints": dict(Counter(r["sportHint"] for r in chosen)),
            "plannedOutput": str(destination), "noVideoOpenedOutsideProxyCache": True,
            "notActionRecognitionAccuracy": True,
        }
    _ollama_preflight(args.model)
    dest_exists = destination.exists()
    if args.resume:
        if not dest_exists or not destination.is_dir():
            raise ValueError("--resume requires existing experiment directory")
    elif dest_exists:
        raise ValueError("Output already exists; use --resume or a new directory")

    state_path = destination / "experiment-state.json"
    if args.resume:
        if state_path.is_symlink() or not state_path.is_file():
            raise ValueError("Resume state missing or symlinked")
        state = json.loads(state_path.read_text())
        if (state.get("version") != VERSION or
                state.get("pilot") != str(pilot) or
                state.get("inputPlanSha256") != plan_sha or
                state.get("model") != args.model or
                state.get("policyFingerprint") != policy):
            raise ValueError("Cannot resume with different pilot, model or policy")
    else:
        destination.mkdir(mode=0o700, parents=True, exist_ok=False)
        (destination / "receipts").mkdir(mode=0o700)
        state = {
            "version": VERSION, "pilot": str(pilot),
            "inputPlanSha256": plan_sha,
            "model": args.model, "policyFingerprint": policy,
            "intent": "model_hypotheses_and_proxy_metrics_not_approved_media_edits",
            "sourceOriginalReads": 0,
        }
        save_json(state_path, state)
    receipts = read_receipts(destination, all_rows, plan_sha, policy)
    # Repair missing output gallery hardlinks from still-verified source proxies
    # without running inference again. Their fingerprints are checked on load.
    all_by_id = {r["recordId"]: r for r in all_rows}
    for rid, receipt in receipts.items():
        if receipt["status"] == "done":
            _safe_preview_link(destination, all_by_id[rid])
    report = None
    attempted = 0

    for idx,row in enumerate(chosen, 1):
        rid = row["recordId"]
        existing = receipts.get(rid)
        if existing and existing["status"] == "done":
            continue
        if existing and existing["status"] == "failed" and not args.retry_failures:
            continue
        # Re-hash source proxy immediately before each decoder/model call.
        video = row["video"]
        if sha256(video.read_bytes()).hexdigest() != row["proxySha256"]:
            raise ValueError("Source preview changed since initial integrity audit")
        _safe_preview_link(destination, row)
        attempted += 1
        receipt = {
            "version": VERSION, "recordId": rid,
            "sourceSha256": row["sourceSha256"],
            "sourceStartMs": row["sourceStartMs"],
            "sourceEndMs": row["sourceEndMs"],
            "proxySha256": row["proxySha256"],
            "inputPlanSha256": plan_sha,
            "policyFingerprint": policy,
        }
        try:
            # Quality telemetry uses 2-fps tiny grayscale proxy frames only.
            quality = quality_measurements(video)
            judgment = infer_proxy(video, model=args.model)
            judgment["sampledSourceRelativeMsApprox"] = [
                row["sourceStartMs"] + t for t in judgment["sampledProxyTimeMs"]
            ]
            if sha256(video.read_bytes()).hexdigest() != row["proxySha256"]:
                raise ValueError("Proxy mutated while inference was running")
            receipt.update(status="done",
                           analysis={"proposal": judgment["proposal"],
                                     "numericQuality": quality,
                                     "sampledProxyTimeMs": judgment["sampledProxyTimeMs"],
                                     "sampledSourceRelativeMsApprox":
                                     judgment["sampledSourceRelativeMsApprox"],
                                     "backend": judgment["backend"],
                                     "modelTagMayChange": True})
        except (ValueError, OSError, RuntimeError, TimeoutError,
                subprocess.TimeoutExpired) as exc:
            receipt.update(status="failed",
                           errorType=type(exc).__name__,
                           reason="read_model_or_decode_failure_unverified")
        path = destination / "receipts" / (rid + ".json")
        # Retry failures can replace only their own failed checkpoint receipt.
        save_json(path, receipt)
        receipts[rid] = receipt
        if attempted % args.report_every == 0:
            report = build_report(receipts, total=len(all_rows),
                                  processed_limit=len(chosen),
                                  source_plan_sha=plan_sha, model=args.model,
                                  pilot=pilot)
            save_json(destination / "summary.json", report)
            (destination / "review.html").write_text(review_html(report), encoding="utf-8")
            print(
                f"[auto-scout] processed {report['processed']}, "
                f"failed {report['failed']}, limit {len(chosen)}, "
                f"catalog {len(all_rows)}",
                file=sys.stderr, flush=True)

    report = build_report(receipts, total=len(all_rows),
                          processed_limit=len(chosen),
                          source_plan_sha=plan_sha, model=args.model,
                          pilot=pilot)
    save_json(destination / "summary.json", report)
    (destination / "review.html").write_text(review_html(report), encoding="utf-8")
    return {
        "version": VERSION, "status": "checkpointed_run_complete",
        "sourcePilot": str(pilot), "output": str(destination),
        "catalogCandidates": len(all_rows),
        "scanned": report["processed"], "failed": report["failed"],
        "pending": report["remaining"],
        "uniqueSources": report["distinctAnalyzedSources"],
        "activityProposals": report["activityProposals"],
        "categoryProposals": report["categoryProposals"],
        "qualityFlagProposals": report["qualityFlagProposals"],
        "review": str(destination / "review.html"),
        "summary": str(destination / "summary.json"),
        "originalDJIVideoReads": 0,
        "modelPredictionsHumanVerified": False,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workspace", required=True, type=Path)
    ap.add_argument("--pilot", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--model", default="gemma3:4b")
    ap.add_argument("--max-items", type=int, default=32)
    ap.add_argument("--report-every", type=int, default=8)
    ap.add_argument("--run", action="store_true", help="Actually call installed local Ollama")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--retry-failures", action="store_true")
    args = ap.parse_args()
    if args.report_every < 1 or args.report_every > 40:
        ap.error("--report-every must be 1..40")
    if not valid_local_model(args.model):
        ap.error("Unsafe local model identifier")
    if (args.resume or args.retry_failures) and not args.run:
        ap.error("--resume/--retry-failures require --run")
    try:
        print(json.dumps(execute(args), indent=2, sort_keys=True, allow_nan=False))
        return 0
    except (ValueError, OSError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print("Auto-Scout refused: "+str(exc), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
