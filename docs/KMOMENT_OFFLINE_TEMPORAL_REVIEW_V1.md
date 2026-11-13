# K-MOMENT Offline Temporal Review V1

**Status:** executable private local preview and human annotation workflow. Uses previously decoded SHA-verified JPEGs and SigLIP2 visual-change evidence. Does not open DJI originals, run models, import a label to SQLite, verify an event or create final video cuts.

## Real input results

The user completed 8/8 dense clips with 649/649 source-verified derived JPEGs across seven recordings, zero failures. Frozen SigLIP2 extracted 641 adjacent pairs under the 7-second gap rule and 24 relative appearance-change candidates. Four unscored exploration windows have 15 candidate changes; four ranked windows have nine. These are not sport-event counts or calibrated editorial quality scores. The resampled frame timestamps meet the connection rule by construction and should not be confused with evidence of recognizable physical motion.

## Run the offline reviewer

Use a clean checkout containing this feature, with the private derived reports from the previous steps.

    export WS="$HOME/AdventureAI-clean.QJnopt"
    export PLAN="$WS/reports/kmoment-dense-inspection-plan-v1.json"

    python scripts/klip_build_temporal_review.py \
      --workspace "$WS" \
      --plan "$PLAN" \
      --decode-name kmoment-dense-pilot-001 \
      --qa-name kmoment-qa-siglip2-001 \
      --name kmoment-review-pilot-001 \
      > "$WS/reports/kmoment-review-build-result.json"

    open "$WS/reports/kmoment-review-pilot-001/review.html"

This accesses only the private derived JPEGs, output receipts and static packaged HTML/JavaScript. No FFmpeg, model weights, network, original media or catalog writes.

## Use the page

Choose an interval, play the sampled stills at 0.5x to 4x speed, and jump to change markers. Inspect **the full interval** rather than assuming the change markers are interesting events. A no-peak interval may contain meaningful sustained action.

Then label: actual observed sport, observable event type, whether setup/onset/outcome were visible, keep/later/skip judgment, source-relative review boundaries and optional descriptive notes. The page has an explicit Save review action, then Export reviewed labels. The downloaded private JSON file is named kmoment-human-preview-reviews.json, with schema klip-kmoment-human-preview-reviews-v1.

The exported records carry source SHA, decoder receipt hash, frozen model key, review bounds, and provenance flags that say the reviewer examined only resampled JPEGs, **not the source movie**. They are deliberately not ingested into the Klip training data or manual labels automatically. A later approval process should compare them with true camera footage and native PTS before updating a ground-truth dataset.

## Evidence and privacy constraints

- Strictly validates plan SHA, decoder completion, per-JPEG SHA, monotone resampled frame times, matching SigLIP2 QA identities and model versions.
- Only serves relative derived JPEG paths, no absolute video paths, network access, GPS metadata or automatic uploads.
- Browser exports JSON directly on user action. It does not silently save reviews, so download before closing the page.
- Model change markers are image cosine-distance peaks and may reflect camera pans, lighting, bubbles, occlusions or scene changes, not verified maneuvers or wildlife.
- The 4fps image-grid times are **not native camera-frame PTS** and should not be treated as exact edit boundaries. Human observation of derived images is not independent event verification.
- The builder is idempotent when hashes match. Existing changed or unsafe generated HTML is refused rather than overwritten.
- It does not solve full 232-video source discovery or the existing slow source Studio playback; those are separate engineering milestones.

## Suggested evaluation before training an event model

Blindly review the four unscored exploration intervals and four ranked intervals. Note whether the most meaningful motion lies near appearance peaks, between peaks, or beyond the proposed clip start/end. Record false positives (camera movement mistaken for action), missing approach and resolution, and intervals that are truly uninteresting. Then qualify events on original footage and compare human cut preference against a plain intensity-peak baseline before training specialized ski, biking or marine action heads.

See docs/KMOMENT_DENSE_VISUAL_QA_V1.md and docs/PROBLEM_FORMULATION.md.
