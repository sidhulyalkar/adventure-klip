"""Read-only diagnostic exports for activity filtering and rider review.

All results are provisional unless human-labelled. No AI analysis modifies the
source media, and review overlays are NEVER published as confirmed injuries.
"""
from __future__ import annotations

import json
from pathlib import Path

from .catalog import connect
from .editorial_quality import _scene_frames, raw_motion_for_asset
from .review_signals import diagnostic_summary
from .safety import protect_source_output


def diagnostics_for_workspace(workspace: Path, *, asset_id: str | None = None) -> dict:
    workspace = Path(workspace).expanduser().resolve()
    assets = []
    with connect(workspace) as db:
        rows = list(db.execute("SELECT * FROM assets ORDER BY created_at,id"))
        if asset_id is not None:
            rows = [r for r in rows if r["id"] == asset_id]
            if not rows:
                raise ValueError(f"Unknown asset ID: {asset_id}")
        for row in rows:
            events = [dict(e) for e in db.execute(
                "SELECT id,origin,kind,start_ms,end_ms,score FROM events "
                "WHERE asset_id=? ORDER BY start_ms", (row["id"],))]
            doc = diagnostic_summary(dict(row), _scene_frames(db,workspace,row["id"]),
                                     raw_motion_for_asset(db,workspace,row["id"]), events)
            assets.append(doc)
    return {"schemaVersion": 1, "assets": assets, "privacy": "local_review_only"}


def _vtt_timestamp(ms: int) -> str:
    hours, remainder = divmod(max(0, ms), 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def overlay_cues(doc: dict, *, mode: str = "review") -> list[dict]:
    """Timed overlay metadata; public mode cannot expose unverified claims.

    Public captions require a separate user approval workflow. No diagnostic
    observation, including manual incidents, becomes public in this module.
    """
    if mode not in ("review", "public"):
        raise ValueError("Mode must be review or public")
    if mode == "public":
        return []
    out = []
    for cue in doc.get("reviewCues", []):
        start, end = int(cue["startMs"]), int(cue["endMs"])
        if end <= start:
            continue
        title = str(cue["title"])
        if cue.get("status") == "unverified" and not title.startswith("Possible"):
            title = "Review: " + title
        out.append({
            "startMs": start, "endMs": end, "title": title,
            "detail": str(cue["message"]), "category": cue["category"],
            "status": cue["status"], "overlayRegion": "lower_safe",
            "privacy": "private_review", "sourceAssetId": doc["assetId"],
            "evidence": cue.get("evidence", {}),
        })
    return out


def render_vtt(cues: list[dict]) -> str:
    lines = ["WEBVTT", "", "NOTE Local-only review annotations. Not performance, health or injury diagnoses.", ""]
    for i, cue in enumerate(sorted(cues, key=lambda c: (c["startMs"], c["endMs"])), 1):
        title = str(cue["title"]).replace("\r", " ").replace("\n", " ").strip()
        detail = str(cue["detail"]).replace("\r", " ").replace("\n", " ").strip()
        # WebVTT is text, not HTML. Keep display concise.
        lines.extend([str(i), f"{_vtt_timestamp(int(cue['startMs']))} --> "
                     f"{_vtt_timestamp(int(cue['endMs']))}",
                     title[:100], detail[:180], ""])
    return "\n".join(lines)


def write_diagnostics(workspace: Path, *, asset_id: str | None = None,
                      output_dir: Path | None = None) -> dict:
    """Write JSON + private review .vtt inside workspace, never near originals."""
    workspace = Path(workspace).expanduser().resolve()
    target = Path(output_dir or workspace / "exports" / "diagnostics").expanduser().resolve()
    if not target.is_relative_to(workspace):
        raise ValueError("Diagnostic output directory must be inside the Klip workspace")
    response = diagnostics_for_workspace(workspace, asset_id=asset_id)
    paths = []
    with connect(workspace) as db:
        for doc in response["assets"]:
            name = doc["assetId"]
            json_path = target / f"{name}-diagnostics.json"
            vtt_path = target / f"{name}-review.vtt"
            protect_source_output(db, json_path)
            protect_source_output(db, vtt_path)
            target.mkdir(parents=True, exist_ok=True)
            # Atomically replace workspace artifacts; source media is untouched.
            cues = overlay_cues(doc)
            tmp_json = json_path.with_suffix(".tmp")
            tmp_vtt = vtt_path.with_suffix(".tmp")
            tmp_json.write_text(json.dumps({
                **doc, "overlays": cues}, indent=2, allow_nan=False)+"\n")
            tmp_vtt.write_text(render_vtt(cues), encoding="utf-8")
            tmp_json.replace(json_path)
            tmp_vtt.replace(vtt_path)
            paths.append({"assetId": name, "json": str(json_path),
                          "vtt": str(vtt_path),
                          "states": len(doc["contextStates"]),
                          "cameraDisturbances": len(doc["cameraDisturbances"]),
                          "reviewedIncidents": len(doc["reviewedIncidents"])})
    return {"generated": len(paths), "paths": paths,
            "warning": "Private, unverified review cues; no confirmed crashes or automatic public overlays"}
