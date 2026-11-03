"""Synthetic qualification for local marine encounter review and exact taxonomy."""
import json

import pytest

from adventure_intelligence.catalog import connect
from adventure_intelligence.cli import main
from adventure_intelligence.ecology_encounters import build_encounter_report
from adventure_intelligence.ecology_taxonomy import load_taxonomy, reconcile_taxa
from adventure_intelligence.indexing import index_folder


@pytest.fixture
def setup(tmp_path, monkeypatch):
    source = tmp_path / "camera"
    source.mkdir()
    original = source / "DJI_VIDEO.MP4"
    original_bytes = b"DO-NOT-TOUCH-ORIGINAL" * 128
    original.write_bytes(original_bytes)
    monkeypatch.setattr("adventure_intelligence.indexing.probe",
                        lambda path: {"duration": 90., "audio": False,
                                      "capture_time": None})
    workspace = tmp_path / "workspace"
    aid = index_folder(source, workspace, activity="snorkeling")["asset_ids"][0]
    return workspace, aid, original, original_bytes


def _frame(t, label, x, *, score=.7, include_box=True):
    obj = {"label": label, "score": score,
           "coordinateSpace": "sampled_image_pixels"}
    if include_box:
        obj["bbox"] = {"xmin": x, "ymin": 3, "xmax": x+18, "ymax": 23}
        obj["bboxFormat"] = "xyxy"
    return {"tMs": t, "decodedPtsMs": 30_000 + t, "objects": [obj]}


def _snapshot(tmp_path, taxa=None):
    if taxa is None:
        taxa = [
            {"taxonId": "worms:1", "scientificName": "Synthetic fish",
             "rank": "species", "group": "fish", "status": "accepted",
             "parentTaxonId": "worms:genus"},
            {"taxonId": "worms:11", "scientificName": "Old synthetic fish name",
             "rank": "species", "group": "fish", "status": "synonym",
             "acceptedTaxonId": "worms:1"},
            {"taxonId": "worms:genus", "scientificName": "Imaginaris",
             "rank": "genus", "group": "fish", "status": "accepted"},
            {"taxonId": "worms:unknown", "scientificName": "Unresolved name",
             "rank": "species", "group": "other", "status": "unresolved"},
        ]
    p = tmp_path / "taxonomy.json"
    p.write_text(json.dumps({"schemaVersion": 1, "source": {
        "provider": "WoRMS frozen reviewed example", "retrievedAt": "2026-11-02",
        "citation": "synthetic references only", "license": "test"},
        "taxa": taxa}))
    return p


def test_taxonomy_reconciles_explicit_synonyms_without_probability_addition(tmp_path):
    source, digest, nodes = load_taxonomy(_snapshot(tmp_path))
    assert source["provider"] == "WoRMS frozen reviewed example"
    assert len(digest) == 64
    candidates = [
        {"taxonId": "worms:11", "scientificName": "Old synthetic fish name",
         "biologicalGroup": "fish", "searchPriority": .9, "supportingRecords": 17},
        {"taxonId": "worms:1", "scientificName": "Synthetic fish",
         "biologicalGroup": "fish", "searchPriority": .8, "supportingRecords": 200},
        {"taxonId": "gbif:999", "scientificName": "Unknown",
         "biologicalGroup": "other", "searchPriority": .1},
    ]
    resolved = reconcile_taxa(candidates, nodes)
    assert resolved["sourceCandidateCount"] == 3
    assert resolved["canonicalCandidateCount"] == 2
    fish = resolved["candidates"][0]
    assert fish["taxonId"] == "worms:1"
    assert fish["scientificName"] == "Synthetic fish"
    assert fish["searchPriority"] == .9
    assert fish["supportingRecords"] == 17  # No summation or inflated source effort.
    assert fish["taxonomy"]["sourceTaxonIds"] == ["worms:1", "worms:11"]
    assert fish["taxonomy"]["isVideoSpeciesIdentification"] is False
    assert resolved["candidates"][1]["taxonomy"]["status"] == "unresolved"


@pytest.mark.parametrize("change,expected", [
    (lambda a: a[1].update(acceptedTaxonId="worms:unknown"), "accepted taxon"),
    (lambda a: a[1].update(acceptedTaxonId="worms:1", group="coral"), "rank/group"),
    (lambda a: a[0].update(parentTaxonId="worms:1"), "Self-link"),
    (lambda a: a[2].update(parentTaxonId="worms:1"), "hierarchy"),
])
def test_taxonomy_fail_closed_on_bad_crosswalks(tmp_path, change, expected):
    reference = json.loads(_snapshot(tmp_path).read_text())
    change(reference["taxa"])
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(reference))
    with pytest.raises(ValueError, match=expected):
        load_taxonomy(path)


def test_no_object_caches_means_unknown_not_no_animals(setup):
    ws, aid, movie, original = setup
    result = build_encounter_report(ws, aid)
    assert result["status"] == "no_cached_broad_visual_evidence"
    assert result["candidateWindows"] == []
    assert result["audit"]["cachedProviders"] == []
    assert movie.read_bytes() == original


def test_box_tracking_clips_are_bounded_and_not_animal_counts(setup, monkeypatch):
    ws, aid, movie, original = setup
    cached = [
        _frame(5000, "fish", 10),
        _frame(7000, "fish", 11),
        _frame(9000, "fish", 12),
        _frame(45000, "fish", 10),
        _frame(47000, "fish", 11),
        _frame(89000, "sea urchin", 7),
    ]

    def fake_frames(db, workspace, asset_id, provider, **kwargs):
        assert workspace == ws and asset_id == aid
        return (cached, '{"provider":"ecology-owlv2"}') if provider == "ecology-owlv2" else ([], None)

    monkeypatch.setattr("adventure_intelligence.ecology_encounters.frames_for_asset",
                        fake_frames)
    result = build_encounter_report(ws, aid)
    clips = result["candidateWindows"]
    assert result["audit"]["validObjectBoxes"] == 6
    fish = sorted((c for c in clips if c["label"] == "fish"),
                  key=lambda c: c["startMs"])
    assert len(fish) == 2  # Separated by 36 seconds, not one long animal track.
    assert fish[0]["startMs"] == 2000 and fish[0]["endMs"] == 14000
    assert fish[0]["sampleCount"] == 3
    assert fish[0]["speciesVerified"] is False
    assert fish[0]["distinctIndividualCount"] is None
    assert fish[0]["firstSampleMs"] == 5000
    assert fish[0]["timingBasis"] == "requested_sample_timestamp"
    assert fish[1]["startMs"] == 42000
    urchin = next(c for c in clips if c["label"] == "sea urchin")
    assert urchin["endMs"] == 90000  # Bound against source duration.
    assert urchin["status"] == "single_frame_unverified_object"
    assert movie.read_bytes() == original


def test_invalid_boxes_and_provider_collision_never_create_tracks(setup, monkeypatch):
    ws, aid, _, _ = setup
    source = [
        _frame(1000, "fish", 2, include_box=False),
        {"tMs": 2000, "objects": [{
            "label": "fish", "score": float("nan"),
            "bbox": {"xmin": 2, "ymin": 0, "xmax": 20, "ymax": 20},
            "coordinateSpace": "sampled_image_pixels"}]},
        _frame(3000, "fish", 2),
    ]
    monkeypatch.setattr("adventure_intelligence.ecology_encounters.frames_for_asset",
                        lambda db, ws2, aid2, provider, **kwargs:
                        (source, '{"provider":"' + provider + '"}'))
    result = build_encounter_report(ws, aid)
    assert result["audit"]["skippedInvalidBoxes"] == 4
    assert len(result["candidateWindows"]) == 2
    assert len({x["id"] for x in result["candidateWindows"]}) == 2
    assert all(x["sampleCount"] == 1 for x in result["candidateWindows"])


def test_encounters_cli_writes_new_report_without_source_reads(setup, monkeypatch, capsys):
    ws, aid, movie, original = setup
    called = []
    monkeypatch.setattr("adventure_intelligence.ecology_encounters.frames_for_asset",
                        lambda *args, **kwargs: (called.append(1) or [], None))
    target = ws / "exports" / "encounters.json"
    args = ["--workspace", str(ws), "ecology-encounters", "--asset", aid,
            "--output", str(target), "--max-results", "4"]
    assert main(args) == 0
    stdout = json.loads(capsys.readouterr().out)
    assert stdout["windowCount"] == 0
    assert target.exists()
    assert main(args) == 2
    assert movie.read_bytes() == original
    assert len(called) >= 2


def test_explicit_absence_and_dropped_obis_do_not_produce_species_prior():
    from adventure_intelligence.ecology_import import normalize_export
    base = {
        "aphiaID": 100, "taxonRank": 220, "scientificName": "Synthetic fish",
        "datasetID": "synthetic-dataset", "decimalLatitude": 36.6,
        "decimalLongitude": -121.9,
    }
    doc = normalize_export({"results": [
        {**base, "id": "1", "absence": True},
        {**base, "id": "2", "dropped": True},
        {**base, "id": "3", "occurrenceStatus": "absent"},
        {**base, "id": "4", "occurrenceStatus": "present"},
        {**base, "id": "5", "absence": "true"},
        {**base, "id": "6", "dropped": 1},
        {**base, "id": "7", "occurrenceStatus": "doubtful"},
    ]}, "obis", retrieved_at="2026-11-02",
        citation="synthetic", license="test")
    assert doc["importStatistics"]["retainedRecords"] == 1
    assert doc["records"][0]["recordId"] == "4"


def test_duplicate_boxes_not_mistaken_for_multiple_animals(setup, monkeypatch):
    ws, aid, _, _ = setup
    first = _frame(5000, "fish", 10)
    repeated = dict(first["objects"][0])
    repeated["score"] = .6
    first["objects"].append(repeated)
    monkeypatch.setattr("adventure_intelligence.ecology_encounters.frames_for_asset",
                        lambda db, ws2, aid2, provider, **kwargs:
                        ([first, {"tMs": 10000, "objects": None}, None], "model-v1")
                        if provider == "ecology-owlv2" else ([], None))
    result = build_encounter_report(ws, aid)
    assert result["audit"]["validObjectBoxes"] == 2
    assert result["audit"]["suppressedDuplicateBoxes"] == 1
    assert len(result["candidateWindows"]) == 1
    assert result["candidateWindows"][0]["proposalBoxCount"] == 1
    assert result["candidateWindows"][0]["distinctIndividualCount"] is None


def test_rank_conflict_abstains_instead_of_promoting_species(tmp_path):
    _, _, nodes = load_taxonomy(_snapshot(tmp_path))
    items = [
        {"taxonId": "worms:11", "scientificName": "Bad source",
         "taxonRank": "family", "biologicalGroup": "fish", "searchPriority": .8},
        {"taxonId": "worms:1", "scientificName": "Synthetic fish",
         "taxonRank": "species", "biologicalGroup": "other", "searchPriority": .4},
    ]
    result = reconcile_taxa(items, nodes)
    assert result["canonicalCandidateCount"] == 2
    conflict = next(c for c in result["candidates"] if c["taxonId"] == "worms:11")
    assert conflict["taxonomy"]["status"] == "taxonomic_disagreement_requires_review"
    assert conflict["speciesIdentifiedInVideo"] is False if "speciesIdentifiedInVideo" in conflict else True
    accepted = next(c for c in result["candidates"] if c["taxonId"] == "worms:1")
    assert accepted["biologicalGroup"] == "fish"
    assert accepted["taxonomy"]["originalBiologicalGroup"] == "other"
