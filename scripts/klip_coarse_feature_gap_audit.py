#!/usr/bin/env python3
"""Read-only timestamp-gap audit of registered K-TIDE SigLIP2 feature caches."""
import argparse
import json
from pathlib import Path
from statistics import median
from adventure_intelligence.catalog import connect
from adventure_intelligence.scene_atlas import frames_for_asset, unit_vector


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--workspace', type=Path, required=True)
    ap.add_argument('--inventory', type=Path, required=True)
    ap.add_argument('--max-gap-ms', type=int, default=7000)
    args = ap.parse_args()
    if not 1 <= args.max_gap_ms <= 120_000:
        ap.error('Invalid max gap')
    if args.inventory.is_symlink() or not args.inventory.is_file():
        ap.error('Missing/unsafe inventory')
    inventory = json.loads(args.inventory.read_bytes())
    if inventory.get('provider') != 'siglip2':
        ap.error('Expected SigLIP2 inventory')
    root = args.workspace.expanduser().resolve(strict=True)
    rows, all_gaps = [], []
    with connect(root) as db:
        for item in inventory['entries']:
            if not item.get('likelyWindowReady'):
                continue
            source = item['sourceSha256']
            frames, key = frames_for_asset(db, root, source, 'siglip2')
            if key != item['featureModelKey']:
                raise ValueError('Cache model revision changed since inventory')
            times = sorted(set(f['tMs'] for f in frames
                               if isinstance(f, dict) and type(f.get('tMs')) is int
                               and unit_vector(f.get('embedding')) is not None))
            gaps = [b-a for a,b in zip(times, times[1:])]
            if any(g <= 0 for g in gaps):
                raise ValueError('Invalid feature timing')
            all_gaps.extend(gaps)
            rows.append({'sourceSha256': source, 'samples':len(times),
                         'pairs':len(gaps),
                         'connectedPairs':sum(g <= args.max_gap_ms for g in gaps),
                         'medianGapMs': median(gaps) if gaps else None})
    print(json.dumps({'version':'klip-readonly-coarse-gap-audit-v1',
       'sources':len(rows), 'samples':sum(r['samples'] for r in rows),
       'adjacentPairs':len(all_gaps),
       'connectedPairs':sum(g <= args.max_gap_ms for g in all_gaps),
       'connectedFraction':sum(g <= args.max_gap_ms for g in all_gaps)/len(all_gaps) if all_gaps else None,
       'medianGapMs':median(all_gaps) if all_gaps else None,
       'maxGapMs':args.max_gap_ms, 'rows':rows,
       'sourceMediaRead':False}, indent=2, sort_keys=True))

if __name__ == '__main__':
    main()
