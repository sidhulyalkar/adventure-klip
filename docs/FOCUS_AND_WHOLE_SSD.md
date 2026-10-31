# Focus Studio and whole DJI SSD workflow

Klip v0.6 offers three separate spaces: Review, Library, and Sequence.

## Review

The default screen presents one source-relative clip in a large player. Use Keep, Skip, or Wrong detection. A reason is required for Skip and Wrong detection. Optional details provide adjustable start/end seconds and a specific scene label. All labels are human-review revisions, never changes to the original footage. Keyboard: 1 Keep, 2 Skip, 3 Wrong detection, Space playback, arrows change clip.

## Library

Group indexed recordings by date, region, activity, source folder, or collection. Search filenames and notes. Date is taken from valid camera capture metadata where possible, otherwise from a recognizable DJI filename and explicitly identified as an estimate. Unknown dates and regions stay unknown. User-authored region, place, collection and activity corrections are stored in workspace SQLite without editing the source videos. Correct incorrectly indexed snorkeling-as-ski footage here.

## Sequence

The existing approved-only clip shelf and revisioned reel drafts remain available. Drag approved clips into order, preview, comment on scene combinations, save the draft and explicitly export the JSON edit plan. Export does not render or publish.

## Safe whole SSD processing on Mac

Complete any other Klip writer before starting. Use a stable mounted SSD and keep workspace on the Mac, not under the source folder.

    cd "$HOME/Documents/Projects/adventure-klip"
    source .venv/bin/activate
    klip --workspace "$HOME/AdventureAI" index "/Volumes/Sid_DJI_SSD" --activity unknown --allow-removable --dry-run
    mkdir -p "$HOME/AdventureAI/logs"
    caffeinate -i klip --workspace "$HOME/AdventureAI" process "/Volumes/Sid_DJI_SSD" --activity unknown --allow-removable --detector baseline --semantic-backend histogram --every 15 --audio-evidence --log-file "$HOME/AdventureAI/logs/dji-library-pass1.log"

Monitor independently:

    tail -f "$HOME/AdventureAI/logs/dji-library-pass1.log"

Do not run a second process, scenes, or acoustics writer against the same workspace while processing. The baseline first pass gathers relatively inexpensive motion, audio, histogram and timeline metadata. It does not establish species or athletic-action accuracy. Times vary with video duration, codec and SSD throughput.

## UI palette and preserved advanced tools

The default UI is intentionally minimal and uses a unified navy / light-grey / salmon / white palette. Review prioritizes one large playing clip, with three decisions. Library organizes days, indexed recordings, corrected activity, user-authored regions and collections. Sequence retains the source-safe approved-only drag-and-drop reel draft workflow.

The previous detailed multi-card analysis and A/B comparison interface remains available at `http://127.0.0.1:<port>/classic` on the **same local studio server**. This is an optional advanced route; the homepage opens the focused interface.

**Do not switch Git branches, reinstall packages or run another Klip writer while the current 90%-complete whole-SSD processing pass is still active.** Finish it first. Once complete, install the v0.6 branch and launch the UI against the same `~/AdventureAI` workspace. There is no need to rerun the current extraction just to view new UI colors or navigation.

## Launch the new interface after processing

    cd "$HOME/Documents/Projects/adventure-klip"
    git fetch origin
    git switch --track origin/feat/focus-review-hierarchical-library
    source .venv/bin/activate
    python -m pip install -e '.[dev,vision,images]'
    klip --workspace "$HOME/AdventureAI" studio

Studio serves only on local loopback, verifies original source SHA-256 on initial stream, and opens video files read-only. The application never moves, renames, uploads, compresses or deletes original files. Some DJI HEVC files play better in Safari than Firefox. The new library is virtual and only changes workspace metadata.

## How more footage improves future algorithms

Use multiple filming days, lighting conditions and activities to collect diverse negative controls: lift rides, low-action skiing, camera handling, surfacing, swimming, uninterrupted underwater filming, wildlife and meaningful action. Track candidate Precision@K, missed highlights, visual repetition, annotation cost, and accepted scene transitions across independent trips. Camera motion or low audio activity alone must never become a definitive rejection rule. Expensive semantic models should run selectively after first-pass inventory.