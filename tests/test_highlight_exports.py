"""Library page cut from originals, exact exports of owner trims, and the showcase bundle.

Uses a few seconds of synthetic video made with ffmpeg; skipped where ffmpeg is missing."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from adventure_intelligence import highlight_media as hm
from adventure_intelligence import highlight_review as review

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                                  reason="needs ffmpeg")
A = "a" * 64
B = "b" * 64


def _cli():
    spec = importlib.util.spec_from_file_location(
        "klip_highlight_library", Path(__file__).parent.parent / "scripts" / "klip_highlight_library.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _video(path: Path, seconds: int = 30) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"testsrc2=size=1280x720:rate=30:duration={seconds}", "-f", "lavfi", "-i",
                    f"sine=frequency=440:duration={seconds}", "-metadata", "location=+12.3+045.6/",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest",
                    "-y", str(path)], check=True)
    return path


def _workspace(tmp_path: Path, original: Path) -> Path:
    reports = tmp_path / "reports"
    disc = reports / "discovery-001"
    (disc / A).mkdir(parents=True)
    (disc / A / "source.json").write_text(json.dumps({"sizeBytes": original.stat().st_size}))
    (disc / "sources.json").write_text(json.dumps(
        [{"sourceSha256": A, "path": str(original), "sport": "ski"}]))
    ledgers = reports / "owner-ledgers"
    ledgers.mkdir()
    grid = {"version": "klip-owner-grid-ledger-v1", "deckSha256": "d" * 64, "index": 0,
            "clips": [{"sourceSha256": A, "startMs": 10000, "endMs": 16000}], "keep": [0], "best": 0}
    (ledgers / "owner-pairs.jsonl").write_text(json.dumps(grid) + "\n")
    (ledgers / "roster.json").write_text(json.dumps({
        "version": "klip-outing-roster-v1", "status": "owner_confirmed",
        "outings": [{"outing": "o1", "title": "First chair", "place": "Alta", "trip": "t1",
                     "sources": [{"sourceSha256": A, "activity": "ski"}]}],
        "trips": {"t1": {"title": "Utah week"}}}))
    return reports


def test_originals_refuse_files_outside_the_root_or_changed(tmp_path):
    reports = tmp_path / "reports"
    disc = reports / "discovery-001"
    (disc / A).mkdir(parents=True)
    src = tmp_path / "drive" / "DJI_1.MP4"
    src.parent.mkdir()
    src.write_bytes(b"x" * 100)
    (disc / A / "source.json").write_text(json.dumps({"sizeBytes": 100}))
    (disc / "sources.json").write_text(json.dumps([{"sourceSha256": A, "path": str(src), "sport": "ski"}]))
    assert hm.Originals(reports, tmp_path / "drive").path(A) == src
    with pytest.raises(ValueError, match="not under --trusted-source-root"):
        hm.Originals(reports, tmp_path / "elsewhere").path(A)
    src.write_bytes(b"x" * 101)
    with pytest.raises(ValueError, match="changed since it was hashed"):
        hm.Originals(reports, tmp_path / "drive").path(A)
    with pytest.raises(ValueError, match="neither a discovery pass"):
        hm.Originals(reports, tmp_path / "drive").path(B)


def test_page_says_why_a_clip_is_missing_and_ticks_exist_for_any_sport(tmp_path):
    item = {"role": "library", "kind": "owner_keep", "sport": "kayaking", "sourceSha256": A,
            "file": None, "day": None, "outing": None, "heldOut": False, "startMs": 0,
            "endMs": 5000}
    deck = {"deck": "x", "order": ["m"], "items": {"m": item}}
    page = review.page(deck, tmp_path, lambda i: None, {"m": "DJI_1.MP4 not found (is the drive mounted?)"})
    assert "No clip yet: DJI_1.MP4 not found" in page
    assert 'value="friend"' in page and "background:#0b0b0c" in page
    assert review.what_for("skiing") == review.WHAT["ski"]


@needs_ffmpeg
def test_page_cuts_from_original_then_trim_export_and_showcase(tmp_path, capsys):
    cli = _cli()
    original = _video(tmp_path / "drive" / "DCIM" / "DJI_20260201100000_0001_D.MP4")
    before = original.read_bytes()
    reports = _workspace(tmp_path, original)
    root = tmp_path / "drive"

    assert cli.main(["build", "--reports", str(reports)]) == 0
    capsys.readouterr()
    # Without the drive: the page says what to do instead of showing nothing.
    assert cli.main(["page", "--reports", str(reports)]) == 0
    page = json.loads(capsys.readouterr().out)
    assert page["withoutClip"] == 1 and "--trusted-source-root" in Path(page["page"]).read_text()
    # With it: a preview with 4 s of context each side is cut, and the page plays it.
    assert cli.main(["page", "--reports", str(reports), "--trusted-source-root", str(root)]) == 0
    page = json.loads(capsys.readouterr().out)
    assert page["withoutClip"] == 0 and page["clipsCutFromOriginals"] == 1
    deck = json.loads((Path(page["page"]).parent / "deck.json").read_text())
    [item_id] = deck["order"]
    assert (deck["items"][item_id]["mediaStartMs"], deck["items"][item_id]["mediaEndMs"]) == (6000, 20000)
    assert "#t=4.00,10.00" in Path(page["page"]).read_text()

    # The owner trims to 11.5-14.0 s and rates it 5; the rebuilt library takes the trim.
    answers = {"version": review.ANSWERS_VERSION, "deck": page["deck"], "answers": {
        item_id: {"decision": "keep", "stars": 5, "what": ["speed"], "note": "private",
                  "bestStartMs": 11500, "bestEndMs": 14000}}}
    f = tmp_path / "answers.json"
    f.write_text(json.dumps(answers))
    assert cli.main(["import-answers", "--reports", str(reports), "--answers", str(f)]) == 0
    assert cli.main(["build", "--reports", str(reports)]) == 0
    capsys.readouterr()

    assert cli.main(["export", "--reports", str(reports), "--trusted-source-root", str(root),
                     "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["wouldExport"] == 1
    assert cli.main(["export", "--reports", str(reports), "--trusted-source-root", str(root),
                     "--preset", "ultrafast"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {**result, "exported": 1, "failed": 0}
    [folder] = [p for p in (reports / "highlight-library-v1" / "exports").iterdir() if p.is_dir()]
    record = json.loads((folder / "export.json").read_text())
    assert (record["startMs"], record["endMs"]) == (11500, 14000)
    assert abs(record["files"]["web.mp4"]["durationS"] - 2.5) < 0.15
    assert record["files"]["master.mp4"]["width"] == 1280
    tags = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json",
                           str(folder / "web.mp4")], capture_output=True, text=True).stdout
    assert "location" not in tags            # no GPS in what may be published
    # A second export reuses the files.
    assert cli.main(["export", "--reports", str(reports), "--trusted-source-root", str(root),
                     "--preset", "ultrafast"]) == 0
    assert json.loads(capsys.readouterr().out)["reused"] == 1
    assert original.read_bytes() == before   # the original was only read

    out = tmp_path / "site" / "public" / "highlights"
    assert cli.main(["showcase", "--reports", str(reports), "--out", str(out),
                     "--base-url", "/highlights/media"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["highlights"] == 1
    doc = json.loads((out / "highlights.json").read_text())
    [h] = doc["highlights"]
    assert h["title"] == "First chair" and h["where"] == "Alta, Utah week" and h["tags"] == ["speed"]
    assert h["video"].startswith("/highlights/media/") and "note" not in h
    assert A not in json.dumps(doc) and "DJI_" not in json.dumps(doc)
    assert (out / "media" / f'{h["id"]}.mp4').is_file() and (out / "media" / f'{h["id"]}.jpg').is_file()
    assert "<dialog" in (out / "index.html").read_text()
