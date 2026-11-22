"""One heavy local-model run at a time.

Two judge / story / benchmark runs sharing Ollama load two large models at once, which on
a 64 GB Mac pushes inference past the GPU working set: calls slow several-fold and time
out, and timings measured under contention are wrong. `exclusive()` takes a lock file
(flock) in ~/.klip/locks, since Ollama is shared by the whole machine (KLIP_LOCK_DIR
overrides it); a second run waits (and says who holds the lock) or,
with wait=False, stops. The lock is released when the process ends, even on a crash.
Nested use in one process (the pipeline calling the judge) is re-entrant.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import sys
import time

_HELD: dict[str, int] = {}   # lock path -> depth, this process


def _holder(path: Path) -> str:
    try:
        info = json.loads(path.read_text(encoding="utf-8") or "{}")
        return f"pid {info.get('pid')} since {info.get('since')}: {info.get('command')}"
    except (OSError, ValueError):
        return "another process"


@contextmanager
def exclusive(name: str = "ollama", *, folder: Path | None = None, wait: bool = True,
              poll_seconds: float = 30.0, log=None):
    base = folder or Path(os.environ.get("KLIP_LOCK_DIR") or Path.home() / ".klip" / "locks")
    path = Path(base).expanduser() / f"{name}.lock"
    key = str(path.resolve())
    if _HELD.get(key):
        _HELD[key] += 1
        try:
            yield path
        finally:
            _HELD[key] -= 1
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+", encoding="utf-8")
    try:
        told = False
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if not wait:
                    raise RuntimeError(f"Another model run holds {path.name} ({_holder(path)})")
                if not told:
                    (log or (lambda m: print(m, file=sys.stderr, flush=True)))(
                        f"waiting for the model lock ({_holder(path)})")
                    told = True
                time.sleep(poll_seconds)
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps({"pid": os.getpid(),
                                 "since": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                 "command": " ".join(Path(sys.argv[0]).name.split() + sys.argv[1:2])}))
        handle.flush()
        _HELD[key] = 1
        try:
            yield path
        finally:
            _HELD.pop(key, None)
            handle.seek(0)
            handle.truncate()
            fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        handle.close()
