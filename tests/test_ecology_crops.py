"""Synthetic source-immutability and crop/PTS checks for bounded ecology evidence."""
from io import BytesIO
import json

import pytest

from adventure_intelligence.cli import main
from adventure_intelligence.ecology_crops import (
    _crop_png, _select_samples, create_crop_packet,
)
from adventure_intelligence.indexing import index_folder


@pytest.fixture
def sample(tmp_path, monkeypatch):
    source = tmp_path / "camera"
    source.mkdir()
    movie = source / "DJI_CROP.MP4"
    original = b"synthetic-source-must-remain-unchanged" * 200
    movie.write_bytes(original)
    monkeypatch.setattr("adventure_intelligence.indexing.probe",
                        lambda p: {"duration": 90., "audio": False,
                                   "capture_time": None})
    workspace = tmp_path / "workspace"
    aid = index_folder(source, workspace, activity="snorkeling")["asset_ids"][0]
    return workspace, aid, movie, original


def _frames():
    def box(x, score=.8):
        return {"label": "fish", "score": score, "bboxFormat": "xyxy",
                "coordinateSpace": "sampled_image_pixels",
                "bbox": {"xmin": x, "ymin": 20, "xmax": x + 30, "ymax": 50}}
    return [
        {"tMs": 5000, "decodedPtsMs": 5020, "objects": [box(30), box(110, .75)]},
        {"tMs": 7000, "decodedPtsMs": 7020, "objects": [box(31)]},
        {"tMs": 9000, "decodedPtsMs": 9020, "objects": [box(32)]},
    ]


def _mock_cached(monkeypatch, frames=None):
    if frames is None:
        frames = _frames()
    monkeypatch.setattr(
        "adventure_intelligence.ecology_encounters.frames_for_asset",
        lambda db, ws, aid, provider, **kwargs: (
            (frames, '{"provider":"ecology-owlv2","version":"test"}')
            if provider == "ecology-owlv2" else ([], None)))


def _png(width=1536, height=864):
    from PIL import Image
    image = Image.new("RGB", (width, height), (10, 80, 150))
    out = BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def test_crop_packet_source_immutable_with_pts_and_provenance(sample, monkeypatch):
    workspace, aid, movie, original = sample
    _mock_cached(monkeypatch)
    seen = []

    def decode(path, t, width=1536):
        assert path == movie
        seen.append(t)
        return _png(width=width, height=width * 9 // 16), {
            "decodedPtsMs": t + 20,
            "timestampBasis": "decoded_frame_pts",
        }

    monkeypatch.setattr("adventure_intelligence.ecology_crops.extract_frame_png", decode)
    dest = workspace / "reports" / "private-crops"
    report = create_crop_packet(workspace, aid, dest, max_events=2, crops_per_event=3)
    assert report["status"] == "review_packet_ready"
    manifest = json.loads((dest / "manifest.json").read_text())
    assert report["cropCount"] >= 2
    assert manifest["sourceAssetSha256"] == aid
    assert manifest["sourceVerifiedBeforeAndAfter"] is True
    assert manifest["uniqueFrameDecodes"] == len(set(seen))
    assert manifest["uniqueFrameDecodes"] <= 6
    assert all(c["cachedSourceDecodedPtsMs"] == c["redecodedSourcePtsMs"]
               for c in manifest["crops"])
    assert all(c["speciesTaxonId"] is None and c["confirmedSighting"] is False
               for c in manifest["crops"])
    assert all((dest / c["image"]).read_bytes().startswith(b"\x89PNG")
               for c in manifest["crops"])
    assert (dest / "index.html").exists()
    assert "Unverified object proposal" in (dest / "index.html").read_text()
    assert movie.read_bytes() == original
    with pytest.raises(ValueError, match="new folder"):
        create_crop_packet(workspace, aid, dest)


def test_missing_cached_source_pts_abstains_without_decoding(sample, monkeypatch):
    workspace, aid, movie, original = sample
    frames = _frames()
    for f in frames:
        f["decodedPtsMs"] = None
    _mock_cached(monkeypatch, frames)
    monkeypatch.setattr("adventure_intelligence.ecology_crops.extract_frame_png",
                        lambda *args, **kwargs: pytest.fail("Decoder must not run"))
    dest = workspace / "exports" / "no-evidence"
    result = create_crop_packet(workspace, aid, dest)
    assert result["status"] == "abstained_no_source_pts_backed_boxes"
    assert not dest.exists()
    assert movie.read_bytes() == original


def test_pts_mismatch_never_saves_a_mismatched_crop(sample, monkeypatch):
    workspace, aid, movie, original = sample
    _mock_cached(monkeypatch)
    monkeypatch.setattr(
        "adventure_intelligence.ecology_crops.extract_frame_png",
        lambda path, t, width: (_png(width, width * 9 // 16),
                                {"decodedPtsMs": t + 400}))
    dest = workspace / "reports" / "pts-mismatch"
    result = create_crop_packet(workspace, aid, dest, max_events=1)
    assert result["status"] == "abstained_no_valid_crops"
    manifest = json.loads((dest / "manifest.json").read_text())
    assert manifest["cropCount"] == 0
    assert len(manifest["failures"]) >= 1
    assert not list(dest.glob("crop-*.png"))
    assert movie.read_bytes() == original


def test_source_mutation_invalidates_packet_before_manifest(sample, monkeypatch):
    workspace, aid, movie, original = sample
    _mock_cached(monkeypatch)

    def tamper(path, requested, width):
        path.write_bytes(original + b"tampered")
        return _png(width, width * 9 // 16), {"decodedPtsMs": requested + 20}

    monkeypatch.setattr("adventure_intelligence.ecology_crops.extract_frame_png", tamper)
    dest = workspace / "reports" / "tampered"
    with pytest.raises(RuntimeError, match="source bytes changed"):
        create_crop_packet(workspace, aid, dest, max_events=1)
    assert not (dest / "manifest.json").exists()
    assert not list(dest.glob("crop-*.png"))


def test_invalid_coordinates_and_bad_png_fail_closed():
    with pytest.raises(ValueError, match="outside"):
        _crop_png(_png(), [380, 50, 500, 80], 1536, margin=.4)
    with pytest.raises(ValueError, match="Malformed"):
        _crop_png(_png(), [20, 20, float("nan"), 40], 1536, margin=.4)
    with pytest.raises(ValueError, match="Invalid frame"):
        _crop_png(b"notpng", [20, 20, 40, 40], 1536, margin=.4)


def test_cli_opt_in_and_source_protection(sample, monkeypatch, capsys):
    workspace, aid, movie, original = sample
    _mock_cached(monkeypatch)
    monkeypatch.setattr(
        "adventure_intelligence.ecology_crops.extract_frame_png",
        lambda path, t, width: (_png(width, width * 9 // 16),
                                {"decodedPtsMs": t + 20}))
    target = workspace / "exports" / "crop-packet"
    command = ["--workspace", str(workspace), "ecology-crops",
               "--asset", aid, "--max-events", "1",
               "--crops-per-event", "1", "--output-dir", str(target)]
    assert main(command) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["cropCount"] == 1
    assert main(command) == 2
    assert movie.read_bytes() == original


def test_sample_selection_spreads_start_middle_end():
    proposals = [{
        "id": "p1", "startMs": 0, "endMs": 10_000,
        "provider": "ecology-owlv2", "modelKeySha256": "a",
        "label": "fish", "biologicalGroup": "fish",
        "sampledBoxEvidence": [
            {"requestedMs": i * 1000, "sourceDecodedPtsMs": i * 1000 + 20,
             "bbox": [10, 20, 30, 40], "bboxFormat": "xyxy",
             "coordinateSpace": "sampled_image_pixels",
             "uncalibratedDetectorScore": .8}
            for i in range(1, 10)
        ],
    }]
    chosen = _select_samples(proposals, max_events=1, crops_per_event=3)
    assert [s["evidence"]["requestedMs"] for s in chosen] == [1000, 5000, 9000]



def test_offline_contact_sheet_escapes_untrusted_object_label():
    from adventure_intelligence.ecology_crops import _contact_sheet
    html = _contact_sheet({"crops": [{
        "image": "crop-001.png", "label": '<script>alert(1)</script>',
        "biologicalGroup": 'fish&coral', "requestedMs": 5000,
    }]})
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "fish&amp;coral" in html
    assert 'src="crop-001.png"' in html
