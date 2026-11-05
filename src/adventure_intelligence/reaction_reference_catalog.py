"""No-download public reaction-reference catalog audit.

A research URL is not a training license. This module is deliberately
incapable of fetching audiovisual content, transcripts or API engagement
statistics. Licensed media require a separately authorized ingest path.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import json
import re

VERSION="klip-public-reaction-reference-candidates-v1"
_ID=re.compile(r"^[A-Za-z0-9_-]{11}$")
_MAX_JSON=2_000_000
_REQUIRED_RIGHTS=(
    "trainingAuthorization","platformAccessPermission",
    "hostVideoRightsPermission","embeddedFootageRightsPermission",
    "audioAndMusicRightsPermission","participantPrivacyOrVoicePermission",
)


def audit_reaction_references(catalog:Path)->dict:
    path=Path(catalog).expanduser().resolve()
    if not path.is_file() or path.stat().st_size>_MAX_JSON:
        raise ValueError("Reference catalog is absent or too large")
    try:
        data=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,UnicodeError,ValueError) as exc:
        raise ValueError("Invalid reference catalog JSON") from exc
    if (not isinstance(data,dict) or data.get("version")!=VERSION
            or not isinstance(data.get("references"),list)
            or not 1<=len(data["references"])<=200):
        raise ValueError("Expected a bounded public-reference-only catalog")
    if any(data.get(k) is not False for k in (
            "mediaDownloaded","framesExtracted","transcriptsDownloaded",
            "modelTrainingPerformed","ownershipOrTrainingRightsVerified")):
        raise ValueError("This catalog may not claim audiovisual extraction, training, or rights clearance")
    seen=set()
    channels=Counter()
    sports=Counter()
    perspectives=Counter()
    for item in data["references"]:
        if not isinstance(item,dict):
            raise ValueError("Malformed public reference item")
        vid=item.get("videoId")
        if not isinstance(vid,str) or not _ID.fullmatch(vid) or vid in seen:
            raise ValueError("Invalid or duplicated YouTube video ID")
        seen.add(vid)
        url=item.get("url")
        if url!=f"https://www.youtube.com/watch?v={vid}":
            raise ValueError("Expected unchanged canonical public YouTube watch URL")
        if any(not isinstance(item.get(k),str) or not item[k].strip()
               for k in ("id","title","channel","sport","perspective","evidenceRole")):
            raise ValueError("Missing human research reference metadata")
        if (item.get("referenceStatus")!="public_link_only"
                or item.get("humanReferenceOnly") is not True
                or item.get("licensedBytesAvailable") is not False
                or item.get("modelTrainingAllowed") is not False
                or item.get("derivedLabelsAllowed") is not False
                or item.get("sourceContentReviewedInFull") is not False
                or any(item.get(k) not in ("not_obtained","not_assessed")
                       for k in _REQUIRED_RIGHTS)):
            raise ValueError(
                "Public reference catalog cannot contain training-approved or processed media; "
                "use separately documented, rights-cleared assets instead")
        channels[item["channel"]]+=1
        sports[item["sport"]]+=1
        perspectives[item["perspective"]]+=1
    return {
        "version":"klip-reaction-reference-audit-v1",
        "status":"reference_only_not_training_eligible",
        "publicReferenceLinks":len(seen),
        "trainingEligible":0,"licenseOrConsentGrantsVerified":0,
        "originalsDownloaded":0,"reactionVideosDownloaded":0,
        "transcriptsDownloaded":0,"modelsTrained":0,
        "sourceVideoReads":0,"externalUploads":0,
        "channels":dict(sorted(channels.items())),
        "sports":dict(sorted(sports.items())),
        "perspectives":dict(sorted(perspectives.items())),
        "nextAction":"Review public videos as research references; obtain separate "
                     "documented rights for host reaction, underlying clips, audio "
                     "and intended ML training before creating any media corpus.",
        "limits":[
            "Valid URL metadata is not proof a YouTube video exists or is accessible now",
            "A catalog entry is not a license, download approval, transcript grant, or participant consent",
            "Popularity and presenter expression must not be used as gold highlight labels",
        ],
    }
