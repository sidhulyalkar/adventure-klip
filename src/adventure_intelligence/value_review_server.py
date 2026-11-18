"""A local web app for the owner: confirm outings, then review clips in the grid.

Runs on 127.0.0.1 with a per-session token, like Studio. Reads the catalog,
discovery output and footage read-only. Writes only two owner files:
- the outing roster (the previous version is kept as a timestamped backup);
- the append-only answer ledger, one line per grid screen as it is answered,
  so nothing waits on an export. A changed answer is a new line; the latest wins.
"""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
import json
import re
import secrets
import shutil
import sqlite3
import threading
import time
from urllib.parse import parse_qs, urlsplit

from . import outing_roster as roster
from . import value_features as features
from . import value_pairs as pairs_mod

VERSION = "klip-review-app-v1"
_SHA = re.compile(r"^[a-f0-9]{64}$")
_CLIP = re.compile(r"^/media/([a-f0-9]{64})/(\d{1,9})-(\d{1,9})\.mp4$")
_DECK = re.compile(r"^grid-[a-f0-9]{12}$")
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
_ASSETS = {"/review.js": ("value_review.js", "text/javascript; charset=utf-8"),
           "/review.css": ("value_review.css", "text/css; charset=utf-8")}
MAX_BODY = 512_000


class ReviewState:
    def __init__(self, workspace: Path, roster_file: Path, ledger_file: Path,
                 experiments: list[Path], source_root: Path | None, seed: int = 0):
        self.workspace = Path(workspace)
        self.roster_file = Path(roster_file)
        self.ledger_file = Path(ledger_file)
        self.folder = self.roster_file.parent
        self.decks = self.folder / "decks"
        self.thumbs = self.folder / "thumbs"
        self.experiments = [Path(e) for e in experiments]
        self.source_root = Path(source_root).expanduser().absolute() if source_root else None
        self.seed = seed
        self.lock = threading.Lock()
        self.thumb_locks: dict[str, threading.Lock] = {}
        self._catalog = None
        self._sources = None

    # ---- catalog and discovery ----------------------------------------------------

    def catalog(self) -> dict[str, dict]:
        if self._catalog is None:
            database = self.workspace / "catalog.sqlite3"
            with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
                db.execute("PRAGMA query_only=ON")
                self._catalog = {r[0]: {"path": r[1], "duration": r[2], "captureTime": r[3]}
                                 for r in db.execute("SELECT sha256,stored_path,duration,"
                                                     "capture_time FROM assets")}
        return self._catalog

    def sources(self) -> dict[str, dict]:
        """Every scanned recording across the discovery folders; the first listed wins."""
        if self._sources is None:
            found = {}
            for folder in self.experiments:
                try:
                    loaded = features.load_experiment(folder)
                except ValueError:
                    continue
                for sha, source in loaded.items():
                    if len(source["motion"]) and sha not in found:
                        source["folder"] = folder
                        found[sha] = source
            self._sources = found
        return self._sources

    def refresh(self) -> None:
        self._sources = None

    # ---- roster -----------------------------------------------------------------------

    def roster_doc(self) -> dict:
        return roster.validate(json.loads(self.roster_file.read_text(encoding="utf-8")))

    def roster_view(self) -> dict:
        doc, cat, scanned = self.roster_doc(), self.catalog(), self.sources()
        outings = []
        for outing in doc["outings"]:
            recs = []
            for row in outing["sources"]:
                sha = row["sourceSha256"]
                info = cat.get(sha, {})
                recs.append({"sha": sha, "file": Path(info.get("path") or "?").name,
                             "captureTime": info.get("captureTime"),
                             "minutes": round((info.get("duration") or 0) / 60, 1),
                             "scanned": sha in scanned})
            times = [r["captureTime"] for r in recs if r["captureTime"]]
            outings.append({"outing": outing["outing"], "sport": outing.get("sport"),
                            "start": min(times) if times else None,
                            "end": max(times) if times else None,
                            "minutes": round(sum(r["minutes"] for r in recs), 1),
                            "recordings": recs})
        return {"status": doc["status"], "sports": list(roster.SPORTS), "outings": outings}

    def save_roster(self, body: dict) -> dict:
        """Apply the owner's outings. Every recording must still be in exactly one outing."""
        current = self.roster_doc()
        rows = {r["sourceSha256"]: r for o in current["outings"] for r in o["sources"]}
        if body.get("status") not in roster.STATUSES or not isinstance(body.get("outings"), list):
            raise ValueError("Roster needs a status and a list of outings")
        outings = []
        for o in body["outings"]:
            shas = o.get("sources") if isinstance(o, dict) else None
            if not isinstance(shas, list) or not shas:
                raise ValueError("Each outing needs recordings")
            if any(sha not in rows for sha in shas):
                raise ValueError("Unknown recording in roster")
            outings.append({"outing": o.get("outing"), "sport": o.get("sport"),
                            "sources": [rows[sha] for sha in shas], "recordings": len(shas)})
        placed = [r["sourceSha256"] for o in outings for r in o["sources"]]
        if sorted(placed) != sorted(rows):
            raise ValueError("Every recording must stay in exactly one outing")
        if body["status"] == "owner_confirmed" and any(o["sport"] is None for o in outings):
            raise ValueError("Set a sport for every outing before confirming "
                             "(use mixed when an outing has more than one)")
        doc = {**current, "status": body["status"], "outings": outings}
        roster.validate(doc)
        with self.lock:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            shutil.copy2(self.roster_file, self.roster_file.with_name(
                f"{self.roster_file.name}.bak-{stamp}"))
            temp = self.roster_file.with_suffix(".json.partial")
            temp.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
            temp.replace(self.roster_file)
        return {"saved": True, "status": doc["status"], "outings": len(outings)}

    def thumbnail(self, sha: str) -> bytes:
        """One frame from 40% into the recording, cached. Originals are only read."""
        if not _SHA.match(sha):
            raise ValueError("Bad recording id")
        cached = self.thumbs / f"{sha}.png"
        if cached.is_file():
            return cached.read_bytes()
        info = self.catalog().get(sha)
        path = Path(info["path"]) if info and info.get("path") else None
        if (path is None or self.source_root is None or not path.is_relative_to(self.source_root)
                or not path.is_file()):
            raise ValueError("Recording not under the footage folder")
        with self.lock:
            lock = self.thumb_locks.setdefault(sha, threading.Lock())
        with lock:
            if not cached.is_file():
                from .frame_timing import extract_frame_png
                png, _ = extract_frame_png(path, int((info.get("duration") or 0) * 400), width=320)
                self.thumbs.mkdir(parents=True, exist_ok=True)
                temp = cached.with_suffix(".partial")
                temp.write_bytes(png)
                temp.replace(cached)
        return cached.read_bytes()

    # ---- grid ---------------------------------------------------------------------------

    def _items(self) -> dict[str, dict]:
        doc = self.roster_doc()
        mapping, sports = roster.outing_by_source(doc), roster.sport_by_source(doc)
        sources = self.sources()
        for sha, source in sources.items():
            source["sport"] = sports.get(sha, source["sport"])
        return features.build_items(sources, features.segment_intervals(sources),
                                    lambda s: roster.group_of(mapping, s), seed=self.seed)

    def deck_list(self) -> dict:
        self.refresh()  # pick up batches the scan finished since the app opened
        answered = self._answers()
        out = []
        for path in sorted(self.decks.glob("grid-*.deck.json")) if self.decks.is_dir() else []:
            deck = json.loads(path.read_text(encoding="utf-8"))
            digest = pairs_mod.deck_digest(deck)
            out.append({"id": path.name.removesuffix(".deck.json"), "sport": deck.get("sport"),
                        "screens": len(deck["screens"]),
                        "answered": sum(1 for (d, _) in answered if d == digest),
                        "created": deck.get("created")})
        sports = sorted({s["sport"] for s in self.sources().values()})
        return {"decks": out, "scannedRecordings": len(self.sources()), "sports": sports}

    def new_deck(self, body: dict) -> dict:
        sport = body.get("sport")
        screens = body.get("screens", 30)
        if type(screens) is not int or not 4 <= screens <= 120:
            raise ValueError("Screens must be 4..120")
        self.refresh()
        items = {i: it for i, it in self._items().items() if sport in (None, it["sport"])}
        deck = pairs_mod.build_grid(items, experiment="review-app", screens=screens,
                                    per_screen=4, repeats=max(1, screens // 8),
                                    seed=int(time.time()))
        deck.update(sport=sport, created=datetime.now().isoformat(timespec="seconds"))
        name = f"grid-{pairs_mod.deck_digest(deck)[:12]}"
        self.decks.mkdir(parents=True, exist_ok=True)
        (self.decks / f"{name}.deck.json").write_text(
            json.dumps(deck, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return {"id": name, "screens": len(deck["screens"]), "outings": deck["outings"]}

    def _deck(self, deck_id: str) -> dict:
        if not _DECK.match(deck_id or ""):
            raise ValueError("Unknown review round")
        path = self.decks / f"{deck_id}.deck.json"
        if not path.is_file():
            raise ValueError("Unknown review round")
        return json.loads(path.read_text(encoding="utf-8"))

    def _answers(self) -> dict[tuple, dict]:
        latest = {}
        for row in pairs_mod.load(self.ledger_file):
            if row.get("version") == pairs_mod.GRID_VERSION:
                latest[(row["deckSha256"], row["index"])] = row
        return latest

    def deck_view(self, deck_id: str) -> dict:
        deck = self._deck(deck_id)
        digest = pairs_mod.deck_digest(deck)
        answers = {i: {"keep": r["keep"], "best": r["best"], "cannotJudge": r["cannotJudge"]}
                   for (d, i), r in self._answers().items() if d == digest}
        return {"id": deck_id, "question": deck["question"],
                "screens": [{"index": s["index"],
                             "clips": [f'/media/{c["sourceSha256"]}/{c["startMs"]}-{c["endMs"]}.mp4'
                                       for c in s["clips"]]} for s in deck["screens"]],
                "answers": answers}

    def answer(self, body: dict) -> dict:
        deck = self._deck(body.get("deck"))
        index = body.get("index")
        one = {"index": index, "keep": body.get("keep"), "best": body.get("best"),
               "cannotJudge": body.get("cannotJudge") is True}
        export = sha256(f"live:{time.time_ns()}:{secrets.token_hex(4)}".encode()).hexdigest()
        rows = pairs_mod.grid_records(
            {**deck, "screens": deck["screens"]},
            {"version": pairs_mod.GRID_ANSWERS_VERSION, "deckSha256": pairs_mod.deck_digest(deck),
             "answers": [one]}, export)
        with self.lock:
            pairs_mod.append(self.ledger_file, rows)
        return {"saved": True, "index": index}

    def clip_path(self, sha: str, start: int, end: int) -> Path:
        source = self.sources().get(sha)
        if source is None:
            raise ValueError("Unknown clip")
        path = source["folder"] / sha / "proxies" / f"{start}-{end}.mp4"
        if path.is_symlink() or not path.is_file():
            raise ValueError("Unknown clip")
        return path


def make_handler(state: ReviewState, token: str):
    page = resources.files("adventure_intelligence").joinpath("studio_assets")

    class Handler(BaseHTTPRequestHandler):
        server_version = "KlipReview/1.0"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            pass

        def _send(self, status, body=b"", kind="application/json; charset=utf-8", extra=None):
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; script-src 'self'; style-src 'self'; "
                             "img-src 'self'; media-src 'self'; connect-src 'self'; "
                             "base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
            for k, v in (extra or {}).items():
                self.send_header(k, str(v))
            self.end_headers()
            if self.command != "HEAD" and body:
                self.wfile.write(body)

        def _json(self, status, value):
            self._send(status, json.dumps(value, allow_nan=False).encode())

        def _local(self) -> bool:
            port = self.server.server_port
            hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
            origin = self.headers.get("Origin")
            return (self.headers.get("Host", "").lower() in hosts
                    and (not origin or origin in {f"http://{h}" for h in hosts}))

        def _authorized(self, q) -> bool:
            supplied = self.headers.get("X-Klip-Token") or (q.get("token") or [""])[0]
            return secrets.compare_digest(supplied, token)

        def do_GET(self):
            parsed = urlsplit(self.path)
            path, q = parsed.path, parse_qs(parsed.query)
            if not self._local():
                return self._json(403, {"error": "Local host required"})
            if path == "/":
                html = page.joinpath("value_review.html").read_text(encoding="utf-8")
                return self._send(200, html.replace("KLIP_SESSION_TOKEN", token).encode(),
                                  "text/html; charset=utf-8")
            if path == "/favicon.ico":
                return self._send(204)
            if path in _ASSETS:
                name, kind = _ASSETS[path]
                return self._send(200, page.joinpath(name).read_bytes(), kind)
            if not self._authorized(q):
                return self._json(403, {"error": "Unauthorized review session"})
            try:
                if path == "/api/roster":
                    return self._json(200, state.roster_view())
                if path == "/api/decks":
                    return self._json(200, state.deck_list())
                if path == "/api/deck":
                    return self._json(200, state.deck_view((q.get("id") or [""])[0]))
                if path.startswith("/thumb/"):
                    return self._send(200, state.thumbnail(path[len("/thumb/"):]), "image/png")
                match = _CLIP.match(path)
                if match:
                    return self._video(state.clip_path(match[1], int(match[2]), int(match[3])))
                return self._json(404, {"error": "Not found"})
            except (ValueError, OSError, KeyError) as error:
                return self._json(404, {"error": str(error)})

        def _video(self, path: Path):
            size = path.stat().st_size
            start, end = 0, size - 1
            head = self.headers.get("Range")
            status = 200
            if head:
                match = _RANGE.match(head.strip())
                if not match or (not match[1] and not match[2]):
                    return self._send(416, extra={"Content-Range": f"bytes */{size}"})
                if match[1]:
                    start = int(match[1])
                    end = min(int(match[2]), size - 1) if match[2] else size - 1
                else:
                    start = max(0, size - int(match[2]))
                if start > end or start >= size:
                    return self._send(416, extra={"Content-Range": f"bytes */{size}"})
                status = 206
            with open(path, "rb") as handle:
                handle.seek(start)
                body = handle.read(end - start + 1)
            extra = {"Accept-Ranges": "bytes"}
            if status == 206:
                extra["Content-Range"] = f"bytes {start}-{end}/{size}"
            return self._send(status, body, "video/mp4", extra)

        def do_POST(self):
            parsed = urlsplit(self.path)
            if not self._local() or not self._authorized(parse_qs(parsed.query)):
                return self._json(403, {"error": "Unauthorized review write"})
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                return self._json(415, {"error": "JSON required"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if not 0 < length <= MAX_BODY:
                return self._json(413, {"error": "Body too large or empty"})
            try:
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("Expected a JSON object")
                if parsed.path == "/api/roster":
                    result = state.save_roster(body)
                    state.refresh()
                elif parsed.path == "/api/decks":
                    result = state.new_deck(body)
                elif parsed.path == "/api/answer":
                    result = state.answer(body)
                else:
                    return self._json(404, {"error": "Unknown route"})
                return self._json(200, result)
            except (ValueError, KeyError, TypeError) as error:
                return self._json(400, {"error": str(error)})

    return Handler


def serve(state: ReviewState, *, port: int = 0, open_browser: bool = True) -> None:
    token = secrets.token_urlsafe(32)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(state, token))
    server.daemon_threads = True
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"[klip] review app running at {url}  (Ctrl+C to stop)", flush=True)
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
