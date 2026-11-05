"""Read-only Social Lens workflow diagnosis for local personal footage artifacts.

Unlike social-lens-preflight, this checks the PERSONAL feature -> temporal ->
blind A/B pipeline. It never discovers or touches media, executes models,
contacts social platforms, repairs or rewrites existing outputs.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json
import shlex

from .social_action_lens import USER_VERSION, FEATURE_VERSION, _json
from .social_temporal_evidence import VERSION as TEMPORAL_VERSION
from .social_blind_pairs import VERSION as PAIR_VERSION


def _workspace_json(root:Path,path:Path,label:str)->tuple[dict|None,dict]:
    path=Path(path).expanduser().resolve()
    if (path==root or not path.is_relative_to(root)
            or path.relative_to(root).parts[0] not in ("reports","exports")):
        raise ValueError(f"{label} path must be inside workspace reports/ or exports/")
    state={"path":str(path),"exists":path.is_file(),
           "status":"missing","sha256":None}
    if not path.is_file():
        return None,state
    try:
        document=_json(path)
    except (ValueError,TypeError,UnicodeError,json.JSONDecodeError,OSError) as exc:
        return None,{**state,"status":"invalid_json",
                     "error":str(exc)[:200]}
    state.update(status="present",sha256=sha256(path.read_bytes()).hexdigest())
    return document,state


def _new_report_hint(root:Path,stem:str)->Path:
    candidate=root/"reports"/stem
    number=2
    while candidate.exists():
        candidate=root/"reports"/f"{stem}-{number}"
        number+=1
    return candidate


def _shell(path:Path)->str:
    return shlex.quote(str(path))


def inspect_story_workflow(workspace:Path,features_file:Path,temporal_file:Path,*,
                           pairs_file:Path|None=None,
                           study_file:Path|None=None)->dict:
    root=Path(workspace).expanduser().resolve()
    features_path=Path(features_file).expanduser().resolve()
    temporal_path=Path(temporal_file).expanduser().resolve()
    features,feature_state=_workspace_json(root,features_path,"Feature template")
    temporal,temporal_state=_workspace_json(root,temporal_path,"Temporal report")
    pair_path=Path(pairs_file).expanduser().resolve() if pairs_file else None
    pairs,pair_state=_workspace_json(root,pair_path,"Blind pair study") if pair_path else (
        None,{"status":"optional_not_requested","exists":False,"path":None})
    study_path=Path(study_file).expanduser().resolve() if study_file else None
    study,study_state=_workspace_json(root,study_path,"Archive study") if study_path else (
        None,{"status":"optional_not_requested","exists":False,"path":None})
    for doc,state,expected in (
        (features,feature_state,USER_VERSION),
        (temporal,temporal_state,TEMPORAL_VERSION),
        (pairs,pair_state,PAIR_VERSION),
        (study,study_state,"archive-value-study-v1"),
    ):
        if doc is not None and doc.get("version")!=expected:
            state["status"]="wrong_schema_version"
            state["expectedVersion"]=expected
            state["detectedVersion"]=doc.get("version")
    counts={}
    feature_valid=(features is not None and feature_state["status"]=="present"
                   and features.get("featureVersion")==FEATURE_VERSION
                   and isinstance(features.get("items"),list))
    if feature_valid:
        counts["personalCandidates"]=len(features["items"])
    else:
        feature_state["status"]=(
            "invalid_feature_template" if features is not None and
            feature_state["status"]=="present" else feature_state["status"])
    temporal_valid=(
        feature_valid and temporal is not None and
        temporal_state["status"]=="present"
        and temporal.get("sourceTemplateSha256")==feature_state["sha256"]
        and temporal.get("mode")=="local_preview_temporal_extraction"
        and isinstance(temporal.get("candidates"),list))
    if temporal is not None and temporal_state["status"]=="present":
        if not feature_valid:
            temporal_state["status"]="cannot_verify_without_feature_template"
        elif temporal.get("sourceTemplateSha256")!=feature_state["sha256"]:
            temporal_state["status"]="feature_template_fingerprint_mismatch"
        elif temporal.get("mode")!="local_preview_temporal_extraction":
            temporal_state["status"]="not_an_executed_temporal_report"
        elif not isinstance(temporal.get("candidates"),list):
            temporal_state["status"]="invalid_candidate_records"
    if temporal_valid:
        rows=[row for row in temporal["candidates"] if isinstance(row,dict)]
        measured=sum(row.get("status")=="temporal_visual_metrics_observed"
                     and row.get("previewVerified") is True for row in rows)
        counts["temporalCandidates"]=len(rows)
        counts["measuredWithVerifiedPreviewAtReportTime"]=measured
        counts["temporalFailures"]=len(temporal.get("failures",[])) if isinstance(
            temporal.get("failures"),list) else None
        # This metadata-only check cannot establish a current valid media SHA.
        temporal_state["currentPreviewReceiptsReverified"]=False
    else:
        measured=0
    pair_valid=(temporal_valid and pairs is not None and
                pair_state["status"]=="present" and
                pairs.get("featureTemplateSha256")==feature_state["sha256"] and
                pairs.get("temporalEvidenceSha256")==temporal_state["sha256"] and
                isinstance(pairs.get("pairs"),list))
    if pairs is not None and pair_state["status"]=="present":
        if not temporal_valid:
            pair_state["status"]="cannot_verify_without_compatible_temporal"
        elif (pairs.get("featureTemplateSha256")!=feature_state["sha256"]
              or pairs.get("temporalEvidenceSha256")!=temporal_state["sha256"]):
            pair_state["status"]="study_artifact_fingerprint_mismatch"
        elif not isinstance(pairs.get("pairs"),list):
            pair_state["status"]="invalid_pair_records"
        else:
            counts["blindPairs"]=len(pairs["pairs"])
    commands=[]
    rootcmd=f"klip --workspace {_shell(root)}"
    if not feature_valid:
        stage="create_or_repair_personal_feature_template"
        if study_path is not None and study_state["status"]=="present":
            commands.append(
                f"{rootcmd} social-lens-feature-template "
                f"--study {_shell(study_path)} --limit 40 "
                f"--output-dir {_shell(_new_report_hint(root,'social-rubric-balanced40-recovery'))}")
        guidance="Create a personal Klip feature template using an existing archive-value-study study.json. No social-reference corpus is involved."
    elif not temporal_valid or measured==0:
        stage="generate_matching_temporal_evidence"
        commands.append(
            f"{rootcmd} social-lens-temporal "
            f"--features {_shell(features_path)} --limit 40 --run "
            f"--prepare-previews --output-dir "
            f"{_shell(_new_report_hint(root,'temporal-proxy40-recovery'))}")
        guidance=(
            "Generate real temporal evidence from the EXACT feature template. "
            "Only --run --prepare-previews may open checksum-verified original videos "
            "to make missing local H264 proxies. A new report directory is required; "
            "update --temporal to the new report's temporal-evidence.json.")
    elif pairs_file is not None and not pair_valid:
        stage="create_matching_blind_pairs_or_omit_pairs"
        if study_path is not None and study_state["status"]=="present":
            commands.append(
                f"{rootcmd} social-lens-pair-review "
                f"--study {_shell(study_path)} --features {_shell(features_path)} "
                f"--temporal {_shell(temporal_path)} --max-pairs 12 "
                f"--output-dir {_shell(_new_report_hint(root,'social-pairs12-recovery'))}")
        guidance=(
            "Your --pairs file is absent/incompatible. You may omit --pairs "
            "entirely to run independent scene narratives now. For a blind "
            "comparison, first generate pairs using this exact temporal report.")
    else:
        stage="ready_for_local_scene_story_pilot"
        guidance=(
            "The JSON artifact chain is compatible and contains measured candidates. "
            "Actual preview SHA/receipt checks and Ollama availability are verified "
            "during story inference, not by this read-only preflight.")
    if temporal_valid and measured>0:
        # Even if the pair file is missing, unpaired stories can proceed.
        commands.append(
            f"{rootcmd} social-lens-stories "
            f"--features {_shell(features_path)} "
            f"--temporal {_shell(temporal_path)} "+
            (f"--pairs {_shell(pair_path)} " if pair_valid else "")+
            f"--model gemma3:4b --limit 6 --run "
            f"--output-dir {_shell(_new_report_hint(root,'scene-stories-recovery'))}")
    return {
        "version":"social-lens-local-workflow-status-v1",
        "workflow":"personal_video_evidence_not_external_social_reference_training",
        "stage":stage,
        "features":feature_state,"temporal":temporal_state,
        "pairs":pair_state,"study":study_state,
        "counts":counts,
        "readyForUnpairedStories":bool(temporal_valid and measured>0),
        "readyForPairTargetedStories":bool(pair_valid and measured>0
                                            and len(pairs["pairs"])>0),
        "nextAction":guidance,"suggestedCommands":commands,
        "originalSourceVideoReads":0,"videoFramesDecoded":0,
        "ollamaCalls":0,"filesWritten":0,"externalUploads":0,
        "limitations":[
            "Inspects existing JSON files only; cannot know whether the user's Mac source files are mounted.",
            "Does not rehash H264 preview files, check local Ollama status, or assert model inference success.",
            "A missing pair manifest does not block unpaired story reconstruction.",
            "Every output directory in the proposed commands must be new.",
        ],
    }
