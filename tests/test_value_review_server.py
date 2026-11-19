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
        running["call"]("/api/library", token_ok=False)
    assert err.value.code == 403


def _body(view, activity="ski", status="provisional"):
    """What the organizer posts: trips with days of recording SHAs, plus per-recording details."""
    return {"status": status,
            "trips": [{"trip": t["trip"], "title": t["title"], "place": t["place"],
                       "note": t["note"], "days": [
                           {"outing": d["outing"], "title": d["title"], "place": d["place"],
                            "note": d["note"], "recordings": list(d["recordings"])}
                           for d in t["days"]]} for t in view["trips"]],
            "recordings": {sha: {"activity": activity, "note": r["note"], "tags": r["tags"]}
                           for sha, r in view["recordings"].items()}}


def test_library_view_groups_days_into_trips_with_suggestions(running):
    _, view, _ = running["call"]("/api/library")
    # Fixture days are Jan 10, 11, 12: one trip, three days, nothing labelled yet.
    assert len(view["trips"]) == 1 and len(view["trips"][0]["days"]) == 3
    assert {r["activity"] for r in view["recordings"].values()} == {"unknown"}
    assert "accidental" in view["activities"]
    first = view["recordings"][view["trips"][0]["days"][0]["recordings"][0]]
    assert first["burst"] and "hints" in first and first["hints"] == {}  # nothing described


def test_suggestions_come_from_the_atlas_and_refresh(running, tmp_path):
    from adventure_intelligence import library_atlas
    sha = running["made"]["shas"][0]
    atlas_dir = tmp_path / "ws" / "reports" / "atlas"
    atlas_dir.mkdir(parents=True)
    head = {"version": library_atlas.HEADER_VERSION, "sourceSha256": sha,
            "video": {"width": 3840, "height": 2160, "fps": 119.88, "pixFmt": "yuv420p10le",
                      "colorTransfer": "bt709"}}
    (atlas_dir / "headers.jsonl").write_text(json.dumps(head) + "\n{cut sho")
    app.ReviewState.HINTS_SECONDS = 0
    try:
        _, view, _ = running["call"]("/api/library")
    finally:
        app.ReviewState.HINTS_SECONDS = 120
    assert view["recordings"][sha]["hints"] == {"mode": "3840x2160 119.88fps 10-bit SDR",
                                                "slowMotion": True}


def test_library_edits_keep_every_recording_details_and_one_backup(running):
    call = running["call"]
    _, view, _ = call("/api/library")
    body = _body(view)
    days = body["trips"][0]["days"]
    lost = json.loads(json.dumps(body))
    lost["trips"][0]["days"] = lost["trips"][0]["days"][:1]
    with pytest.raises(HTTPError) as err:
        call("/api/library", lost)
    assert err.value.code == 400  # recordings would be lost
    days[0]["recordings"] += days[1]["recordings"]
    del days[1]
    body["trips"][0].update(title="French Polynesia", place="Moorea")
    days[0].update(title="Turtle day", note="Lagoon snorkel")
    odd = days[1]["recordings"][0]
    body["recordings"][odd].update(activity="accidental", note="pocket", tags=["mistake", " ", "x"])
    for _ in range(3):  # autosave: many saves, one backup per session
        _, saved, _ = call("/api/library", body)
    assert saved["trips"] == 1 and saved["outings"] == 2 and saved["unlabelled"] == 0
    doc = roster.validate(json.loads((running["ledgers"] / "roster.json").read_text()))
    assert doc["trips"][body["trips"][0]["trip"]]["title"] == "French Polynesia"
    assert doc["outings"][0]["title"] == "Turtle day"
    assert doc["outings"][0]["sources"][0]["captureTime"]  # catalog details preserved
    row = next(r for o in doc["outings"] for r in o["sources"] if r["sourceSha256"] == odd)
    assert row["note"] == "pocket" and row["tags"] == ["mistake", "x"]
    assert roster.excluded_sources(doc) == {odd}
    assert len(list(running["ledgers"].glob("roster.json.bak-*"))) == 1
    _, again, _ = call("/api/library")
    assert again["recordings"][odd]["note"] == "pocket"
    assert again["trips"][0]["title"] == "French Polynesia"
    bad = json.loads(json.dumps(body))
    bad["recordings"][odd]["activity"] = "parasailing"
    with pytest.raises(HTTPError):
        call("/api/library", bad)


def test_originals_stream_only_from_the_footage_folder(running):
    sha = running["made"]["shas"][0]
    status, body, headers = running["call"](f"/original/{sha}", headers={"Range": "bytes=0-"})
    assert status == 206 and body == b"video" and headers["Content-Range"] == "bytes 0-4/5"
    with pytest.raises(HTTPError) as err:
        running["call"](f"/original/{'0' * 64}")
    assert err.value.code == 404


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
    _, view, _ = call("/api/library")
    body = _body(view, status="owner_confirmed")
    day0 = body["trips"][0]["days"][0]["recordings"]
    for sha in day0:
        body["recordings"][sha]["activity"] = "snorkeling"
    accidental = day0[0]
    body["recordings"][accidental]["activity"] = "accidental"
    call("/api/library", body)
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


def test_thumbnail_falls_back_to_the_first_frame(running, monkeypatch):
    from adventure_intelligence import frame_timing
    asked = []

    def picky(path, ms, width):
        asked.append(ms)
        if ms:
            raise RuntimeError("FFmpeg failed to decode the requested video frame")
        return b"\x89PNG first", {}

    monkeypatch.setattr(frame_timing, "extract_frame_png", picky)
    sha = running["made"]["shas"][1]
    status, body, _ = running["call"](f"/thumb/{sha}")
    assert status == 200 and body == b"\x89PNG first" and asked == [80_000, 0]
