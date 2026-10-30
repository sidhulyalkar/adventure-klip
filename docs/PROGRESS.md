# Live terminal progress and estimated completion

Klip now writes progress lines to stderr, without changing the final machine-readable JSON printed on stdout.
Each stage displays discovered input files, completed files, sampled frames, and a **stage-specific** estimated remaining time based on measured throughput. This is not an end-to-end ETA and can fluctuate with clip sizes, file codecs, filesystem speed and model load time.

## Install or update

```bash
cd "$HOME/Documents/Projects/adventure-klip"
git fetch origin
git switch --track origin/feat/progress-stage-eta
source .venv/bin/activate
python -m pip install -e '.[dev,vision,images]'
```

## Process a small existing DJI collection

```bash
mkdir -p "$HOME/AdventureAI/logs"
klip --workspace "$HOME/AdventureAI" process "$HOME/Desktop/Ski Videos" --activity ski \
  --detector flow --semantic-backend histogram --every 4 \
  --log-file "$HOME/AdventureAI/logs/ski-processing.log"
```

Output lines look like `[klip] flow:DJI_....MP4: 200/800 decoded frame(s) | elapsed 1m 05s | stage ETA 3m 15s`. This is *illustrative*, not measured performance.

Progress lines are emitted to the terminal and appended to the optional log. Any `--log-file` path within an input media folder or a previously indexed source directory is rejected. `--no-progress` hides terminal progress, and cannot be combined with `--log-file`.

View the live log with `tail -f "$HOME/AdventureAI/logs/ski-processing.log"`.

## Current running Klip process

Progress reporting is only available in a newly launched process using the updated code. Do **not** change the files underneath an already running Python process. It is safe to leave an active analysis running; the changes will apply to the next invocation. Previously completed checksum-verified feature and semantic artifacts are reused.

To check activity without interrupting it, run `ps -axo pid,etime,%cpu,%mem,command | grep -E '[f]fmpeg|[k]lip|[p]ython'` in another Terminal tab. FFmpeg with high CPU activity is likely decoding frames; the Python parent can be largely idle while it waits for the FFmpeg output stream.

## Why the optical flow run can take a while

The current dense Farneback optical-flow stage samples grayscale frames at four frames per second. The FFmpeg decoder still has to decode the source codec to reach those frames. Full SHA-256 integrity verification happens during indexing, before analysis, and after extraction. Progress shows the work; it does not inherently accelerate the decoder or eliminate verification.

Use `--detector baseline` for a cheaper first pass. For significant speed improvements, future work should add a shared batch decoder, verified low-resolution DJI preview pairing, optional hardware acceleration benchmarks, adaptive temporal sampling and staged model inference.

## Data safety

Input videos are opened for reading only, source/workspace overlap is prohibited, and explicit generated outputs cannot be written into indexed input folders. Workspaces and progress logs must be on separate paths. No source video is deleted or renamed by this pipeline. Independent backups are still important.