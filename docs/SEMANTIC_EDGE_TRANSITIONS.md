# Klip 0.7: Edge-aware objects and smarter joins

The local Studio now compares the **exit of one scene** with the **entrance of the next**, rather than using only whole-clip embedding averages. All inputs are previously completed, checksum-verified caches. The original DJI footage is never changed.

## Evidence

- Open-vocabulary OWLv2 object query labels, sampled-frame counts, and maximum raw model score. These are **unverified hypotheses**, not reliable fish species, plant taxonomy, or identities.
- Relative SigLIP2 scene-context similarities, with explicit unknown values where context was not run.
- Only same-model/revision/preprocessing edge embeddings can be compared. Cosine similarities are uncalibrated and don't prove physical continuity.
- Optional audio-change cues, which may signal a jarring sound cut but never prove breathing, crashes, or animal sounds.
- Separate entrance/exit sampling counts. A sparse sample interval cannot detect every short event.

Open **Details** on a Review clip to see these evidence layers. No annotations obstruct the video itself.

## Sequence editing

Each saved sequence now includes versioned join hints for natural cuts or soft breaks, shared object query words, timing proximity and model comparability. Scene joins remain editable and human-reviewable. The export includes an explicit transitions array with exact adjacent approved-example IDs.

### Selective rendering

The new optional `--transition smart` mode respects only valid, matched joins from the exported plan, applying video/audio fade-through-black **only** at suggested soft breaks. Other joins retain a direct cut. This is not a true overlapping cross-dissolve or frame interpolation. Existing `cut` and `fade` modes continue to work, and `cut` remains the default.

~~~bash
klip --workspace "$HOME/AdventureAI" render \
  "$HOME/AdventureAI/exports/sequences/<REVISION-ID>.json" \
  --output "$HOME/AdventureAI/reels/my-edit.mp4" \
  --height 720 --transition smart --fade-seconds 0.35
~~~

Replace REVISION-ID with the actual exported filename. Output is a separate new workspace MP4. Originals remain read-only and content-verified.

## Mac setup after the SSD processing pass finishes

~~~bash
cd "$HOME/Documents/Projects/adventure-klip"
git fetch origin
git switch --track origin/feat/semantic-edge-transition-evidence
source .venv/bin/activate
python -m pip install -e '.[dev,vision,images]'
klip --workspace "$HOME/AdventureAI" studio
~~~

If the branch already exists, switch to it and pull fast-forward. Preserve any local uncommitted changes before branch switching. No new extraction is required to show the updated interface.

### Targeted semantic processing after the cheap whole-SSD inventory

The first histogram pass does **not** provide validated animal or plant detections. To obtain object labels and model embeddings, opt in to much more expensive models on a small subset:

~~~bash
python -m pip install -e '.[semantic]'
klip --workspace "$HOME/AdventureAI" scenes --backend siglip2 --every 3 --limit 3
klip --workspace "$HOME/AdventureAI" scenes --backend owlv2 --every 3 --limit 3
klip --workspace "$HOME/AdventureAI" scenes --backend context --every 3 --limit 3
~~~

The limit selects catalog-ordered assets, not necessarily your favorite individual recordings. Different backends keep independent, checkpointed caches. Sampling every three seconds misses short actions and can leave scene boundaries unknown. Download and hardware costs vary, especially for zero-shot detection.

## Scientific limitations and next evaluation

1. Collect reviewer judgments on scene combinations and compare the suggestions to human choices, including near-identical scenery and abrupt activity changes.
2. Create a verified taxonomic benchmark before reporting fish or tree species accuracy. Labels across two edges do not establish the same individual animal.
3. Validate detection coverage, track hypotheses, false positives, and transition acceptance rates separately.
4. Compare source camera motion and color at boundaries, then evaluate true xfade/acrossfade only after normalizing video timebases.
5. Never infer a crash, breathing quality, skill, identity or dangerous behavior solely from camera motion or raw audio.

Current release changes metadata inspection and the optional rendering policy, not object detector weights or model training.