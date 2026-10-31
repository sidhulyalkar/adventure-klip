"""Read-only performance diagnostics for one or more append-only Klip log runs.

No workspace, SQLite, media, or source path access. Stage ETAs are reported as
observations, never extrapolated into a whole-library completion promise.
"""
from __future__ import annotations

from collections import Counter, deque
from pathlib import Path
import re
from statistics import median

VERSION = "run-log-audit-v1"
_DURATION_RE = re.compile(r"(?:(\d+)h\s*)?(?:(\d+)m\s*)?(?:(\d+)s)?$")
_FRAMES_RE = re.compile(
    r"^(?P<stage>baseline|flow):(?P<file>[^:]+):\s*[^:]+:\s*"
    r"(?P<done>\d+)/(?P<total>\d+) decoded frame\(s\)\s*"
    r"\| elapsed (?P<elapsed>[^|]+)\| stage ETA (?P<eta>[^|]+)$"
)
_ITEMS_RE = re.compile(
    r"^(?P<stage>index|analysis|semantics|acoustics):\s*"
    r"(?P<done>\d+)/(?P<total>\d+)"
    r"(?:\s+\(\d+%\))?\s+(?P<message>.*?)\s*\|\s*"
    r"elapsed (?P<elapsed>[^|]+)\| stage ETA (?P<eta>[^|]+)$"
)
_START_RE = re.compile(r"^(?P<stage>[\w.:-]+): starting (?P<expected>\d+) item\(s\)$")
_HASH_RE = re.compile(
    r"^(?P<stage>index-hash|source-check):\s*(?P<file>[^:]+):\s*"
    r"hashed (?P<done>[\d.]+)/(?P<total>[\d.]+) MiB \((?P<percent>\d+)%\)$"
)


def _seconds(raw: str) -> int | None:
    raw = raw.strip()
    if raw == "calculating":
        return None
    if not raw or not _DURATION_RE.fullmatch(raw):
        return None
    hours, minutes, seconds = _DURATION_RE.fullmatch(raw).groups()
    if not any((hours,minutes,seconds)):
        return None
    return int(hours or 0)*3600+int(minutes or 0)*60+int(seconds or 0)


def audit_log(log_path: Path) -> dict:
    """Stream a log of arbitrary length; analyze the latest run separately.

    The log emits no wall-clock timestamp per progress line. This intentionally
    cannot establish that a process is *currently* alive or give a global ETA.
    """
    log_path = Path(log_path).expanduser()
    if not log_path.is_file():
        raise ValueError("Log file not found")
    run_number = 0
    lines = 0
    stage_starts: dict[str,int] = {}
    stages: dict[str,dict] = {}
    decoder: dict[tuple[str,str],dict] = {}
    recent = deque(maxlen=12)
    hash_observations = Counter()
    hash_files: set[tuple[str,str]] = set()
    errors = []
    last_event = None
    stage_mismatches: set[str] = set()

    def reset():
        stage_starts.clear();stages.clear();decoder.clear();recent.clear()
        hash_observations.clear();hash_files.clear();errors.clear()
        stage_mismatches.clear()

    with log_path.open("r",encoding="utf-8",errors="replace") as handle:
        for line in handle:
            if not line.startswith("[klip] "):
                continue
            lines += 1
            msg = line.removeprefix("[klip] ").strip()
            if msg.startswith("pipeline: source "):
                run_number += 1
                reset()
                last_event = {"kind":"pipeline_start"}
                continue
            if not run_number:
                run_number = 1
            if msg.startswith("pipeline: completed"):
                last_event = {"kind":"pipeline","status":"completed"}
            elif msg.startswith("pipeline: partial failure"):
                last_event = {"kind":"pipeline","status":"partial_failure"}
            if match := _START_RE.fullmatch(msg):
                key = match["stage"]
                stage_starts[key] = int(match["expected"])
                if key in {"index","analysis","semantics","acoustics"}:
                    stages[key]={"observedDone":0,"reportedTotal":int(match["expected"]),
                                 "latestMessage":"started"}
                last_event={"kind":"stage_start","stage":key}
                continue
            if match := _FRAMES_RE.fullmatch(msg):
                stage, filename = match["stage"], Path(match["file"]).name
                done, total = int(match["done"]), int(match["total"])
                elapsed = _seconds(match["elapsed"])
                start_key = stage+":"+filename
                expected = stage_starts.get(start_key)
                if expected is not None and expected != total:
                    stage_mismatches.add(start_key)
                if done <= 0 or total <= 0 or done > total:
                    continue
                rate = round(done/elapsed,3) if elapsed and elapsed>0 else None
                decoder[(stage,filename)]={
                    "stage":stage,"file":filename,"frames":done,"expectedFrames":total,
                    "elapsedSeconds":elapsed,"stageEtaSeconds":_seconds(match["eta"]),
                    "outputFramesPerSecond":rate,"observedComplete":done>=total,
                }
                last_event={"kind":"decode","stage":stage,"file":filename}
                if not recent or recent[-1] != (stage,filename):
                    recent.append((stage,filename))
                continue
            if match := _ITEMS_RE.fullmatch(msg):
                stage=match["stage"]
                done,total=int(match["done"]),int(match["total"])
                if done<0 or total<0 or done>total:
                    continue
                outcome=match["message"]
                # Only retain a bounded caption, not arbitrary source paths.
                short=outcome.split()[-1] if outcome else ""
                stages[stage] = {
                    "observedDone":done,"reportedTotal":total,
                    "elapsedSeconds":_seconds(match["elapsed"]),
                    "stageEtaSeconds":_seconds(match["eta"]),
                    "latestMessage":short if short in
                        {"indexed","unchanged","analyzed","cached","failed"} else "progress",
                }
                if "failed" in outcome.lower() and len(errors)<25:
                    errors.append({"stage":stage,"itemNumber":done})
                last_event={"kind":"stage_progress","stage":stage}
                continue
            if match := _HASH_RE.fullmatch(msg):
                stage=match["stage"]
                hash_observations[stage]+=1
                hash_files.add((stage,Path(match["file"]).name))
                if int(match["percent"])>=100:
                    last_event={"kind":"source_check","stage":stage,
                                "file":Path(match["file"]).name,
                                "sizeMiB":float(match["total"])}
                continue
            if ": failed" in msg.lower() and len(errors)<25:
                errors.append({"stage":"unknown","itemNumber":None})
    rows=sorted(decoder.values(),key=lambda x:(x["stage"],x["file"]))
    completed=[x for x in rows if x["observedComplete"]
               and x["outputFramesPerSecond"] is not None]
    latest=decoder.get((last_event.get("stage"),last_event.get("file"))) if (
        last_event and last_event.get("kind")=="decode") else None
    return {
        "schemaVersion":1,"version":VERSION,"runsSeen":run_number,
        "logLinesRead":lines,"lastEvent":last_event,
        "stageProgress":stages,
        "latestDecode":latest,
        "decodeVideosObserved":len(rows),
        "decodeVideosWithFinalFrameObserved":sum(x["observedComplete"] for x in rows),
        "medianOutputFramesPerSecondOfObservedCompletions":
            round(median(x["outputFramesPerSecond"] for x in completed),3)
            if completed else None,
        "decodeSamples":[decoder[key] for key in recent if key in decoder],
        "sourceVerification":{"observedChecksByStage":dict(hash_observations),
                              "distinctStageFilePairs":len(hash_files),
                              "note":"Heartbeat lines are not unique hash operations"},
        "progressCountMismatches":sorted(stage_mismatches)[:25],
        "failureObservations":errors,
        "limitations":[
            "Progress is from the latest run segment; log may stop before stage completion.",
            "No per-line wall-clock timestamps; cannot establish current liveness or global ETA.",
            "Output frame rate measures sparse FFmpeg output, not source decoder throughput.",
            "Missing final frame progress does not prove a stage failed.",
            "Log alone cannot measure clip quality, recall, source capture duration or GPU utilization.",
        ],
    }
