# Automated discovery experiment runner | 2026-11-16

**Evidence level:** implemented, synthetic CI passed. Exercised end to end on generated
test video (real ffmpeg decode and proxy cuts) with a fake judge. Not run on owner footage
and not run against a real model.

## What it does

`scripts/klip_discovery_experiment.py run` takes an owner-written list of at most 12
recordings and, per recording: verifies SHA-256, decodes a 2 fps motion timeline, segments
it, cuts a small silent H.264 proxy for each proposed and control segment, asks the judge
panel its fact questions, and writes `report.json`.

- Every stage writes a receipt and is skipped when the receipt exists. Rerunning the same
  command resumes; only judges that abstained are asked again.
- Settings and the source list are frozen per output directory. Changing either requires a
  new directory, so results from different settings cannot mix.
- Sources must sit under an explicit trusted root, are opened read-only, and output is
  refused inside the footage directory. Reports hold SHA prefixes and relative proxy
  paths, never absolute source paths.
- A failed recording is recorded with its reason and the run continues.
- With `--ledger`, each recording that has owner-marked Keep spans gets the fixed-window
  comparison.
- The CLI checks that Ollama is running and the judge models are pulled before starting.

## Not established

- Runtime on real 4K recordings. The SHA pass and motion decode read each file fully;
  on a 30-minute file expect minutes per stage, unmeasured here.
- Whether motion segmentation or either judge works on real footage.
- Audio is dropped from proxies; nothing uses audio yet.

## Next decision

Owner runs it on 8-12 chosen recordings (this is the bounded re-scan and needs explicit
approval and the SSD mounted). Start with `--stages verify motion segments` to see
segments and timing before spending any model time.
