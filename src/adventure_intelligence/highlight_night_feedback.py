"""Find existing human review evidence for an immutable Klip pilot.

Only reads small private JSON documents in the workspace or explicit
user review export paths. Never looks at catalog stored_path or removable
media. Does not invent an empty review or call models.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json
import re

from .highlight_feedback_discovery import VERSION as PLAN_VERSION
from .highlight_curation_review import REVIEW_VERSION

MAX_BYTES=3_000_000
SHA=re.compile(r"^[a-f0-9]{64}$")


def _load(path:Path)->dict|None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        n=path.stat().st_size
        if not 2<=n<=MAX_BYTES:
            return None
        obj=json.loads(path.read_bytes())
        return obj if isinstance(obj,dict) else None
    except (OSError,ValueError,UnicodeDecodeError):
        return None


def _within(path:Path,roots:list[Path])->bool:
    p=path.resolve()
    return any(p.is_relative_to(x) for x in roots)


def resolve_human_feedback(workspace:Path,pilot_manifest:Path,
                           preferred_plan:Path|None=None,
                           preferred_reviews:Path|None=None)->dict:
    root=Path(workspace).expanduser().resolve()
    manifest_path=Path(pilot_manifest).expanduser().resolve()
    if (not manifest_path.is_relative_to(root) or
            manifest_path.relative_to(root).parts[0] not in ("reports","exports")):
        raise ValueError("Pilot manifest must be a workspace-local report")
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("Original frozen manifest does not exist")
    raw=manifest_path.read_bytes()
    manifest=json.loads(raw)
    digest=sha256(raw).hexdigest()
    if not isinstance(manifest,dict) or not isinstance(manifest.get("records"),list):
        raise ValueError("Original manifest is invalid")
    report_roots=[root/"reports",root/"exports"]
    allowed_reviews=[*report_roots,Path.home()/"Downloads"]
    plan_paths=[]
    if preferred_plan is not None:
        plan_paths.append(Path(preferred_plan).expanduser())
    for folder in report_roots:
        if folder.is_dir() and not folder.is_symlink():
            plan_paths.extend(folder.rglob("review-discovery-plan.json"))
    plans=[]
    seen=set()
    for p in plan_paths:
        if not _within(p,report_roots):
            continue
        canonical=str(p.resolve())
        if canonical in seen:
            continue
        seen.add(canonical)
        doc=_load(p)
        if (doc and doc.get("version")==PLAN_VERSION and
                doc.get("frozenPilotManifestSha256")==digest and
                isinstance(doc.get("decisions"),list) and doc["decisions"]):
            plans.append((p.stat().st_mtime_ns,canonical,len(doc["decisions"])))
    if plans:
        plans.sort(reverse=True)
        return {
            "version":"klip-night-feedback-discovery-v1",
            "status":"matching_existing_plan",
            "manifestSha256":digest,
            "selectedPath":plans[0][1],
            "matchedExistingPlans":len(plans),
            "reviewedWindows":plans[0][2],
            "reviewExportsMatched":0,
            "noSourceMediaReads":True,
        }

    review_paths=[]
    if preferred_reviews is not None:
        review_paths.append(Path(preferred_reviews).expanduser())
    # No recursive scan of Home, removable media, or unrelated personal data.
    downloads=Path.home()/"Downloads"
    if downloads.is_dir() and not downloads.is_symlink():
        review_paths.extend(downloads.glob("*.json"))
    for folder in report_roots:
        if folder.is_dir() and not folder.is_symlink():
            review_paths.extend(folder.rglob("*reviews*.json"))
            review_paths.extend(folder.rglob("*curation*export*.json"))
    reviews=[]
    seen.clear()
    for p in review_paths:
        if not _within(p,allowed_reviews):
            continue
        canonical=str(p.resolve())
        if canonical in seen:
            continue
        seen.add(canonical)
        doc=_load(p)
        if (doc and doc.get("version")==REVIEW_VERSION and
                doc.get("manifestSha256")==digest and
                isinstance(doc.get("reviews"),list) and doc["reviews"]):
            reviews.append((len(doc["reviews"]),p.stat().st_mtime_ns,canonical))
    if reviews:
        reviews.sort(reverse=True)
        return {
            "version":"klip-night-feedback-discovery-v1",
            "status":"matching_raw_review_export",
            "manifestSha256":digest,
            "selectedPath":reviews[0][2],
            "matchedExistingPlans":0,
            "reviewedWindows":reviews[0][0],
            "reviewExportsMatched":len(reviews),
            "noSourceMediaReads":True,
        }
    return {
        "version":"klip-night-feedback-discovery-v1",
        "status":"no_matching_human_feedback",
        "manifestSha256":digest,
        "selectedPath":None,
        "matchedExistingPlans":0,
        "reviewedWindows":0,
        "reviewExportsMatched":0,
        "action":"Export human-review JSON to ~/Downloads or set REVIEWS=/absolute/path/to/review.json. Cache-only temporal analysis can still run.",
        "noSourceMediaReads":True,
    }
