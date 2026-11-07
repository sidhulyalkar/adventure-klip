#!/usr/bin/env python3
"""Audit an *offline* Klip pull-request graph. Does NOT merge or mutate anything.

The snapshot records mergeability as last observed by GitHub but not human
approval, checks, current branch permissions or main-branch merge status.
No code is approved or automatically merged by this diagnostic.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path


def audit(snapshot: dict) -> dict:
    if snapshot.get("version") != "klip-mainline-pr-snapshot-v1":
        raise ValueError("Unrecognized PR snapshot schema")
    rows = snapshot.get("records")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Missing PR rows")
    by_number = {}
    by_head = {}
    for p in rows:
        if not isinstance(p, dict):
            raise ValueError("Invalid PR metadata")
        number = p.get("number")
        head = p.get("head")
        base = p.get("base")
        if (type(number) is not int or number < 1 or
                type(head) is not str or not head or
                type(base) is not str or not base or
                type(p.get("draft")) is not bool or
                type(p.get("mergeable")) is not bool or
                type(p.get("merged")) is not bool):
            raise ValueError("Incomplete PR metadata")
        if number in by_number or head in by_head:
            raise ValueError("Duplicate PR number or head ref")
        by_number[number] = p
        by_head[head] = number
    parents = {}
    children = defaultdict(list)
    dangling = []
    for number, p in by_number.items():
        parent = None if p["base"] == "main" else by_head.get(p["base"])
        parents[number] = parent
        if parent is None and p["base"] != "main":
            dangling.append(number)
        if parent is not None:
            children[parent].append(number)
    if dangling:
        raise ValueError(f"Base branch missing from snapshot for PRs {sorted(dangling)}")

    depth = {}
    visiting = set()
    def visit(n: int) -> int:
        if n in depth:
            return depth[n]
        if n in visiting:
            raise ValueError(f"Cycle in PR branch dependency graph at #{n}")
        visiting.add(n)
        parent = parents[n]
        depth[n] = 0 if parent is None else 1 + visit(parent)
        visiting.remove(n)
        return depth[n]
    for number in sorted(by_number):
        visit(number)
    order = sorted(by_number, key=lambda n: (depth[n], n))
    ancestors = {}
    for n in order:
        parent = parents[n]
        ancestors[n] = ([] if parent is None else [*ancestors[parent], parent])

    # Checks and formal review approval are NOT available in this
    # metadata snapshot. The only candidates to inspect next are direct
    # main PRs that GitHub called mergeable and are not drafts.
    direct_candidates = sorted(
        n for n, p in by_number.items()
        if parents[n] is None and not p["draft"] and p["mergeable"]
    )
    counts = Counter()
    for p in rows:
        counts["draft" if p["draft"] else "nonDraft"] += 1
        counts["mergeable" if p["mergeable"] else "conflicted"] += 1
    return {
        "totalOpen": len(rows),
        "draft": counts["draft"],
        "nonDraft": counts["nonDraft"],
        "mergeableAsReported": counts["mergeable"],
        "conflictedAsReported": counts["conflicted"],
        "mainRootPRs": sorted(n for n in by_number if parents[n] is None),
        "candidateForHumanMergeReview": direct_candidates,
        "conflictedPRs": sorted(n for n, p in by_number.items() if not p["mergeable"]),
        "forkPoints": [
            {"number": n, "children": sorted(children[n])}
            for n in sorted(children) if len(children[n]) > 1
        ],
        "leafPRs": sorted(n for n in by_number if n not in children),
        "topologicalOrder": order,
        "dependencies": {
            str(n): {
                "parentPR": parents[n],
                "depth": depth[n],
                "draft": by_number[n]["draft"],
                "mergeable": by_number[n]["mergeable"],
                "requiresAncestorMerge": ancestors[n],
                "readyForAutomaticMerge": False,
            }
            for n in order
        },
        "safety": {
            "updatesGithub": False,
            "mergeActionsPerformed": 0,
            "allActualReviewApprovalsKnown": False,
            "checksAreCurrent": False,
            "branchHeadsAreRevalidatedNow": False,
            "humanApprovalRequired": True,
        },
        "note": (
            "A non-draft/mergeable PR is not necessarily approved or safe to "
            "merge. Refresh all GitHub statuses and reviews, test the intended "
            "target base, and merge through supported GitHub UI in dependency order."
        ),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        snapshot = json.loads(args.snapshot.read_text())
        report = audit(snapshot)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(3, f"CONSOLIDATION AUDIT BLOCKED: {exc}\n")
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Open: {report['totalOpen']} | non-draft: {report['nonDraft']} | "
              f"draft: {report['draft']} | conflicts: {report['conflictedAsReported']}")
        print(f"PRs targeting main: {report['mainRootPRs']}")
        print(f"First candidates for HUMAN review only: "
              f"{report['candidateForHumanMergeReview']}")
        print(f"Conflicted PRs: {report['conflictedPRs']}")
        print(f"Fork points: {report['forkPoints']}")
        print(f"Leaves: {report['leafPRs']}")
        print("No approval or merge inferred. Snapshot can become stale.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
