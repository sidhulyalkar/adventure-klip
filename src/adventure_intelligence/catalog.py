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
CREATE TABLE IF NOT EXISTS event_revisions (
 revision_id TEXT PRIMARY KEY, event_id TEXT NOT NULL, asset_id TEXT NOT NULL REFERENCES assets(id),
 payload TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS review_history (
 id INTEGER PRIMARY KEY, binding_id TEXT NOT NULL,
 revision_id TEXT NOT NULL REFERENCES event_revisions(revision_id),
 decision TEXT NOT NULL, payload TEXT NOT NULL, reason TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS semantic_jobs (
 asset_id TEXT NOT NULL REFERENCES assets(id), config_id TEXT NOT NULL,
 config_json TEXT NOT NULL, status TEXT NOT NULL, error TEXT,
 filename TEXT, sha256 TEXT, PRIMARY KEY(asset_id,config_id)
);
CREATE TABLE IF NOT EXISTS semantic_frames (
 asset_id TEXT NOT NULL, config_id TEXT NOT NULL, t_ms INTEGER NOT NULL,
 payload TEXT NOT NULL, sha256 TEXT NOT NULL,
 PRIMARY KEY(asset_id,config_id,t_ms),
 FOREIGN KEY(asset_id,config_id) REFERENCES semantic_jobs(asset_id,config_id)
);
CREATE TABLE IF NOT EXISTS semantic_active (
 asset_id TEXT NOT NULL, provider TEXT NOT NULL, config_id TEXT NOT NULL,
 PRIMARY KEY(asset_id,provider),
 FOREIGN KEY(asset_id,config_id) REFERENCES semantic_jobs(asset_id,config_id)
);
CREATE TABLE IF NOT EXISTS example_revisions (
 revision_id TEXT PRIMARY KEY,
 example_id TEXT NOT NULL,
 previous_revision_id TEXT,
 asset_id TEXT NOT NULL REFERENCES assets(id),
 start_ms INTEGER NOT NULL,
 end_ms INTEGER NOT NULL,
 label TEXT NOT NULL,
 decision TEXT NOT NULL CHECK(decision IN ('confirmed','rejected','uncertain')),
 highlight TEXT NOT NULL CHECK(highlight IN ('keep','skip','neutral')),
 reviewer_note TEXT NOT NULL DEFAULT '',
 reviewer TEXT NOT NULL DEFAULT 'local_user',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CHECK(end_ms > start_ms),
 FOREIGN KEY(previous_revision_id) REFERENCES example_revisions(revision_id)
);
CREATE INDEX IF NOT EXISTS example_reviews_by_example
 ON example_revisions(example_id,created_at);
CREATE INDEX IF NOT EXISTS example_reviews_by_asset
 ON example_revisions(asset_id,start_ms,end_ms);
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
    from .history import snapshot_event, record_review
    incoming = {}
    for ev in events:
        if ev['id'] in incoming:
            raise ValueError('Duplicate event ID')
        incoming[ev['id']] = dict(id=ev['id'],asset_id=asset_id,kind=ev['kind'],
            start_ms=ev['start_ms'],end_ms=ev['end_ms'],confidence=ev['confidence'],
            score=ev['score'],origin=origin,version=version,
            evidence_json=json.dumps(ev.get('evidence',{}),sort_keys=True,allow_nan=False))
        existing = db.execute('SELECT asset_id,origin FROM events WHERE id=?',(ev['id'],)).fetchone()
        if existing and (existing['asset_id']!=asset_id or existing['origin']!=origin):
            raise ValueError('Event ID collides with another asset or origin')
    previous = list(db.execute('SELECT * FROM events WHERE asset_id=? AND origin=?',(asset_id,origin)))
    for old in previous:
        snapshot_event(db,old)
        if incoming.get(old['id']) == dict(old):
            continue
        for binding in db.execute('SELECT * FROM bindings WHERE event_id=?',(old['id'],)).fetchall():
            record_review(db,binding,old,'invalidated_by_event_revision')
        db.execute('DELETE FROM bindings WHERE event_id=?',(old['id'],))
        db.execute('DELETE FROM events WHERE id=?',(old['id'],))
    db.execute("DELETE FROM episodes WHERE asset_id=? AND origin='derived'", (asset_id,))
    for ev in incoming.values():
        db.execute(
            """INSERT INTO events (id,asset_id,kind,start_ms,end_ms,confidence,score,
               origin,version,evidence_json) VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING""",
            (ev["id"], asset_id, ev["kind"], ev["start_ms"], ev["end_ms"],
             ev["confidence"], ev["score"], origin, version,
             ev["evidence_json"]),
        )
        snapshot_event(db,db.execute('SELECT * FROM events WHERE id=?',(ev['id'],)).fetchone())
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
