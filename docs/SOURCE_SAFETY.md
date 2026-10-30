# Keeping original DJI recordings safe

Klip source folders are read-only inputs. Indexing reads and registers existing media in place. Removable-card ingestion makes verified copies into the workspace without deleting anything from the card.

## Software safeguards
- Source and workspace roots cannot overlap.
- Reel rendering verifies SHA-256 source identity before and after rendering.
- Generated output paths for reels, review reports, planned edits, and public manifests cannot be inside any indexed source-video directory, including through symlinked paths.
- Original videos are not renamed, formatted, or deleted by any normal Klip command.
- This is not a backup or a guarantee against disk failure or other software; keep an independent backup.

## macOS smoke test

1. Make sure the Desktop sample folder exists: $HOME/Desktop/Ski Videos (not /Desktop/Ski Videos unless that is actually mounted).
2. Install the feature branch, create an independent workspace: $HOME/AdventureAI.
3. Preview with: klip --workspace "$HOME/AdventureAI" index "$HOME/Desktop/Ski Videos" --activity ski --dry-run
4. Process with: klip --workspace "$HOME/AdventureAI" process "$HOME/Desktop/Ski Videos" --activity ski
5. Review with: klip --workspace "$HOME/AdventureAI" review
6. For a stable SSD, use: klip --workspace "$HOME/AdventureAI" process "/Volumes/Sid_DJI_SSD" --activity unknown --allow-removable

Before and after a small test run, generate file inventories and SHA-256 hashes for all MP4/MOV/M4V source files using find and shasum -a 256; compare manifests with diff -u. An unchanged report establishes that no inventoried video was removed, renamed, or modified during the run. Keep inventories outside source folders.

All generated media, reports, databases, semantic arrays, and caches must remain under the independent workspace, never inside the camera library.