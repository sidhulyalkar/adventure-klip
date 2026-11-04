# Fast Klip review and private AI pre-triage

## Why the Studio spinner appeared

The benchmark Studio loads the full indexed DJI source for each new review
assignment. Its read-only streaming gate rehashes the complete source
file the first time it is accessed in a server session, which can take
tens of seconds or longer on an external SSD. Browser codec support and
seeking into DJI HEVC/H.265 or difficult MP4 timebases can also stall
playback. The screenshot alone does not establish which cause occurred.

The update preserves source integrity but makes delays explainable:
- The player displays a clear slow-loading warning after 12 seconds,
  with a retry-original option.
- A "Prepare short preview" control creates a private, cached, bounded
  960-pixel H.264 MP4 preview from only the requested interval.
- Prepared previews are preferred over full originals for subsequent
  opens, reducing repeated decoding and browser codec requirements.
- Full original SHA-256 is still verified before producing a derivative,
  and cached previews have source-hash-derived IDs and their own checksums.
- No source copy, automatic media upload or change to reviewed labels.

## Fast decisions

The visible review controls are now:
- **K** or click **Keep**, then automatically advance.
- **S** or click **Skip**, then automatically advance.
- **U** or click **Unsure**, then automatically advance.
- **Next unreviewed** jumps past existing human decisions.
- The detailed 12-criterion rubric is collapsed by default, but still
  available when the event is important.

Every decision remains a human-made, append-only SQLite revision.
A bare quick keep/skip is a **preference**, not proof of wildlife or a
successfully executed ski/bike maneuver. You can revisit any clip later.

## Optional AI judge, locally with Ollama

The new judge does NOT call a remote API. It explicitly invokes
http://127.0.0.1:11434/api/chat and only if the user enables it.
Only three small still images extracted from the short cached preview
are sent to the locally running model. The model has not watched all
frames or listened to audio and may miss brief wildlife, action
outcomes and camera movements.

Ollama's multimodal model catalog lists Gemma 3 4B as an image-capable
model. Install Ollama using its official instructions and explicitly
download a visual model, for example:

~~~bash
ollama pull gemma3:4b
ollama list
~~~

Starting the Studio *without* --local-judge-model disables model requests
and hides the AI button. To enable local suggestions:

~~~bash
export WS="$HOME/AdventureAI-clean.QJnopt"

klip --workspace "$WS" studio \
  --benchmark "$WS/reports/archive-review-first16-v1/assignments.json" \
  --local-judge-model gemma3:4b
~~~

You may click "Ask local AI judge" on one interval. It will prepare a
source-verified proxy if necessary, sample three frames, then ask Ollama
for one *provisional* Keep/Skip/Unsure explanation. The model cannot
write human reviews or approve clips.

For less manual work, run a bounded, balanced pre-triage pilot in a
separate terminal while Studio is stopped:

~~~bash
# Dry run: inspect selected interval assignments, no source reads or AI.
klip --workspace "$WS" archive-local-triage \
  --assignments "$WS/reports/archive-review-first16-v1/assignments.json" \
  --model gemma3:4b --limit 8

# Explicitly process up to eight selected unreviewed intervals.
caffeinate -i klip --workspace "$WS" archive-local-triage \
  --assignments "$WS/reports/archive-review-first16-v1/assignments.json" \
  --model gemma3:4b --limit 8 --run
~~~

This command chooses a bounded mix across sports and includes systematic
controls to expose missed events. It saves private preview/model caches,
not human review labels; it reports failures without silently skipping
them. The first run incurs full source verification, FFmpeg decoding,
and local model time. On repeated runs, matching previews and AI
suggestions are cached; a nonmatching model must judge again.

Now reopen Studio with --local-judge-model. Its review queue ranks
provisional AI Keeps and Unsures early, retains systematic controls,
and keeps every skipped/unknown clip accessible. It never silently
hides or discards any clip.

## Scientific guardrails

- A potentially rare manta/eagle ray under murky water can deserve
  investigation even if a vision model prefers clear sand or reef.
- Three frames are insufficient for species verification, animal
  abundance, ski turn execution, bike landing, or reliable event boundaries.
- A model "Skip" is only a draft sorting signal. Every interval, including
  deterministic controls, must remain reviewable.
- Human review labels, factual event observations, and LLM triage judgments
  are stored separately. No model result becomes a positive/negative
  benchmark label by itself.
- The first 16-video benchmark is not session-disjoint or independently
  random; it cannot produce unbiased whole-archive event recall.
- No external cloud uploads are made by this path. Ollama itself must be
  running locally, and its model should already be installed.
- For active SSD processing, do not run preview preparation at the same
  time; both operations read source footage and may contend for I/O.
- A failed preview or local model inference never changes the original
  frozen archive plan or previously saved human decisions.

## Qualification

Run tests on both Ubuntu and macOS, and inspect playback manually in
Firefox/Safari using at least one previously stuck HEVC DJI file. Confirm
short H.264 preview plays, original is immutable, fast ratings save, and
AI suggestions never auto-approve clips.

This initial AI path is *per-interval visual triage*, not a video-native
model or a fully calibrated personalized ranker. Before expanding,
compare AI-selected candidates against independently reviewed controls,
with event recall and time per reviewed high-value moment.
