#!/usr/bin/env python3
"""Read-only measured ETA for an active Klip source-video campaign.

Reads only workspace audit, checkpoint and SHA-receipted per-source JSON.
Never stats, opens, hashes or decodes original video files.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta
from hashlib import sha256
import json
import math
from pathlib import Path
from statistics import median
import sys


def _load(path: Path) -> tuple[dict, bytes]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Missing or symlinked report: {path}")
    raw = path.read_bytes()
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError(f"Invalid JSON object: {path}")
    return result, raw


def _quantile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    pos = (len(ordered) - 1) * fraction
    i = int(pos)
    return ordered[i] + (ordered[min(i + 1, len(ordered) - 1)] - ordered[i]) * (pos - i)


def _clock(seconds: float | None) -> str:
    if seconds is None:
        return "unavailable"
    count = max(0, int(round(seconds)))
    days, count = divmod(count, 86400)
    hours, count = divmod(count, 3600)
    minutes, _ = divmod(count, 60)
    return (f"{days}d " if days else "") + f"{hours}h {minutes:02d}m"


def campaign_eta(campaign_dir: Path, now: datetime | None = None) -> dict:
    """Forecast remaining automatic attempts; failures require separate triage."""
    root = Path(campaign_dir).expanduser()
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Campaign directory missing or symlinked")
    audit, audit_bytes = _load(root / "audit" / "campaign-audit.json")
    state, _ = _load(root / "coarse-scan" / "scan-state.json")
    if state.get("campaignAuditSha256") != sha256(audit_bytes).hexdigest():
        raise ValueError("Scan state does not match frozen campaign audit")
    assets = audit.get("assets")
    if not isinstance(assets, list) or len(assets) != audit.get("catalogAssetCount"):
        raise ValueError("Invalid frozen catalog assets")
    ids = [a["sourceAssetSha256"] for a in assets]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate source identities in audit")
    known = state.get("assets")
    if not isinstance(known, dict) or not set(known).issubset(ids):
        raise ValueError("Scan checkpoint contains invalid assets")

    pending = []
    counts = Counter()
    sha_rates, decode_rates, overheads = [], [], []
    for item in assets:
        aid = item["sourceAssetSha256"]
        entry = known.get(aid) or {}
        status = entry.get("status")
        if status == "coarse_completed":
            report_path = root / "coarse-scan" / "per-source" / (aid + ".json")
            try:
                report, body = _load(report_path)
                if (
                    sha256(body).hexdigest() != entry.get("reportSha256")
                    or report.get("verifiedOriginalSha256") != aid
                    or report.get("sourceAssetSha256") != aid
                    or report.get("shaVerifiedDuringThisScan") is not True
                    or report.get("sourceDurationMs") != item.get("durationMsFromCatalog")
                    or report.get("originalSizeBytes") != item.get("sourceBytesFromCatalog")
                    or not isinstance(report.get("candidateHypotheses"), list)
                ):
                    raise ValueError("Receipt/source mismatch")
                counts["completed"] += 1
                size = report.get("originalSizeBytes")
                duration = report.get("sourceDurationMs")
                sha_s = report.get("measuredSourceShaSeconds")
                decoder_s = report.get("measuredDecoderWallSeconds")
                values = (size, duration, sha_s, decoder_s)
                if all(type(v) in (float, int) and math.isfinite(v) and v > 0 for v in values):
                    sha_rates.append((size / 1048576) / sha_s)
                    decode_rates.append((duration / 1000) / decoder_s)
                    elapsed = entry.get("attemptWallSeconds")
                    if type(elapsed) in (float, int) and math.isfinite(elapsed):
                        overheads.append(max(0, elapsed - sha_s - decoder_s))
            except (OSError, ValueError, KeyError, TypeError, OverflowError):
                counts["invalidReceipts"] += 1
        elif status == "failed":
            counts["failed"] += 1
        elif status == "blocked_missing_local_media":
            counts["blocked"] += 1
        elif status is None:
            pending.append(item)
        else:
            raise ValueError(f"Unrecognized source status for {aid}: {status}")

    measured = min(len(sha_rates), len(decode_rates))
    estimate = optimistic = pessimistic = None
    if not pending:
        estimate = optimistic = pessimistic = 0.0
    elif measured >= 3:
        remaining_mib = sum(a.get("sourceBytesFromCatalog") or 0 for a in pending) / 1048576
        remaining_duration_s = sum(a.get("durationMsFromCatalog") or 0 for a in pending) / 1000
        if remaining_mib > 0 and remaining_duration_s > 0:
            overhead = median(overheads) if overheads else 0.0
            def predict(quantile):
                sha_rate = _quantile(sha_rates, quantile)
                decoder_rate = _quantile(decode_rates, quantile)
                return (remaining_mib / sha_rate + remaining_duration_s / decoder_rate
                        + len(pending) * overhead)
            estimate = predict(.5)
            optimistic = predict(.75)
            pessimistic = predict(.25)

    now = now or datetime.now().astimezone()
    eta = (now + timedelta(seconds=estimate)).isoformat(timespec="minutes") if estimate is not None else None
    total = len(assets)
    attempted = total - len(pending)
    return {
        "catalog": total,
        "completed": counts["completed"],
        "failed": counts["failed"],
        "blocked": counts["blocked"],
        "invalidReceipts": counts["invalidReceipts"],
        "pending": len(pending),
        "attempted": attempted,
        "completedPct": round(counts["completed"] * 100 / total, 1) if total else 0.0,
        "attemptedPct": round(attempted * 100 / total, 1) if total else 0.0,
        "measuredSources": measured,
        "remainingSecondsEstimate": round(estimate) if estimate is not None else None,
        "remainingSecondsFast": round(optimistic) if optimistic is not None else None,
        "remainingSecondsSlow": round(pessimistic) if pessimistic is not None else None,
        "etaLocal": eta,
        "sourceMediaRead": False,
        "note": "Forecasts only remaining automatic source attempts. Heuristic range is not a confidence interval. Existing failures require separate investigation.",
    }


def format_progress(data: dict) -> str:
    head = (
        f"[klip-eta] completed {data['completed']}/{data['catalog']} ({data['completedPct']}%)"
        f" | attempted {data['attempted']}/{data['catalog']} ({data['attemptedPct']}%)"
        f" | pending {data['pending']}"
        f" | failed {data['failed']}, blocked {data['blocked']},"
        f" bad receipts {data['invalidReceipts']}"
    )
    if data["remainingSecondsEstimate"] is None:
        return head + f" | ETA unavailable ({data['measuredSources']} measured sources; need 3)"
    return (
        head
        + f" | remaining ~{_clock(data['remainingSecondsEstimate'])}"
        + f" (heuristic {_clock(data['remainingSecondsFast'])} to {_clock(data['remainingSecondsSlow'])})"
        + f" | projected local finish {data['etaLocal']}"
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-dir", required=True, type=Path)
    parser.add_argument("--json", action="store_true", help="Machine-readable progress/ETA")
    args = parser.parse_args(argv)
    try:
        result = campaign_eta(args.campaign_dir)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"[klip-eta] unavailable: {exc}", file=sys.stderr)
        return 3
    print(json.dumps(result, indent=2, sort_keys=True) if args.json
          else format_progress(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
