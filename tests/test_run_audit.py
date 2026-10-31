"""Read-only audit of large Klip progress logs, including partial runs."""
from __future__ import annotations

import json
import pytest

from adventure_intelligence.run_audit import audit_log, _seconds
from adventure_intelligence.cli import main


def sample_run():
    return "\n".join([
        "[klip] pipeline: source /Volumes/Sid_DJI_SSD; workspace /Users/someone/private/AdventureAI",
        "[klip] index: starting 315 item(s)",
        "[klip] index-hash: DJI_0090.MP4: hashed 477.4/477.4 MiB (100%)",
        "[klip] index: 152/315 (48%) DJI_0090.MP4 indexed | elapsed 3h 41m | stage ETA 3h 57m",
        "[klip] index: 315/315 (100%) DJI_0092.MP4 unchanged | elapsed 7h 09m | stage ETA 0s",
        "[klip] analysis: starting 315 item(s)",
        "[klip] baseline:DJI_0092.MP4: starting 71 item(s)",
        "[klip] baseline:DJI_0092.MP4: DJI_0092.MP4: 1/72 decoded frame(s) | elapsed 2s | stage ETA 2m 30s",
        "[klip] source-check: DJI_0092.MP4: hashed 477.4/477.4 MiB (100%)",
        "[klip] baseline:DJI_0092.MP4: DJI_0092.MP4: 57/72 decoded frame(s) | elapsed 58s | stage ETA 15s",
    ])+"\n"


def test_durations_and_insufficient_events():
    assert _seconds("2h 04m 02s")==7442
    assert _seconds("3h 41m")==13260
    assert _seconds("1m 15s")==75
    assert _seconds("0s")==0
    assert _seconds("calculating") is None


def test_exposes_stage_progress_without_inventing_global_eta(tmp_path):
    log=tmp_path/"run.log"
    log.write_text(sample_run())
    doc=audit_log(log)
    assert doc["runsSeen"]==1
    assert doc["stageProgress"]["index"]["observedDone"]==315
    assert doc["stageProgress"]["analysis"]["observedDone"]==0
    current=doc["latestDecode"]
    assert current["file"]=="DJI_0092.MP4"
    assert current["frames"]==57 and current["expectedFrames"]==72
    assert current["elapsedSeconds"]==58 and current["stageEtaSeconds"]==15
    assert current["outputFramesPerSecond"]==round(57/58,3)
    assert current["observedComplete"] is False
    assert "baseline:DJI_0092.MP4" in doc["progressCountMismatches"]
    assert doc["sourceVerification"]["distinctStageFilePairs"]==2
    assert doc["decodeVideosWithFinalFrameObserved"]==0
    assert doc["medianOutputFramesPerSecondOfObservedCompletions"] is None
    assert "global ETA" in json.dumps(doc["limitations"])
    assert "/Volumes/" not in json.dumps(doc)
    assert "/Users/" not in json.dumps(doc)


def test_multiple_runs_only_use_last_run_metrics(tmp_path):
    log=tmp_path/"logs.txt"
    log.write_text(sample_run()+sample_run().replace("57/72", "72/72").replace(
        "58s | stage ETA 15s", "75s | stage ETA 0s"))
    out=audit_log(log)
    assert out["runsSeen"]==2
    assert out["stageProgress"]["index"]["observedDone"]==315
    assert out["decodeVideosObserved"]==1
    assert out["decodeVideosWithFinalFrameObserved"]==1
    assert out["latestDecode"]["frames"]==72
    assert out["medianOutputFramesPerSecondOfObservedCompletions"]==round(72/75,3)


def test_partial_log_with_missing_completion_does_not_report_success(tmp_path):
    log=tmp_path/"partial.log"
    log.write_text("[klip] analysis: starting 4 item(s)\n"+
                   "[klip] baseline:sample.mp4: sample.mp4: 3/10 decoded frame(s)"
                   " | elapsed 4s | stage ETA 9s\n")
    out=audit_log(log)
    assert out["runsSeen"]==1
    assert out["latestDecode"]["frames"]==3
    assert out["decodeVideosWithFinalFrameObserved"]==0
    assert out["lastEvent"]["kind"]=="decode"


def test_audit_cli_is_readonly_and_does_not_initialize_workspace(tmp_path,capsys):
    log=tmp_path/"active.log";log.write_text(sample_run())
    workspace=tmp_path/"never-create-me"
    code=main(["--workspace",str(workspace),"audit-log",str(log)])
    assert code==0
    out=json.loads(capsys.readouterr().out)
    assert out["version"]=="run-log-audit-v1"
    assert out["latestDecode"]["frames"]==57
    assert not workspace.exists()


def test_missing_log_is_reported_without_side_effects(tmp_path):
    with pytest.raises(ValueError, match="Log file not found"):
        audit_log(tmp_path/"not-there.log")


def test_log_rejects_malformed_frame_counts(tmp_path):
    log=tmp_path/"broken.log"
    log.write_text("[klip] baseline:a.mp4: a.mp4: 12/10 decoded frame(s) |"
                   " elapsed 10s | stage ETA 0s\n")
    doc=audit_log(log)
    assert doc["decodeVideosObserved"]==0
    assert doc["latestDecode"] is None
