# Adventure Klip 🏔️ 🐠 🚵

**Local-first action-camera intelligence.** Index recordings from existing Mac folders without moving footage; import removable DJI media with verified copies; extract timestamped evidence, group confirmed maneuvers, draft reels, and publish approved Sid's iLand discovery bindings.

[![CI](https://github.com/sidhulyalkar/adventure-klip/actions/workflows/ci.yml/badge.svg)](https://github.com/sidhulyalkar/adventure-klip/actions/workflows/ci.yml)

> **Research-stage software:** Camera motion is evidence, not proof of a ski jump or a speed measurement. Semantic object labels are unverified proposals until reviewed. Models are optional, downloaded only when explicitly enabled; no media leaves your computer by default.

## Install

Python 3.11+ and FFmpeg/FFprobe required. On macOS:

```bash
brew install ffmpeg
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[vision,images,dev]'
klip init
```

The old `adventure` command remains as a compatibility alias. The Python import package remains `adventure_intelligence` while the distribution and primary CLI are named `adventure-klip` / `klip`.

## Use existing folders, without moving anything

```bash
# 1. Index videos right where they already live, including new nested folders.
klip index "$HOME/Movies/DJI Footage" --activity ski --dry-run
klip index "$HOME/Movies/DJI Footage" --activity ski

# 2. Lightweight measured motion evidence and video-local timeline.
klip analyze
klip analyze --detector flow              # needs [vision]
klip timeline --source pixel-change
klip timeline --source optical-flow

# 3. Sparse visual descriptors (no model download).
klip scenes --backend histogram --every 4
klip timeline --source pixel-change       # merges existing scene descriptors

# 4. Review candidate episodes and draft an editable reel.
klip sessions
klip review
klip plan --recipe ski-highlights --style chronological --seconds 75 --output "$HOME/AdventureAI/exports/plan.json"
klip render "$HOME/AdventureAI/exports/plan.json" --transition fade --output "$HOME/AdventureAI/reels/ski.mp4"
```

`klip index` registers content-addressed identities with **original local paths**. It reads but does not copy/move/rename footage. If a video changes in-place, re-index the folder. Originals must remain accessible to analyze or render; missing paths are reported, not fabricated. The first scan hashes every file, later unchanged scans reuse the catalog. All generated artifacts are placed under `~/AdventureAI` or `ADVENTURE_HOME`, not in your source folder. The `fade` transition is currently a **fade-through-black** (audio and video), not a cross-dissolve.

## DJI SD cards: verified import instead of zero-copy

```bash
klip ingest '/Volumes/YOUR_DJI_CARD/DCIM' --activity ski --dry-run
klip ingest '/Volumes/YOUR_DJI_CARD/DCIM' --activity ski
klip analyze
```

Reading removable cards in-place is possible with `klip index --allow-removable`, but strongly discouraged: the card may disappear before analysis or rendering. The normal `ingest` mode copies originals to content-addressed local storage and verifies them, preserving sidecars when available. It **never deletes** source files. The optional macOS mount-triggered agent is in `macos/` and should be tested manually before enabling unattended imports.

## Optional semantic understanding

Frame observations can be indexed using multiple feature families, with timestamps and versioned provenance:

| CLI backend | Outputs | Limitations |
|---|---|---|
| `histogram` | Cheap 48D RGB histogram for visual similarity | Not semantic; no objects |
| `siglip2` | Real semantic image embeddings via Hugging Face | Downloads model weights; no object boxes |
| `owlv2` | Open-vocabulary objects + boxes + scores | Labels are proposals, not species-verified |
| `stack` | SigLIP2 embeddings + OWLv2 objects, optional human pose | Heavy; needs adequate memory and validated model versions |

Install the opt-in dependencies first:

```bash
pip install -e '.[semantic]'
klip scenes --backend siglip2 --every 4 --limit 1
klip scenes --backend owlv2 --every 4 --limit 1
klip scenes --backend stack --every 6 --limit 1
```

Optional human-pose keypoints on frames with a *visible person*:

```bash
pip install -e '.[semantic,pose]'
klip scenes --backend stack --pose-model /path/to/pose_landmarker.task --limit 1
```

You must independently obtain a compatible MediaPipe `.task` model. A helmet-mounted first-person video typically cannot show your own body's pose, so human-pose landmarks should not be mistaken for skier kinematics. For ski and bike POV biomechanics, future stages should include camera-ego-motion compensation, calibrated IMUs and visually observed body/equipment landmarks where available.

Large foundation-model weights aren't packaged in the wheel. By default, Klip does not access any remote video API or transmit personal media. Model-host downloads are opt-in and subject to upstream model licenses. On Apple Silicon, the SigLIP backend attempts to use MPS; object detection is model/backend dependent. Run the baseline first and benchmark with a small verified sample before a full library scan.

## What is actually calibrated

`klip timeline` produces `~/AdventureAI/timelines/<asset>-<detector>-<bin>.json`, with each time bin containing raw video motion, robust per-video normalized scores, event references, and optional timestamped object/keypoint candidates and embedding locations. **Per-video normalization is not cross-video probability calibration**. The score is a visual-activity heuristic, not scientific confidence that a maneuver occurred or automatic highlight quality.

Human review labels use `klip labels examples/labels.example.json`. Sequence patterns include jump chains, carving flow, tree weaving, and technical-feature chains **only from appropriately labeled events**. Automatically inferred flow candidates retain camera-motion labels. See [Architecture](docs/ARCHITECTURE.md) and [iLand adapter](docs/ILAND_INTEGRATION.md).

## Editing and publishing

- `klip plan` selects non-overlapping candidate episodes under a time budget. `--style chronological` honors verified capture dates when present; `--style crescendo` orders source groups from lower to higher episode score while keeping each video's events in source order. These are transparent heuristics, not a learned film editor.
- `klip render --transition cut|fade` makes reproducible MP4 review exports. The fade option is a tested black fade, not an optical cross-dissolve.
- `klip bind ... --approve` and `klip export` enforce explicit approvals and real playback URLs for discovery objects, preventing model hallucinations from appearing on the public site.

## Validation and development

```bash
pip install -e '.[vision,images,dev]'
pytest -q
python -m build
```

CI tests the core in Python 3.11 and 3.12. Test media is synthetically generated; no personal recordings or pretrained model weights are committed. An evaluation set of genuine DJI POV skiing, biking, and underwater footage is required to quantify semantic object accuracy, athletic-event recall, and editorial quality. See [Contributing](CONTRIBUTING.md).

## Roadmap

1. Calibrate event boundaries against human-annotated DJI footage; add motion compensation and activity-specific multi-signal event detectors.
2. Review interface with frame/episode-level feedback, label correction, and an explicit train/validation/test split by trip/camera.
3. Sequence-based editor with shot diversity, pacing, subject motion, music licensing, and optional true cross-dissolves when verified on supported FFmpeg builds.
4. Distill embeddings, object tracks and labeled maneuvers into trainable temporal models; quantify out-of-trip generalization and uncertainty.
5. Sid's iLand discovery matches with real-place vs virtual-place disambiguation and sourced species annotation.

Apache-2.0 licensed. Raw footage, private data, and model weights are deliberately excluded from Git.
