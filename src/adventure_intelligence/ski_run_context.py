"""Klip: source-safe expansion from a 20s discovery window to a run context.

An extended preview is NOT an automatically detected continuous ski run.
It is an explicitly bounded, single-source context *hypothesis* for reviewing
multiple terrain features and marking complete lines without losing chronology.
"""
from __future__ import annotations

from hashlib import sha256
import json
import math
from pathlib import Path
import re

from .auto_scout_v2 import _bounded_workspace_path
from .full_archive_campaign import AUDIT_VERSION, SCAN_VERSION

VERSION = "klip-ski-source-run-context-v1"
OWNER_VERSION = "klip-owner-run-annotations-v1"
SHA = re.compile(r"[0-9a-f]{64}\Z")
SOURCE_PREFIX = re.compile(r"[0-9a-f]{12,64}\Z")
FEATURES = frozenset((
    "jump", "rail", "box", "halfpipe", "tree_section", "speed_flow",
    "moguls", "turn_sequence", "transition", "stopped", "lift", "unknown",
))


def match_anchor(rows: list[dict], source_prefix: str, start_ms: int,
                 end_ms: int) -> dict:
    """Match exactly one frozen preview by source SHA prefix and source times."""
    if not isinstance(source_prefix, str) or not SOURCE_PREFIX.fullmatch(source_prefix):
        raise ValueError("Source identifier must be a 12..64-character lowercase SHA prefix")
    if type(start_ms) is not int or type(end_ms) is not int or not 0 <= start_ms < end_ms:
        raise ValueError("Invalid source-relative anchor interval")
    matches = [
        row for row in rows
        if row["sourceSha256"].startswith(source_prefix)
        and row["sourceStartMs"] == start_ms
        and row["sourceEndMs"] == end_ms
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Expected one source SHA + time anchor, found {len(matches)}. "
            "Check the source prefix and source-relative times in the blind review."
        )
    return matches[0]


def plan_context(anchor: dict, source_duration_ms: int, *, before_s: int=40,
                 after_s: int=180, max_s: int=240) -> dict:
    """Bound an exploration window to the ONE original source timeline."""
    if (type(source_duration_ms) is not int or not 1000 <= source_duration_ms <= 86_400_000
            or any(type(x) is not int for x in (before_s, after_s, max_s))
            or not 0 <= before_s <= 300 or not 0 <= after_s <= 480
            or not 30 <= max_s <= 600):
        raise ValueError("Invalid bounded run-context configuration")
    start, end = anchor["sourceStartMs"], anchor["sourceEndMs"]
    if (type(start) is not int or type(end) is not int
            or not 0 <= start < end <= source_duration_ms
            or end-start > max_s*1000):
        raise ValueError("Frozen anchor is outside source duration or exceeds context budget")
    a=max(0,start-before_s*1000)
    b=min(source_duration_ms,end+after_s*1000)
    if b-a>max_s*1000:
        # Preserve the full anchor and prioritize forward chronology because
        # an unseen third jump may occur after the old 20-second cutoff.
        b=min(b,a+max_s*1000)
        if b<end:
            a=max(0,end-max_s*1000)
            b=min(source_duration_ms,a+max_s*1000)
    if not a <= start < end <= b or b-a>max_s*1000:
        raise ValueError("Run-context expansion failed to contain the original clip")
    original=end-start
    return {
        "version":VERSION,"recordId":anchor["recordId"],
        "sourceAssetSha256":anchor["sourceSha256"],
        "sourceProxySha256":anchor["proxySha256"],
        "sourceDurationMs":source_duration_ms,
        "anchorSourceStartMs":start,"anchorSourceEndMs":end,
        "contextSourceStartMs":a,"contextSourceEndMs":b,
        "contextDurationMs":b-a,
        "extensionBeforeMs":start-a,"extensionAfterMs":b-end,
        "oldPreviewCoverageFraction":round(original/(b-a),4),
        "kind":"expanded_single_source_run_context_hypothesis",
        "sourceContinuousAcrossContextByProvenance":True,
        "continuousAthleticRunVerified":False,
        "specificJumpOrLandingVerified":False,
        "actualRunBoundariesVerified":False,
        "nativePtsAlignmentVerified":False,
        "notASuccessfulTrickClassification":True,
        "sourceOriginalReadForPlanning":False,
        "humanRunBoundaryAnnotationRequired":True,
    }


def _private_json(path: Path, maximum: int=20_000_000) -> dict:
    if (path.is_symlink() or not path.is_file()
            or not 2 <= path.stat().st_size <= maximum):
        raise ValueError("Missing or unsafe frozen campaign JSON")
    value=json.loads(path.read_bytes())
    if not isinstance(value,dict):
        raise ValueError("Expected frozen campaign JSON object")
    return value


def find_qualified_audit(workspace: Path, source_sha: str, *,
                         audit_file: Path | None=None) -> tuple[dict, dict, Path]:
    """Find the newest SHA-linked, already-scanned audit for the source.

    This checks only private JSON receipts. NO stats/realpaths of the original
    camera path occur during a dry-run. An explicit audit is preferred when
    the workspace contains multiple frozen campaign versions.
    """
    if not SHA.fullmatch(source_sha):
        raise ValueError("Invalid full source SHA")
    root=Path(workspace).expanduser().resolve(strict=True)
    if audit_file is not None:
        audit_path=_bounded_workspace_path(root,Path(audit_file).parent,existing=True)/Path(audit_file).name
        paths=[audit_path]
    else:
        paths=sorted((root/"reports").glob("*/audit/campaign-audit.json"))
        # Nested campaign reports, if any, are discovered as metadata only.
        paths+=sorted((root/"reports").glob("*/*/audit/campaign-audit.json"))
    matches=[]
    for path in paths:
        try:
            audit=_private_json(path)
            if (audit.get("version")!=AUDIT_VERSION
                    or audit.get("workspace")!=str(root)
                    or not isinstance(audit.get("assets"),list)):
                continue
            assets=[x for x in audit["assets"] if
                    isinstance(x,dict) and x.get("sourceAssetSha256")==source_sha]
            if len(assets)!=1:
                continue
            asset=assets[0]
            duration=asset.get("durationMsFromCatalog")
            source_bytes=asset.get("sourceBytesFromCatalog")
            name=asset.get("storedPathAsCatalogued")
            if (type(duration) is not int or not 1000<=duration<=86_400_000
                    or type(source_bytes) is not int or source_bytes<100
                    or not isinstance(name,str) or not name):
                continue
            scan=path.parent.parent/"coarse-scan"
            state=_private_json(scan/"scan-state.json")
            audit_bytes=path.read_bytes()
            if (state.get("version")!=SCAN_VERSION
                    or state.get("campaignAuditSha256")!=sha256(audit_bytes).hexdigest()):
                continue
            rec=(state.get("assets") or {}).get(source_sha) or {}
            if rec.get("status")!="coarse_completed":
                continue
            per_source=scan/"per-source"/(source_sha+".json")
            receipt=_private_json(per_source)
            if (sha256(per_source.read_bytes()).hexdigest()!=rec.get("reportSha256")
                    or receipt.get("sourceAssetSha256")!=source_sha
                    or receipt.get("sourceDurationMs")!=duration
                    or receipt.get("verifiedOriginalSha256")!=source_sha
                    or receipt.get("shaVerifiedDuringThisScan") is not True):
                continue
            matches.append((path.stat().st_mtime,asset,path))
        except (ValueError,TypeError,KeyError,OSError,OverflowError,json.JSONDecodeError):
            if audit_file is not None:
                raise ValueError("Explicit source audit is invalid or not qualified")
            continue
    if not matches:
        raise ValueError("No matching SHA-qualified local campaign audit for source. "
                         "Do not guess another camera file or scan the entire archive.")
    _,asset,path=max(matches,key=lambda x:(x[0],str(x[2])))
    return asset,{"qualifiedAudit":str(path),"priorSourceShaVerified":True},path


def owner_annotation_template(plan: dict) -> dict:
    return {
        "version": OWNER_VERSION,
        "sourceAssetSha256": plan["sourceAssetSha256"],
        "anchorRecordId":plan["recordId"],
        "contextSourceStartMs":plan["contextSourceStartMs"],
        "contextSourceEndMs":plan["contextSourceEndMs"],
        "runStartMs":None,"runEndMs":None,
        "runKind":"unverified",
        "featureMarkers":[],
        "preference":"unsure",
        "sourceAbsoluteTimesApproximate":True,
        "reviewerVerifiedContinuousRun":False,
    }


def validate_owner_run(doc:dict,plan:dict) -> dict:
    expected=set(owner_annotation_template(plan))
    if not isinstance(doc,dict) or set(doc)!=expected:
        raise ValueError("Run owner annotation schema mismatch")
    if (doc["version"]!=OWNER_VERSION
            or doc["sourceAssetSha256"]!=plan["sourceAssetSha256"]
            or doc["anchorRecordId"]!=plan["recordId"]
            or doc["contextSourceStartMs"]!=plan["contextSourceStartMs"]
            or doc["contextSourceEndMs"]!=plan["contextSourceEndMs"]):
        raise ValueError("Run source context identity mismatch")
    a,b=doc["runStartMs"],doc["runEndMs"]
    if ((a is None)!=(b is None)
            or (a is not None and (type(a) is not int or type(b) is not int
              or not plan["contextSourceStartMs"]<=a<b<=plan["contextSourceEndMs"]))):
        raise ValueError("Run bounds must be a complete interval in context")
    if doc["runKind"] not in ("unverified","terrain_park","tree_line","speed_line","mixed","transit"):
        raise ValueError("Invalid run type")
    if doc["preference"] not in ("keep","skip","unsure"):
        raise ValueError("Invalid run preference")
    if (type(doc["sourceAbsoluteTimesApproximate"]) is not bool
            or doc["sourceAbsoluteTimesApproximate"] is not True
            or type(doc["reviewerVerifiedContinuousRun"]) is not bool):
        raise ValueError("Invalid run evidence basis")
    if doc["reviewerVerifiedContinuousRun"] and a is None:
        raise ValueError("Cannot verify a continuous run without boundaries")
    markers=doc["featureMarkers"]
    if not isinstance(markers,list) or len(markers)>50:
        raise ValueError("Invalid feature marker list")
    for marker in markers:
        if (not isinstance(marker,dict) or set(marker)!={"kind","sourceMs"}
                or marker["kind"] not in FEATURES
                or type(marker["sourceMs"]) is not int
                or not plan["contextSourceStartMs"]<=marker["sourceMs"]<=plan["contextSourceEndMs"]):
            raise ValueError("Feature marker must be explicit and inside context")
    return doc
