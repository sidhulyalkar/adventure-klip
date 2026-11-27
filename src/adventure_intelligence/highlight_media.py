"""Where a highlight's pixels come from, and how its final files are cut.

Three kinds of media, from cheapest to best:
  * review clips that already exist (discovery proxies, search clips): small, 960 px;
  * a preview cut from the camera original around a moment, with a few seconds of context
    on each side, so the owner can see it and set exact in and out points;
  * the export of a moment the owner kept: a master at the original's resolution, a
    1080p web file without camera metadata, and a poster frame, cut exactly to the owner's
    trim.

Originals are only ever read. A path is used only if it sits under the trusted source root
and still has the size recorded when it was hashed, so a moved, re-copied or edited file is
refused rather than silently used. Every write goes to a .partial file first, then is renamed.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import shutil
import subprocess

from .discovery_experiment import cut_proxy
from .media import require_tool

EXPORT_VERSION = "klip-highlight-export-v1"


def _read(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def discovery_sources(reports: Path) -> dict[str, dict]:
    """sha -> {path, sizeBytes, sport, proxies: [(startMs, endMs, path)]} from every
    discovery folder (judge-sources relabels first; the first folder naming a sha wins)."""
    out: dict[str, dict] = {}
    for folder in sorted(Path(reports).glob("discovery-*")):
        rows = _read(folder / "judge-sources.json") or _read(folder / "sources.json") or []
        for row in rows:
            sha = row["sourceSha256"]
            source = _read(folder / sha / "source.json") or {}
            d = out.setdefault(sha, {"path": row["path"], "sport": row.get("sport"),
                                     "sizeBytes": source.get("sizeBytes"), "proxies": []})
            if d["sizeBytes"] is None and source.get("sizeBytes") is not None:
                d["sizeBytes"] = source["sizeBytes"]
            for p in sorted((folder / sha / "proxies").glob("*.mp4")):
                try:
                    a, b = (int(x) for x in p.stem.split("-"))
                except ValueError:
                    continue
                d["proxies"].append((a, b, p))
    return out


def catalog_sources(workspace: Path) -> dict[str, dict]:
    """sha -> {path, sizeBytes, durationMs} of each imported original (read-only)."""
    db_path = Path(workspace) / "catalog.sqlite3"
    if db_path.is_symlink() or not db_path.is_file():
        return {}
    db = sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)
    try:
        db.execute("PRAGMA query_only=ON")
        return {sha: {"path": path, "sizeBytes": size,
                      "durationMs": round(duration * 1000) if duration else None}
                for sha, path, size, duration in
                db.execute("SELECT sha256,stored_path,size_bytes,duration FROM assets")}
    finally:
        db.close()


class Originals:
    """Camera originals by SHA-256, refused unless under the trusted root and unchanged."""

    def __init__(self, reports: Path, trusted_root: Path | None, *,
                 discovery: dict | None = None):
        self.discovery = discovery if discovery is not None else discovery_sources(reports)
        self.catalog = catalog_sources(Path(reports).parent)
        self.trusted = Path(trusted_root).expanduser().resolve() if trusted_root else None

    def recorded(self, sha: str) -> tuple[Path, int | None]:
        d = self.discovery.get(sha)
        if d is not None and d.get("sizeBytes") is not None:
            return Path(d["path"]).expanduser(), d["sizeBytes"]
        if sha in self.catalog:          # hashed when it was imported into the catalog
            c = self.catalog[sha]
            return Path(c["path"]).expanduser(), c["sizeBytes"]
        if d is not None:
            return Path(d["path"]).expanduser(), None
        raise ValueError("recording is in neither a discovery pass nor the catalog")

    def path(self, sha: str) -> Path:
        if self.trusted is None:
            raise ValueError("needs --trusted-source-root (no review clip covers this window)")
        path, size = self.recorded(sha)
        if not path.resolve().is_relative_to(self.trusted):
            raise ValueError(f"{path.name} is not under --trusted-source-root "
                             f"(recorded at {path.parent}; is the drive mounted?)")
        if not path.is_file():
            raise ValueError(f"{path.name} not found at {path.parent} (is the drive mounted?)")
        if size is None or path.stat().st_size != size:
            raise ValueError(f"{path.name} changed since it was hashed (size differs)")
        return path

    def proxy(self, sha: str, start_ms: int, end_ms: int) -> tuple[Path, int, int] | None:
        """The shortest existing review clip containing the window."""
        covering = [(a, b, p) for a, b, p in (self.discovery.get(sha) or {}).get("proxies", [])
                    if a <= start_ms and end_ms <= b]
        if not covering:
            return None
        a, b, p = min(covering, key=lambda o: o[1] - o[0])
        return p, a, b


def preview(originals: Originals, sha: str, start_ms: int, end_ms: int, folder: Path, *,
            pad_ms: int = 4000, after_ms: int | None = None,
            duration_ms: int | None = None) -> tuple[Path, int, int]:
    """A review clip of [start - pad, end + after] cut from the original (960 px, with
    sound; after defaults to pad). Cached by window, so a rerun reuses it."""
    after = pad_ms if after_ms is None else after_ms
    lo = max(0, start_ms - pad_ms)
    hi = end_ms + after if duration_ms is None else min(duration_ms, end_ms + after)
    dest = Path(folder) / f"{sha[:16]}-{lo}-{hi}.mp4"
    if not dest.is_file():
        dest.parent.mkdir(parents=True, exist_ok=True)
        cut_proxy(originals.path(sha), dest, lo, hi, width=960)
    return dest, lo, hi


# ---------------------------------------------------------------- exports

@dataclass(frozen=True)
class Profile:
    """How exports are encoded. Changing any field re-cuts on the next export."""
    master_crf: int = 18
    web_crf: int = 23
    web_width: int = 1920
    web_maxrate_kbps: int = 8000
    poster_width: int = 1280
    preset: str = "veryfast"

    def settings(self) -> dict:
        return {"version": EXPORT_VERSION, **self.__dict__}


def _ffmpeg(args: list[str], seconds: float) -> None:
    job = subprocess.run([require_tool("ffmpeg"), "-hide_banner", "-loglevel", "error",
                          "-nostdin", *args], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                         timeout=max(300, int(seconds * 30)), check=False)
    if job.returncode != 0:
        raise ValueError(f"ffmpeg failed: {job.stderr.decode(errors='replace')[-300:].strip()}")


def _encode(source: Path, dest: Path, start_ms: int, end_ms: int, video: list[str],
            audio: list[str], *, strip: bool) -> None:
    partial = dest.with_suffix(".partial" + dest.suffix)
    seconds = (end_ms - start_ms) / 1000
    # -ss before -i with a re-encode is frame-accurate; only the first video and audio
    # streams are kept (DJI files carry timecode and telemetry data streams).
    meta = ["-map_metadata", "-1", "-map_chapters", "-1"] if strip else []
    _ffmpeg(["-ss", f"{start_ms / 1000:.3f}", "-i", str(source), "-t", f"{seconds:.3f}",
             "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn", *meta, *video, *audio,
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-y", str(partial)], seconds)
    if not partial.is_file() or partial.stat().st_size < 1000:
        partial.unlink(missing_ok=True)
        raise ValueError("encode produced no file")
    partial.replace(dest)


def _poster(source: Path, dest: Path, at_ms: int, width: int) -> None:
    partial = dest.with_suffix(".partial.jpg")
    _ffmpeg(["-ss", f"{at_ms / 1000:.3f}", "-i", str(source), "-frames:v", "1",
             "-map_metadata", "-1", "-vf", f"scale='min({width},iw)':-2", "-q:v", "3",
             "-y", str(partial)], 1)
    if not partial.is_file():
        raise ValueError("poster produced no file")
    partial.replace(dest)


def probe(path: Path) -> dict:
    job = subprocess.run([require_tool("ffprobe"), "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=width,height:format=duration",
                          "-of", "json", str(path)], capture_output=True, timeout=60, check=False)
    try:
        doc = json.loads(job.stdout or b"{}")
        s = (doc.get("streams") or [{}])[0]
        return {"width": s.get("width"), "height": s.get("height"),
                "durationS": round(float(doc["format"]["duration"]), 3)}
    except (ValueError, KeyError, TypeError):
        return {}


def file_sha256(path: Path) -> str:
    h = sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


DEFAULT_PROFILE = Profile()


def export_moment(original: Path, moment: dict, folder: Path,
                  profile: Profile = DEFAULT_PROFILE, *, poster_ms: int | None = None) -> dict:
    """Cut one moment exactly to its window: master.mp4 (original resolution), web.mp4
    (<= 1080p, no camera metadata, streamable) and poster.jpg. Idempotent: an export with the
    same window and settings is reused; anything else is cut into a new folder by moment id,
    so earlier exports are never overwritten."""
    folder = Path(folder)
    start, end = int(moment["startMs"]), int(moment["endMs"])
    if end - start < 500:
        raise ValueError("window shorter than half a second")
    want = {"sourceSha256": moment["sourceSha256"], "startMs": start, "endMs": end,
            "settings": profile.settings()}
    record = _read(folder / "export.json")
    if record and {k: record.get(k) for k in want} == want and all(
            (folder / n).is_file() for n in ("master.mp4", "web.mp4", "poster.jpg")):
        return {**record, "reused": True}
    folder.mkdir(parents=True, exist_ok=True)
    x264 = ["-c:v", "libx264", "-preset", profile.preset]
    _encode(original, folder / "master.mp4", start, end, [*x264, "-crf", str(profile.master_crf)],
            ["-c:a", "aac", "-b:a", "192k"], strip=False)
    rate = profile.web_maxrate_kbps
    _encode(original, folder / "web.mp4", start, end,
            [*x264, "-crf", str(profile.web_crf), "-maxrate", f"{rate}k",
             "-bufsize", f"{rate * 2}k", "-vf", f"scale='min({profile.web_width},iw)':-2",
             "-profile:v", "high"],
            ["-c:a", "aac", "-b:a", "128k"], strip=True)
    at = poster_ms if poster_ms is not None and start <= poster_ms < end else (start + end) // 2
    _poster(original, folder / "poster.jpg", at, profile.poster_width)
    record = {**want, "momentId": moment["id"], "original": original.name,
              "posterMs": at, "files": {n: {"bytes": (folder / n).stat().st_size,
                                            "sha256": file_sha256(folder / n),
                                            **(probe(folder / n) if n.endswith(".mp4") else {})}
                                        for n in ("master.mp4", "web.mp4", "poster.jpg")}}
    tmp = folder / "export.json.tmp"
    tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
    tmp.replace(folder / "export.json")
    return {**record, "reused": False}


def export_sequence(parts: list[tuple[Path, dict]], folder: Path, sequence_id: str,
                    profile: Profile = DEFAULT_PROFILE) -> dict:
    """One file for a sequence of moments: parts on one recording are cut as one span from the
    first start to the last end (so the footage between them is included: the rest of the
    line, the run-up to the crash); parts on different recordings are cut and joined in
    order. parts: (original path, moment) in time order. Same files and idempotency as
    export_moment."""
    spans: list[list] = []
    for original, m in parts:
        if spans and spans[-1][1]["sourceSha256"] == m["sourceSha256"]:
            spans[-1][3] = max(spans[-1][3], int(m["endMs"]))
        else:
            spans.append([original, m, int(m["startMs"]), int(m["endMs"])])
    folder = Path(folder)
    want = {"sequenceId": sequence_id,
            "spans": [{"sourceSha256": m["sourceSha256"], "startMs": a, "endMs": b}
                      for _, m, a, b in spans], "settings": profile.settings()}
    record = _read(folder / "export.json")
    if record and {k: record.get(k) for k in want} == want and all(
            (folder / n).is_file() for n in ("master.mp4", "web.mp4", "poster.jpg")):
        return {**record, "reused": True}
    folder.mkdir(parents=True, exist_ok=True)
    work = folder / "parts.partial"
    work.mkdir(exist_ok=True)
    try:
        pieces = []
        for i, (original, m, a, b) in enumerate(spans):
            sub = work / str(i)
            export_moment(original, {"id": f"{sequence_id}-{i}", "sourceSha256": m["sourceSha256"],
                                     "startMs": a, "endMs": b}, sub, profile)
            pieces.append(sub)
        for name in ("master.mp4", "web.mp4"):
            if len(pieces) == 1:
                (folder / f"{name}.partial").write_bytes((pieces[0] / name).read_bytes())
            else:
                listing = work / f"{name}.txt"
                listing.write_text("".join(f"file '{(p / name).resolve()}'\n" for p in pieces))
                _ffmpeg(["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy",
                         "-movflags", "+faststart", "-f", "mp4", "-y",
                         str(folder / f"{name}.partial")], 60)
            (folder / f"{name}.partial").replace(folder / name)
        (folder / "poster.jpg").write_bytes((pieces[0] / "poster.jpg").read_bytes())
    finally:
        shutil.rmtree(work, ignore_errors=True)
    record = {**want, "members": [m["id"] for _, m in parts],
              "files": {n: {"bytes": (folder / n).stat().st_size,
                            "sha256": file_sha256(folder / n),
                            **(probe(folder / n) if n.endswith(".mp4") else {})}
                        for n in ("master.mp4", "web.mp4", "poster.jpg")}}
    tmp = folder / "export.json.tmp"
    tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
    tmp.replace(folder / "export.json")
    return {**record, "reused": False}
