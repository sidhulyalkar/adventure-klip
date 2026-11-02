"""Deterministic, conservative hierarchical episode discovery."""
from __future__ import annotations
from hashlib import sha256
from statistics import mean, pstdev


def _make(asset_id: str, kind: str, events: list[dict], evidence: dict | None = None) -> dict:
    ordered = sorted(events, key=lambda e:(e["start_ms"], e["id"]))
    start = min(e["start_ms"] for e in ordered)
    end = max(e["end_ms"] for e in ordered)
    ids = [e["id"] for e in ordered]
    # Prefer high-scoring events, but reward unbroken continuity.
    gap = sum(max(0, ordered[i]["start_ms"] - ordered[i-1]["end_ms"])
              for i in range(1,len(ordered)))
    duration = max(1,end-start)
    flow = max(0.0,1-gap/duration)
    score = min(1,max(0,0.8*mean(e["score"] for e in ordered)+0.2*flow))
    digest = sha256(f"{asset_id}|{kind}|{'|'.join(ids)}".encode()).hexdigest()[:20]
    return {"id":f"ep-{digest}","asset_id":asset_id,"kind":kind,"start_ms":start,
            "end_ms":end,"event_ids":ids,"score":round(score,4),
            "evidence":{"continuity":round(flow,4),**(evidence or {})}}


def _groups(events: list[dict], max_gap_ms: int) -> list[list[dict]]:
    groups: list[list[dict]] = []
    for e in sorted(events,key=lambda a:(a["start_ms"],a["id"])):
        if not groups or e["start_ms"] - max(x["end_ms"] for x in groups[-1]) > max_gap_ms:
            groups.append([e])
        else:
            groups[-1].append(e)
    return groups


def build_episodes(asset_id: str, events: list[dict], duration_ms: int = 0) -> list[dict]:
    """Patterns operate on annotated events, not guessed biomechanics.

    Episodes may overlap. A sequence of turns in trees can also be a carving episode.
    """
    out: list[dict] = []
    patterns = [
        ("jump_chain", {"jump"}, 2, 12000),
        ("carving_flow", {"turn", "carve"}, 4, 6000),
        ("tree_weaving", {"tree_weave", "obstacle_pass"}, 3, 8000),
        ("technical_feature_chain", {"jump", "drop", "berm", "rock_garden"}, 3, 12000),
        ("active_sequence", {"visual_activity"}, 2, 7000),
    ]
    for kind, kinds, required, gap in patterns:
        candidates=[e for e in events if e["kind"] in kinds]
        for group in _groups(candidates,gap):
            if len(group)<required:
                continue
            evidence={"pattern":kind}
            if kind=="carving_flow":
                starts=[e["start_ms"] for e in group]
                intervals=[b-a for a,b in zip(starts,starts[1:])]
                cv=(pstdev(intervals)/mean(intervals)) if intervals and mean(intervals)>0 else 1
                if cv>0.8:
                    continue
                evidence["turn_interval_cv"]=round(cv,4)
            out.append(_make(asset_id,kind,group,evidence))
    # Low-level camera movement can automatically suggest long intervals to review.
    # Do NOT rename these episodes "turns", "runs" or "jumps" without corroboration.
    tentative = {"rhythmic_camera_sweep": ("camera_rhythm_candidate", 1500),
                 "sustained_camera_motion": ("sustained_motion_candidate", 2500)}
    for e in events:
        if e["kind"] in tentative:
            title, minimum = tentative[e["kind"]]
            if e["end_ms"]-e["start_ms"] >= minimum:
                out.append(_make(asset_id,title,[e],{"verified_action":False,
                         "requires_review":True}))
    # Group tracks from the same explicitly labeled subject, never different animals.
    by_subject: dict[str,list[dict]]={}
    for e in events:
        if e["kind"]!="animal_presence":
            continue
        import json
        try:
            subject=json.loads(e.get("evidence_json","{}") or "{}").get("subject_id")
        except (ValueError,TypeError):
            subject=None
        if subject:
            by_subject.setdefault(str(subject),[]).append(e)
    for subject, matches in by_subject.items():
        for group in _groups(matches,15000):
            if len(group)>=2:
                out.append(_make(asset_id,"wildlife_encounter",group,{"subject_id":subject}))
    # Only a human-authored crash/near-miss label can create an incident
    # episode; camera shake and optical flow never create a verified crash.
    manual_incident_kinds={"crash","fall","wipeout","near_miss",
                           "unstable_landing","loss_of_control"}
    for e in events:
        if e.get("origin")=="manual" and e["kind"] in manual_incident_kinds:
            out.append(_make(asset_id, "incident_review", [e],
                             {"requires_review": True,
                              "source": "manual_event_label",
                              "kind": e["kind"]}))
    # The full recording is not automatically called a downhill run.
    if events and duration_ms>0:
        summary=_make(asset_id,"recording_summary",events,
                         {"recording_duration_ms":duration_ms})
        summary["start_ms"]=0
        summary["end_ms"]=max(duration_ms,summary["end_ms"])
        out.append(summary)
    return sorted(out,key=lambda e:(e["start_ms"],e["kind"],e["id"]))
