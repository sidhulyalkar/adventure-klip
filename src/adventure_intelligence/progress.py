"""Low-overhead terminal progress, bounded stage ETA and optional append-only log.

Estimates use measured progress for the current stage. They are not promises,
and a single large asset can differ considerably from a short one.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import TextIO


class Progress:
    def __init__(self, stream: TextIO | None = None, *, log_file: Path | None = None,
                 interval_seconds: float = 3.0):
        self.stream = stream if stream is not None else sys.stderr
        self.log_file = log_file
        self.interval_seconds = max(0.0, interval_seconds)
        self.started: dict[str, float] = {}
        self._last_updates: dict[str, float] = {}

    @staticmethod
    def _duration(seconds: float | None) -> str:
        if seconds is None:
            return "calculating"
        seconds = max(0, int(round(seconds)))
        if seconds < 60:
            return f"{seconds}s"
        if seconds < 3600:
            return f"{seconds // 60}m {seconds % 60:02d}s"
        return f"{seconds // 3600}h {(seconds // 60) % 60:02d}m"

    def emit(self, message: str) -> None:
        line = f"[klip] {message}"
        print(line, file=self.stream, flush=True)
        if self.log_file is not None:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            with self.log_file.open("a", encoding="utf-8") as output:
                output.write(line + "\n")

    def start(self, stage: str, total: int) -> None:
        self.started[stage] = time.monotonic()
        self.emit(f"{stage}: starting {total} item(s)")

    def note(self, stage: str, detail: str) -> None:
        self.emit(f"{stage}: {detail}")

    def item(self, stage: str, done: int, total: int, detail: str) -> None:
        elapsed = time.monotonic() - self.started.get(stage, time.monotonic())
        eta = elapsed / done * max(0, total - done) if done else None
        percent = 100 * done / total if total else 100
        self.emit(f"{stage}: {done}/{total} ({percent:.0f}%) {detail} | "
                  f"elapsed {self._duration(elapsed)} | "
                  f"stage ETA {self._duration(eta)}")

    def bytes(self, stage: str, read: int, total: int, label: str) -> None:
        """Hashing heartbeat for very large files; prints at most every interval."""
        now = time.monotonic()
        last = self._last_updates.get(stage, -float("inf"))
        if now - last < self.interval_seconds and read < total:
            return
        self._last_updates[stage] = now
        mib = 1024 * 1024
        self.emit(f"{stage}: {label}: hashed {read / mib:.1f}/"
                  f"{total / mib:.1f} MiB ({100 * read / total if total else 100:.0f}%)")

    def frames(self, stage: str, done: int, total: int, label: str) -> None:
        """Model inference heartbeat, throttled to avoid filling terminal logs."""
        now = time.monotonic()
        last = self._last_updates.get(stage, -float("inf"))
        if done < total and now - last < self.interval_seconds:
            return
        self._last_updates[stage] = now
        elapsed = now - self.started.setdefault(stage, now)
        eta = elapsed / done * max(0, total - done) if done else None
        self.emit(f"{stage}: {label}: {done}/{total} decoded frame(s) | "
                  f"elapsed {self._duration(elapsed)} | "
                  f"stage ETA {self._duration(eta)}")

