"""Immutable event snapshots and append-only review decisions."""
import json
from hashlib import sha256


def snapshot_event(db, event):
    payload = json.dumps(dict(event), sort_keys=True, separators=(',', ':'), allow_nan=False)
    revision = sha256(payload.encode()).hexdigest()
    db.execute('''INSERT OR IGNORE INTO event_revisions(revision_id,event_id,asset_id,payload)
                  VALUES (?,?,?,?)''', (revision,event['id'],event['asset_id'],payload))
    return revision


def record_review(db, binding, event, reason):
    revision = snapshot_event(db,event)
    db.execute('''INSERT INTO review_history(binding_id,revision_id,decision,payload,reason)
                  VALUES (?,?,?,?,?)''', (binding['id'],revision,binding['review_state'],
                  json.dumps(dict(binding),sort_keys=True),reason))


def event_history(workspace, asset_id):
    from .catalog import connect
    with connect(workspace) as db:
        revisions = [dict(r) for r in db.execute(
            'SELECT * FROM event_revisions WHERE asset_id=? ORDER BY created_at,revision_id',
            (asset_id,))]
        reviews = [dict(r) for r in db.execute('''SELECT h.* FROM review_history h
            JOIN event_revisions e ON e.revision_id=h.revision_id
            WHERE e.asset_id=? ORDER BY h.id''',(asset_id,))]
    return {'assetId':asset_id,'revisions':revisions,'reviews':reviews}
