# Klip evidence ledger | state as of 2026-11-16

This is a **reconstruction of known experiment outputs from reviewed repository sources and owner-shared terminal logs**. It is not a fresh audit of private video files, and no dates/times/animal or jump detections below claim native source inspection.

| Stage | What was run / observed | What it established | What it did NOT establish |
|---|---|---|---|
| Archive inventory / first coarse scans | About 232 cataloged original sources, SSD-backed, with versioned scan and candidate plans | Source-aware reproducible exploration framework | Complete source-timeline candidate recall |
| V2 review pilot | Frozen 120 sampled source-linked H.264 proxies | Playable local review inputs, immutable time spans | 120 complete events or all 232 sources |
| V2 Auto-Scout (Gemma 3 4B) | 108 analyzed of 120, 12 model_schema failures; no original camera reads | Repeatable 7-still proposals and failure diagnostics | Ground-truth category or aesthetic accuracy |
| Suspicious label frequencies | ski_park 45/45 proposed ski clips; blur 102/108 completed | Model quality/category flags may be overbroad | Proof that any actual footage is blurred or in a park |
| K-MOMENT dense sample | 8 windows, 649 frames, 641 connected pairs, 768-d embedding vectors, 24 visual-change proposals | Dense temporal feature audit and connected frame comparisons | Verified action boundaries or reliable preference ranking |
| V2 blind calibration | 36-clip human-review interface with frozen source sample | An honest path to measuring model disagreement | A completed, representative 36-clip human ground-truth benchmark |
| Source-linked ski run expansion | A previously cropped 16–36 s window expanded to the full 48.128 s source | Original preview missed context; longer local playable review | Native PTS-aligned final editing or automatic run discovery |
| Human ski run annotations | Owner marked run 5.558–39.270s; jumps 11.846, 17.960, 23.714, 37.639s, Keep | Explicit source-linked sequence preference | Grounded takeoff/landing of all jumps from the annotations alone |
| Multi-scale ski edit | Owner preferred first 3 jumps as one line; 4th as bonus; later confirmed preview looked good | Edited group preference for this one verified scene | A trained/generalizable time-series boundary model |
| Top 24 reranking | Two owner-run edits, then model-only snorkeling, ski and bike proposals | Playable source-backed ranked preview gallery | Whole-library best moments or true quality probabilities |
| Ranking failure exposed | Unknown clips at #5/#8; very short 1–4s fragments; animal tag piles; generic bike promotions | Need separate quarantine, quality/scene significance and human evidence | Evidence that unknown sources contain no good footage |
| Marine-focused 57-clip study | Owner showed first scene and reported it had been identified as important previously; animal-only review was insufficient | Need a general scene description, meaning, intention and story role | Animal presence as the reason for its significance |
| Scene-first review v2 (this branch) | v2 optional description + scene interpretation; v1 export compatibility and same frozen sample | Human meaning is representable separately from optional wildlife evidence | Already-trained semantics or automatic reuse of every prior owner label |

## Key engineering decisions

1. Do not use one 20-second motion-peak window as the unit of a whole ski run or underwater encounter.
2. Distinguish source recording, broad scene, event, brief moment and editorial cut.
3. Place **Why keep this scene?** ahead of sport/animal taxonomy in human review.
4. Keep untrusted model tags and owner-approved descriptions in separate evidence namespaces.
5. A strong wildlife encounter depends on actual visible animal and framing, not the number of model marine category tags.
6. Human preference and descriptive intent should be reused after provenance-aware reconciliation, not copied into a different clip merely because filenames resemble each other.
7. Stable source SHA and approximate proxy time are necessary but not sufficient for frame-accurate final rendering.
8. The long-term objective is scene discovery → event/story understanding → personalized ranking → reviewed edits, rather than a growing set of special-case labeling pages.

## Unresolved questions / work to assign

- Which past review JSON files contain owner Keep labels for the visually compelling reef scene shown in the marine page? Their exact paths and overlap must be matched privately, not guessed.
- What fraction of the full archive already has sufficiently dense cached features for complete-scene retrieval?
- How often does a seven-still VLM hallucinate rays vs miss genuine clearly filmed animals?
- What are the correct evidence-based boundaries of skiing and snorkeling scenes when context extends beyond 20s or across verified source files?
- Does frozen embedding retrieval using human-authored scene descriptions outperform prior rare-tag heuristics on independent outings?
- What full-resolution output and audio/PTS strategy meets user preferences without damaging event continuity?

## Immediate research decision

**Implement scene-first owner evidence, verify old-label compatibility, and consolidate the log first.** Next implement read-only prior-label reconciliation and a bounded full-timeline representative source pilot. Do not yet launch an unqualified whole-library redetection or claim that label export automatically changes a model.
