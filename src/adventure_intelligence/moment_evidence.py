"""Describe every recording with pretrained models, once, and keep the embeddings.

What it stores per recording (under an evidence folder, one subfolder per SHA):
- frames.npz: keyframe times and image embeddings (SigLIP2), about one frame
  every `step` seconds, decoded with keyframes only so the 4K original is read
  quickly and never written;
- audio.npz: window times and audio embeddings (CLAP), two-second windows;
- meta.json: models, their revisions, settings and timing. Written last, so a
  recording without it is simply not done yet.

Concepts ("a sea turtle", "people laughing", ...) are scored later from the
stored embeddings against a vocabulary encoded once by the same models. Adding
a concept therefore needs only `describe --vocabulary-only`, never a new pass
over the footage. Scores are zero-shot model similarities: evidence for the
ranking, never the owner's judgment.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json
import os
import subprocess
import threading
import time

import numpy as np

from .media import probe, require_tool

VERSION = "klip-moment-evidence-v1"
DEFAULT_IMAGE_MODEL = "google/siglip2-so400m-patch14-384"
DEFAULT_AUDIO_MODEL = "laion/clap-htsat-unfused"
AUDIO_RATE = 48_000
AUDIO_WINDOW_S = 2.0
FRAME_WIDTH = 448

# What a moment can show. Owner terms from vocabulary.json are added to these.
SEE = {
    "people": ["a person", "a group of friends together", "a child", "a person's face close to the camera",
               "people waving or smiling at the camera", "a person jumping into the water"],
    "animals": ["a sea turtle", "a manta ray", "a stingray", "a shark", "a school of colorful tropical fish",
                "a dolphin", "a whale", "an octopus", "a dog", "a bird"],
    "water": ["a coral reef underwater", "clear turquoise lagoon water", "ocean waves and surf",
              "a kayak on the water", "a sandy beach with palm trees", "a boat"],
    "snow": ["a skier going fast down a steep slope", "deep powder snow spraying", "a skier in the air off a jump",
             "a skier falling in the snow", "a chairlift or gondola", "snowy mountain peaks", "trees covered in snow"],
    "land": ["a mountain bike trail through a forest", "a scenic viewpoint over a valley", "a sunset or sunrise",
             "a waterfall", "a town or village"],
    "quality": ["a blurry out of focus image", "a very dark image", "an image full of motion blur"],
}
HEAR = ["people laughing", "people cheering", "someone shouting with excitement", "people talking",
        "water splashing", "underwater bubbles", "strong wind noise", "skis carving on snow",
        "a bicycle on gravel", "music", "silence"]
ACTIVITY = {
    "ski": ["skiing on a snowy slope", "riding a chairlift above snow"],
    "snorkeling": ["snorkeling underwater over a reef", "swimming at the surface of clear ocean water"],
    "kayak": ["paddling a kayak on calm water"],
    "bike": ["riding a mountain bike on a dirt trail"],
    "hike": ["hiking on a trail in nature"],
    "surf": ["surfing a wave"],
    "accidental": ["the inside of a pocket or bag, almost black", "a blurry ceiling or floor indoors",
                   "a camera lying on a table pointing at nothing"],
    "other": ["a restaurant or hotel room", "a car or airplane interior", "a city street"],
}


def vocabulary(owner_terms: list[str] | None = None) -> dict:
    """The concept list, with owner terms (people, places, animals they care about) added."""
    see = [f"see:{t}" for group in SEE.values() for t in group]
    see += [f"see:{t.strip()}" for t in owner_terms or [] if t.strip()]
    hear = [f"hear:{t}" for t in HEAR]
    activity = [(a, p) for a, prompts in ACTIVITY.items() for p in prompts]
    return {"see": sorted(set(see), key=see.index), "hear": hear, "activity": activity}


def vocabulary_digest(vocab: dict) -> str:
    return sha256(json.dumps(vocab, sort_keys=True).encode()).hexdigest()[:16]


# ---- encoders (pretrained, frozen) ---------------------------------------------------

def _device(torch):
    if torch.backends.mps.is_available():
        return "mps"
    return "cuda" if torch.cuda.is_available() else "cpu"


def _features(out):
    """Newer transformers return a model output where older ones return a tensor."""
    if hasattr(out, "norm"):  # already a tensor
        return out
    pooled = getattr(out, "pooler_output", None)
    if pooled is None:
        raise ValueError("Model returned no pooled features")
    return pooled


class ImageEncoder:
    """SigLIP2: images and text in one space; sigmoid(scale * cosine + bias) is its match probability."""

    def __init__(self, model_id: str = DEFAULT_IMAGE_MODEL):
        import torch
        from transformers import AutoModel, AutoProcessor
        self.torch, self.model_id = torch, model_id
        self.processor = AutoProcessor.from_pretrained(model_id)
        self.model = AutoModel.from_pretrained(model_id).eval()
        self.device = _device(torch)
        self.model.to(self.device)
        self.revision = getattr(self.model.config, "_commit_hash", None)
        self.scale = float(self.model.logit_scale.exp().item())
        self.bias = float(self.model.logit_bias.item())

    def _norm(self, x):
        x = _features(x)
        return self.torch.nn.functional.normalize(x, dim=-1).float().cpu().numpy()

    def images(self, frames: list[np.ndarray], batch: int = 16) -> np.ndarray:
        from PIL import Image
        out = []
        for i in range(0, len(frames), batch):
            pil = [Image.fromarray(f) for f in frames[i:i + batch]]
            inputs = self.processor(images=pil, return_tensors="pt").to(self.device)
            with self.torch.inference_mode():
                out.append(self._norm(self.model.get_image_features(**inputs)))
        return np.concatenate(out) if out else np.zeros((0, 0), np.float32)

    def texts(self, texts: list[str]) -> np.ndarray:
        inputs = self.processor(text=texts, padding="max_length", max_length=64,
                                truncation=True, return_tensors="pt").to(self.device)
        with self.torch.inference_mode():
            return self._norm(self.model.get_text_features(**inputs))


class AudioEncoder:
    """CLAP: audio windows and text in one space."""

    def __init__(self, model_id: str = DEFAULT_AUDIO_MODEL):
        import torch
        from transformers import ClapModel, ClapProcessor
        self.torch, self.model_id = torch, model_id
        self.processor = ClapProcessor.from_pretrained(model_id)
        self.model = ClapModel.from_pretrained(model_id).eval()
        self.device = _device(torch)
        self.model.to(self.device)
        self.revision = getattr(self.model.config, "_commit_hash", None)
        self.scale = float(self.model.logit_scale_a.exp().item())

    def _norm(self, x):
        x = _features(x)
        return self.torch.nn.functional.normalize(x, dim=-1).float().cpu().numpy()

    def windows(self, clips: list[np.ndarray], batch: int = 16) -> np.ndarray:
        out = []
        for i in range(0, len(clips), batch):
            part = list(clips[i:i + batch])
            try:
                inputs = self.processor(audio=part, sampling_rate=AUDIO_RATE, return_tensors="pt")
            except (TypeError, ValueError):  # older transformers name this argument `audios`
                inputs = self.processor(audios=part, sampling_rate=AUDIO_RATE, return_tensors="pt")
            inputs = inputs.to(self.device)
            with self.torch.inference_mode():
                out.append(self._norm(self.model.get_audio_features(**inputs)))
        return np.concatenate(out) if out else np.zeros((0, 0), np.float32)

    def texts(self, texts: list[str]) -> np.ndarray:
        inputs = self.processor(text=texts, padding=True, return_tensors="pt").to(self.device)
        with self.torch.inference_mode():
            return self._norm(self.model.get_text_features(**inputs))


# ---- decoding (read-only) -------------------------------------------------------------

def keyframe_times(path: Path) -> list[float]:
    """Keyframe timestamps from packet flags: no decoding needed."""
    result = subprocess.run(
        [require_tool("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries",
         "packet=pts_time,flags", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True, timeout=600)
    times = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(",")
        if len(parts) >= 2 and "K" in parts[1] and parts[0] not in ("", "N/A"):
            times.append(float(parts[0]))
    return sorted(times)


def sample_frames(path: Path, step_s: float, width: int = FRAME_WIDTH) -> tuple[list[int], list[np.ndarray]]:
    """About one keyframe every step_s seconds, as RGB arrays width pixels wide.

    Only keyframes are decoded (-skip_frame nokey), in order, so the n-th
    decoded frame is the n-th keyframe and its time comes from the packet list.
    """
    info = probe(path)
    w, h = info.get("width"), info.get("height")
    if not w or not h:
        raise ValueError("No video stream")
    height = max(2, int(round(width * h / w / 2)) * 2)
    times = keyframe_times(path)
    keep, last = set(), None
    for i, t in enumerate(times):
        if last is None or t - last >= step_s - 1e-6:
            keep.add(i)
            last = t
    process = subprocess.Popen(
        [require_tool("ffmpeg"), "-v", "error", "-nostdin", "-skip_frame", "nokey", "-i", str(path),
         "-map", "0:v:0", "-an", "-sn", "-fps_mode", "passthrough",
         "-vf", f"scale={width}:{height}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    errors: list[bytes] = []
    reader = threading.Thread(target=lambda: errors.append(process.stderr.read()), daemon=True)
    reader.start()
    size = width * height * 3
    out_t, out_f, i = [], [], 0
    try:
        while True:
            raw = process.stdout.read(size)
            if len(raw) < size:
                break
            if i in keep and i < len(times):
                out_t.append(int(round(times[i] * 1000)))
                out_f.append(np.frombuffer(raw, np.uint8).reshape(height, width, 3).copy())
            i += 1
    finally:
        process.stdout.close()
        code = process.wait(timeout=600)
        reader.join(timeout=5)
    if code != 0 and not out_f:
        raise RuntimeError("ffmpeg could not decode keyframes: "
                           + b"".join(errors).decode(errors="replace")[-300:])
    return out_t, out_f


def audio_track(path: Path) -> np.ndarray:
    """Mono 48 kHz float samples; empty when the recording has no audio."""
    if not probe(path).get("audio"):
        return np.zeros(0, np.float32)
    result = subprocess.run(
        [require_tool("ffmpeg"), "-v", "error", "-nostdin", "-i", str(path), "-map", "0:a:0",
         "-vn", "-ac", "1", "-ar", str(AUDIO_RATE), "-f", "f32le", "-"],
        capture_output=True, check=True, timeout=1800)
    return np.frombuffer(result.stdout, np.float32)


def audio_windows(samples: np.ndarray) -> tuple[list[int], list[np.ndarray]]:
    n = int(AUDIO_WINDOW_S * AUDIO_RATE)
    times, clips = [], []
    for start in range(0, len(samples) - n + 1, n):
        times.append(int(start / AUDIO_RATE * 1000))
        clips.append(samples[start:start + n])
    return times, clips


# ---- the store ---------------------------------------------------------------------------

def _atomic(path: Path, write) -> None:
    temp = path.with_name(path.name + ".partial")
    write(temp)
    os.replace(temp, path)


def _save(path: Path, **arrays) -> None:
    def write(temp):
        with open(temp, "wb") as handle:
            np.savez(handle, **arrays)
    _atomic(path, write)


def describe(sha: str, path: Path, store: Path, *, image, audio, step_s: float = 2.0,
             sample=sample_frames, track=audio_track) -> dict:
    """Embed one recording. Resumable: a finished recording with the same models is skipped."""
    folder = Path(store) / sha
    meta_file = folder / "meta.json"
    wanted = {"version": VERSION, "imageModel": image.model_id,
              "audioModel": audio.model_id if audio else None, "stepSeconds": step_s}
    if meta_file.is_file():
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        if all(meta.get(k) == v for k, v in wanted.items()):
            return {**meta, "skipped": True}
    folder.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    times, frames = sample(path, step_s)
    if not frames:
        raise ValueError("No frames decoded")
    decoded = time.monotonic()
    vectors = image.images(frames).astype(np.float16)
    _save(folder / "frames.npz", times=np.array(times, np.int64), embeddings=vectors)
    embedded = time.monotonic()
    audio_count = 0
    if audio is not None:
        a_times, clips = audio_windows(track(path))
        a_vectors = (audio.windows(clips) if clips else np.zeros((0, 1))).astype(np.float16)
        audio_count = len(a_times)
        _save(folder / "audio.npz", times=np.array(a_times, np.int64), embeddings=a_vectors)
    meta = {**wanted, "sourceSha256": sha, "imageRevision": image.revision,
            "audioRevision": audio.revision if audio else None, "frames": len(times),
            "audioWindows": audio_count,
            "seconds": {"decode": round(decoded - started, 1), "image": round(embedded - decoded, 1),
                        "audio": round(time.monotonic() - embedded, 1)}}
    _atomic(meta_file, lambda p: p.write_text(json.dumps(meta, indent=2), encoding="utf-8"))
    return meta


def encode_vocabulary(store: Path, vocab: dict, *, image, audio) -> dict:
    """Text embeddings for the vocabulary, cached by models and vocabulary digest."""
    store = Path(store)
    store.mkdir(parents=True, exist_ok=True)
    see = image.texts([t.split(":", 1)[1] for t in vocab["see"]])
    act = image.texts([p for _, p in vocab["activity"]])
    hear = audio.texts([t.split(":", 1)[1] for t in vocab["hear"]]) if audio else np.zeros((0, 1))
    doc = {"version": VERSION, "vocabulary": vocab, "digest": vocabulary_digest(vocab),
           "imageModel": image.model_id, "imageScale": image.scale, "imageBias": image.bias,
           "audioModel": audio.model_id if audio else None,
           "audioScale": audio.scale if audio else None}
    _save(store / "vocabulary.npz", see=see, activity=act, hear=hear)
    _atomic(store / "vocabulary.json", lambda p: p.write_text(json.dumps(doc, indent=2), encoding="utf-8"))
    return doc


class Store:
    """Read side: concept scores per frame and per interval, from stored embeddings."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        doc_file, arrays = self.folder / "vocabulary.json", self.folder / "vocabulary.npz"
        if not doc_file.is_file() or not arrays.is_file():
            raise ValueError("Evidence folder has no encoded vocabulary; run describe first")
        self.doc = json.loads(doc_file.read_text(encoding="utf-8"))
        with np.load(arrays) as data:
            self.see, self.act, self.hear = data["see"], data["activity"], data["hear"]
        self.vocab = self.doc["vocabulary"]
        self._cache: dict[str, dict | None] = {}

    def recording(self, sha: str) -> dict | None:
        if sha in self._cache:
            return self._cache[sha]
        folder = self.folder / sha
        meta_file = folder / "meta.json"
        entry = None
        if meta_file.is_file():
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            if meta.get("imageModel") == self.doc["imageModel"]:
                with np.load(folder / "frames.npz") as f:
                    times, emb = f["times"], f["embeddings"].astype(np.float32)
                probs = 1 / (1 + np.exp(-(self.doc["imageScale"] * (emb @ self.see.T)
                                          + self.doc["imageBias"])))
                entry = {"meta": meta, "times": times, "embeddings": emb, "see": probs,
                         "audioTimes": np.zeros(0, np.int64), "hear": np.zeros((0, len(self.vocab["hear"])))}
                audio_file = folder / "audio.npz"
                if (audio_file.is_file() and meta.get("audioModel") == self.doc.get("audioModel")
                        and len(self.hear)):
                    with np.load(audio_file) as a:
                        a_emb = a["embeddings"].astype(np.float32)
                        if len(a_emb):
                            logits = self.doc["audioScale"] * (a_emb @ self.hear.T)
                            logits -= logits.max(axis=1, keepdims=True)
                            # Which of the sounds is most like this window, as a share.
                            share = np.exp(logits) / np.exp(logits).sum(axis=1, keepdims=True)
                            entry.update(audioTimes=a["times"], hear=share)
        self._cache[sha] = entry
        return entry

    def interval(self, sha: str, start: int, end: int) -> dict | None:
        """Peak score of every concept inside [start, end) and when it peaks."""
        entry = self.recording(sha)
        if entry is None:
            return None
        out = {"concepts": {}, "peaksMs": {}, "embedding": None}
        inside = (entry["times"] >= start) & (entry["times"] < end)
        if inside.any():
            probs, times = entry["see"][inside], entry["times"][inside]
            top = probs.argmax(axis=0)
            for k, name in enumerate(self.vocab["see"]):
                out["concepts"][name] = float(probs[top[k], k])
                out["peaksMs"][name] = int(times[top[k]])
            mean = entry["embeddings"][inside].mean(axis=0)
            norm = float(np.linalg.norm(mean))
            out["embedding"] = mean / norm if norm > 1e-8 else None
        heard = (entry["audioTimes"] >= start) & (entry["audioTimes"] < end)
        if heard.any():
            share, times = entry["hear"][heard], entry["audioTimes"][heard]
            top = share.argmax(axis=0)
            for k, name in enumerate(self.vocab["hear"]):
                out["concepts"][name] = float(share[top[k], k])
                out["peaksMs"][name] = int(times[top[k]])
        return out if out["concepts"] else None

    def activity(self, sha: str) -> dict | None:
        """Proposed activity for a whole recording: the share of frames each activity wins."""
        entry = self.recording(sha)
        if entry is None or not len(entry["embeddings"]):
            return None
        sims = entry["embeddings"] @ self.act.T
        names = [a for a, _ in self.vocab["activity"]]
        labels = sorted(set(names), key=names.index)
        best = np.array([max(range(len(names)), key=lambda k: row[k]) for row in sims])
        votes = {a: float(np.mean([names[b] == a for b in best])) for a in labels}
        ranked = sorted(votes.items(), key=lambda kv: -kv[1])
        return {"proposal": ranked[0][0], "share": round(ranked[0][1], 3),
                "runnerUp": ranked[1][0] if len(ranked) > 1 else None,
                "votes": {a: round(v, 3) for a, v in ranked if v > 0},
                "isModelProposalNotOwnerJudgment": True}
