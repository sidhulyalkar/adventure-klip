# Context-aware skiing and snorkeling highlights

## Goal

Preserve smooth, continuous snorkeling camera footage. Detect proposed waterline or surface interruptions from **visual** evidence, and use **audio transients** (bubbles, splashes, camera handling or other loud sounds) as corroborating evidence. These signals do not measure the person's breathing or health.

## Read-only local workflow

Work from the separate `~/AdventureAI` workspace. Original DJI files remain untouched.

```bash
cd "$HOME/Documents/Projects/adventure-klip"
source .venv/bin/activate
# Start with one representative snorkeling folder
klip --workspace "$HOME/AdventureAI" process "/Volumes/Sid_DJI_SSD/Snorkeling" \
  --activity snorkeling --allow-removable --detector baseline \
  --semantic-backend context --every 2 --audio-evidence
klip --workspace "$HOME/AdventureAI" continuity
klip --workspace "$HOME/AdventureAI" plan --recipe snorkel-discoveries \
  --ranking context --context-policy suggest --seconds 90 \
  --output "$HOME/AdventureAI/exports/snorkel-context.json"
klip --workspace "$HOME/AdventureAI" review
open "$HOME/AdventureAI/exports/review.html"
```

Replace `/Volumes/Sid_DJI_SSD/Snorkeling` with a real folder containing only snorkeling footage. Do not point a snorkeling-specific run at a mixed collection unless you intend to mark all discovered assets as snorkeling.

## Algorithms

- Zero-shot SigLIP2 context compares frozen text prompts describing a fully submerged POV scene with surface/waterline footage; it also compares chairlift vs descent and stationary bike vs riding. Similarity scores are *not calibrated probabilities*.
- `submersion_samples` and `continuous_underwater_intervals` propose runs of consecutive submerged observations separated by visually observed surface transitions or sampling gaps. One uncertain sample may be bridged if positive evidence returns; never bridge confirmed surface evidence.
- `acoustics` decodes a low-rate 8 kHz mono audio stream to 1-second RMS and zero-crossing features. It labels rare high-energy acoustic changes as *unattributed transients*, not gasping, inhaling, exhaling, coughing or distress.
- Editorial evidence correlates audio transients with surface observations within a 2-second tolerance. Neither bubbles nor splashes alone prove the camera surfaced.
- `plan --ranking context` produces short bounded candidate windows with source intervals, explainable score components, optional continuity evidence, and original episode references. `--context-policy suggest` is the safe default. `automatic` is an explicitly selected, uncalibrated scene-gating experiment, **not validated for unattended public edits**.
- Manual labels such as `surface_break`, `surface_transition`, `ski_lift`, `chairlift`, `waiting`, `camera_adjustment`, and `rest` are hard excludes. All predicted context remains reviewable.
- The HTML report shows up to three short suggested clips per asset and keeps all raw optical-flow proposals in a disclosure section. Timeline bars show actual positions rather than only durations.

## Limits and evaluation

An audio microphone can be submerged, above water or enclosed in a housing. A transitory sound may be fish bubbles, waves, camera contact, music, speech or a breath. Surface transitions and waterline visibility depend heavily on angle, lighting, refraction and bubbles; zero-shot predictions must be tested with manually labeled DJI footage.

Future real-data checks should measure underwater/subsurface interval boundary error, submerged footage precision/recall, false interruptions caused by bubbles, false positives caused by blue skies, and human preference for resulting snorkeling sequences. Similar ski-lift negative examples are needed for skiing before promoting scene ranking to automatic exclusions.

No pretrained model is automatically downloaded unless the user selects `context`. Install optional `[semantic]` dependencies when running it. If no scene model is run, acoustic features and manually labeled exclusions can still be inspected, but Klip cannot claim to identify sustained submersion from color histograms alone.