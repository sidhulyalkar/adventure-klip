# Klip 0.6 · Focused Review & Library

## Interface decision

The everyday job is **watch → judge → annotate (only when needed) → next**. A video deserves almost all of the first screen. The original v0.5 Inbox, Inspector, A/B Comparison and Sequence Editor were individually useful but together too visually dense.

The local studio now opens **Review** at `/`. **Library** is a second top-level view, not a sidebar competing with the player. The full older studio is preserved at `/classic`, with its A/B and Sequence features intact.

## Review

- The current review candidate fills a large native video viewer, with its original source streamed read-only. Playback is limited to its suggested in/out interval; it does not export or trim a file.
- Three primary editorial choices are **Keep**, **Later**, **Skip**. All decisions are saved to revisioned human-review records. Quick decisions always leave the scene label **uncertain** unless it was explicitly marked correct in the annotation panel. A human **Keep** enters the sequence clip shelf regardless of label certainty, with the unverified model label explicitly separated from the export kind. Keeping a cinematic moment does not mean its action classification was accurate.
- **Add details** reveals start/end seconds, scene label, independent label accuracy, editorial decision, and a note. The player is not covered by the form.
- Navigation uses the existing bounded `/api/queue` (up to 3 distinct candidates from each of 6 recordings). It never silently discards source footage.
- Shortcuts when not typing in a form: **Space** play/pause; **←/→** previous/next; **K** Keep; **M** Later; **X** Skip; **[ / ]** set in/out. Next reviewed suggestion is surfaced automatically. Shortcuts can be displayed via **Shortcuts ?**.
- Clips explicitly kept or skipped disappear from the next review queue even if their action classification remains uncertain. "Later" remains eligible for later review.\n- The queue is provisional. Prioritized/visual-similarity hypotheses are not confirmed events. An empty queue is not evidence that all good moments were found. **Later** survives as uncertain data and can appear in a future session.

## Library

Browse a **virtual hierarchy**, built from the one SQLite catalog. The tree can group indexed recordings by:

- **Day**: indexed capture timestamp; missing timestamps appear under *Unknown date*, never guessed from the file name.
- **Source folders**: name of the existing original directory. The real source hierarchy and recording bytes stay unchanged.
- **Region**: manually assigned label such as *Lake Tahoe*. Region assignments are recorded in the `asset_regions` table. They are **recording-level**, not automatic GPS/geographic inference.
- **Activity**: already indexed activity.
- **Review status**: recording has any review versus none, not a claim that every second was reviewed.

Select a recording to inspect metadata, model-suggested intervals, and previously reviewed moments, or create a manual interval even if automatic analysis found none. Search filters the active virtual folder by filename, date, region, source directory and activity. These are dynamic *views*, not copied folder structures or migrations of your SSD.

## Data/security contract

- UI is local-only at `127.0.0.1`; uses per-session token and existing Host/Origin protections. Never proxy to cloud services.
- Original media is read-only, validated by catalog hash and streamed via bounded byte ranges. A virtual region assignment is a SQLite write, not a rename/move of originals.
- Review feedback remains append-only and explicit; `/api/reviews` handles actual video interval labels. No automatic semantic inference on page navigation.
- The old `/classic` editor is retained so A/B comparisons and exportable edit plans remain accessible.
- Tests cover default+classic routing, static assets, auth, region input validation/persistence, read-only originals, and editorial-versus-classification separation.

## Run

```bash
cd "$HOME/Documents/Projects/adventure-klip"
git fetch origin
git switch --track origin/feat/focused-review-library
source .venv/bin/activate
python -m pip install -e '.[dev,vision,images]'
klip --workspace "$HOME/AdventureAI" studio
```

If the branch already exists locally, use `git switch feat/focused-review-library && git pull --ff-only`.

## Known limits and next improvements

1. **No location inference:** region names must be assigned manually, and a recording spanning multiple locations still has only one region. Later introduce interval-level region annotations, optional GPS provenance and a more flexible controlled tag dictionary.
2. **Playback:** native browser support varies for DJI HEVC/H.265; Safari may be required. No proxies/thumbnails are created automatically.
3. **Large libraries:** the overview currently returns all recording metadata; candidate review is still bounded by pages. Add server-side catalog pagination and indexed full-text querying once actual SSD scale warrants it.
4. **Usability:** test 20–30 mixed candidates across ski, bike and snorkeling recordings. Measure median decision time, false-rejects, model/scene-label disagreement, and high-quality clips found per 10 minutes. The interface should make evaluation faster before tuning models.
5. **Future navigation:** if needed, add true indexed subfolders for Year → Month → Day → Recording → Reviewed clip, source-tree depth and saved smart collections. Keep them optional, not compulsory navigation layers.
