"""A record of every run, written automatically.

Each command that does work writes REPORTS/runs/<time>-<command>.json: the code commit (and
whether the checkout had uncommitted changes), module versions, arguments (home folder
shown as ~), start, duration, exit status and the error if any. `export_markdown` turns
the records into a table for logs/experiments/ with private paths already redacted, so the
research record can be committed without leaking the footage layout.
"""
from __future__ import annotations

from datetime import datetime, timezone
import importlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

VERSION = "klip-run-record-v1"
MODULES = ("discovery_experiment", "judge_panel", "story_harness", "highlight_compile",
           "judge_reliability", "boundary_review", "holdout", "blind_ab", "owner_labels")
READ_ONLY = {"status", "show", "propose", "profile", "runs"}


def redact(text: str) -> str:
    home = str(Path.home())
    out = str(text).replace(home, "~")
    return out.replace("/Volumes/", "<volume>/")


def _git() -> dict:
    root = Path(__file__).resolve().parents[2]
    try:
        commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                                text=True, timeout=10).stdout.strip()
        dirty = bool(subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                                    capture_output=True, text=True, timeout=10).stdout.strip())
        branch = subprocess.run(["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
                                capture_output=True, text=True, timeout=10).stdout.strip()
        return {"commit": commit or None, "branch": branch or None, "uncommittedChanges": dirty}
    except (OSError, subprocess.SubprocessError):
        return {"commit": None}


def _versions() -> dict:
    out = {}
    for name in MODULES:
        try:
            out[name] = getattr(importlib.import_module(f"adventure_intelligence.{name}"),
                                "VERSION", None)
        except ImportError:
            continue
    return out


def _reports(argv: list[str]) -> Path | None:
    if "--reports" in argv:
        i = argv.index("--reports")
        if i + 1 < len(argv):
            return Path(argv[i + 1]).expanduser()
    return None


def main_recorded(main, script: str, argv: list[str] | None = None) -> int:
    """Run main(argv) and record it, unless it is a dry run or a read-only command."""
    argv = list(sys.argv[1:] if argv is None else argv)
    reports = _reports(argv)
    command = argv[0] if argv and not argv[0].startswith("-") else ""
    if reports is None or "--dry-run" in argv or command in READ_ONLY:
        return main(argv)
    started = datetime.now(timezone.utc)
    began = time.monotonic()
    code, error = 2, None
    try:
        code = main(argv)
        return code
    except BaseException as exc:          # recorded, then re-raised unchanged
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        doc = {"version": VERSION, "script": script, "command": command,
               "argv": [redact(a) for a in argv],
               "startedAt": started.isoformat(timespec="seconds"),
               "seconds": round(time.monotonic() - began, 1), "exitCode": code,
               "error": redact(error) if error else None, "code": _git(),
               "modules": _versions(), "python": platform.python_version(),
               "machine": platform.machine(), "system": platform.system()}
        folder = reports / "runs"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            name = f"{started.strftime('%Y%m%dT%H%M%S%fZ')}-{script}{'-' + command if command else ''}.json"
            (folder / name).write_text(json.dumps(doc, indent=2), encoding="utf-8")
        except OSError:
            pass                           # a record must never break the run itself


def load(reports: Path) -> list[dict]:
    out = []
    for path in sorted((Path(reports) / "runs").glob("*.json")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def export_markdown(records: list[dict]) -> str:
    lines = ["| started (UTC) | script | command | minutes | exit | commit | notes |",
             "|---|---|---|---|---|---|---|"]
    for r in records:
        commit = (r.get("code") or {}).get("commit") or "?"
        dirty = " (+local changes)" if (r.get("code") or {}).get("uncommittedChanges") else ""
        lines.append(f"| {r['startedAt']} | {r['script']} | {r.get('command') or ''} | "
                     f"{r['seconds'] / 60:.1f} | {r['exitCode']} | {commit[:8]}{dirty} | "
                     f"{redact(r.get('error') or '')} |")
    return "\n".join(lines) + "\n"
