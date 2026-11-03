"""Ecological search context: taxonomy uncertainty, provenance, privacy and no sightings."""
import json

import pytest

from adventure_intelligence.catalog import connect
from adventure_intelligence.cli import main
from adventure_intelligence.ecology_context import (
    build_ecology_context, validate_site, validate_snapshot,
)
from adventure_intelligence.indexing import index_folder


@pytest.fixture
def site_and_snapshot(tmp_path, monkeypatch):
    camera = tmp_path / "camera"
    camera.mkdir()
    footage = camera / "DJI_001.MP4"
    raw = b"unmodified-original-video" * 200
    footage.write_bytes(raw)
    monkeypatch.setattr("adventure_intelligence.indexing.probe",
                        lambda _: {"duration": 120, "audio": False, "capture_time": None})
    workspace = tmp_path / "workspace"
    asset_id = index_folder(camera, workspace, activity="snorkeling")["asset_ids"][0]
    site = {"schemaVersion": 1, "latitude": 36.60, "longitude": -121.90,
            "horizontalUncertaintyKm": 1.0, "month": 6,
            "habitats": ["kelp_forest"], "depthMeters": 7}
    source = {"provider": "fixture", "retrievedAt": "2026-11-02",
              "citation": "Synthetic test records, no natural history claims",
              "license": "test"}
    records = [
        {"recordId": "a1", "taxonId": "worms:100", "scientificName": "Example kelp",
         "rank": "species", "group": "macroalgae", "latitude": 36.601,
         "longitude": -121.901, "datasetId": "survey-1", "month": 6,
         "habitats": ["kelp_forest"], "depthMeters": 5,
         "surveyId": "site-a"},
        {"recordId": "a2", "taxonId": "worms:101", "scientificName": "Example fish",
         "rank": "species", "group": "fish", "latitude": 36.601,
         "longitude": -121.900, "datasetId": "survey-1", "month": 6,
         "habitats": ["kelp_forest"], "depthMeters": 8, "surveyId": "site-a"},
        {"recordId": "a3", "taxonId": "worms:101", "scientificName": "Example fish",
         "rank": "species", "group": "fish", "latitude": 36.602,
         "longitude": -121.902, "datasetId": "survey-2", "month": 7,
         "habitats": ["kelp_forest"]},
        {"recordId": "a4", "taxonId": "worms:102", "scientificName": "Example coral",
         "rank": "species", "group": "coral", "latitude": 36.601,
         "longitude": -121.900, "datasetId": "survey-3", "month": 12,
         "habitats": ["tropical_reef"], "depthMeters": 150},
        {"recordId": "a5", "taxonId": "worms:103", "scientificName": "Distant example",
         "rank": "species", "group": "marine_mammal", "latitude": 20.,
         "longitude": -155., "datasetId": "survey-4", "month": 6},
        {"recordId": "a6", "taxonId": "worms:104", "scientificName": "Very uncertain",
         "rank": "genus", "group": "fish", "latitude": 36.601,
         "longitude": -121.900, "datasetId": "survey-5", "month": 6,
         "coordinateUncertaintyMeters": 50_000},
    ]
    site_path = tmp_path / "site.json"
    snapshot_path = tmp_path / "occurrences.json"
    site_path.write_text(json.dumps(site))
    snapshot_path.write_text(json.dumps(
        {"schemaVersion": 1, "source": source, "records": records}))
    return workspace, asset_id, footage, raw, site, site_path, snapshot_path


def test_geo_shortlist_separates_video_evidence_and_respects_privacy(site_and_snapshot):
    workspace, aid, footage, raw, _, site_path, snapshot_path = site_and_snapshot
    result = build_ecology_context(workspace, aid, site_path, snapshot_path, top=20)
    taxa = {c["taxonId"]: c for c in result["candidates"]}
    assert set(taxa) == {"worms:100", "worms:101", "worms:102"}
    assert result["status"] == "ecological_shortlist_only"
    assert taxa["worms:101"]["independentDatasets"] == 2
    assert taxa["worms:102"]["ecologicalPriority"] < taxa["worms:100"]["ecologicalPriority"]
    assert all(c["videoConfirmation"] == "not_evaluated" for c in taxa.values())
    assert all(c["speciesIdentifiedInVideo"] is False for c in taxa.values())
    assert result["context"]["preciseCoordinatesOmitted"]
    assert "36.6" not in json.dumps(result)
    assert "-121.9" not in json.dumps(result)
    assert result["neighborAssociations"] == [{
        "taxonIds": ["worms:100", "worms:101"], "sharedExternalSurveyCount": 1,
        "status": "historical_same_survey_not_video_cooccurrence"}]
    assert footage.read_bytes() == raw


def test_broad_owl_proposal_does_not_create_species_identification(
        site_and_snapshot, monkeypatch):
    workspace, aid, _, _, _, site_path, snapshot_path = site_and_snapshot
    monkeypatch.setattr("adventure_intelligence.scene_atlas.frames_for_asset",
                        lambda *a, **kw: ([{
                            "tMs": 42_000, "objects": [
                                {"label": "fish", "score": .72},
                                {"label": "kelp", "score": .6},
                                {"label": "fish", "score": float("nan")},
                            ]}], "key"))
    out = build_ecology_context(workspace, aid, site_path, snapshot_path)
    assert set(out["broadVisualProposals"]) == {"fish", "macroalgae"}
    assert out["broadVisualProposals"]["fish"]["sampleTimesMs"] == [42_000]
    fish = next(c for c in out["candidates"] if c["biologicalGroup"] == "fish")
    assert fish["broadVisualProposal"]["status"] == "unverified_broad_object_query"
    assert fish["videoConfirmation"] == "not_evaluated"
    assert fish["needsVisualSpeciesValidation"] is True


def test_geo_uncertainty_abstains_instead_of_widening_implicitly(site_and_snapshot):
    workspace, aid, _, _, site, site_path, snapshot_path = site_and_snapshot
    site["horizontalUncertaintyKm"] = 75
    site_path.write_text(json.dumps(site))
    result = build_ecology_context(workspace, aid, site_path, snapshot_path)
    assert result["status"] == "abstained_site_uncertainty_exceeds_search_radius"
    assert result["candidates"] == []


def test_taxonomy_duplicate_and_coordinate_validation(site_and_snapshot):
    _, _, _, _, site, _, snapshot_path = site_and_snapshot
    with pytest.raises(ValueError, match="latitude"):
        validate_site({**site, "latitude": 900})
    with pytest.raises(ValueError, match="month"):
        validate_site({**site, "month": True})
    doc = json.loads(snapshot_path.read_text())
    doc["records"].append({**doc["records"][0], "scientificName": "Different species"})
    with pytest.raises(ValueError, match="Conflicting duplicated"):
        validate_snapshot(doc)
    doc["records"][-1]["recordId"] = "another"
    with pytest.raises(ValueError, match="Conflicting taxon"):
        validate_snapshot(doc)


def test_cli_new_local_report_and_no_reuse_or_source_overwrite(
        site_and_snapshot, capsys):
    workspace, aid, footage, raw, _, site_path, snapshot_path = site_and_snapshot
    target = workspace / "reports" / "ecology-pilot.json"
    args = ["--workspace", str(workspace), "ecology-context",
            "--asset", aid, "--site", str(site_path),
            "--snapshot", str(snapshot_path), "--output", str(target)]
    assert main(args) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["candidateCount"] == 3
    assert "latitude" not in printed
    assert target.is_file()
    assert main(args) == 2
    assert footage.read_bytes() == raw
    with connect(workspace) as db:
        assert db.execute("SELECT id FROM assets WHERE id=?", (aid,)).fetchone()


def test_missing_occurrences_is_unknown_not_false_negative(site_and_snapshot):
    workspace, aid, _, _, _, site_path, snapshot_path = site_and_snapshot
    doc = json.loads(snapshot_path.read_text())
    doc["records"] = []
    snapshot_path.write_text(json.dumps(doc))
    result = build_ecology_context(workspace, aid, site_path, snapshot_path)
    assert result["status"] == "no_nearby_snapshot_records"
    assert result["candidates"] == []
    assert "unknown" in result["limitations"][-1]


def test_cached_semantic_text_retrieval_is_not_species_confidence():
    from adventure_intelligence.ecology_embeddings import (
        prompt_for, score_cached_vectors,
    )
    candidates = [
        {"taxonId": "worms:101", "scientificName": "Example fish", "biologicalGroup": "fish"},
        {"taxonId": "worms:100", "scientificName": "Example kelp", "biologicalGroup": "macroalgae"},
    ]
    images = [
        {"tMs": 10_000, "embedding": [1.0, 0.0]},
        {"tMs": 20_000, "embedding": [0.0, 1.0]},
    ]
    assert "marine macroalgae" in prompt_for(candidates[1])
    result = score_cached_vectors(candidates, images, [[1., 0.], [0., 1.]],
                                  model_key="test-model")
    fish = result["worms:101"]
    assert fish["score"] == 1.
    assert fish["topRequestedOrDecodedTimesMs"][0] == 10_000
    assert fish["isConfirmedSpeciesSighting"] is False
    assert fish["status"] == "uncalibrated_candidate_retrieval"
    with pytest.raises(ValueError, match="dimensions"):
        score_cached_vectors(candidates[:1], images, [[1., 0., 0.]],
                             model_key="test-model")


def test_opt_in_embeddings_abstain_without_cached_frame_features(site_and_snapshot):
    workspace, aid, footage, raw, _, site_path, snapshot_path = site_and_snapshot
    result = build_ecology_context(workspace, aid, site_path, snapshot_path,
                                   with_cached_siglip2=True)
    assert result["embeddingRetrieval"]["status"] == "unavailable_no_cached_siglip2_frames"
    assert all("visualRetrieval" not in c for c in result["candidates"])
    assert footage.read_bytes() == raw


def test_offline_gbif_normalization_group_map_and_replay(site_and_snapshot, tmp_path):
    from adventure_intelligence.ecology_import import normalize_export, import_export
    raw = {"results": [
        {"gbifID": "10001", "acceptedTaxonKey": 99, "acceptedScientificName": "Synthetic kelp",
         "taxonRank": "SPECIES", "datasetKey": "study-A",
         "decimalLatitude": 36.6, "decimalLongitude": -121.9,
         "eventDate": "2026-06-08", "coordinateUncertaintyInMeters": 50},
        {"gbifID": "10002", "taxonKey": 11, "scientificName": "No coordinates",
         "taxonRank": "SPECIES", "datasetKey": "study-A"},
    ]}
    doc = normalize_export(raw, "gbif", retrieved_at="2026-11-02",
                           citation="Synthetic GBIF fixture",
                           license="CC0", group_map={"gbif:99": "macroalgae"})
    assert doc["importStatistics"]["retainedRecords"] == 1
    assert doc["importStatistics"]["skippedRecords"] == 1
    assert doc["records"][0]["taxonId"] == "gbif:99"
    assert doc["records"][0]["month"] == 6
    assert doc["records"][0]["group"] == "macroalgae"
    raw_path = tmp_path / "source.json"
    raw_path.write_text(json.dumps(raw))
    out_path = tmp_path / "normalized.json"
    saved = import_export(raw_path, "gbif", out_path,
                          retrieved_at="2026-11-02",
                          citation="Synthetic GBIF fixture", license="CC0")
    assert saved["retainedRecords"] == 1
    assert json.loads(out_path.read_text())["records"][0]["group"] == "other"
    with pytest.raises(FileExistsError):
        import_export(raw_path, "gbif", out_path,
                      retrieved_at="2026-11-02",
                      citation="Synthetic GBIF fixture", license="CC0")


def test_offline_obis_normalization_does_not_invent_names_or_ranks():
    from adventure_intelligence.ecology_import import normalize_export
    raw = {"results": [
        {"id": "obis-1", "aphiaID": 500, "scientificName": "Synthetic reef fish",
         "taxonRank": 220, "datasetID": "survey-7",
         "decimalLatitude": 36.6, "decimalLongitude": -121.9,
         "eventDate": "2025-05-01", "eventID": "survey-evt-1"},
        {"id": "obis-2", "aphiaID": None, "scientificName": "Unknown",
         "taxonRank": None, "datasetID": "survey-7",
         "decimalLatitude": 36.6, "decimalLongitude": -121.9},
    ]}
    doc = normalize_export(raw, "obis", retrieved_at="2026-11-02",
                           citation="Synthetic OBIS export", license="CC-BY")
    assert doc["importStatistics"]["retainedRecords"] == 1
    assert doc["records"][0]["taxonId"] == "worms:500"
    assert doc["records"][0]["surveyId"] == "survey-evt-1"
    assert doc["records"][0]["group"] == "other"


def test_ecology_object_queries_are_explicit_and_do_not_change_default():
    from adventure_intelligence.semantic import ECOLOGY_QUERIES, DEFAULT_QUERIES
    assert "kelp" in ECOLOGY_QUERIES and "seagrass" in ECOLOGY_QUERIES
    assert "sea urchin" in ECOLOGY_QUERIES and "nudibranch" in ECOLOGY_QUERIES
    assert "kelp" not in DEFAULT_QUERIES  # Opt-in avoids silent model-cost change.
    assert len(ECOLOGY_QUERIES) <= 24
    assert len(ECOLOGY_QUERIES) == len(set(ECOLOGY_QUERIES))


def test_visual_groups_collect_both_object_providers_without_species_claim(
        site_and_snapshot, monkeypatch):
    workspace, aid, _, _, _, site_path, snapshot_path = site_and_snapshot

    def fake_source(db, workspace, aid, provider="siglip2"):
        if provider == "owlv2":
            return [{"tMs": 1000, "objects": [{"label": "fish", "score": .3}]}], "old"
        if provider == "ecology-owlv2":
            return [{"tMs": 2000, "objects": [
                {"label": "sea urchin", "score": .4},
                {"label": "kelp", "score": .5},
                {"label": "fish", "score": .7}]}], "new"
        return [], None

    monkeypatch.setattr("adventure_intelligence.scene_atlas.frames_for_asset", fake_source)
    result = build_ecology_context(workspace, aid, site_path, snapshot_path)
    proposals = result["broadVisualProposals"]
    assert proposals["fish"]["sampleCount"] == 2
    assert proposals["fish"]["providers"] == ["ecology-owlv2", "owlv2"]
    assert proposals["macroalgae"]["labels"] == ["kelp"]
    assert proposals["invertebrate"]["labels"] == ["sea urchin"]
    assert all(c["speciesIdentifiedInVideo"] is False for c in result["candidates"])
