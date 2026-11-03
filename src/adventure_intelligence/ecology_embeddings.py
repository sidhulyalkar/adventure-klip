"""Optional species text-to-image retrieval against ALREADY CACHED SigLIP2 frames.

Do not confuse text-image similarity with species identification.  Model
weights must already be on disk: this path never makes network requests or
reopens video files. The frozen image-encoder revision/preprocessing must match.
"""
from __future__ import annotations

import hashlib
import json
import math
from statistics import median

from .scene_atlas import frames_for_asset, unit_vector, similarity

VERSION = "ecology-siglip2-text-retrieval-v1"
MODEL_ID = "google/siglip2-base-patch16-224"


def prompt_for(taxon: dict) -> str:
    name = taxon["scientificName"]
    # Avoid suggesting that coral is a plant, or that algae are vascular plants.
    broad = {
        "fish": "fish", "marine_mammal": "marine mammal",
        "marine_reptile": "marine reptile", "invertebrate": "marine invertebrate",
        "coral": "coral animal", "macroalgae": "marine macroalgae",
        "seagrass": "seagrass", "marine_plant": "marine plant",
        "other": "marine organism",
    }[taxon["biologicalGroup"]]
    return f"Underwater photograph of {name}, a {broad}, in its natural habitat."


def score_cached_vectors(candidates: list[dict], frames: list[dict],
                         text_vectors: list[list[float]], *, model_key: str) -> dict:
    """Pure deterministic cosine retrieval; never turn results into sightings."""
    if len(candidates) != len(text_vectors):
        raise ValueError("Text vector/candidate count mismatch")
    usable = []
    seen = set()
    for f in frames:
        t = f.get("decodedPtsMs")
        if type(t) is not int or t < 0:
            t = f.get("tMs")
        v = unit_vector(f.get("embedding"))
        if type(t) is int and t >= 0 and t not in seen and v is not None:
            seen.add(t)
            usable.append((t, v))
    usable.sort()
    # A large completed file must not inflate the max-score merely because it
    # contains far more samples than a short recording. A limited report only.
    if len(usable) > 128:
        usable = [usable[(i * (len(usable) - 1)) // 127] for i in range(128)]
    retrieved = {}
    for item, raw in zip(candidates, text_vectors):
        vector = unit_vector(raw)
        scored = []
        if vector is not None:
            for t, frame in usable:
                if len(vector) != len(frame):
                    raise ValueError("Incompatible cached image and text dimensions")
                value = similarity(vector, frame)
                if value is not None and math.isfinite(value):
                    scored.append((round(value, 6), t))
        scored.sort(key=lambda x: (-x[0], x[1]))
        retrieved[item["taxonId"]] = {
            "version": VERSION, "method": "siglip2_image_text_cosine",
            "modelKeySha256": hashlib.sha256(model_key.encode()).hexdigest(),
            "status": "uncalibrated_candidate_retrieval",
            "isConfirmedSpeciesSighting": False,
            "sampledFrameCount": len(usable),
            "score": scored[0][0] if scored else None,
            "medianSimilarity": round(median(x[0] for x in scored), 6) if scored else None,
            "topRequestedOrDecodedTimesMs": [x[1] for x in scored[:3]],
            "warning": "High cosine similarity is NOT species confidence or proof of presence",
        }
    return retrieved


def cached_siglip2_retrieval(workspace, asset_id: str, candidates: list[dict]) -> dict:
    """Load exact locally cached text model only when a matching image cache exists."""
    from .catalog import connect
    with connect(workspace) as db:
        frames, key = frames_for_asset(db, workspace, asset_id, "siglip2")
    if not frames or not key:
        return {"status": "unavailable_no_cached_siglip2_frames", "proposals": {}}
    config = json.loads(key)
    if (config.get("provider") != "siglip2" or config.get("modelVersion") != MODEL_ID
            or not config.get("resolvedRevision") or not config.get("imageProcessor")):
        return {"status": "unavailable_unverifiable_model_provenance", "proposals": {}}
    if not candidates:
        return {"status": "no_taxa_candidates", "proposals": {}}
    try:
        import torch
        from transformers import AutoModel, AutoProcessor
    except ImportError:
        return {"status": "unavailable_optional_semantic_dependencies", "proposals": {}}
    # No implicit network, download, or remote prompt execution is permitted.
    try:
        processor = AutoProcessor.from_pretrained(
            MODEL_ID, revision=config["resolvedRevision"], local_files_only=True)
        model = AutoModel.from_pretrained(
            MODEL_ID, revision=config["resolvedRevision"], local_files_only=True)
    except (OSError, ValueError):
        return {"status": "unavailable_matching_local_model_weights", "proposals": {}}
    if processor.image_processor.to_dict() != config["imageProcessor"]:
        return {"status": "unavailable_image_preprocessing_mismatch", "proposals": {}}
    resolved = getattr(model.config, "_commit_hash", None)
    if resolved is not None and resolved != config["resolvedRevision"]:
        return {"status": "unavailable_model_revision_mismatch", "proposals": {}}
    device = ("mps" if torch.backends.mps.is_available()
              else "cuda" if torch.cuda.is_available() else "cpu")
    model.eval().to(device)
    prompts = [prompt_for(candidate) for candidate in candidates]
    # Taxa are bounded by the CLI's top<=100. Process text in small batches.
    vectors = []
    with torch.inference_mode():
        for start in range(0, len(prompts), 16):
            encoded = processor(text=prompts[start:start + 16],
                                padding="max_length", truncation=True,
                                return_tensors="pt").to(device)
            output = model.get_text_features(**encoded)
            if hasattr(output, "pooler_output"):
                output = output.pooler_output
            output = torch.nn.functional.normalize(output, dim=-1)
            vectors.extend(output.cpu().float().tolist())
    return {
        "status": "uncalibrated_retrieval_complete",
        "proposals": score_cached_vectors(candidates, frames, vectors, model_key=key),
        "modelKeySha256": hashlib.sha256(key.encode()).hexdigest(),
    }
