# Klip research mission: discover experiences, not tags

**Status:** target product/research design, 2026-11-16. This document describes the intended system, not completed model capability.

## The job to be done

Given a large personal library of sports, exploration and underwater video, find the **moments worth preserving** without requiring the owner to rewatch everything. Understand the event, its context, its aesthetic and personal significance, rank and construct a coherent story from the strongest scenes, then use human feedback to improve future discovery and ranking.

The ordering is intentional:

1. **Discovery:** locate plausible important scenes across full recordings, including quiet scenery, wildlife, high-skill action, narrative transitions, unusual observations and personal memories.
2. **Understanding:** explain what happens, what changes over time, why an event might matter and what evidence supports it. Species, action completion and subjective significance must be distinguished.
3. **Editorial candidates:** derive full experiences, connected lines and shorter moments, each with reversible edit variants.
4. **Ranking:** organize scenes by value to the owner's selected story, personal interest, subject visibility, filming quality and diversity. Avoid repetitive near-duplicates.
5. **Human assessment:** provide playable evidence and editable interpretations. Collect free-text descriptions, corrections, Keep/Skip/Unsure, optional preference comparisons, event boundaries and filming intent.
6. **Learning loop:** freeze and source-link review exports; compare predictions against independent human reviews; train or adjust only after measurable held-out improvements. Never rewrite past evidence to make a model look right.
7. **Export:** produce private preview reels and accept edit feedback; render original-quality masters only with verified timebase and explicit approval.

**A scene can be worth keeping with no animal or trick.** A beautiful swim above coral, an unexpected view, personal memory or establishing shot may matter on its own. Conversely, detecting a ray or rail does not prove the composition is beautiful or the event complete.

## Three independent judgments

| Question | Evidence owner | Example | Never conflate |
|---|---|---|---|
| What is visibly happening? | Model observation + verification state | Possible ray, reef, skier, jump, change | Frame tags vs completed event |
| Why might it matter? | Model hypothesis with uncertainty | Three jumps in sequence; striking reveal | Description vs actual personal intent |
| Should it be kept? | Human review, story goal and context | Keep a scenic reef pan or three-jump line | Personal preference vs model confidence |

Keep object detection, event quality, personal meaning, visual attractiveness and final-reel relevance as distinct axes.

**Ranking correction:** among human-kept underwater scenes, explicit owner-rated scene interest and filming/story quality can matter more than merely confirming animal presence. This is a transparent rule based on human labels, not a trained semantic model.

## Multiple time scales

- **Recording:** immutable source SHA, duration, original timebase, outing/camera.
- **Scene:** meaningful context, often tens of seconds or minutes.
- **Event:** action/encounter with onset, development and resolution.
- **Moment:** short payoff or visual beat.
- **Edit variant:** immersive, highlight, montage/transition, all reversible.
- **Reel:** ordered scenes with pacing, variety and narrative continuity.

The owner-reviewed ski pilot marks four jumps at 11.846, 17.960, 23.714 and 37.639 seconds. The desired edit keeps jumps 1–3 as one connected park line and jump 4 as a separate extra. These markers are not verified native-PTS takeoff/landing boundaries.

## What success looks like

**Discovery:** full-timeline Recall@K of owner Keep scenes, rare-event recall, quiet-control coverage, no lost scenes across files.

**Understanding:** descriptive adequacy, event/scene boundary agreement, subject visibility, action continuity, false animal claims and uncertainty.

**Ranking:** owner preference agreement, source/outing diversity, quality and story-goal relevance, rank stability and transparent explanations.

**Editing:** completeness of approach/landing/ride-away, source-accurate PTS, audio continuity, human A/B preference for connected line vs isolated jumps.

**Efficiency:** compute and decoding per recording hour, cache reuse, checkpoint integrity and reproducibility.

## Non-goals / cautions

- No claim of online or continual model learning until an evaluated feedback-to-model process exists.
- 120 sampled proxies are not whole-library coverage of roughly 232 source recordings.
- No species confirmation from generic category labels.
- No fabricated personal memories, recording intent, or reasons for keeping a scene.
- Never delete unknown/short/model-failed scenes because of classifier uncertainty.
- No default external uploads, modification of originals, cross-source concatenation without continuity evidence, unapproved publication or deployment.

See [architecture](KLIP_ARCHITECTURE_V3.md), [roadmap](KLIP_IMPLEMENTATION_ROADMAP.md), [evidence log](../logs/experiments/2026-11-16-state-and-lessons.md) and [Claude Code brief](KLIP_CLAUDE_CODE_BRIEF.md).
