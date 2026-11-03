"""Qualification of sparse sport-specific segment hypotheses and safe execution."""

import pytest

from adventure_intelligence.segment_intelligence import (
    PROMPTS,FAMILIES,score_segment_frame,build_segments,
    coarse_timestamps,refinement_times,overlay_events,wildlife_priority_times,html_review,
)
from adventure_intelligence.segment_workflow import scan_segments
from adventure_intelligence.activity_confirm import confirm_activity
from adventure_intelligence.catalog import connect
from adventure_intelligence.indexing import index_folder
from adventure_intelligence.cli import main


def test_prompt_states_cover_all_three_activities():
    for sport,states in PROMPTS.items():
        assert set(states)==set(FAMILIES[sport])
        assert len(states)>=5
        assert all(isinstance(p,list) and len(p)>=2 for p in states.values())
    assert "underwater_sand" in PROMPTS["snorkeling"]
    assert "kayak_boat" in PROMPTS["snorkeling"]
    assert "ski_lift" in PROMPTS["ski"]
    assert "bike_technical_terrain_candidate" in PROMPTS["bike"]


def _basis(sport):
    names=list(PROMPTS[sport])
    return {k:[[int(i==j) for i in range(len(names))]]
            for j,k in enumerate(names)}


@pytest.mark.parametrize("sport,expected",[
    ("snorkeling","underwater_sand"),
    ("ski","ski_lift"),
    ("bike","bike_technical_terrain_candidate"),
])
def test_correct_sport_state_not_inferred_skill_probability(sport,expected):
    bank=_basis(sport)
    v=bank[expected][0]
    result=score_segment_frame(v,sport,bank)
    assert result["label"]==expected
    assert result["calibratedProbability"] is None
    assert result["status"]=="unverified_scene_similarity"


def test_reef_and_sand_ambiguity_falls_back_to_underwater_family():
    states=_basis("snorkeling")
    size=len(states)
    names=list(states)
    img=[0.]*size
    img[names.index("underwater_sand")]=.7
    img[names.index("underwater_reef")]=.7
    outcome=score_segment_frame(img,"snorkeling",states)
    assert outcome["label"]=="underwater_habitat_unresolved"
    assert outcome["family"]=="underwater"
    assert outcome["resolution"]=="family_only"


def test_unknown_never_determined_by_single_frame_and_gaps_remain():
    a={"requestedMs":0,"label":"underwater_sand","family":"underwater",
       "relativeFamilyMargin":.1,"decodedPtsMs":250}
    b={**a,"requestedMs":90_000,"decodedPtsMs":90_250}
    out=build_segments([a,b],100_000,max_support_radius_ms=12000)
    assert len(out["segments"])==2
    assert out["unsampledMs"]>=76000
    assert all(s["status"]=="single_frame_navigation_hint" for s in out["segments"])
    assert out["segments"][0]["endMs"]<out["segments"][1]["startMs"]
    assert out["segments"][0]["sourcePtsEvidenceMs"]==[250]


def test_temporal_run_merges_adjacent_same_label_and_splits_transitions():
    samples=[
        {"requestedMs":t,"label":label,"family":"underwater",
         "relativeFamilyMargin":.08,"decodedPtsMs":t+100}
        for t,label in [(0,"underwater_sand"),(12000,"underwater_sand"),
                        (24000,"underwater_reef")]]
    report=build_segments(samples,37000,max_support_radius_ms=12000)
    assert [s["label"] for s in report["segments"]]==[
        "underwater_sand","underwater_reef"]
    assert report["segments"][0]["evidenceFrameCount"]==2
    assert report["segments"][0]["status"]=="provisional_multi_frame"
    assert report["segments"][1]["evidenceFrameCount"]==1
    assert report["segments"][0]["endMs"]<=report["segments"][1]["startMs"]


def test_bounded_adaptive_refinement_chases_uncertainty_only():
    obs=[
        {"requestedMs":0,"label":"underwater_sand","family":"underwater",
         "relativeFamilyMargin":.05},
        {"requestedMs":16000,"label":"unknown","family":"unknown",
         "relativeFamilyMargin":.001},
        {"requestedMs":32000,"label":"underwater_reef","family":"underwater",
         "relativeFamilyMargin":.07}]
    assert refinement_times(obs,budget=0)==[]
    followup=refinement_times(obs,budget=2)
    assert followup==[8000,24000]
    assert coarse_timestamps(240000,step_ms=15000,max_frames=18,
                             refine_budget=6)==sorted(set(
        coarse_timestamps(240000,step_ms=15000,max_frames=18,
                           refine_budget=6)))
    with pytest.raises(ValueError):
        coarse_timestamps(240000,step_ms=15000,max_frames=5,refine_budget=0)


def test_wildlife_overlay_is_independent_dedup_and_not_species_identity():
    segments=[{
        "startMs":0,"endMs":10000,"label":"underwater_sand",
        "family":"underwater", "segmentId":"seg-0001"}]
    objects=[{"label":"fish","score":.6}]
    overlay_events(segments,{
        "ecology-owlv2":[{"tMs":4500,"objects":objects}],
        "owlv2":[{"tMs":4500,"objects":objects},
                 {"tMs":8500,"objects":[{"label":"fish","score":.7}]}]})
    evidence=segments[0]["independentEventHints"]
    assert len(evidence)==1
    assert evidence[0]["sampleTimesMs"]==[4500,8500]
    assert evidence[0]["notSpeciesEvidence"] is True
    assert evidence[0]["notAnimalCount"] is True


@pytest.fixture
def source(tmp_path,monkeypatch):
    footage=tmp_path/"camera"
    footage.mkdir()
    video=footage/"POV_JOURNEY.MP4"
    content=b"immutable-source" * 20000
    video.write_bytes(content)
    monkeypatch.setattr("adventure_intelligence.indexing.probe",
                        lambda p:{"duration":60.,"audio":False,
                                  "capture_time":None})
    workspace=tmp_path/"workspace"
    aid=index_folder(footage,workspace,activity="unknown")["asset_ids"][0]
    return workspace,aid,video,content


class FakeModel:
    def __init__(self, *, allow_model_download=False):
        self.revision="fake-model-commit"
        self.processor_hash="fake-processor-sha"
        self.text_vectors={s:_basis(s) for s in PROMPTS}

    def image_vectors(self,images):
        dims=len(PROMPTS["snorkeling"])
        # Frame content determines an entirely synthetic scene; no external model.
        vectors=[]
        for img in images:
            name="underwater_sand" if b"sand" in img else "underwater_reef"
            i=list(PROMPTS["snorkeling"]).index(name)
            vectors.append([float(j==i) for j in range(dims)])
        return vectors


def _png():
    from PIL import Image
    image=Image.new("RGB",(384,216),(215,208,180))
    out=BytesIO()
    image.save(out,format="PNG")
    return out.getvalue()


def test_end_to_end_segments_preserve_source_and_catalog(source,monkeypatch,capsys):
    ws,aid,video,original=source
    monkeypatch.setattr("adventure_intelligence.segment_workflow.SegmentModel",
                        FakeModel)
    png=_png()
    def decode(path,requested,width=384):
        assert path==video
        assert width==384
        return png,{"timestampBasis":"decoded_frame_pts",
                    "decodedPtsMs":requested+230}
    monkeypatch.setattr("adventure_intelligence.segment_workflow.extract_frame_png",
                        decode)
    report_dir=ws/"reports"/"segments-synthetic"
    assert main(["--workspace",str(ws),"segments","--asset",aid,
                 "--activity","snorkeling","--output-dir",str(report_dir),
                 "--max-frames","12","--refine-frames","3",
                 "--step-seconds","10"])==0
    resp=json.loads(capsys.readouterr().out)
    assert resp["sampledFrames"]>0
    assert resp["activityHypothesisOnly"]
    report=json.loads((report_dir/"report.json").read_text())
    assert report["sourceVerifiedBeforeRun"] is True
    assert report["sourceStatUnchangedAfterRun"] is True
    assert all(x["decodedPtsMs"]==x["requestedMs"]+230
               for x in report["samples"])
    assert report["budget"]["actualSuccessful"]<=12
    assert report["coverage"]["coveredFraction"]<=1
    assert (report_dir/"review.html").is_file()
    assert all((report_dir/f["image"]).is_file() for f in report["previewFrames"])
    assert video.read_bytes()==original
    with connect(ws) as db:
        assert db.execute("SELECT activity FROM assets WHERE id=?",(aid,)).fetchone()[0]=="unknown"
    cached=scan_segments(ws,aid,report_dir,activity="snorkeling",
                         max_frames=12,refine_frames=3,step_seconds=10)
    assert cached["cached"] is True


def test_preexisting_activity_requires_match_and_manual_confirmation(source,monkeypatch):
    ws,aid,video,original=source
    confirm_activity(ws,aid,"snorkeling",reviewed=True,apply=True)
    with pytest.raises(ValueError,match="conflicts"):
        scan_segments(ws,aid,ws/"reports"/"wrong",activity="ski")
    with pytest.raises(ValueError,match="new|incompatible|Positive|Invalid"):
        scan_segments(ws,aid,ws/"reports"/"bad",max_frames=3)
    assert video.read_bytes()==original


def test_no_valid_source_pts_abstains_without_report(source,monkeypatch):
    ws,aid,video,original=source
    monkeypatch.setattr("adventure_intelligence.segment_workflow.SegmentModel",
                        FakeModel)
    monkeypatch.setattr("adventure_intelligence.segment_workflow.extract_frame_png",
                        lambda path,t,width=384:(_png(),{
                            "timestampBasis":"requested_seek_pts_unavailable",
                            "decodedPtsMs":None}))
    dest=ws/"reports"/"no-pts"
    with pytest.raises(RuntimeError,match="No PTS-backed"):
        scan_segments(ws,aid,dest,activity="snorkeling",max_frames=8,
                      refine_frames=2)
    assert not dest.exists()
    assert video.read_bytes()==original


def test_html_escapes_all_untrusted_label_text():
    fake={"activity":'<script>alert(9)</script>',"durationMs":10000,
          "coverage":{"coveredFraction":.7,"sampleCount":2},
          "segments":[{
              "startMs":0,"endMs":2000,"label":'<img src=x onerror=alert(1)>',
              "family":"underwater","status":"single_frame_navigation_hint",
              "evidenceFrameCount":1,"independentEventHints":[]}],
          "previewFrames":[{"image":"thumbnails/a.jpg","requestedMs":0,
                            "label":"underwater_sand"}]}
    rendered=html_review(fake)
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "<img src=x onerror" not in rendered



def test_cached_marine_priority_times_include_approach_and_aftermath_only():
    cached={
        "ecology-owlv2":[
            {"tMs":15000,"objects":[{"label":"ray","score":.92}]},
            {"tMs":15000,"objects":[{"label":"ray","score":.7}]},
            {"tMs":34000,"objects":[{"label":"kelp","score":.3}]},
        ],
        "owlv2":[{"tMs":15000,"objects":[{"label":"ray","score":.86}]}],
    }
    assert wildlife_priority_times(cached,60000,limit=3)==[13000,15000,17000]
    assert wildlife_priority_times(cached,60000,limit=0)==[]
    assert len(wildlife_priority_times(cached,60000,limit=6))<=6
    # A blue object proposal or invalid score cannot become wildlife evidence.
    assert wildlife_priority_times({
        "owlv2":[{"tMs":1000,"objects":[{"label":"blue water","score":.8},
                                         {"label":"fish","score":float("nan")}]}]
    },60000,limit=6)==[]
