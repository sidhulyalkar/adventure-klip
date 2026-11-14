"""Synthetic Klip V2 CLI and FFmpeg boundary regression tests."""
from __future__ import annotations

import argparse
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "klip_build_collections_v2.py"
spec = importlib.util.spec_from_file_location("klip_build_collections_v2", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def _fixture(tmp_path, monkeypatch, *, complete=True):
    root = tmp_path / "workspace"
    campaign = root / "reports" / "campaign"
    packet = campaign / "review-packet"
    media = packet / "media"
    media.mkdir(parents=True)
    rid, source = "1"*24, "a"*64
    proxy = media / (rid + ".mp4")
    proxy.write_bytes(b"synthetic-proxy-bytes" * 20)
    checksum = sha256(proxy.read_bytes()).hexdigest()
    rows = [{
        "recordId": rid, "sourceAssetSha256": source,
        "sourceStartMs": 10000, "sourceEndMs": 30000,
        "sport": "ski", "verifiedProxy": True,
        "proxySha256": checksum, "selectionRole": "uniform_timeline_control",
        "humanPreference": None, "humanRatings": None,
        "humanCurationV2": None, "mediaPath": str(proxy),
    }]
    status = {
        "goNoGo": {"coarseScannedEveryCatalogSource": complete},
        "reviewPacket": {"matchesCurrentScanCheckpoint": True,
                         "verifiedPlaybackClipsReported": 1},
        "catalogAssetCount": 1,
    }
    monkeypatch.setattr(module, "campaign_progress", lambda *a, **k: status)
    monkeypatch.setattr(
        module, "_packet_candidates",
        lambda *a, **k: (rows, {"invalidProxyReceipts": 0,
                                "manifestSha256": "b"*64}),
    )
    args = argparse.Namespace(workspace=root, campaign_dir=campaign,
                              packet_dir=packet, output_dir=root/"reports"/"v2-output",
                              reviews=None, annotations=None, mode="exploratory",
                              seconds=100, max_clips=4, per_source=2, render=False)
    return args, proxy, rid


def test_end_to_end_no_source_media_or_network_read(tmp_path, monkeypatch):
    args, proxy, rid = _fixture(tmp_path, monkeypatch)
    original = proxy.read_bytes()
    result = module.run(args)
    out = args.output_dir
    assert result["render"] == "not_requested"
    assert result["originalMediaRead"] is False
    assert Path(result["ownerLabelStudio"]).is_file()
    assert Path(result["reviewPage"]).is_file()
    assert (out/"media"/(rid+".mp4")).read_bytes() == original
    assert proxy.read_bytes() == original
    assert json.loads((out/"moment-plan.json").read_text())["momentCount"] == 1
    assert json.loads((out/"annotation-template.json").read_text())["manifestSha256"] == "b"*64
    assert "ski_park" in (out/"label.html").read_text()
    assert "connect-src 'none'" in (out/"label.html").read_text()


def test_incomplete_campaign_fails_before_creating_output(tmp_path, monkeypatch):
    args, _, _ = _fixture(tmp_path, monkeypatch, complete=False)
    with pytest.raises(ValueError, match="fully SHA-qualified"):
        module.run(args)
    assert not args.output_dir.exists()


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                    reason="FFmpeg/ffprobe not installed")
def test_real_proxy_trim_is_reversible_and_readable(tmp_path):
    source = tmp_path/"original-synthetic-proxy.mp4"
    target = tmp_path/"trimmed.mp4"
    ffmpeg = shutil.which("ffmpeg")
    p = subprocess.run([
        ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=30:d=4",
        "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
    ], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=45)
    assert p.returncode == 0, p.stderr.decode(errors="replace")
    old = sha256(source.read_bytes()).hexdigest()
    result = module._render_one(ffmpeg, source, target, {
        "relativeStartMs": 700, "durationMs": 2000,
    })
    assert target.is_file()
    assert result["sha256"] == sha256(target.read_bytes()).hexdigest()
    assert result["nativePtsVerified"] is False
    assert sha256(source.read_bytes()).hexdigest() == old
