# Klip 0.6.1: Cinematic Focus

The default studio is a **screening room**, not a detection dashboard. Visual language: midnight navy, atmospheric shadow, cool grey metadata, salmon actions, and white movie-facing typography. Detail should appear only when needed.

## Refinements

- One dominant 16:9 film gate with fine frame corners, softly lit edges, and a quiet source-verification state. None of the UI effects change source videos or exports.
- A narrow salmon rule shows progress within the *current review batch*. It is not an estimate of analysis completion or a prediction of video quality.
- Cinema layout (or F) enlarges the film area and minimizes nonessential chrome while keeping Keep, Skip and Wrong Detection accessible. It does not use operating-system fullscreen, and does not copy media.
- Replay (or R) seeks back to the current review in-point.
- A discreet ? in the footer opens keyboard help. Reduced-motion settings disable transitions.
- Library and Sequence retain their existing functions. No inference, remote fetch, tracking or automatic rendering was added.

## Keyboard

| Key | Action |
|---|---|
| Space | Play/pause |
| R | Replay from selected in-point |
| F | Toggle cinema layout |
| Left / Right | Previous / next proposed moment |
| 1 | Keep |
| 2 | Skip with explanation |
| 3 | Reject incorrect detection with explanation |
| Esc | Exit cinema layout |

Shortcuts are disabled while editing form fields or when a modal is open. Native browser player controls remain usable.

## Install after the current processing pass completes

Do not interrupt or reinstall the project during a long Klip writer process. After pipeline completion:

~~~bash
cd "$HOME/Documents/Projects/adventure-klip"
git fetch origin
git switch --track origin/feat/cinematic-minimal-focus
source .venv/bin/activate
python -m pip install -e '.[dev,vision,images]'
klip --workspace "$HOME/AdventureAI" studio
~~~

If the branch already exists, switch to it and pull fast-forward instead. Preserve local changes before switching branches.

The previous detailed UI remains available at the same server's /classic route. All source files stay at indexed locations and are opened read-only. The interface upgrade does not require reprocessing media.

## Current limits

- Verifying a large video SHA-256 can delay first playback.
- Browser codec support varies. Safari is useful for DJI HEVC.
- Candidate scene classes and editorial scores remain hypotheses until reviewed.
- The upgrade changes visuals and interactions, not models, ranking or the batch-processing algorithm.
- This single-user loopback server should never be made publicly accessible.
