"""Cheap timestamped acoustic-change observations from camera audio.

Audio can corroborate an apparent waterline transition or a camera disturbance.
RMS/transients cannot distinguish snorkeling breathing, distress, speech or waves.
Source media is *only read*; the analysis is cached separately.
"""
from __future__ import annotations

from array import array
from pathlib import Path
from statistics import median
import math
import subprocess
import sys

from .media import require_tool, probe
from .sources import resolve_source, verified_digest
from .artifacts import read_artifact, write_artifact
from .catalog import connect

AUDIO_VERSION = "acoustic-multifeature-int16-8khz-v2"


def _rms(block: bytes) -> tuple[float, float]:
    samples = array("h")
    samples.frombytes(block[:len(block)//2*2])
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return 0.0, 0.0
    power = sum(int(v) * int(v) for v in samples) / len(samples)
    # Keep source volume rather than inferring amplitude-independent events.
    rms = math.sqrt(power) / 32768
    crossings = sum((samples[i] < 0) != (samples[i-1] < 0)
                    for i in range(1, len(samples))) / max(1, len(samples)-1)
    return round(rms, 6), round(crossings, 6)



def acoustic_metrics(block: bytes, previous_spectrum=None) -> tuple[dict, object]:
    """Nonsemantic audio metrics. FFT features are optional when NumPy exists."""
    amplitudes = array("h")
    amplitudes.frombytes(block[:len(block)//2*2])
    if sys.byteorder != "little":
        amplitudes.byteswap()
    if not amplitudes:
        return {"peak": 0., "clippingFraction": 0., "crestFactor": 0.,
                "spectralCentroidHz": None, "spectralFlatness": None,
                "spectralFlux": None, "lowBandFraction": None}, None
    rms, _ = _rms(block)
    peak = max(abs(int(v)) for v in amplitudes) / 32768
    clipped = sum(abs(int(v)) >= 32760 for v in amplitudes) / len(amplitudes)
    values = {"peak": round(peak,6),
              "clippingFraction": round(clipped,6),
              "crestFactor": round(peak/max(rms,1e-8),4),
              "spectralCentroidHz": None, "spectralFlatness": None,
              "spectralFlux": None, "lowBandFraction": None}
    try:
        import numpy as np
    except ImportError:
        return values, None
    audio = np.asarray(amplitudes,dtype=np.float32)/32768
    spectrum = np.abs(np.fft.rfft(audio*np.hanning(len(audio))))+1e-12
    freqs = np.fft.rfftfreq(len(audio),1/8000)
    energy = spectrum**2
    total=float(energy.sum())
    centroid = float((freqs*spectrum).sum()/spectrum.sum())
    flatness = float(np.exp(np.log(spectrum).mean())/spectrum.mean())
    low = float(energy[freqs<=300].sum()/total)
    unit = spectrum/max(float(np.linalg.norm(spectrum)),1e-12)
    flux=(float(np.linalg.norm(unit-previous_spectrum))
          if previous_spectrum is not None and len(unit)==len(previous_spectrum) else None)
    values.update({"spectralCentroidHz":round(centroid,3),
                   "spectralFlatness":round(flatness,6),
                   "spectralFlux":round(flux,6) if flux is not None else None,
                   "lowBandFraction":round(low,6)})
    return values,unit


def audio_series(path: Path, *, sample_rate: int = 8000,
                 window_ms: int = 1000) -> list[dict]:
    if sample_rate != 8000 or window_ms != 1000:
        raise ValueError("Audio v1 uses fixed 8 kHz, 1 second windows")
    if not probe(path).get("audio"):
        return []
    cmd = [require_tool("ffmpeg"), "-hide_banner", "-loglevel", "error",
           "-nostdin", "-i", str(path), "-map", "0:a:0", "-vn", "-sn",
           "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "pipe:1"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout is not None
    frame_bytes = sample_rate * 2
    out: list[dict] = []
    index = 0
    previous_spectrum = None
    try:
        while True:
            data = proc.stdout.read(frame_bytes)
            if not data:
                break
            rms, crossings = _rms(data)
            metrics, previous_spectrum = acoustic_metrics(data,previous_spectrum)
            out.append({"t_ms": index * window_ms, "end_ms": (index+1) * window_ms,
                        "rms": rms, "zeroCrossingRate": crossings, **metrics,
                        "unit": "normalized_pcm_amplitude",
                        "interpretation": "acoustic change only; no breathing assessment"})
            index += 1
    finally:
        proc.stdout.close()
        try:
            code = proc.wait(timeout=90)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise RuntimeError("Audio decode did not close")
        if code:
            message = proc.stderr.read(4096).decode("utf-8", "replace") if proc.stderr else ""
            raise RuntimeError(f"Audio decode failed ({code}): {message[:200]}")
        if proc.stderr:
            proc.stderr.close()
    return out


def acoustic_changes(series: list[dict]) -> list[dict]:
    """Uncalibrated acoustic activity spikes, with no semantic attribution."""
    values = [float(x["rms"]) for x in series]
    if len(values) < 3:
        return []
    center = median(values)
    mad = median(abs(x-center) for x in values)
    threshold = max(0.015, center + max(0.012, 3.5 * 1.4826 * mad),
                    center * 2.5)
    return [{
        "startMs": int(x["t_ms"]), "endMs": int(x["end_ms"]),
        "rms": x["rms"], "evidence": "relative_acoustic_transient",
        "classification": "unknown_acoustic_cause",
    } for x in series if x["rms"] >= threshold]


def analyze_audio(workspace: Path, *, asset_ids: set[str] | None = None,
                  limit: int | None = None, progress=None) -> dict:
    if limit is not None and limit <= 0:
        raise ValueError("limit must be positive")
    result = {"processed": 0, "cached": 0, "silent": 0, "failed": []}
    with connect(workspace) as db:
        assets = [a for a in db.execute("SELECT * FROM assets ORDER BY created_at,id")
                  if asset_ids is None or a["id"] in asset_ids]
        if limit is not None:
            assets = assets[:limit]
        if progress:
            progress.start("acoustics", len(assets))
        for i, asset in enumerate(assets, 1):
            aid = asset["id"]
            try:
                if read_artifact(db, workspace, aid, AUDIO_VERSION) is not None:
                    result["cached"] += 1
                    detail = "cached"
                else:
                    source = resolve_source(db, asset)
                    samples = audio_series(source)
                    if verified_digest(source) != aid:
                        raise OSError("Source changed during audio analysis")
                    write_artifact(db, workspace, aid, AUDIO_VERSION, samples)
                    result["processed"] += 1
                    if not samples:
                        result["silent"] += 1
                    detail = "analyzed" if samples else "no audio stream"
                if progress:
                    progress.item("acoustics", i, len(assets), f"{aid[:12]} {detail}")
            except Exception as exc:
                result["failed"].append({"assetId": aid, "error": str(exc)})
                if progress:
                    progress.item("acoustics", i, len(assets), f"{aid[:12]} failed")
    return result
