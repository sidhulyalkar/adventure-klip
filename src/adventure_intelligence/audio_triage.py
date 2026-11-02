"""Cheap acoustic triage for deciding where to spend visual inference time.

Audio is a *routing* signal, never a label for skiing, crashing, breathing,
animals, or whether a clip deserves to be omitted. Missing audio is unknown.
"""
from __future__ import annotations
from statistics import median
import math

VERSION = "audio-triage-v1"


def audio_window(samples: list[dict] | None, start_ms: int, end_ms: int) -> dict:
    if start_ms < 0 or end_ms <= start_ms:
        raise ValueError("Invalid audio interval")
    if not samples:
        return {"available": False, "status": "unknown", "reason": "no_cached_audio"}
    valid = [x for x in samples if x.get("t_ms", -1) < end_ms
             and x.get("end_ms", -1) > start_ms
             and isinstance(x.get("rms"),(int,float))
             and math.isfinite(x["rms"])]
    if not valid:
        return {"available": False, "status": "unknown", "reason": "no_samples_in_window"}
    rms = [max(0,float(x["rms"])) for x in valid]
    background = median(max(0,float(x["rms"])) for x in samples
                        if isinstance(x.get("rms"),(int,float))
                        and math.isfinite(x["rms"]))
    clipped = [float(x["clippingFraction"]) for x in valid
               if isinstance(x.get("clippingFraction"),(float,int))]
    flux = [float(x["spectralFlux"]) for x in valid
            if isinstance(x.get("spectralFlux"),(float,int))
            and math.isfinite(x["spectralFlux"])]
    changes = sum(abs(rms[i]-rms[i-1]) >= max(.03, background*1.5)
                  for i in range(1,len(rms)))
    # Prioritize acoustic transitions / defects for additional inspection.
    # A quiet, constant signal never proves 'boring' footage.
    high_clipping = bool(clipped and max(clipped) > .03)
    transition = changes >= 1 or bool(flux and max(flux) > .55)
    return {
        "available": True, "status": "uncalibrated",
        "observations": len(valid),
        "medianRms": round(median(rms), 6),
        "relativeToRecording": round(median(rms)/max(background,1e-6), 4),
        "rmsTransitions": changes,
        "peakClippingFraction": round(max(clipped),6) if clipped else None,
        "maxSpectralFlux": round(max(flux),6) if flux else None,
        "possibleDistortion": high_clipping,
        "unusualAcousticChange": transition,
        "suggestVisualInspection": high_clipping or transition,
        "interpretation": "Unattributed audio; never alone excludes a clip or confirms an event",
        "version": VERSION,
    }
