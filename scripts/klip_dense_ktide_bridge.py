#!/usr/bin/env python3
"""Read-only audit / explicitly grouped, receipt-verified K-MOMENT -> K-TIDE bridge.

Run from the Adventure-Klip repo's active virtualenv. This utility never reads
original camera media. audit/template emit JSON to stdout; export writes a
NEW private dataset beneath workspace/reports. No catalog changes.
"""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
import math
from pathlib import Path
import statistics

from adventure_intelligence.moment_dense_qa import _read_verified_clips
from adventure_intelligence.moment_dense_decode import _read_plan
from adventure_intelligence.encoder_dataset import (
    KEY, SHA, VERSION, MAX_FRAMES, validate_manifest, safe_training_workspace,
)
from adventure_intelligence.scene_profiles import SUBSPORT_PRIORS
from adventure_intelligence.scene_relevance import GOAL_WEIGHTS


def digest(p: Path) -> str:
    h = sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read_json(p: Path, limit: int) -> dict:
    if p.is_symlink() or not p.is_file() or not 2 <= p.stat().st_size <= limit:
        raise ValueError(f'Missing or unsafe JSON: {p.name}')
    obj = json.loads(p.read_bytes())
    if not isinstance(obj, dict):
        raise ValueError(f'Expected object in {p.name}')
    return obj


def checked_qa(root: Path, plan_path: Path, decode_name: str, qa_name: str):
    if not (root / 'reports').is_dir() or (root / 'reports').is_symlink():
        raise ValueError('Workspace reports directory missing/unsafe')
    plan, plan_sha = _read_plan(root, plan_path)
    decoded = _read_verified_clips(root, plan, plan_sha, decode_name)
    qa_root = root / 'reports' / qa_name
    if qa_root.is_symlink() or not qa_root.is_dir():
        raise ValueError('QA directory missing or symlinked')
    summary = read_json(qa_root / 'summary.json', 1_000_000)
    for k, val in [('backend', 'siglip2'), ('decodeName', decode_name),
                   ('analysisName', qa_name), ('planSha256', plan_sha)]:
        if summary.get(k) != val:
            raise ValueError(f'QA summary {k} mismatch')
    results = []
    model_key, dimensions = None, None
    for item in decoded:
        cid, source = item['clipId'], item['sourceSha256']
        folder = qa_root / f'clip-{cid}' if not str(cid).startswith('clip-') else qa_root / cid
        # K-MOMENT writes under clip-*/; accept exactly the existing clip ID as named.
        if not folder.exists():
            folder = qa_root / cid
        if folder.is_symlink() or not folder.is_dir():
            raise ValueError(f'Missing/unsafe QA clip directory: {cid}')
        analysis_path, feature_path = folder / 'analysis.json', folder / 'frozen_features.json'
        analysis = read_json(analysis_path, 250_000)
        features = read_json(feature_path, 30_000_000)
        if (analysis.get('clipId') != cid or analysis.get('sourceSha256') != source
                or analysis.get('inputReceiptSha256') != item['receiptSha256']
                or analysis.get('backend') != 'siglip2'
                or analysis.get('featuresSha256') != digest(feature_path)):
            raise ValueError(f'Analysis or feature SHA mismatch: {cid}')
        if (features.get('sourceSha256') != source
                or features.get('denseInputReceiptSha256') != item['receiptSha256']
                or features.get('backend') != 'frozen_siglip2'
                or features.get('modelKey') != analysis.get('frozenModelKey')):
            raise ValueError(f'Source/model/receipt mismatch: {cid}')
        observations = features.get('observations')
        receipt_frames = item['receipt']['frames']
        if (not isinstance(observations, list) or len(observations) < 4
                or len(observations) != len(receipt_frames)
                or features.get('frameCount') != len(observations)
                or analysis.get('observedFrames') != len(observations)):
            raise ValueError(f'Invalid frame coverage: {cid}')
        times, vecs = [], []
        for ob, fr in zip(observations, receipt_frames):
            t = ob.get('sampledSourceRelativeMs') if isinstance(ob, dict) else None
            v = ob.get('embedding') if isinstance(ob, dict) else None
            if (type(t) is not int or t != fr.get('sampledFilterSourceRelativeMs')
                    or ob.get('imageSha256') != fr.get('sha256')
                    or not isinstance(v, list) or not 8 <= len(v) <= 8192
                    or any(type(x) not in (int, float) or not math.isfinite(x) for x in v)):
                raise ValueError(f'Mismatched timestamp/image/vector: {cid}')
            norm = math.sqrt(sum(x*x for x in v))
            if not norm > 1e-8:
                raise ValueError(f'Degenerate vector: {cid}')
            times.append(t)
            vecs.append([x/norm for x in v])
        if times != sorted(set(times)) or times[-1] >= 86_400_000:
            raise ValueError(f'Invalid/duplicate timestamps: {cid}')
        d = len(vecs[0])
        if any(len(x) != d for x in vecs):
            raise ValueError(f'Mixed vector widths in {cid}')
        k = features['modelKey']
        if not isinstance(k, str) or not KEY.fullmatch(k):
            raise ValueError(f'Invalid model key: {cid}')
        if model_key is None:
            model_key, dimensions = k, d
        if k != model_key or d != dimensions:
            raise ValueError('Dense clips use incompatible frozen model keys/dimensions')
        gaps = [b-a for a,b in zip(times, times[1:])]
        connected = sum(0 < gap <= 7000 for gap in gaps)
        runs = []
        run = 1
        for gap in gaps:
            if 0 < gap <= 7000:
                run += 1
            else:
                runs.append(run)
                run = 1
        runs.append(run)
        results.append({
            'clipId': cid, 'sourceSha256': source,
            'startMs': times[0], 'endMs': times[-1] + 1,
            'frames': len(times), 'vectorDimensions': d,
            'connectedPairs': connected, 'adjacentPairs': len(gaps),
            'medianGapMs': statistics.median(gaps) if gaps else None,
            'largestConnectedRun': max(runs),
            'hasLocalOrderEvidence': max(runs) >= 3,
            'inputReceiptSha256': item['receiptSha256'],
            'featuresSha256': digest(feature_path),
            'analysisSha256': digest(analysis_path),
            'times': times, 'vectors': vecs,
        })
    return model_key, dimensions, results, plan_sha


def public_summary(model_key: str, dimensions: int, clips: list[dict], plan_sha: str):
    groups = Counter(c['sourceSha256'] for c in clips)
    return {
        'status': 'verified_derived_features_not_native_camera_pts',
        'planSha256': plan_sha, 'modelKey': model_key,
        'vectorDimensions': dimensions,
        'clips': len(clips), 'distinctSources': len(groups),
        'totalFrames': sum(c['frames'] for c in clips),
        'connectedPairs': sum(c['connectedPairs'] for c in clips),
        'adjacentPairs': sum(c['adjacentPairs'] for c in clips),
        'windowsWithLocalOrderEvidence': sum(c['hasLocalOrderEvidence'] for c in clips),
        'clipReports': [
            {k: v for k, v in c.items() if k not in ('vectors', 'times')}
            for c in clips
        ],
        'sourceVideoRead': False, 'rankingImprovementMeasured': False,
    }


def assignments_template(clips: list[dict]):
    return {'version': 'klip-ktide-source-assignments-v1', 'sources': [
        {'sourceSha256': sha, 'groupId': '', 'goal': '', 'subsport': ''}
        for sha in sorted({c['sourceSha256'] for c in clips})
    ]}


def validate_assignments(path: Path, clips: list[dict]) -> dict:
    doc = read_json(path, 2_000_000)
    if doc.get('version') != 'klip-ktide-source-assignments-v1':
        raise ValueError('Wrong assignments schema')
    rows = doc.get('sources')
    if not isinstance(rows, list):
        raise ValueError('Missing sources array')
    found = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'sourceSha256','groupId','goal','subsport'}:
            raise ValueError('Unexpected assignment fields')
        sha, group = row['sourceSha256'], row['groupId']
        if (not isinstance(sha, str) or not SHA.fullmatch(sha)
                or sha in found or not isinstance(group, str) or not KEY.fullmatch(group)
                or group.startswith('provisional_')
                or row['goal'] not in GOAL_WEIGHTS
                or row['subsport'] not in SUBSPORT_PRIORS):
            raise ValueError('Missing/invalid assignment; manually verify trips and profiles')
        found[sha] = row
    needed = {c['sourceSha256'] for c in clips}
    if set(found) != needed:
        raise ValueError('Assignment roster differs from verified QA source roster')
    if len({v['groupId'] for v in found.values()}) < 3:
        raise ValueError('At least 3 independently verified trip groups required')
    return found


def export(root: Path, out_name: str, clips: list[dict], model_key: str, dims: int,
           assignment_path: Path):
    import re
    import numpy as np
    if not re.fullmatch(r'ktide-[A-Za-z0-9_-]{4,60}', out_name):
        raise ValueError('Output name must begin ktide-')
    dest = root / 'reports' / out_name
    if dest.exists() or dest.is_symlink():
        raise ValueError('Refusing to replace an existing dataset')
    assigned = validate_assignments(assignment_path, clips)
    rows = []
    payload = []
    for c in clips:
        # Uniform deterministic reduction, if a dense clip exceeds K-TIDE max.
        n = min(c['frames'], 96)
        indices = [i * (c['frames']-1) // (n-1) for i in range(n)]
        if len(set(indices)) != n:
            raise ValueError('Degenerate downsample indexes')
        times = np.asarray([c['times'][i] for i in indices], dtype=np.int64)
        vectors = np.asarray([c['vectors'][i] for i in indices], dtype=np.float32)
        if len(times) < 4 or len(times) > MAX_FRAMES:
            raise ValueError('Invalid downsampled feature count')
        sid = 'dense-' + sha256((c['clipId']+':'+c['sourceSha256']).encode()).hexdigest()[:24]
        relpath = 'shards/' + sha256(sid.encode()).hexdigest()[:24] + '.npz'
        info = assigned[c['sourceSha256']]
        rows.append({
            'sampleId': sid, 'sourceSha256': c['sourceSha256'],
            'groupId': info['groupId'], 'goal': info['goal'], 'subsport': info['subsport'],
            'startMs': c['startMs'], 'endMs': c['endMs'],
            'shard': relpath, 'shardSha256': '0'*64, 'sampleCount': len(times),
        })
        payload.append((times, vectors))
    if len({r['sampleId'] for r in rows}) != len(rows):
        raise ValueError('Duplicate clip samples')
    if len({r['groupId'] for r in rows}) < 3:
        raise ValueError('Selected clips do not cover at least three trip groups')
    # Validate shape/roster before touching the filesystem.
    manifest = {'version': VERSION, 'featureModelKey': model_key,
                'embeddingDim': dims, 'samples': rows}
    validate_manifest(manifest)
    dest.mkdir(mode=0o700)
    (dest / 'shards').mkdir(mode=0o700)
    for row, (times, vectors) in zip(rows, payload):
        path = dest / row['shard']
        with path.open('xb') as f:
            np.savez_compressed(f, t_ms=times, decoded_pts_ms=np.full(len(times), -1, dtype=np.int64),
                                embeddings=vectors)
        row['shardSha256'] = digest(path)
    validate_manifest(manifest, root=dest, verify_shards=True)
    with (dest/'manifest.json').open('x') as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        f.write('\n')
    provenance = {
        'version': 'klip-kmoment-ktide-dense-bridge-v1',
        'note': 'Decoded PTS unknown (-1). Resampled filter source-relative times are not native PTS.',
        'assignmentsSha256': digest(assignment_path), 'sourceMediaRead': False,
        'clips': [{k: c[k] for k in ('clipId','sourceSha256','inputReceiptSha256','featuresSha256','analysisSha256')}
                  for c in clips],
    }
    with (dest/'bridge-provenance.json').open('x') as f:
        json.dump(provenance, f, indent=2, sort_keys=True)
        f.write('\n')
    return {'manifest': str(dest/'manifest.json'), 'samples': len(rows),
            'distinctOutings': len({r['groupId'] for r in rows}),
            'semanticModelKey': model_key, 'sourceVideoRead': False,
            'qualifiedForRetrievalClaim': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workspace', type=Path, required=True)
    p.add_argument('--plan', type=Path, default=None)
    p.add_argument('--qa-name', default='kmoment-qa-siglip2-001')
    p.add_argument('--decode-name', default='kmoment-dense-pilot-001')
    p.add_argument('--mode', choices=('audit','template','export'), default='audit')
    p.add_argument('--assignments', type=Path)
    p.add_argument('--name', default='ktide-dense-verified-pilot-001')
    a = p.parse_args()
    ws = safe_training_workspace(a.workspace.expanduser())
    plan = (a.plan or (ws/'reports/kmoment-dense-inspection-plan-v1.json')).expanduser()
    key, dim, clips, plan_sha = checked_qa(ws, plan, a.decode_name, a.qa_name)
    if a.mode == 'audit':
        out = public_summary(key, dim, clips, plan_sha)
    elif a.mode == 'template':
        out = assignments_template(clips)
    else:
        if a.assignments is None:
            p.error('--assignments required for export')
        out = export(ws, a.name, clips, key, dim, a.assignments.expanduser())
    print(json.dumps(out, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
