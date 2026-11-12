"""Synthetic time-only source neighborhood proposals never certify geography."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

from adventure_intelligence.catalog import connect
from adventure_intelligence.workspace import init
from adventure_intelligence.moment_archive_context import (
    VERSION, capture_neighborhoods,
)


def catalog_fixture(tmp_path):
    root=tmp_path/"workspace"
    init(root)
    sources=[
        ("a"*64, "2024-12-21T10:00:00Z", 60.0),
        ("b"*64, "2024-12-21T10:03:00Z", 120.0),
        ("c"*64, "2024-12-21T10:09:00Z", 90.0),
        ("d"*64, "2024-12-23T15:00:00Z", 60.0),
        ("e"*64, None, 60.0),
        ("f"*64, "2024-12-23T15:30:00", 60.0),
    ]
    with connect(root) as db:
        for aid,stamp,duration in sources:
            db.execute(
                "INSERT INTO assets (id,sha256,stored_path,size_bytes,suffix,"
                "duration,capture_time,activity,metadata_json) VALUES (?,?,?,?,?,?,?,?,?)",
                (aid,aid,"/Volumes/SID_SSD/UNMOUNTED/RAW.mp4",1234,".mp4",
                 duration,stamp,"unknown","{}"))
    return root


def test_capture_graph_only_proposes_time_links_without_trip_ids(tmp_path):
    root=catalog_fixture(tmp_path)
    before=(root/"catalog.sqlite3").stat().st_mtime_ns
    result=capture_neighborhoods(root,max_gap_hours=.25,max_neighbors=4)
    assert result["version"]==VERSION
    assert result["catalogSourceCount"]==6
    assert result["sourcesWithParseableTimezoneAwareCapture"]==4
    assert result["sourcesWithUnknownOrAmbiguousCaptureTime"]==2
    assert result["candidateRelationships"]==3
    assert len(result["links"])==3
    assert all(edge["sameOuting"]=="unknown" for edge in result["links"])
    assert all(edge["sameLocation"]=="unknown" for edge in result["links"])
    assert all(not edge["humanConfirmed"] for edge in result["links"])
    assert all(not edge["relativeVisualAppearanceCompared"] for edge in result["links"])
    assert result["verifiedOutingGroups"]==0
    assert not result["geolocationInferred"]
    assert not result["trainingGroupAssignmentsGenerated"]
    assert not result["sourceVideoRead"]
    assert not result["catalogModified"]
    assert set(result["unlinkedOrUndatedSourceSha256"]) == {"d"*64,"e"*64,"f"*64}
    assert (root/"catalog.sqlite3").stat().st_mtime_ns==before
    assert "/Volumes/" not in json.dumps(result)


def test_no_hard_place_inference_from_date_or_archive_order(tmp_path):
    root=catalog_fixture(tmp_path)
    result=capture_neighborhoods(root,max_gap_hours=12.,max_neighbors=1)
    assert result["candidateRelationships"]<=4
    for edge in result["links"]:
        assert edge["sameSkiRunOrDiveSite"]=="unknown"
        assert edge["sharedSubjects"]=="unknown"
        assert edge["capturedClockAccuracyVerified"] is False
        assert edge["evidenceType"]=="capture_metadata_temporal_proximity"
    assert result["maxNeighborsProposedPerSource"]==1


def test_bad_timestamp_and_limits_fail_closed(tmp_path):
    root=catalog_fixture(tmp_path)
    for gap in [float("nan"),0.0,999.0]:
        with pytest.raises(ValueError,match="Invalid source"):
            capture_neighborhoods(root,max_gap_hours=gap)
    with pytest.raises(ValueError,match="source limit"):
        capture_neighborhoods(root,limit=2)
    with pytest.raises(ValueError,match="Invalid source"):
        capture_neighborhoods(root,max_neighbors=0)


def test_archive_graph_cli_stdout_only(tmp_path):
    root=catalog_fixture(tmp_path)
    project=Path(__file__).resolve().parents[1]
    args=[sys.executable,str(project/"scripts/klip_moment_context.py"),
          "--workspace",str(root),"--max-gap-hours",".25"]
    result=subprocess.run(args,check=True,capture_output=True,text=True,cwd=project)
    data=json.loads(result.stdout)
    assert data["catalogSourceCount"]==6
    assert data["sourceVideoRead"] is False
    assert data["candidateRelationships"]==3
    assert not (root/"reports"/"moment-context.json").exists()
