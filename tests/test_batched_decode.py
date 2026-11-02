"""Batch FFmpeg safety and performance-path tests, including actual local video."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest

from adventure_intelligence import batched_decode as bd
from adventure_intelligence.frame_timing import extract_frame_png


def test_grouped_interest_windows_never_bridge_coverage_gaps():
    times = [0, 2000, 4000, 45000, 47000, 90000]
    assert list(bd.groups(times)) == [[0, 2000, 4000], [45000, 47000], [90000]]
    assert list(bd.groups([0, 1000, 2000, 3000], max_frames=2)) == [
        [0, 1000], [2000, 3000]]
    with pytest.raises(ValueError):
        list(bd.groups([2000, 1000]))
    with pytest.raises(ValueError):
        list(bd.groups([True, 1000]))


def test_png_pipe_rejects_truncated_and_untrusted_chunks():
    # Structurally sufficient IEND stream to test chunk boundaries.
    png = bd._SIGNATURE + b"\x00\x00\x00\x00IEND\xae\x42\x60\x82"
    assert bd._png_stream(png + png) == [png, png]
    for invalid in (png[:-3], b"x" + png, png + b"trailing",
                    png[:-1] + b"x",  # invalid PNG chunk CRC
                    bd._SIGNATURE + b"\xff\xff\xff\xffIDAT" + b"nope"):
        with pytest.raises(ValueError):
            bd._png_stream(invalid)


def test_decoder_falls_back_to_verified_singles(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(bd, "_decode_batch",
                        lambda *args: (_ for _ in ()).throw(ValueError("PTS drift")))
    def single(path, t, width):
        seen.append(t)
        return b"png", {"requestedMs": t, "timestampBasis": "decoded_frame_pts"}
    monkeypatch.setattr(bd, "extract_frame_png", single)
    counters = {}
    times = [1000, 2000, 3000, 45000]
    frames = list(bd.decode_scheduled(tmp_path / "never_open.mp4", times,
                                      mode="batched", counters=counters))
    assert [x[0] for x in frames] == times
    assert seen == times
    assert counters == {"batchAttempts": 1, "batchSucceeded": 0,
                        "batchFallbacks": 1, "singleSeekFrames": 4,
                        "batchedFrames": 0}


@pytest.fixture
def actual_video(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("FFmpeg is not installed")
    path = tmp_path / "original.mp4"
    subprocess.run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=160x96:rate=10",
        "-t", "4", "-c:v", "mpeg4", "-q:v", "3", str(path),
    ], check=True, capture_output=True, timeout=30)
    return path


def test_batch_matches_single_source_pts_on_real_video(actual_video):
    original = actual_video.read_bytes()
    times = [800, 1200, 1600, 2000]
    counters = {}
    frames = list(bd.decode_scheduled(actual_video, times, mode="batched",
                                      counters=counters))
    assert [t for t, _, _ in frames] == times
    assert counters["batchAttempts"] == 1
    assert counters["batchSucceeded"] == 1, "Batch failed; check FFmpeg select/PTS semantics"
    assert counters["batchedFrames"] == len(times)
    assert counters["singleSeekFrames"] == 0
    for (requested, image, timing) in frames:
        single_image, single_timing = extract_frame_png(actual_video, requested)
        assert image.startswith(bd._SIGNATURE)
        assert image == single_image
        assert timing["decodedPtsMs"] == single_timing["decodedPtsMs"]
        assert timing["timestampBasis"] == "decoded_frame_pts"
    assert actual_video.read_bytes() == original


def test_batch_decoder_preserves_source_on_failure(actual_video, monkeypatch):
    original = actual_video.read_bytes()
    monkeypatch.setattr(bd, "_decode_batch", lambda *args: (_ for _ in ()).throw(
        subprocess.TimeoutExpired("ffmpeg", 1)))
    frames = list(bd.decode_scheduled(actual_video, [700, 1200],
                                      mode="batched"))
    assert len(frames) == 2
    assert actual_video.read_bytes() == original
