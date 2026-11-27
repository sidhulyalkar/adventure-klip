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


def test_a_single_trim_mark_keeps_its_meaning():
    # A start alone runs to the moment's end; past the moment, to the clip's end.
    assert review._trim(19048, None, 0, 40000, 10000, 30000) == ((19048, 30000), "start")
    assert review._trim(33000, None, 0, 40000, 10000, 30000) == ((33000, 40000), "start")
    assert review._trim(None, 15000, 0, 40000, 10000, 30000) == ((10000, 15000), "end")
    assert review._trim(None, 5000, 0, 40000, 10000, 30000) == ((0, 5000), "end")
    assert review._trim(9000, 8000, 0, 40000, 10000, 30000) == ((None, None), None)
    assert review._trim(50000, None, 0, 40000, 10000, 30000) == ((None, None), None)


def test_ratings_separate_highlights_from_memories_and_linked_moments_form_sequences():
    from adventure_intelligence import highlight_library as lib
    f1, f2 = "DJI_20250301100000_0001_D.MP4", "DJI_20250301100130_0002_D.MP4"   # 90 s apart

    def label(sha, s, e, stars, **kw):
        return {"item": f"{sha[:2]}{s}", "sourceSha256": sha, "startMs": s, "endMs": e,
                "decision": "keep", "stars": stars, "what": [], **kw}
    labels = [label(A, 10000, 20000, 5), label(A, 40000, 50000, 2),          # 20 s apart
              label(B, 5000, 9000, 3, moreBefore=True),                       # next file
              label(A, 70000, 75000, 1)]                                       # 20 s after
    ctx = {A: {"sport": "ski", "outing": "o1", "file": f1}, B: {"sport": "ski", "outing": "o1", "file": f2}}
    library = lib.build(lib.review_evidence(labels), ctx)
    by = {(m["sourceSha256"], m["startMs"]): m for m in library["moments"]}
    assert by[(A, 10000)]["purpose"] == "highlight" and by[(A, 40000)]["purpose"] == "memory"
    assert by[(B, 5000)]["purpose"] == "keep" and by[(B, 5000)]["wantMore"] == {"before": True, "after": False}
    # A 70-75 s ends at 10:01:15; B starts at 10:01:35 (+5 s): 20 s later, so one chain of four.
    [seq] = library["sequences"]
    assert [by[(A, 10000)]["id"], by[(A, 40000)]["id"], by[(A, 70000)]["id"], by[(B, 5000)]["id"]] == seq["members"]
    assert by[(B, 5000)]["sequence"] == {"id": seq["id"], "index": 4, "of": 4} and seq["purpose"] == "highlight"
    # Far apart: no sequence.
    apart = lib.build(lib.review_evidence([label(A, 0, 5000, 4), label(A, 200000, 205000, 4)]), ctx)
    assert apart["sequences"] == [] and all("sequence" not in m for m in apart["moments"])


@needs_ffmpeg
def test_a_sequence_exports_one_span_per_recording_joined_in_order(tmp_path):
    a = _video(tmp_path / "a.mp4", 20)
    b = _video(tmp_path / "b.mp4", 10)
    parts = [(a, {"id": "m1", "sourceSha256": A, "startMs": 2000, "endMs": 4000}),
             (a, {"id": "m2", "sourceSha256": A, "startMs": 7000, "endMs": 8000}),
             (b, {"id": "m3", "sourceSha256": B, "startMs": 1000, "endMs": 2500})]
    profile = hm.Profile(preset="ultrafast")
    record = hm.export_sequence(parts, tmp_path / "seq", "s1", profile)
    # A: 2-8 s (the gap between the two kept moments is included), then B: 1-2.5 s.
    assert [(x["startMs"], x["endMs"]) for x in record["spans"]] == [(2000, 8000), (1000, 2500)]
    assert abs(record["files"]["web.mp4"]["durationS"] - 7.5) < 0.3
    assert not (tmp_path / "seq" / "parts.partial").exists()
    assert hm.export_sequence(parts, tmp_path / "seq", "s1", profile)["reused"] is True


def test_page_shows_sequence_parts_and_import_keeps_more_context_requests(tmp_path):
    item = {"role": "library", "kind": "owner_keep", "sport": "ski", "sourceSha256": A,
            "file": None, "day": None, "outing": "o1", "heldOut": False, "startMs": 0,
            "endMs": 5000, "mediaStartMs": 0, "mediaEndMs": 9000,
            "sequence": {"id": "s", "index": 2, "of": 3}}
    deck = {"deck": "x", "order": ["m"], "items": {"m": item}}
    page = review.page(deck, tmp_path, lambda i: tmp_path / "c.mp4")
    assert "part 2 of 3 of a sequence (continues the clip above)" in page
    assert page.count('aria-pressed="false"') == 2 and page.count("<fieldset>") == 4
    answers = {"version": review.ANSWERS_VERSION, "deck": "x", "answers": {
        "m": {"decision": "keep", "moreBefore": True, "bestStartMs": 1000}}}
    out = review.import_answers(tmp_path, deck, answers, json.dumps(answers).encode())
    from adventure_intelligence import owner_labels
    [label] = owner_labels.load(tmp_path, "highlight")
    assert out["added"] == 1 and label["moreBefore"] is True and label["moreAfter"] is False
    assert (label["bestStartMs"], label["bestEndMs"], label["trimSides"]) == (1000, 5000, "start")
