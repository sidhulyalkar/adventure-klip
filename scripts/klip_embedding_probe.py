#!/usr/bin/env python3
"""K-MOMENT dense-feature embedding probe (offline, unlabeled).

Produces deterministic retrieval candidates and temporal-corruption controls
from previously SHA-verified K-MOMENT derived embeddings. This script neither
trains a model nor measures human ranking accuracy. It never opens camera video.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path

import numpy as np


def unit(vector: np.ndarray) -> np.ndarray | None:
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(norm) or norm <= 1e-8:
        return None
    return vector / norm


def representations(frames: np.ndarray) -> dict[str, np.ndarray | None]:
    """Equal-input static, time-weighted, and appearance-change controls."""
    if frames.ndim != 2 or len(frames) < 4 or not np.isfinite(frames).all():
        raise ValueError('Invalid frame-feature sequence')
    count = len(frames)
    weights = np.linspace(0.5, 1.5, count, dtype=np.float64)
    differences = np.diff(frames, axis=0)
    return {
        'frozen_mean': unit(np.mean(frames, axis=0)),
        'linear_time_weighted': unit(np.average(frames, axis=0, weights=weights)),
        'mean_absolute_delta': unit(np.mean(np.abs(differences), axis=0)),
    }


def distance(left: np.ndarray | None, right: np.ndarray | None) -> float | None:
    if left is None or right is None:
        return None
    return round(float(np.clip(1 - np.dot(left, right), 0.0, 2.0)), 8)


def inspect_sequence(clip: dict, *, seed: int) -> dict:
    frames = np.asarray(clip['vectors'], dtype=np.float64)
    times = np.asarray(clip['times'], dtype=np.int64)
    if len(times) != len(frames) or not np.all(np.diff(times) > 0):
        raise ValueError('Inconsistent frame/time sequence')
    gaps = np.diff(times)
    if np.any(gaps > 7000):
        raise ValueError('Dense feature sequence crosses unsupported time gaps')
    variants = representations(frames)
    reverse = representations(frames[::-1])
    stable_seed = int.from_bytes(sha256((str(seed)+clip['clipId']).encode()).digest()[:8], 'big')
    shuffled = representations(frames[np.random.default_rng(stable_seed).permutation(len(frames))])
    adjacent = np.clip(1 - np.sum(frames[:-1] * frames[1:], axis=1), 0.0, 2.0)
    return {
        'clipId': clip['clipId'],
        'sourceSha256': clip['sourceSha256'],
        'frameCount': len(frames),
        'connectedPairs': int(np.sum(gaps <= 7000)),
        'meanAdjacentCosineDistance': round(float(adjacent.mean()), 8),
        'maxAdjacentCosineDistance': round(float(adjacent.max()), 8),
        'reversalDistanceByMethod': {
            name: distance(vec, reverse[name]) for name, vec in variants.items()
        },
        'shuffleDistanceByMethod': {
            name: distance(vec, shuffled[name]) for name, vec in variants.items()
        },
    }


def retrieval(clip_data: list[dict], vectors: list[dict], *, top_k: int = 3) -> dict:
    """Rank *different-source* candidates; these are unjudged, not relevance scores."""
    methods = ('frozen_mean', 'linear_time_weighted', 'mean_absolute_delta')
    report = {}
    for method in methods:
        queries = []
        for idx, clip in enumerate(clip_data):
            query = vectors[idx][method]
            matches = []
            if query is not None:
                for j, other in enumerate(clip_data):
                    if j == idx or other['sourceSha256'] == clip['sourceSha256']:
                        continue
                    candidate = vectors[j][method]
                    if candidate is None:
                        continue
                    similarity = float(np.dot(query, candidate))
                    matches.append((similarity, other['clipId'], other['sourceSha256']))
            matches.sort(key=lambda x: (-x[0], x[1]))
            queries.append({
                'queryClipId': clip['clipId'], 'sourceSha256': clip['sourceSha256'],
                'candidateCount': len(matches),
                'topUnjudgedCandidates': [
                    {'clipId': cid, 'sourceSha256': source,
                     'cosineSimilarity': round(score, 8)}
                    for score, cid, source in matches[:top_k]
                ],
            })
        report[method] = queries
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--qa-name', default='kmoment-qa-siglip2-001')
    parser.add_argument('--decode-name', default='kmoment-dense-pilot-001')
    parser.add_argument('--seed', default=31, type=int)
    parser.add_argument('--top-k', default=3, type=int)
    args = parser.parse_args()
    if not 0 <= args.seed < 2**32 or not 1 <= args.top_k <= 20:
        parser.error('Invalid seed or top-k')
    from adventure_intelligence.encoder_dataset import safe_training_workspace
    from klip_dense_ktide_bridge import checked_qa
    root = safe_training_workspace(args.workspace.expanduser())
    plan = args.plan or (root/'reports/kmoment-dense-inspection-plan-v1.json')
    key, dim, clips, plan_sha = checked_qa(root, plan, args.decode_name, args.qa_name)
    embeddings = [representations(np.asarray(c['vectors'], dtype=np.float64)) for c in clips]
    report = {
        'version': 'klip-embedding-probe-v1',
        'purpose': 'unlabeled_retrieval_and_order_diagnostics_only',
        'sourceMediaRead': False, 'modelTrainingPerformed': False,
        'rankingQualityMeasured': False, 'tripIndependenceUnverified': True,
        'modelKey': key, 'embeddingDim': dim, 'planSha256': plan_sha,
        'seed': args.seed, 'clips': len(clips),
        'uniqueSources': len({c['sourceSha256'] for c in clips}),
        'sequenceDiagnostics': [inspect_sequence(c, seed=args.seed) for c in clips],
        'crossSourceRetrieval': retrieval(clips, embeddings, top_k=args.top_k),
    }
    print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
