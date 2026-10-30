"""Transactional SQLite catalog for assets, provenance, analyses and curated events."""
from __future__ import annotations
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS assets (
 id TEXT PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, stored_path TEXT NOT NULL,
 size_bytes INTEGER NOT NULL, suffix TEXT NOT NULL, duration REAL,
 capture_time TEXT, activity TEXT NOT NULL DEFAULT 'unknown',
 metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS imports (
 source_key TEXT NOT NULL, asset_id TEXT NOT NULL REFERENCES assets(id),
 original_name TEXT NOT NULL, original_path TEXT NOT NULL, size_bytes INTEGER NOT NULL,
 mtime_ns INTEGER NOT NULL, quick_signature TEXT NOT NULL,
 imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (source_key, asset_id)
);
CREATE INDEX IF NOT EXISTS imports_source_key ON imports(source_key);
CREATE TABLE IF NOT EXISTS events (
 id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id),
 kind TEXT NOT NULL, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
 confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
 score REAL NOT NULL CHECK(score BETWEEN 0 AND 1),
 origin TEXT NOT NULL, version TEXT NOT NULL, evidence_json TEXT NOT NULL DEFAULT '{}',
 CHECK (end_ms > start_ms)
);
CREATE INDEX IF NOT EXISTS events_asset ON events(asset_id);
CREATE TABLE IF NOT EXISTS episodes (
 id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id),
 kind TEXT NOT NULL, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
 score REAL NOT NULL CHECK(score BETWEEN 0 AND 1),
 event_ids_json TEXT NOT NULL, evidence_json TEXT NOT NULL,
 origin TEXT NOT NULL, version TEXT NOT NULL,
 CHECK (end_ms > start_ms)
);
CREATE INDEX IF NOT EXISTS episodes_asset ON episodes(asset_id);
CREATE TABLE IF NOT EXISTS analyses (
 asset_id TEXT NOT NULL REFERENCES assets(id), stage TEXT NOT NULL,
 version TEXT NOT NULL, status TEXT NOT NULL, error TEXT,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (asset_id, stage)
);
CREATE TABLE IF NOT EXISTS artifacts (
 asset_id TEXT NOT NULL REFERENCES assets(id), version TEXT NOT NULL,
 filename TEXT NOT NULL, sha256 TEXT NOT NULL,
 PRIMARY KEY (asset_id, version)
);
CREATE TABLE IF NOT EXISTS bindings (
 id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(id),
 world_entity_id TEXT NOT NULL, match_type TEXT NOT NULL,
 review_state TEXT NOT NULL CHECK(review_state IN ('proposed', 'approved', 'rejected')),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


@contextmanager
def connect(root: Path) -> Iterator[sqlite3.Connection]:
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / "catalog.sqlite3", timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=30000")
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def as_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def add_events(db: sqlite3.Connection, asset_id: str, events: list[dict], origin: str,
               version: str) -> int:
    # Preserve hand-curated labels when a model is updated.
    db.execute("DELETE FROM episodes WHERE asset_id=? AND origin='derived'", (asset_id,))
    db.execute("DELETE FROM events WHERE asset_id=? AND origin=?", (asset_id, origin))
    for ev in events:
        db.execute(
            """INSERT INTO events (id,asset_id,kind,start_ms,end_ms,confidence,score,
               origin,version,evidence_json) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (ev["id"], asset_id, ev["kind"], ev["start_ms"], ev["end_ms"],
             ev["confidence"], ev["score"], origin, version,
             json.dumps(ev.get("evidence", {}), sort_keys=True)),
        )
    return len(events)


def all_events(db: sqlite3.Connection, asset_id: str) -> list[dict]:
    return [dict(r) for r in db.execute(
        "SELECT * FROM events WHERE asset_id=? ORDER BY start_ms, end_ms, id", (asset_id,))]


def put_episodes(db: sqlite3.Connection, asset_id: str, episodes: list[dict],
                 version: str = "episodes-v1") -> None:
    db.execute("DELETE FROM episodes WHERE asset_id=? AND origin='derived'", (asset_id,))
    for ep in episodes:
        db.execute(
            """INSERT INTO episodes (id,asset_id,kind,start_ms,end_ms,score,
               event_ids_json,evidence_json,origin,version) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (ep["id"],asset_id,ep["kind"],ep["start_ms"],ep["end_ms"],ep["score"],
             json.dumps(ep["event_ids"]),json.dumps(ep.get("evidence",{}),sort_keys=True),
             "derived",version),
        )
