"""The owner review app over real HTTP: roster edits, thumbnails, grid answers, clips."""
from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from adventure_intelligence import catalog, outing_roster as roster
from adventure_intelligence import value_pairs as pairs_mod
from adventure_intelligence import value_review_server as app

from test_value_learning import write_experiment


@pytest.fixture
def running(tmp_path, monkeypatch):
    made = write_experiment(tmp_path / "ws" / "reports" / "discovery-001", outings=3)
    footage = tmp_path / "ssd"
    footage.mkdir()
    with catalog.connect(tmp_path / "ws") as db:
        for i, sha in enumerate(made["shas"]):
            outing = int(made["mapping"][sha].split("-")[1])
            (footage / f"{i}.mp4").write_bytes(b"video")
            db.execute("INSERT INTO assets (id,sha256,stored_path,size_bytes,suffix,duration,"
                       "capture_time,activity) VALUES (?,?,?,?,?,?,?,?)",
                       (sha, sha, str(footage / f"{i}.mp4"), 5, ".mp4", 200.0,
                        f"2026-01-{10 + outing:02d}T09:{i:02d}:00", "unknown"))
    ledgers = tmp_path / "ws" / "reports" / "owner-ledgers"
    ledgers.mkdir()
    (ledgers / "roster.json").write_text(json.dumps(
        roster.propose(roster.catalog_rows(tmp_path / "ws"))))
    for p in (tmp_path / "ws" / "reports" / "discovery-001").glob("*/proxies/*.mp4"):
        p.write_bytes(bytes(range(256)) * 4)
    calls = []

    def fake_frame(path, ms, width):
        calls.append(path)
        return b"\x89PNG fake", {}

    from adventure_intelligence import frame_timing
    monkeypatch.setattr(frame_timing, "extract_frame_png", fake_frame)
    state = app.ReviewState(tmp_path / "ws", ledgers / "roster.json", ledgers / "owner-pairs.jsonl",
                            [tmp_path / "ws" / "reports" / "discovery-001"], footage)
    token = "t" * 40
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.make_handler(state, token))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def call(path, body=None, token_ok=True, headers=None):
        h = {"X-Klip-Token": token if token_ok else "nope", **(headers or {})}
        data = None
        if body is not None:
            h["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        with urlopen(Request(base + path, data=data, headers=h)) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if r.headers["Content-Type"].startswith(
                "application/json") else raw), r.headers

    yield {"call": call, "base": base, "made": made, "ledgers": ledgers, "calls": calls,
           "token": token}
    server.shutdown()


def test_page_needs_no_token_but_data_does(running):
    status, body, headers = running["call"]("/", token_ok=False)
    assert status == 200 and b"value_review" not in body and b"review.js" in body
    assert "script-src 'self'" in headers["Content-Security-Policy"]
    with pytest.raises(HTTPError) as err:
        running["call"]("/api/roster", token_ok=False)
    assert err.value.code == 403


def _trips(view, activity="ski"):
    return [{"trip": t["trip"], "place": t["place"], "outings": [
        {"outing": o["outing"], "sources": [{"sha": r["sha"], "activity": activity}
                                             for r in o["recordings"]]}
        for o in t["outings"]]} for t in view["trips"]]


def test_roster_view_groups_days_into_trips(running):
    _, view, _ = running["call"]("/api/roster")
    # Fixture days are Jan 10, 11, 12: one trip, three days, nothing labelled yet.
    assert len(view["trips"]) == 1 and len(view["trips"][0]["outings"]) == 3
    assert {r["activity"] for o in view["trips"][0]["outings"] for r in o["recordings"]} == {"unknown"}
    assert "accidental" in view["activities"]


def test_roster_edits_keep_every_recording_and_back_up(running):
    call = running["call"]
    _, view, _ = call("/api/roster")
    trips = _trips(view)
    days = trips[0]["outings"]
    merged = {**trips[0], "place": "Moorea",
              "outings": [{**days[0], "sources": days[0]["sources"] + days[1]["sources"]}, days[2]]}
    with pytest.raises(HTTPError) as err:
        call("/api/roster", {"status": "owner_confirmed",
                             "trips": [{**merged, "outings": merged["outings"][:1]}]})
    assert err.value.code == 400  # a recording would be lost
    merged["outings"][1]["sources"][0]["activity"] = "accidental"
    _, saved, _ = call("/api/roster", {"status": "owner_confirmed", "trips": [merged]})
    assert saved["trips"] == 1 and saved["outings"] == 2 and saved["unlabelled"] == 0
    doc = roster.validate(json.loads((running["ledgers"] / "roster.json").read_text()))
    assert doc["status"] == "owner_confirmed" and {o["place"] for o in doc["outings"]} == {"Moorea"}
    assert doc["outings"][0]["sources"][0]["captureTime"]  # catalog details preserved
    assert len(roster.excluded_sources(doc)) == 1
    assert list(running["ledgers"].glob("roster.json.bak-*"))
    with pytest.raises(HTTPError):
        bad = json.loads(json.dumps(merged))
        bad["outings"][0]["sources"][0]["activity"] = "parasailing"
        call("/api/roster", {"status": "provisional", "trips": [bad]})


def test_thumbnail_is_read_once_and_cached(running):
    sha = running["made"]["shas"][0]
    for _ in range(2):
        status, body, headers = running["call"](f"/thumb/{sha}")
        assert status == 200 and body.startswith(b"\x89PNG") and headers["Content-Type"] == "image/png"
    assert len(running["calls"]) == 1


def test_round_answers_save_immediately_and_latest_wins(running):
    call = running["call"]
    _, made, _ = call("/api/decks", {"sport": None, "screens": 8})
    _, deck, _ = call(f"/api/deck?id={made['id']}")
    first = deck["screens"][0]
    call("/api/answer", {"deck": made["id"], "index": first["index"], "keep": [0, 2], "best": 2,
                         "cannotJudge": False})
    call("/api/answer", {"deck": made["id"], "index": first["index"], "keep": [1], "best": None,
                         "cannotJudge": False})
    _, again, _ = call(f"/api/deck?id={made['id']}")
    assert again["answers"][str(first["index"])]["keep"] == [1]
    rows = pairs_mod.load(running["ledgers"] / "owner-pairs.jsonl")
    assert len(rows) == 2 and pairs_mod.grid_judgments(rows)
    _, listing, _ = call("/api/decks")
    assert listing["decks"][0]["answered"] == 1
    with pytest.raises(HTTPError) as err:
        call("/api/answer", {"deck": made["id"], "index": first["index"], "keep": [0], "best": 3})
    assert err.value.code == 400


def test_rounds_use_recording_activity_and_skip_accidental_ones(running):
    call = running["call"]
    _, view, _ = call("/api/roster")
    trips = _trips(view)
    days = trips[0]["outings"]
    for item in days[0]["sources"]:
        item["activity"] = "snorkeling"
    days[0]["sources"][0]["activity"] = "accidental"
    call("/api/roster", {"status": "owner_confirmed", "trips": trips})
    accidental = days[0]["sources"][0]["sha"]
    _, listing, _ = call("/api/decks")
    assert "accidental" not in listing["sports"]
    _, made, _ = call("/api/decks", {"sport": None, "screens": 12})
    deck = json.loads((running["ledgers"] / "decks" / f"{made['id']}.deck.json").read_text())
    shown = {c["sourceSha256"] for s in deck["screens"] for c in s["clips"]}
    assert accidental not in shown and shown
    _, made, _ = call("/api/decks", {"sport": "ski", "screens": 4})
    deck = json.loads((running["ledgers"] / "decks" / f"{made['id']}.deck.json").read_text())
    assert {s["sport"] for s in deck["screens"]} == {"ski"}


def test_clips_stream_with_byte_ranges_and_nothing_else_is_served(running):
    call = running["call"]
    _, made, _ = call("/api/decks", {"screens": 4})
    _, deck, _ = call(f"/api/deck?id={made['id']}")
    clip = deck["screens"][0]["clips"][0]
    status, body, headers = call(clip, headers={"Range": "bytes=10-19"})
    assert status == 206 and body == bytes(range(10, 20))
    assert headers["Content-Range"] == "bytes 10-19/1024"
    for bad in ("/media/" + "0" * 64 + "/0-1.mp4", "/media/../../etc/passwd", "/thumb/xyz"):
        with pytest.raises(HTTPError) as err:
            call(bad)
        assert err.value.code in (403, 404)
