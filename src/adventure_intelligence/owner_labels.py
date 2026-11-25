"""Append-only owner labels that are not keep/skip judgments.

The evidence ledger holds keep/skip/unsure judgments of intervals. Other owner answers get
their own namespace, one JSONL file per kind, under REPORTS/owner-ledgers/:

    boundary-labels.jsonl   where a clip should start and end (from the reel gallery)
    fact-labels.jsonl       whether a judged fact is true (from the fact-check deck)
    claim-labels.jsonl      whether a story's claim is true (from the story review page)

Every record names the deck it came from, the SHA-256 of the downloaded answers file and
the import time. Importing the same answer twice adds nothing; a changed answer is a new
record (the latest per item wins when read). Model output never goes in here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path

VERSION = "klip-owner-labels-v1"
KINDS = ("boundary", "fact", "claim")


def path_for(reports: Path, kind: str) -> Path:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    return Path(reports) / "owner-ledgers" / f"{kind}-labels.jsonl"


def _id(body: dict) -> str:
    return sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def load(reports: Path, kind: str, *, latest: bool = True) -> list[dict]:
    path = path_for(reports, kind)
    if not path.is_file():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    if not latest:
        return rows
    newest: dict[str, dict] = {}
    for row in rows:            # file order is import order; the later answer wins
        newest[row["item"]] = row
    return list(newest.values())


def append(reports: Path, kind: str, *, deck: str, answers_sha256: str,
           items: list[dict]) -> dict:
    """items: [{"item": stable id, ...payload}]. Returns counts."""
    path = path_for(reports, kind)
    seen = {r["labelId"] for r in load(reports, kind, latest=False)}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    new = []
    for it in items:
        if "item" not in it:
            raise ValueError("Every label needs an item id")
        body = {"version": VERSION, "kind": kind, "reviewer": "owner", "deck": deck, **it}
        label_id = _id(body)
        if label_id in seen:
            continue
        seen.add(label_id)
        new.append({**body, "labelId": label_id, "answersSha256": answers_sha256,
                    "importedAt": now})
    if new:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            for row in new:
                f.write(json.dumps(row, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
    return {"kind": kind, "added": len(new), "alreadyPresent": len(items) - len(new),
            "file": str(path)}
