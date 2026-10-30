"""Progress telemetry remains stderr-only; source media is always read-only."""
import json
import shutil
import subprocess

import pytest

from adventure_intelligence.progress import Progress
from adventure_intelligence.cli import main


def test_progress_stage_eta_and_heartbeat(capsys, tmp_path):
    log = tmp_path / "progress.log"
    p = Progress(log_file=log, interval_seconds=0)
    p.start("index", 2)
    p.bytes("hash", 1024, 2048, "clip.mp4")
    p.item("index", 1, 2, "clip.mp4 indexed")
    p.start("flow:clip.mp4", 12)
    p.frames("flow:clip.mp4", 6, 12, "clip.mp4")
    output = capsys.readouterr()
    assert not output.out
    assert "stage ETA" in output.err
    assert "6/12 decoded frame(s)" in output.err
    assert log.read_text().count("[klip]") == 5


def test_logs_cannot_target_unindexed_source(tmp_path, capsys):
    folder = tmp_path / "Ski Videos"
    folder.mkdir()
    video = folder / "DJI_original.mp4"
    video.write_bytes(b"untouched")
    workspace = tmp_path / "AdventureAI"
    assert main(["--workspace", str(workspace), "process", str(folder),
                 "--log-file", str(folder / "progress.log")]) == 2
    assert video.read_bytes() == b"untouched"
    assert not (folder / "progress.log").exists()
    assert "input media folder" in capsys.readouterr().err


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg not available")
def test_process_progress_does_not_change_source_and_stdout_is_json(tmp_path, capsys):
    folder = tmp_path / "DJI"
    folder.mkdir()
    video = folder / "DJI_0001.MP4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=7",
        "-t", "2", "-c:v", "mpeg4", str(video)
    ], check=True)
    from adventure_intelligence.ingest import sha_file
    original = sha_file(video)
    workspace = tmp_path / "AdventureAI"
    log_file = workspace / "logs" / "processing.log"
    status = main(["--workspace", str(workspace), "process", str(folder),
                   "--activity", "ski", "--detector", "flow",
                   "--log-file", str(log_file)])
    streams = capsys.readouterr()
    assert status == 0
    doc = json.loads(streams.out)
    assert doc["status"] == "completed"
    assert "[klip] index:" in streams.err
    assert "[klip] analysis:" in streams.err
    assert "decoded frame(s)" in streams.err
    assert "[klip] timeline:" in streams.err
    assert "stage ETA" in log_file.read_text()
    assert sha_file(video) == original
    assert sorted(p.name for p in folder.iterdir()) == ["DJI_0001.MP4"]


def test_quiet_progress_writes_json_only(tmp_path, monkeypatch, capsys):
    from adventure_intelligence.process import process_folder
    folder = tmp_path / "DJI"
    folder.mkdir()
    work = tmp_path / "AdventureAI"
    # Do not invoke external processing in this parsing test.
    monkeypatch.setattr(
        "adventure_intelligence.cli.process_folder",
        lambda *args, **kwargs: {
            "status": "completed",
            "progressWasDisabled": kwargs.get("progress") is None,
        }
    )
    assert main(["--workspace", str(work), "process", str(folder),
                 "--no-progress"]) == 0
    streams = capsys.readouterr()
    assert json.loads(streams.out)["progressWasDisabled"]
    assert streams.err == ""
