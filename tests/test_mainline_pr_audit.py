"""Offline PR graph audit tests. No GitHub connection, media, or merge required."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


PATH = Path(__file__).resolve().parents[1] / "scripts" / "klip_mainline_pr_audit.py"
SPEC = importlib.util.spec_from_file_location("klip_mainline_pr_audit", PATH)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def snapshot(rows):
    return {"version":"klip-mainline-pr-snapshot-v1", "records":rows}


def pr(number, base, head, draft=False, mergeable=True):
    return {"number":number, "base":base, "head":head,
            "draft":draft, "mergeable":mergeable, "merged":False}


def test_dependency_graph_distinguishes_draft_and_merge_conflicts():
    graph=snapshot([
        pr(2,"main","a"),
        pr(3,"a","b",mergeable=False),
        pr(4,"b","c"),
        pr(5,"b","d",draft=True),
        pr(28,"main","e",mergeable=False),
    ])
    result=mod.audit(graph)
    assert result["totalOpen"]==5
    assert result["draft"]==1
    assert result["nonDraft"]==4
    assert result["conflictedPRs"]==[3,28]
    assert result["mainRootPRs"]==[2,28]
    assert result["candidateForHumanMergeReview"]==[2]
    assert result["forkPoints"]==[{"number":3,"children":[4,5]}]
    assert result["dependencies"]["4"]["requiresAncestorMerge"]==[2,3]
    assert not any(x["readyForAutomaticMerge"]
                   for x in result["dependencies"].values())
    assert result["safety"]["mergeActionsPerformed"]==0


def test_out_of_order_rows_are_topologically_sorted():
    graph=snapshot([pr(7,"c","d"),pr(2,"main","a"),
                    pr(4,"b","c"),pr(3,"a","b")])
    result=mod.audit(graph)
    assert result["topologicalOrder"]==[2,3,4,7]


def test_missing_open_parent_rejected_for_snapshot():
    with pytest.raises(ValueError,match="Base branch missing"):
        mod.audit(snapshot([pr(3,"unknown","child")]))


def test_duplicate_branch_and_numbers_fail_closed():
    with pytest.raises(ValueError,match="Duplicate"):
        mod.audit(snapshot([pr(2,"main","a"),pr(3,"main","a")]))
    with pytest.raises(ValueError,match="Duplicate"):
        mod.audit(snapshot([pr(2,"main","a"),pr(2,"main","b")]))


def test_cycles_fail_closed():
    with pytest.raises(ValueError,match="Cycle"):
        mod.audit(snapshot([pr(2,"b","a"),pr(3,"a","b")]))


def test_draft_on_main_is_not_auto_eligible():
    report=mod.audit(snapshot([pr(8,"main","research",draft=True)]))
    assert report["candidateForHumanMergeReview"]==[]


def test_no_approval_field_is_synthesized():
    report=mod.audit(snapshot([pr(2,"main","safe")]))
    assert report["candidateForHumanMergeReview"]==[2]
    assert report["safety"]["allActualReviewApprovalsKnown"] is False
    assert not report["dependencies"]["2"]["readyForAutomaticMerge"]
