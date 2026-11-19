"""Index the whole library in one pass: each recording read from disk once.

Before this, a recording was read four times by separate runs: the SHA check, the
motion decode, the audio timeline, and later the description pass (twice more:
keyframes, then audio). Run one at a time, every read after the first comes from the
OS file cache, and the next recording is read ahead while the current one is decoded,
so the disk and the decoder work at the same time.

Per recording, in order, while its file is cached:

    SHA check -> motion -> audio -> segments -> review clips    (discovery stages, unchanged)
    -> pretrained description (SigLIP frames, CLAP audio)       (moment_evidence, unchanged)

The judge panel is not run here: it is a separate, opt-in model step.

Output goes to ordinary discovery-NNN folders of at most 12 recordings, so every
existing reader (evaluate, deck, the review app, plan) uses it unchanged. Read-only on
footage; resumable; one failed recording never stops the rest.
"""
from __future__ import annotations

from pathlib import Path
import itertools
import json
import re
import threading
import time

from . import discovery_experiment as experiment

VERSION = "klip-library-index-v1"
STAGES = ("verify", "motion", "audio", "segments", "proxies")  # no judge panel
_FOLDER = re.compile(r"^discovery-(\d{3,})$")


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


class Prefetch:
    """Read files into the OS cache in the background; ready(path) waits for one."""

    def __init__(self, chunk: int = 8 << 20):
        self.chunk, self._jobs = chunk, {}

    def _read(self, path: Path) -> None:
        try:
            with open(path, "rb") as handle:
                while handle.read(self.chunk):
                    pass
        except OSError:
            pass  # the stage that needs the file reports the error

    def warm(self, path: Path | None) -> None:
        if path is not None and path not in self._jobs:
            job = threading.Thread(target=self._read, args=(path,), daemon=True)
            job.start()
            self._jobs[path] = job

    def ready(self, path: Path) -> None:
        self.warm(path)
        self._jobs.pop(path).join()


def survey(reports: Path) -> dict:
    """What earlier runs already did, from discovery folders only."""
    scanned, attempted, sizes, unfinished, numbers = set(), set(), {}, [], [0]
    for folder in sorted(Path(reports).glob("discovery-*")):
        frozen = _read(folder / "experiment.json")
        if not frozen:
            continue
        if (m := _FOLDER.match(folder.name)):
            numbers.append(int(m.group(1)))
        for sha in frozen["sources"]:
            attempted.add(sha)
            identity = _read(folder / sha / "source.json")
            if identity and (folder / sha / "motion.json").is_file():
                scanned.add(sha)
                sizes[sha] = identity["sizeBytes"]
        # An index batch that stopped part-way is finished before new batches start.
        if (folder / "sources.json").is_file() and not (folder / "report.json").is_file():
            unfinished.append(folder)
    return {"scanned": scanned, "attempted": attempted, "sizeBytes": sizes,
            "unfinished": unfinished, "nextNumber": max(numbers) + 1}


def select(assets: dict[str, dict], done: dict, described: set[str], *,
           first: dict[str, float] | None = None, retry_failed: bool = False,
           describe: bool = True) -> dict:
    """Recordings to scan (and describe), and scanned recordings still to describe.

    assets: sha -> {"path", "sport"} for recordings under the footage root, accidental
    ones already removed. Owner-labelled recordings go first (their pairs become
    usable), then the rest in path order, which for DJI names is capture order, so
    whole days finish together.
    """
    first = first or {}

    def order(sha):
        return (-first.get(sha, 0.0), assets[sha]["path"])

    failed = done["attempted"] - done["scanned"]
    scan = sorted((s for s in assets if s not in done["scanned"]
                   and (retry_failed or s not in failed)), key=order)
    rest = sorted((s for s in assets if s in done["scanned"] and s not in described),
                  key=order) if describe else []
    return {"scan": scan, "describeOnly": rest,
            "skippedFailedBefore": sorted(s for s in assets if s in failed and not retry_failed
                                          and s not in done["scanned"])}


def run(assets: dict[str, dict], *, workspace: Path, trusted_root: Path, reports: Path,
        described: set[str], describe_one=None, motion=None, first=None,
        max_recordings: int | None = None, retry_failed: bool = False,
        allow_mounted: bool = True, run_batch=experiment.run, prefetch: Prefetch | None = None,
        log=lambda message: None, clock=time.monotonic) -> dict:
    """Scan, then describe, every selected recording; returns totals.

    describe_one(sha, path) -> dict runs the description pass (None to skip it).
    motion(path, fps, seconds) -> (series, decoder) decodes the motion timeline.
    """
    reports = Path(reports)
    done = survey(reports)
    plan = select(assets, done, described, first=first, retry_failed=retry_failed,
                  describe=describe_one is not None)
    prefetch = prefetch or Prefetch()
    totals = {"scanned": 0, "scanFailed": 0, "described": 0, "describeFailed": 0,
              "skippedFailedBefore": len(plan["skippedFailedBefore"]), "batches": []}
    budget = [max_recordings if max_recordings is not None else float("inf")]
    work = sum(assets[s].get("durationS") or 0 for s in plan["scan"] + plan["describeOnly"])
    started, footage = clock(), [0.0]

    def progress(sha, note, name=None):
        footage[0] += assets.get(sha, {}).get("durationS") or 0
        spent = clock() - started
        speed = footage[0] / spent if spent > 0 else 0
        left = (work - footage[0]) / speed / 60 if speed else 0
        log(f"{name or Path(assets[sha]['path']).name}: {note} "
            f"(running at {speed:.1f}x real time, about {left:.0f} min left)")

    def describe(sha, path, log_file=None) -> str:
        if describe_one is None:
            return "description off"
        began = clock()
        try:
            meta = describe_one(sha, path)
            totals["described"] += 1
            note = "already described" if meta.get("skipped") else f"described in {clock() - began:.0f}s"
        except Exception as error:  # one bad recording never stops the pass
            totals["describeFailed"] += 1
            meta, note = {"failure": f"{type(error).__name__}: {str(error)[:200]}"}, "describe failed"
        if log_file is not None:
            with open(log_file, "a", encoding="utf-8") as handle:
                handle.write(json.dumps({"version": VERSION, "sourceSha256": sha,
                                         "describe": {k: meta.get(k) for k in
                                                      ("frames", "audioWindows", "seconds",
                                                       "failure", "skipped")}}) + "\n")
        return note

    # 1. Finish any batch that stopped part-way, then scan the rest in new batches.
    batches = [(folder, None) for folder in done["unfinished"]]
    queue = [s for s in plan["scan"]
             if not any(s in (_read(f / "experiment.json") or {}).get("sources", [])
                        for f in done["unfinished"])]

    def new_batches():
        """Batches made one at a time, so a scan another process ran meanwhile (or a
        folder it created) is seen before each batch rather than duplicated or clobbered."""
        nonlocal queue
        while queue and budget[0] > 0:
            now = survey(reports)
            # Only what someone else attempted since this run began (a retry stays queued).
            queue = [q for q in queue if q not in now["attempted"] - done["attempted"]]
            take = queue[:int(min(experiment.MAX_SOURCES, budget[0]))]
            if not take:
                return
            number = now["nextNumber"]
            while True:
                folder = reports / f"discovery-{number:03d}"
                try:
                    folder.mkdir(parents=True, exist_ok=False)
                    break
                except FileExistsError:
                    number += 1
            queue = queue[len(take):]
            budget[0] -= len(take)
            yield folder, take

    decoders: dict[str, str] = {}
    clocks: dict[str, float] = {}

    def decode(path, fps, seconds):
        series, decoder = motion(path, fps, seconds)
        decoders[str(path)] = decoder
        return series

    for folder, take in itertools.chain(batches, new_batches()):
        if take is not None:
            (folder / "sources.json").write_text(json.dumps(
                [{"sourceSha256": s, "path": assets[s]["path"], "sport": assets[s]["sport"]}
                 for s in take], indent=2), encoding="utf-8")
        rows = experiment.load_sources(folder / "sources.json")
        # Only files with work left are read ahead (a resumed batch skips finished ones).
        reads = [Path(r["path"]) for r in rows if r["sourceSha256"] not in done["scanned"]
                 or (describe_one is not None and r["sourceSha256"] not in described)]
        log_file = folder / "index.jsonl"

        def before(row, _reads=reads):
            path = Path(row["path"])
            if path in _reads:
                k = _reads.index(path)
                prefetch.ready(path)
                prefetch.warm(_reads[k + 1] if k + 1 < len(_reads) else None)
            clocks[row["sourceSha256"]] = clock()

        def after(row, failed, _log=log_file):
            sha, path = row["sourceSha256"], Path(row["path"])
            took = clock() - clocks[sha]
            with open(_log, "a", encoding="utf-8") as handle:
                handle.write(json.dumps({"version": VERSION, "sourceSha256": sha,
                                         "scanFailed": failed, "scanSeconds": round(took, 1),
                                         "motionDecoder": decoders.get(str(path))}) + "\n")
            if failed:
                totals["scanFailed"] += 1
                progress(sha, "scan failed (see the batch report); not described", path.name)
                return
            totals["scanned"] += 1
            # Recordings since marked accidental (or moved) are scanned but not described.
            note = describe(sha, path, _log) if sha in assets else "not described (not selected)"
            progress(sha, f"scanned in {took:.0f}s, {note}", path.name)

        log(f"batch {folder.name}: {len(rows)} recordings")
        report = run_batch(folder / "sources.json", folder, workspace=workspace,
                           trusted_root=trusted_root, allow_mounted=allow_mounted,
                           stages=STAGES, decode=decode if motion else None,
                           before=before, after=after, log=lambda m: None)
        totals["batches"].append({"folder": folder.name, **report["totals"]})

    # 2. Recordings scanned by earlier runs that still need describing.
    rest = plan["describeOnly"]
    if budget[0] != float("inf"):
        rest = rest[:max(0, int(budget[0]))]
    for k, sha in enumerate(rest):
        path = Path(assets[sha]["path"])
        prefetch.ready(path)
        prefetch.warm(Path(assets[rest[k + 1]]["path"]) if k + 1 < len(rest) else None)
        try:
            # The file was hashed when it was scanned; a size change means it was replaced.
            if path.stat().st_size != done["sizeBytes"][sha]:
                raise ValueError("file changed since it was verified")
        except (OSError, ValueError) as error:
            totals["describeFailed"] += 1
            progress(sha, f"not described: {error}")
            continue
        progress(sha, describe(sha, path))
    totals["seconds"] = round(clock() - started, 1)
    return totals
