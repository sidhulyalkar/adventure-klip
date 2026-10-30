# Adventure Klip: activity quality, boring footage and incident review

This feature extends the read-only processing pipeline. It **does not delete, rename, crop or overwrite the original DJI recordings**. It does not diagnose athletic skill, an injury, breathing or a crash from camera motion.

## Models and inference contracts

1. Inexpensive motion evidence (flow) already in the cache can propose an abrupt camera jolt followed by stillness. The result is a `cameraDisturbance`, never an automatically verified crash. Sudden camera pointing, cuts, stopping recording, landing and real falls can produce similar evidence.
2. Optional SigLIP2 text-image comparisons produce uncalibrated hypotheses for chairlift, stopped, slow flat traverses, downhill skiing, stationary biking and other activity states. At least three temporally close positive frames are needed to propose a state interval. The text prompt, model version, relative similarity and time spans are preserved. This is *not* speed estimation.
3. The context-aware reel planner mildly **downranks**, but does not automatically exclude, overlapping low-value hypotheses. Hard exclusions require explicit human labels (`ski_lift`, `slow_flat_skiing`, `waiting`, `non_action` etc). Missing scene evidence is UNKNOWN rather than a negative.
4. Optional human `fall`, `crash`, `near_miss` or `unstable_landing` labels yield separate `incident_review` episodes. The `incident-review` recipe selects only these human-labelled episodes. Automated camera jolts remain in private `diagnose` output and do not become confirmed crashes.
5. Derived `reviewCues` and private WebVTT overlays contain simple observable prompts: inspect approach, transition and recovery. They do not assert unsafe technique, actual joint loading, knee angle, speed, landing forces, intent, injury or skill quality.

## Use with the six cached skiing recordings

```bash
cd "$HOME/Documents/Projects/adventure-klip"
git fetch origin
git switch --track origin/feat/activity-quality-incident-review
source .venv/bin/activate
python -m pip install -e '.[dev,vision,images]'
# Explore camera jolts using existing optical-flow features; no new decode needed.
klip --workspace "$HOME/AdventureAI" diagnose
klip --workspace "$HOME/AdventureAI" review
open "$HOME/AdventureAI/exports/review.html"
```

The private JSON and WebVTT review tracks appear under `~/AdventureAI/exports/diagnostics/`. Their cue times are source-video-relative. To view VTT as synchronized captions, attach the VTT track to a compatible local video player; the original MP4 is never modified.

## Optional deeper scene analysis

```bash
python -m pip install -e '.[semantic]'
# Start with one indexed asset because model inference can be expensive.
klip --workspace "$HOME/AdventureAI" scenes --backend context --every 2 --limit 1
klip --workspace "$HOME/AdventureAI" diagnose
klip --workspace "$HOME/AdventureAI" plan --recipe ski-highlights --ranking context \
  --context-policy suggest --style chronological --seconds 90 \
  --output "$HOME/AdventureAI/exports/ski-context-90.json"
```

On the six existing videos, histogram descriptors alone cannot identify a chairlift. The context model must run to produce scene-state evidence. The first model download and first pass may be slow. Do not assume the default camera-motion ranking scores are meaningful technique grades.

## Correct the scene with a single label

Use the complete 64-character asset SHA from `klip status`. Example:

```bash
klip --workspace "$HOME/AdventureAI" mark <FULL_ASSET_SHA256> \
  --kind ski_lift --start 50.0 --end 80.0 --note 'On chairlift'

klip --workspace "$HOME/AdventureAI" mark <FULL_ASSET_SHA256> \
  --kind slow_flat_skiing --start 92.0 --end 113.0 \
  --note 'Flat section with little downhill action'

klip --workspace "$HOME/AdventureAI" mark <FULL_ASSET_SHA256> \
  --kind crash --start 170.0 --end 174.5 \
  --note 'I fell here; review the approach and recovery'
```

All `mark` commands append labels while preserving existing manual events and approvals. Timestamp ranges are validated against the video duration. The older `klip labels` command still **replaces all manual events** for a given asset and should not be used to add a single label.

Then run:

```bash
klip --workspace "$HOME/AdventureAI" diagnose --asset <FULL_ASSET_SHA256>
klip --workspace "$HOME/AdventureAI" review
klip --workspace "$HOME/AdventureAI" plan --recipe incident-review \
  --ranking context --seconds 45 \
  --output "$HOME/AdventureAI/exports/incident-review.json"
```

## Overlay data schema

`diagnose` exports `contextStates`, `cameraDisturbances`, `reviewedIncidents` and `reviewCues` as private JSON and WebVTT. Each carries `startMs`, `endMs`, class/status, evidence and uncertainty. This is deliberately independent of the final MP4 so a review can show more annotations than a cinematic reel.

Public overlay export from this diagnostic path is intentionally empty. A future explicit editorial approval step will be required before public captions, validated metrics or narrated coaching feedback are emitted.

## Scientific and UX limitations

- Weak camera movement does not prove a flat run or lack of technical difficulty. Lift and flat scenes require calibrated scene evidence and/or user annotation.
- Snow spray, low light, horizon changes, DJI EIS, a hand-held pan and cuts confound optical flow.
- Better crash candidates will require IMU/camera gyro metadata, camera pose, temporal models and human-labelled negative examples. Ground truth should include intentional hard landings, stops, dropped cameras and close calls.
- Coaching should separate observable information from unobservable biomechanics. Without visible body pose, depth calibration or IMU measurements, do not infer center of mass, edge angle, actual speed, forces, injury or the cause of a fall.
- A validated benchmark must split by filming day and location, and report state precision/recall, false-positive crash-review rate, temporal boundary error and human reel preferences.

This increment is a foundation for inspecting the current local footage and collecting trustworthy annotations. No true action recognition or crash detection accuracy has been measured on the six DJI videos yet.