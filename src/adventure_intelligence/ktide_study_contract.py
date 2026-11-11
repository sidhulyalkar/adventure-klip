"""K-TIDE held-out study: frozen, blind cross-trip human relevance judgments.

Pure JSON contracts and deterministic sampling only. No model inference, media
reads, upload or guessed labels. Any unreviewed candidate remains UNKNOWN.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
import random

from .encoder_dataset import group_splits, load_manifest, validate_manifest

VERSION = "klip-ktide-blind-retrieval-study-v1"
TASKS = {"scene_situation", "temporal_event", "editorial_context"}
K_LEVELS = (1, 3, 5, 10)
MAX_QUERIES = 500
MAX_CANDIDATES = 100


def load_json(path: Path, *, max_bytes: int = 32_000_000) -> tuple[dict, str]:
    if (path.is_symlink() or not path.is_file()
            or not 2 <= path.stat().st_size <= max_bytes):
        raise ValueError("Missing, symlinked, or oversized local study JSON")
    content = path.read_bytes()
    try:
        parsed = json.loads(content)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Malformed study JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Study JSON must be an object")
    return parsed, sha256(content).hexdigest()


def validate_training_lineage(training: dict, manifest: dict,
                              manifest_sha: str) -> dict[str, str]:
    """Require the *exact* training split and dataset lineage to be frozen."""
    if (not isinstance(training, dict)
            or training.get("version") != "klip-ktide-training-run-v1"
            or training.get("datasetManifestSha256") != manifest_sha
            or training.get("teacherModelKey") != manifest["featureModelKey"]
            or training.get("testNotUsedForSelection") is not True
            or training.get("featureCacheOnly") is not True
            or training.get("rawMediaRead") is not False):
        raise ValueError("Unqualified or mismatched K-TIDE training provenance")
    seed = training.get("seed")
    if type(seed) is not int:
        raise ValueError("Missing split seed")
    computed = group_splits(manifest, seed=seed)
    if training.get("splitAssignments") != computed:
        raise ValueError("Frozen training group split mismatch")
    checkpoint = training.get("checkpointSha256")
    if (not isinstance(checkpoint, str) or len(checkpoint) != 64
            or any(c not in "0123456789abcdef" for c in checkpoint)):
        raise ValueError("Missing checkpoint identity")
    if training.get("humanPreferenceRankingQualified") is not False:
        raise ValueError("Do not treat SSL as validated human preference prediction")
    return computed


def build_blind_deck(manifest: dict, manifest_sha: str, training: dict, *,
                     max_queries: int = 60, candidates_per_query: int = 10,
                     seed: int = 31, task: str = "scene_situation") -> dict:
    """Model-independent, source/trip disjoint, deterministic blind query deck.

    Candidate selection is random with a same-subsport preference, NEVER
    nearest-neighbor or editorial score based. Do not auto-assign relevance.
    """
    validate_manifest(manifest)
    splits = validate_training_lineage(training, manifest, manifest_sha)
    if (type(max_queries) is not int or not 2 <= max_queries <= MAX_QUERIES
            or type(candidates_per_query) is not int
            or not 3 <= candidates_per_query <= MAX_CANDIDATES
            or type(seed) is not int or not 0 <= seed < 2**32
            or task not in TASKS):
        raise ValueError("Invalid blind-deck design parameters")
    available = [row for row in manifest["samples"]
                 if splits[row["groupId"]] == "test"]
    groups = defaultdict(list)
    for row in available:
        groups[row["groupId"]].append(row)
    if len(groups) < 2:
        raise ValueError("Cross-trip evaluation requires >=2 held-out test trips; add more outings")
    rng = random.Random(seed)
    # Interleave trips so one long outing cannot dominate the query roster.
    by_group = {name: sorted(rows, key=lambda r: r["sampleId"])
                for name, rows in groups.items()}
    for rows in by_group.values():
        rng.shuffle(rows)
    group_names = sorted(by_group)
    rng.shuffle(group_names)
    roster = []
    index = 0
    while len(roster) < max_queries:
        active = False
        for group in group_names:
            if index < len(by_group[group]) and len(roster) < max_queries:
                roster.append(by_group[group][index])
                active = True
        if not active:
            break
        index += 1
    queries = []
    for row in roster:
        pool = [other for other in available
                if other["groupId"] != row["groupId"]
                and other["sourceSha256"] != row["sourceSha256"]]
        if len(pool) < candidates_per_query:
            continue
        same = [x for x in pool if x["subsport"] == row["subsport"]]
        other = [x for x in pool if x["subsport"] != row["subsport"]]
        rng.shuffle(same)
        rng.shuffle(other)
        options = (same + other)[:candidates_per_query]
        rng.shuffle(options)
        queries.append({
            "querySampleId": row["sampleId"], "task": task,
            "candidates": [
                {"sampleId": candidate["sampleId"], "relevance": None,
                 "adjudication": "unreviewed", "evidenceIds": []}
                for candidate in options
            ],
        })
    if len(queries) < 2 or len({next(r for r in available
        if r["sampleId"] == q["querySampleId"])["groupId"] for q in queries}) < 2:
        raise ValueError("Insufficient independent test-trip candidates for blind study")
    return {
        "version": VERSION, "datasetManifestSha256": manifest_sha,
        "splitSeed": training["seed"], "selectionSeed": seed,
        "protocol": "cross_trip_test_only",
        "queries": queries, "scoresExposedDuringAnnotation": False,
        "labelsGeneratedByModel": False,
    }


def validate_deck(deck: dict, manifest: dict, manifest_sha: str,
                  training: dict, *, require_complete: bool = True) -> dict:
    split = validate_training_lineage(training, manifest, manifest_sha)
    if (not isinstance(deck, dict)
            or set(deck) != {
                "version", "datasetManifestSha256", "splitSeed", "selectionSeed",
                "protocol", "queries", "scoresExposedDuringAnnotation",
                "labelsGeneratedByModel",
            } or deck["version"] != VERSION
            or deck["datasetManifestSha256"] != manifest_sha
            or deck["splitSeed"] != training["seed"]
            or deck["protocol"] != "cross_trip_test_only"
            or deck["scoresExposedDuringAnnotation"] is not False
            or deck["labelsGeneratedByModel"] is not False
            or type(deck["selectionSeed"]) is not int):
        raise ValueError("Unrecognized or unblinded study deck")
    samples = {r["sampleId"]: r for r in manifest["samples"]}
    queries = deck["queries"]
    if not isinstance(queries, list) or not 1 <= len(queries) <= MAX_QUERIES:
        raise ValueError("Invalid query roster")
    seen_queries, seen_groups = set(), set()
    totals = Counter()
    answerability = Counter()
    for q in queries:
        if not isinstance(q, dict) or set(q) != {"querySampleId", "task", "candidates"}:
            raise ValueError("Invalid study query")
        qid = q["querySampleId"]
        if (not isinstance(qid, str) or qid not in samples
                or qid in seen_queries or q["task"] not in TASKS):
            raise ValueError("Invalid/duplicate query sample")
        seen_queries.add(qid)
        base = samples[qid]
        seen_groups.add(base["groupId"])
        if split[base["groupId"]] != "test":
            raise ValueError("Query would leak train/validation data")
        options = q["candidates"]
        if not isinstance(options, list) or not 3 <= len(options) <= MAX_CANDIDATES:
            raise ValueError("Each query needs 3..100 independently judged candidates")
        seen_options, positive, negative = set(), 0, 0
        for option in options:
            if (not isinstance(option, dict) or set(option) != {
                    "sampleId", "relevance", "adjudication", "evidenceIds"}):
                raise ValueError("Invalid candidate annotation")
            cid = option["sampleId"]
            if (not isinstance(cid, str) or cid not in samples
                    or cid in seen_options or cid == qid):
                raise ValueError("Duplicate or missing candidate")
            seen_options.add(cid)
            candidate = samples[cid]
            if (split[candidate["groupId"]] != "test"
                    or candidate["groupId"] == base["groupId"]
                    or candidate["sourceSha256"] == base["sourceSha256"]):
                raise ValueError("Candidate crosses split or repeats the query outing")
            label, status = option["relevance"], option["adjudication"]
            ids = option["evidenceIds"]
            if (status not in {"unreviewed", "uncertain", "confirmed"}
                    or not isinstance(ids, list)
                    or any(not isinstance(x, str) or not x.strip()
                           or len(x) > 200 for x in ids)):
                raise ValueError("Malformed adjudication provenance")
            if status == "confirmed":
                if type(label) is not int or label not in (0, 1, 2, 3) or not ids:
                    raise ValueError("Confirmed relevance requires a 0..3 rating and evidence IDs")
                positive += label >= 2
                negative += label <= 1
                totals["confirmed"] += 1
            elif label is not None:
                raise ValueError("Unreviewed/uncertain relevance MUST be null")
            else:
                totals[status] += 1
        if all(o["adjudication"] == "confirmed" for o in options):
            if positive == 0:
                answerability["no_match"] += 1
            elif negative == 0:
                answerability["all_positive"] += 1
            else:
                answerability["mixed_relevance"] += 1
        else:
            answerability["incomplete"] += 1
            if require_complete:
                raise ValueError("Incomplete query: every candidate needs a human-adjudicated grade")
    return {
        "queryCount": len(queries), "heldOutQueryGroups": len(seen_groups),
        "candidateJudgments": dict(sorted(totals.items())),
        "queryAnswerability": dict(sorted(answerability.items())),
        "fullyAdjudicated": totals["unreviewed"] == totals["uncertain"] == 0,
        "metricsEligible": (totals["unreviewed"] == totals["uncertain"] == 0
                            and answerability["mixed_relevance"] > 0),
    }


def validate_embeddings(report: dict, manifest: dict, training: dict) -> dict:
    """Bind learned and baseline vectors to every exact immutable sample ID."""
    if (not isinstance(report, dict)
            or report.get("version") != "klip-ktide-embedding-report-v1"
            or report.get("modelVersion") != training.get("modelVersion")
            or report.get("checkpointSha256") != training.get("checkpointSha256")
            or report.get("featureModelKey") != manifest["featureModelKey"]
            or report.get("mediaRead") is not False
            or report.get("eventClaimsVerified") is not False):
        raise ValueError("Embedding lineage mismatch")
    rows = report.get("results")
    if not isinstance(rows, list) or report.get("numSamples") != len(rows):
        raise ValueError("Invalid embedding row count")
    by_id = {r["sampleId"]: r for r in manifest["samples"]}
    if len(rows) != len(by_id):
        raise ValueError("Missing or duplicated embedding roster")
    found = {}
    dims = {}
    for item in rows:
        if not isinstance(item, dict) or set(item) - {
                "sampleId", "sourceSha256", "groupId",
                "ktideEmbedding", "meanTeacherEmbedding",
                "temporalChangeEmbedding", "untrainedEmbedding"}:
            raise ValueError("Invalid embedding result")
        sid = item.get("sampleId")
        if not isinstance(sid, str) or sid not in by_id or sid in found:
            raise ValueError("Unknown/duplicate embedding sample")
        expected = by_id[sid]
        if item.get("sourceSha256") != expected["sourceSha256"] or item.get("groupId") != expected["groupId"]:
            raise ValueError("Embedding sample provenance mismatch")
        for method in ("ktideEmbedding", "meanTeacherEmbedding"):
            vec = item.get(method)
            if (not isinstance(vec, list) or not 8 <= len(vec) <= 8192
                    or any(isinstance(x, bool) or not isinstance(x, (int, float))
                           or not isfinite(x) for x in vec)):
                raise ValueError("Malformed or missing evaluation embedding: " + method)
            dim = dims.setdefault(method, len(vec))
            if len(vec) != dim or sum(float(x)**2 for x in vec) < 1e-12:
                raise ValueError("Degenerate or dimension-inconsistent embedding")
        for optional in ("untrainedEmbedding", "temporalChangeEmbedding"):
            if optional not in item or item[optional] is None:
                continue
            vec = item[optional]
            if (not isinstance(vec, list) or not 8 <= len(vec) <= 8192
                    or any(isinstance(x, bool) or not isinstance(x, (int, float))
                           or not isfinite(x) for x in vec)
                    or sum(float(x)**2 for x in vec) < 1e-12):
                raise ValueError("Invalid optional control embedding: " + optional)
            dimension = dims.setdefault(optional, len(vec))
            if len(vec) != dimension:
                raise ValueError("Mixed optional control dimensions")
        found[sid] = item
    return found



def review_worksheet(deck: dict, manifest: dict, manifest_sha: str,
                     training: dict) -> dict:
    """Render timestamp locator information for a private blind review session.

    This is not a video player. The human must use an independently verified
    local preview/source reader before assigning evidence-backed grades.
    """
    summary = validate_deck(deck, manifest, manifest_sha, training,
                            require_complete=False)
    by_id = {r["sampleId"]: r for r in manifest["samples"]}
    def locator(sample_id):
        row = by_id[sample_id]
        return {
            "sampleId": sample_id,
            "sourceSha256": row["sourceSha256"],
            "startMs": row["startMs"],
            "endMs": row["endMs"],
            "subsportDeclaredByOwner": row["subsport"],
        }
    return {
        "version": "klip-ktide-private-review-worksheet-v1",
        "studyVersion": VERSION,
        "judgmentCoverage": summary,
        "queryCards": [
            {
                "query": locator(q["querySampleId"]),
                "task": q["task"],
                "candidates": [
                    {**locator(c["sampleId"]),
                     "humanRelevance": c["relevance"],
                     "adjudication": c["adjudication"]}
                    for c in q["candidates"]
                ],
            }
            for q in deck["queries"]
        ],
        "modelScoresHidden": True,
        "previewPlaybackAndReceiptVerificationPerformed": False,
        "originalVideoRead": False,
    }
