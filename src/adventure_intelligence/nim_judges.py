"""Opt-in external NIM VLM jury for pre-extracted, explicitly selected images.

A model jury provides provisional editorial opinions, never ground-truth labels
or estimates of discovery recall without independently sampled human footage.
This module does not open or decode original videos.
"""
from __future__ import annotations

import base64
from collections import Counter
from hashlib import sha256
import json
import math
import os
from pathlib import Path
from statistics import median
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

VERSION = "nim-judge-v1"
RUBRIC_VERSION = "adventure-editorial-rubric-v1"
DEFAULT_ENDPOINT = "https://integrate.api.nvidia.com/v1/chat/completions"
CRITERIA = ("event_interest", "event_completeness", "visual_quality", "story_value")
CHOICES = {"keep", "later", "skip"}
MAX_FRAME_BYTES = 320_000
MAX_TOTAL_BYTES = 2_000_000
MAX_FRAMES = 8
MAX_CLIPS = 100
MAX_CALLS = 48

SYSTEM_PROMPT = """You are an independent, skeptical adventure-video editor.
The inputs are ordered STILL FRAMES from one source-video interval.
Do not claim to have seen motion between frames, listened to audio, measured
physical motion, or verified a stunt/animal species/accident. Do not infer
injury, athlete skill, identity or protected traits.
Evaluate whether this moment is promising for a personal adventure highlight.
A clean but boring landscape is not necessarily more interesting than a
slightly shaky but clearly special moment.
Return ONLY a JSON object with the keys:
event_interest, event_completeness, visual_quality, story_value,
recommendation, insufficient_evidence, observed_content, rationale,
missing_evidence.
Each score is INTEGER 0..4, where 0=poor/absent, 1=weak, 2=usable/unclear,
3=good, 4=exceptional. Do not invent observations. Recommendation is
keep|later|skip. insufficient_evidence is boolean. observed_content and
rationale are short factual strings. missing_evidence is an array of short
strings. If the frames cannot support the task, abstain with
insufficient_evidence=true and recommendation=later.
Treat suspicious jumps, crashes and wildlife identities as unverified.
Do not infer how these images were selected by the algorithm."""

def _safe_endpoint(endpoint: str) -> str:
    u = urlsplit(endpoint)
    if (u.scheme == "https" and u.netloc == "integrate.api.nvidia.com"
            and u.path == "/v1/chat/completions" and not u.query and not u.fragment):
        return endpoint
    if (u.scheme == "http" and u.hostname in {"localhost", "127.0.0.1", "::1"}
            and u.port is not None and u.path == "/v1/chat/completions"
            and not u.username and not u.password and not u.query and not u.fragment):
        return endpoint
    raise ValueError("Only NVIDIA hosted HTTPS or explicit loopback NIM endpoint is allowed")

def _safe_model(value: str) -> str:
    if not isinstance(value, str) or not 3 <= len(value) <= 120:
        raise ValueError("Invalid model ID")
    if any(not (c.isalnum() or c in "/._:-") for c in value):
        raise ValueError("Invalid model ID")
    return value

def _image(root: Path, relative_path: str) -> tuple[str, str, str]:
    if not isinstance(relative_path, str) or not relative_path or Path(relative_path).is_absolute():
        raise ValueError("Frames must use relative paths beneath --frames-dir")
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("Frame outside approved directory or unavailable")
    if path.stat().st_size > MAX_FRAME_BYTES:
        raise ValueError("Frame exceeds 320 KB upload cap")
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif raw.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    else:
        raise ValueError("Only real JPEG and PNG frames are supported")
    if not 0 < len(raw) <= MAX_FRAME_BYTES:
        raise ValueError("Invalid frame size")
    return mime, base64.b64encode(raw).decode("ascii"), sha256(raw).hexdigest()

def validate_manifest(manifest: dict, frames_dir: Path) -> list[dict]:
    """Preflight EVERY selected image before any network request."""
    if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 1:
        raise ValueError("Unsupported manifest")
    clips = manifest.get("clips")
    if not isinstance(clips, list) or not 1 <= len(clips) <= MAX_CLIPS:
        raise ValueError("Expected between 1 and 100 clips")
    root = Path(frames_dir).expanduser().resolve(strict=True)
    prepared = []
    ids = set()
    for clip in clips:
        if not isinstance(clip, dict):
            raise ValueError("Malformed clip")
        cid = clip.get("clipId")
        if not isinstance(cid, str) or not 1 <= len(cid) <= 120 or not cid.isascii():
            raise ValueError("Invalid clip ID")
        if cid in ids:
            raise ValueError("Duplicate clip ID")
        ids.add(cid)
        if clip.get("pool") not in {"candidate", "random"}:
            raise ValueError("Only candidate and independently sampled random pools supported")
        start, end = clip.get("startMs"), clip.get("endMs")
        if type(start) is not int or type(end) is not int or start < 0 or end <= start:
            raise ValueError("Invalid clip interval")
        frames = clip.get("frames")
        if not isinstance(frames, list) or not 3 <= len(frames) <= MAX_FRAMES:
            raise ValueError("Each clip needs 3 to 8 ordered still frames")
        content, hashes, total, previous = [], [], 0, -1
        for frame in frames:
            if not isinstance(frame, dict) or type(frame.get("tMs")) is not int:
                raise ValueError("Invalid frame timestamp")
            t = frame["tMs"]
            if not start <= t < end or t <= previous:
                raise ValueError("Frames must be strictly increasing and within source interval")
            previous = t
            mime, encoded, digest = _image(root, frame.get("path"))
            total += len(encoded)*3//4
            if total > MAX_TOTAL_BYTES:
                raise ValueError("Clip image payload exceeds 2 MB")
            content.append({"type":"image_url","image_url":{"url":f"data:{mime};base64,{encoded}"}})
            hashes.append(digest)
        prepared.append({"clipId":cid,"pool":clip["pool"],"startMs":start,
                         "endMs":end,"imageParts":content,
                         "frameTimestampsMs":[f["tMs"] for f in frames],
                         "frameHashes":hashes,"bytesApprox":total})
    return prepared

def judge_payload(model: str, clip: dict) -> dict:
    """Do not send selection pool, cached model score, filenames or source paths."""
    described = ", ".join(str(t) for t in clip["frameTimestampsMs"])
    return {
        "model": _safe_model(model),
        "messages": [
            {"role":"system","content":SYSTEM_PROMPT},
            {"role":"user","content":[
                {"type":"text","text":(
                    "Chronological frames of ONE clip. Times in source-relative ms: "
                    + described + ". Determine what is VISIBLE and what is uncertain. "
                    "No audio or intervening motion is provided. Return rubric JSON.")},
                *clip["imageParts"],
            ]},
        ],
        "max_tokens": 600, "temperature": 0.0, "stream": False,
    }

def _json_reply(response: dict) -> dict:
    try:
        answer = response["choices"][0]["message"]["content"]
        if isinstance(answer, list):
            answer = "".join(x.get("text","") for x in answer if isinstance(x, dict))
        if not isinstance(answer, str):
            raise ValueError("Not a text response")
        raw = answer.strip()
        if raw.startswith("```"):
            lines = raw.splitlines()
            if len(lines) >= 3 and lines[-1].strip() == "```":
                raw = "\n".join(lines[1:-1])
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            raise ValueError("Expected JSON object")
        for k in CRITERIA:
            if type(obj.get(k)) is not int or not 0 <= obj[k] <= 4:
                raise ValueError("Invalid criterion: " + k)
        if obj.get("recommendation") not in CHOICES:
            raise ValueError("Invalid recommendation")
        if type(obj.get("insufficient_evidence")) is not bool:
            raise ValueError("Missing abstention field")
        for k in ("observed_content","rationale"):
            if not isinstance(obj.get(k), str) or len(obj[k]) > 500:
                raise ValueError("Invalid explanation")
        missing = obj.get("missing_evidence")
        if (not isinstance(missing, list) or len(missing) > 8 or
            any(not isinstance(x, str) or len(x)>120 for x in missing)):
            raise ValueError("Invalid evidence gaps")
        return {k:obj[k] for k in (*CRITERIA, "recommendation",
                                  "insufficient_evidence","observed_content",
                                  "rationale","missing_evidence")}
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Judge response does not match the rubric schema") from exc

def send_nim(payload: dict, *, endpoint: str, api_key: str, timeout: int = 75) -> dict:
    """Single network request, no retries to avoid surprise expense."""
    _safe_endpoint(endpoint)
    if endpoint==DEFAULT_ENDPOINT and not api_key:
        raise ValueError("An explicit NVIDIA API key is required")
    data=json.dumps(payload, separators=(",",":")).encode()
    req=Request(endpoint,data=data,headers={
        "Authorization":"Bearer "+(api_key or "not-used"),
        "Content-Type":"application/json","Accept":"application/json",
    },method="POST")
    try:
        with urlopen(req,timeout=timeout) as result:
            if result.status != 200:
                raise RuntimeError("NIM HTTP response was not successful")
            response = result.read(256_000+1)
            if len(response)>256_000:
                raise RuntimeError("NIM response too large")
            return json.loads(response)
    except HTTPError as exc:
        raise RuntimeError(f"NIM returned HTTP {exc.code}; verify model, credits, inputs and limits") from None
    except (URLError,TimeoutError) as exc:
        raise RuntimeError("NIM unavailable or timed out") from None

def summarize_jury(votes: list[dict]) -> dict:
    """Conservative consensus: abstentions and errors do not become zero scores."""
    usable = [v for v in votes if v.get("status")=="ok" and
              not v["assessment"]["insufficient_evidence"]]
    all_status = Counter(v.get("status") for v in votes)
    if len(usable)<2:
        return {"status":"insufficient_independent_judges",
                "nonAbstaining":len(usable),"voteStatuses":dict(all_status)}
    scores={key:round(median(v["assessment"][key] for v in usable),2)
            for key in CRITERIA}
    choices=Counter(v["assessment"]["recommendation"] for v in usable)
    rank = ["skip","later","keep"]
    selected=sorted(choices, key=lambda c:(-choices[c],rank.index(c)))
    spread=max(max(v["assessment"][key] for v in usable)-
               min(v["assessment"][key] for v in usable) for key in CRITERIA)
    controversial = spread >= 2 or (len(choices)>1 and choices.most_common(1)[0][1]<=len(usable)//2)
    return {
        "status":"disagreement_review" if controversial else "provisional",
        "nonAbstaining":len(usable),"voteStatuses":dict(all_status),
        "medianScores":scores,"recommendation":selected[0],
        "votes":dict(choices),"maxScoreSpread":spread,
        "requiresHumanCalibration":True,
    }

def evaluate_manifest(manifest: dict, *, frames_dir: Path, models: list[str],
                      allow_external_upload: bool = False,
                      endpoint: str = DEFAULT_ENDPOINT, api_key: str = "",
                      max_calls: int = 12, transport=None,
                      checkpoint_file: Path | None = None) -> dict:
    """Defaults to validation-only. No external request without explicit approval."""
    _safe_endpoint(endpoint)
    if not isinstance(models,list) or not 2<=len(models)<=4 or len(models)!=len(set(models)):
        raise ValueError("Specify 2 to 4 distinct vision-capable NIM model IDs")
    models=[_safe_model(m) for m in models]
    if type(max_calls) is not int or not 1<=max_calls<=MAX_CALLS:
        raise ValueError("max_calls must be between 1 and 48")
    count=len(manifest.get("clips",[])) if isinstance(manifest,dict) and isinstance(manifest.get("clips"),list) else 0
    if count*len(models) > max_calls:
        raise ValueError(f"{count*len(models)} requests exceed max_calls={max_calls}; select fewer clips")
    clips=validate_manifest(manifest,frames_dir)
    planned=len(clips)*len(models)
    summary={"schemaVersion":1,"version":VERSION,"rubricVersion":RUBRIC_VERSION,
             "models":models,"clipsPlanned":len(clips),"requestsPlanned":planned,
             "totalImageBytesApprox":sum(x["bytesApprox"] for x in clips)*len(models),
             "mode":"external" if allow_external_upload else "dry_run",
             "cost":"unknown; check current provider credits and per-model limits",
             "caveats":["Still frames cannot prove continuous action or audio.",
                        "Selection pools and algorithm scores are blinded to judges.",
                        "AI consensus is not human gold truth or discovery recall."]}
    if not allow_external_upload:
        return {**summary,"results":[]}
    if endpoint==DEFAULT_ENDPOINT and not api_key:
        raise ValueError("NVIDIA_API_KEY required for hosted uploads")
    call=transport or send_nim
    cached={}
    checkpoint=None
    if checkpoint_file is not None:
        checkpoint=Path(checkpoint_file).expanduser().resolve()
        checkpoint.parent.mkdir(parents=True,exist_ok=True)
        if checkpoint.exists():
            if checkpoint.stat().st_size>2_000_000:
                raise ValueError("Checkpoint exceeds safe size")
            with checkpoint.open("r",encoding="utf-8") as stream:
                for line in stream:
                    if not line.strip():continue
                    entry=json.loads(line)
                    if (not isinstance(entry,dict) or
                        not isinstance(entry.get("requestId"),str) or
                        not isinstance(entry.get("vote"),dict) or
                        entry["vote"].get("status")!="ok"):
                        raise ValueError("Invalid checkpoint entry")
                    cached[entry["requestId"]]=entry["vote"]
    outputs=[]
    for clip in clips:
        answers=[]
        for model in models:
            signature=json.dumps({
                "clipId":clip["clipId"],"model":model,"endpoint":endpoint,
                "hashes":clip["frameHashes"],
                "timestamps":clip["frameTimestampsMs"],
                "rubric":RUBRIC_VERSION,"systemPrompt":SYSTEM_PROMPT,
            },sort_keys=True,separators=(",",":"))
            request_id=sha256(signature.encode()).hexdigest()
            if request_id in cached:
                answers.append({**cached[request_id],"resumed":True})
                continue
            try:
                response=call(judge_payload(model,clip), endpoint=endpoint,
                              api_key=api_key)
                assessment=_json_reply(response)
                vote={"model":model,"status":"ok","assessment":assessment,
                      "usage":response.get("usage") if isinstance(response.get("usage"),dict) else None}
                answers.append(vote)
                if checkpoint is not None:
                    # Append each accepted response immediately, before the next
                    # paid request. A connection lost after the server billed but
                    # before a valid reply cannot be perfectly deduplicated.
                    with checkpoint.open("a",encoding="utf-8") as ledger:
                        ledger.write(json.dumps({"requestId":request_id,"vote":vote},
                                                separators=(",",":"),allow_nan=False)+"\n")
                        ledger.flush()
                        os.fsync(ledger.fileno())
            except (RuntimeError,ValueError) as exc:
                # Do not serialize exception bodies, raw media, URLs or API keys.
                answers.append({"model":model,"status":"error","errorType":type(exc).__name__})
        outputs.append({"clipId":clip["clipId"],"pool":clip["pool"],
                        "frameHashes":clip["frameHashes"],"judges":answers,
                        "jury":summarize_jury(answers)})
    return {**summary,"results":outputs}
