"use strict";
/* Offline K-MOMENT still-frame review. No fetch, storage or backend writes.
   The export contains only explicitly saved human reviews. */
(() => {
  const source = document.getElementById("klip-review-data");
  const data = JSON.parse(source.textContent);
  const el = id => document.getElementById(id);
  const clips = data.clips;
  const drafts = new Map();
  const committed = new Map();
  let clipIndex = 0;
  let frameIndex = 0;
  let playing = false;
  let playbackTimer = null;
  let exportedCount = 0;

  const nowClip = () => clips[clipIndex];
  const fmt = ms => (ms / 1000).toFixed(2) + "s";
  function pause() {
    playing = false;
    if (playbackTimer !== null) clearTimeout(playbackTimer);
    playbackTimer = null;
    el("play").textContent = "Play frames";
  }

  function setFrame(next) {
    const clip = nowClip();
    frameIndex = Math.max(0, Math.min(next, clip.frames.length - 1));
    const frame = clip.frames[frameIndex];
    el("frame").src = frame.source;
    el("frame").alt = "Derived image at " + fmt(frame.atMs);
    el("scrub").value = String(frameIndex);
    el("frameLabel").textContent = fmt(frame.atMs) + " · " +
      (frameIndex + 1) + "/" + clip.frames.length;
    el("previous").disabled = frameIndex === 0;
    el("next").disabled = frameIndex === clip.frames.length - 1;
  }

  function scheduleNext() {
    if (!playing) return;
    const clip = nowClip();
    if (frameIndex >= clip.frames.length - 1) {
      pause();
      return;
    }
    const gap = clip.frames[frameIndex + 1].atMs - clip.frames[frameIndex].atMs;
    const rate = Number(el("rate").value) || 1;
    playbackTimer = setTimeout(() => {
      if (!playing) return;
      setFrame(frameIndex + 1);
      scheduleNext();
    }, Math.max(50, Math.min(1200, gap / rate)));
  }

  function renderNavigation() {
    const nav = el("clips");
    nav.replaceChildren();
    clips.forEach((clip, i) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "clip" + (clipIndex === i ? " active" : "");
      const header = document.createElement("span");
      header.textContent = (i + 1) + ". " +
        clip.sportHintUnverified + " · " + clip.selectionType;
      const secondary = document.createElement("span");
      secondary.className = "meta";
      secondary.textContent = fmt(clip.expandedStartMs) + "–" +
        fmt(clip.expandedEndMs) + " · " + clip.frameCount + " frames · " +
        clip.visualChangeCandidates.length + " visual changes";
      button.append(header, secondary);
      if (committed.has(clip.clipId)) {
        const check = document.createElement("span");
        check.className = "meta reviewed";
        check.textContent = "✓ Human review saved";
        button.append(check);
      }
      button.addEventListener("click", () => selectClip(i));
      nav.append(button);
    });
    el("reviewCount").textContent = committed.size +
      " reviewed / " + clips.length + " clips";
  }

  function initialDraft(clip) {
    return {
      sport: "uncertain",
      kind: "unknown",
      completion: "uncertain",
      value: "later",
      startMs: clip.coreStartMs,
      endMs: clip.coreEndMs,
      note: ""
    };
  }

  function saveUncommittedDraft() {
    if (!clips.length) return;
    const clip = nowClip();
    const existing = drafts.get(clip.clipId) || initialDraft(clip);
    drafts.set(clip.clipId, {
      ...existing,
      sport: el("sport").value,
      kind: el("kind").value,
      completion: el("completion").value,
      value: el("value").value,
      note: el("note").value
    });
  }

  function renderDraft() {
    const clip = nowClip();
    const draft = drafts.get(clip.clipId) ||
      committed.get(clip.clipId) || initialDraft(clip);
    drafts.set(clip.clipId, { ...draft });
    ["sport", "kind", "completion", "value"].forEach(name => {
      el(name).value = draft[name];
    });
    el("note").value = draft.note;
    el("startLabel").textContent = "Start: " + fmt(draft.startMs);
    el("endLabel").textContent = "End: " + fmt(draft.endMs);
  }

  function selectClip(next) {
    pause();
    saveUncommittedDraft();
    clipIndex = Math.max(0, Math.min(next, clips.length - 1));
    const clip = nowClip();
    el("clipTitle").textContent = "Source " + clip.sourceSha256.slice(0, 12);
    el("clipDetails").textContent =
      "Clip " + clip.clipId + " · model " + clip.modelKey +
      " · candidate core " + fmt(clip.coreStartMs) + "–" +
      fmt(clip.coreEndMs);
    el("scrub").max = String(Math.max(0, clip.frames.length - 1));
    el("overview").src = clip.storyboard;
    const peakBar = el("peakBar");
    peakBar.replaceChildren();
    if (!clip.visualChangeCandidates.length) {
      const span = document.createElement("span");
      span.className = "muted";
      span.textContent = "No appearance-change peak detected; still review the whole sequence.";
      peakBar.append(span);
    }
    for (const peak of clip.visualChangeCandidates) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = fmt(peak.atMs) +
        " · Δ " + peak.cosineDistance.toFixed(3);
      button.title = "Visual appearance change, not verified action";
      button.addEventListener("click", () => {
        pause();
        let nearest = 0;
        let smallest = Infinity;
        clip.frames.forEach((f, i) => {
          const gap = Math.abs(f.atMs - peak.atMs);
          if (gap < smallest) { smallest = gap; nearest = i; }
        });
        setFrame(nearest);
      });
      peakBar.append(button);
    }
    setFrame(0);
    renderDraft();
    el("status").textContent = "Inspect the full interval before saving an event label.";
    renderNavigation();
  }

  function setBoundary(which) {
    const clip = nowClip();
    const previous = drafts.get(clip.clipId) || initialDraft(clip);
    const value = which === "startMs"
      ? clip.frames[frameIndex].atMs
      : Math.min(clip.expandedEndMs, clip.frames[frameIndex].atMs + 250);
    const updated = { ...previous, [which]: value };
    drafts.set(clip.clipId, updated);
    renderDraft();
    el("status").textContent = "Boundary proposal recorded; review the complete action before saving.";
  }

  function saveReview() {
    saveUncommittedDraft();
    const clip = nowClip();
    const draft = drafts.get(clip.clipId);
    if (!(draft.startMs >= clip.expandedStartMs &&
          draft.endMs <= clip.expandedEndMs &&
          draft.startMs < draft.endMs)) {
      el("status").textContent = "Start must precede end, within the inspected interval.";
      return;
    }
    if (draft.kind === "unknown" && draft.completion === "complete") {
      el("status").textContent =
        "Select an observable event before declaring it complete.";
      return;
    }
    const record = {
      clipId:clip.clipId,
      sourceSha256:clip.sourceSha256,
      denseSourceReceiptSha256:clip.denseSourceReceiptSha256,
      previewModelKey:clip.modelKey,
      sourceRelativeStartMs:draft.startMs,
      sourceRelativeEndMs:draft.endMs,
      observedSport:draft.sport,
      observedEvent:draft.kind,
      eventCompleteness:draft.completion,
      editorialDecision:draft.value,
      reviewerNote:draft.note.trim(),
      basedOnDerivedFramesOnly:true,
      confirmedAgainstOriginalVideo:false,
      modelProposalsAreNotGroundTruth:true
    };
    committed.set(clip.clipId, record);
    renderNavigation();
    el("status").textContent = "Review saved in this page. Export JSON before closing.";
  }

  function exportReviews() {
    const reviews = clips.map(c => committed.get(c.clipId)).filter(Boolean);
    if (!reviews.length) {
      el("status").textContent = "Save at least one human review before exporting.";
      return;
    }
    const payload = {
      version:"klip-kmoment-human-preview-reviews-v1",
      sourceReviewPacketVersion:data.version,
      inspectionPlanSha256:data.planSha256,
      modelKey:data.modelKey,
      basis:"locally_reviewed_derived_jpeg_sequences_not_original_video",
      totalReviewed:reviews.length,
      reviews
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)],
                          { type:"application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "kmoment-human-preview-reviews.json";
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    exportedCount = committed.size;
    el("status").textContent = "Export started. Save the JSON privately for later analysis.";
  }

  el("scrub").addEventListener("input", () => {
    pause();
    setFrame(Number(el("scrub").value));
  });
  el("previous").addEventListener("click", () => {
    pause(); setFrame(frameIndex - 1);
  });
  el("next").addEventListener("click", () => {
    pause(); setFrame(frameIndex + 1);
  });
  el("play").addEventListener("click", () => {
    if (playing) { pause(); return; }
    if (frameIndex === nowClip().frames.length - 1) setFrame(0);
    playing = true;
    el("play").textContent = "Pause";
    scheduleNext();
  });
  el("markStart").addEventListener("click", () => setBoundary("startMs"));
  el("markEnd").addEventListener("click", () => setBoundary("endMs"));
  el("save").addEventListener("click", saveReview);
  el("reset").addEventListener("click", () => {
    const clip = nowClip();
    drafts.set(clip.clipId, initialDraft(clip));
    committed.delete(clip.clipId);
    renderDraft(); renderNavigation();
    el("status").textContent = "Draft reset. Re-review before exporting.";
  });
  el("download").addEventListener("click", exportReviews);
  ["sport","kind","completion","value","note"].forEach(id =>
    el(id).addEventListener("change", saveUncommittedDraft));
  document.addEventListener("keydown", event => {
    if (["INPUT","TEXTAREA","SELECT","BUTTON"].includes(
          document.activeElement?.tagName)) return;
    if (event.key === " ") {
      event.preventDefault(); el("play").click();
    } else if (event.key === "ArrowRight") {
      event.preventDefault(); el("next").click();
    } else if (event.key === "ArrowLeft") {
      event.preventDefault(); el("previous").click();
    }
  });
  window.addEventListener("beforeunload", event => {
    if (committed.size > 0 && exportedCount !== committed.size) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
  if (clips.length) {
    el("coverage").textContent =
      data.observedFrames + " cached frames · " +
      data.visualChangeCandidateCount +
      " unverified visual changes";
    selectClip(0);
  } else {
    el("coverage").textContent = "No verified intervals available.";
    el("download").disabled = true;
  }
})();
