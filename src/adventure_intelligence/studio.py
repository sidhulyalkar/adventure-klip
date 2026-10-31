"""Local-only, token-protected review studio for read-only DJI source media.

No arbitrary filesystem access, no remote content, no auto-merge and no uploads.
The studio serves only catalog-verified original assets, with HTTP byte ranges
for macOS video seek behavior. Human choices go to append-only SQLite reviews.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from importlib.resources import files
from urllib.parse import parse_qs, urlsplit
from collections import Counter
import hashlib
import json
import os
import re
import secrets
import threading

from .catalog import connect
from .examples import review_example, review_inventory
from .comparisons import compare_clips, comparison_summary
from .editorial_quality import editorial_candidates
from .diagnostics import diagnostics_for_workspace
from .locking import lock_workspace
from .sources import resolve_source
from .sequence_editor import sequence_state, save_sequence, review_combination, export_sequence
from .curation_queue import review_queue
from .library_index import library_index, organize_asset
from .transition_evidence import EdgeEvidence,VERSION as EDGE_VERSION

SHA_RE=re.compile(r"^[a-f0-9]{64}$")
_RANGE_RE=re.compile(r"^bytes=(\d*)-(\d*)$")
_RESOURCE={
    "/studio.css":("studio.css","text/css; charset=utf-8"),
    "/studio.js":("studio.js","text/javascript; charset=utf-8"),
    "/studio_editor.js":("studio_editor.js","text/javascript; charset=utf-8"),
    "/editor.css":("editor.css","text/css; charset=utf-8"),
    "/focus.css":("focus.css","text/css; charset=utf-8"),
    "/classic_theme.css":("classic_theme.css","text/css; charset=utf-8"),
    "/focus.js":("focus.js","text/javascript; charset=utf-8"),
    "/focus_sequence.js":("focus_sequence.js","text/javascript; charset=utf-8"),
}


def studio_overview(workspace: Path) -> dict:
    with connect(workspace) as db:
        assets=[]
        for r in db.execute("""SELECT a.id,a.duration,a.activity,a.capture_time,
            a.size_bytes,a.stored_path,
            (SELECT i.original_name FROM imports i WHERE i.asset_id=a.id
             ORDER BY i.imported_at DESC LIMIT 1) AS original_name
            FROM assets a ORDER BY a.created_at,a.id"""):
            asset=dict(r)
            assets.append({
                "id":asset["id"],
                "title":asset["original_name"] or Path(asset["stored_path"]).name,
                "duration":float(asset["duration"] or 0),
                "activity":asset["activity"],"captureTime":asset["capture_time"],
                "sizeBytes":asset["size_bytes"],
            })
        counts={r["asset_id"]:r["n"] for r in db.execute(
            "SELECT asset_id,COUNT(*) n FROM episodes GROUP BY asset_id")}
    reviews=review_inventory(workspace)
    comparisons=comparison_summary(workspace)
    return {"schemaVersion":1,"assets":[
        {**a,"proposals":counts.get(a["id"],0),
         "reviews":sum(e["assetId"]==a["id"] for e in reviews["examples"])}
        for a in assets],
        "reviewed":reviews["reviewed"],
        "conflicts":reviews["overlapConflicts"],
        "comparisons":comparisons["current"],
        "sourceSafety":"Source video is served read-only. Reviews stored in Klip workspace.",
        "localOnly":True}


def studio_asset(workspace: Path, asset_id: str) -> dict:
    if not SHA_RE.fullmatch(asset_id):
        raise ValueError("Invalid asset id")
    with connect(workspace) as db:
        asset=db.execute("SELECT * FROM assets WHERE id=?",(asset_id,)).fetchone()
        if asset is None:
            raise KeyError("Asset not found")
        rows=[dict(x) for x in db.execute(
            "SELECT * FROM episodes WHERE asset_id=? ORDER BY start_ms",(asset_id,))]
        candidates, exclusions=editorial_candidates(db,workspace,
            [e for e in rows if e["kind"]!="recording_summary"],policy="suggest")
    candidates.sort(key=lambda x:(-x["score"],x["startMs"]))
    diagnostics=diagnostics_for_workspace(workspace,asset_id=asset_id)["assets"][0]
    reviews=review_inventory(workspace,asset_id=asset_id)
    return {"schemaVersion":1,"assetId":asset_id,
            "activity":asset["activity"],"durationMs":round(float(asset["duration"] or 0)*1000),
            "suggestions":[{
                "id":f"{c['episodeId']}:{c['startMs']}",
                "startMs":c["startMs"],"endMs":c["endMs"],
                "score":c["score"],"kind":c["kind"],
                "reasonCodes":c.get("reasonCodes",[]),
                "scene":c.get("sceneEvidence"),"motion":c.get("motionEvidence"),
                "audio":c.get("acousticEvidence"),"reviewedKeep":c.get("reviewedKeepFraction"),
                "requiresReview":True,
            } for c in candidates[:90]],
            "diagnostics":{
                "contextStates":diagnostics["contextStates"][:100],
                "cameraDisturbances":diagnostics["cameraDisturbances"][:100],
                "reviewedIncidents":diagnostics["reviewedIncidents"][:100],
            },
            "reviews":reviews["examples"],
            "conflicts":reviews["overlapConflicts"],
            "exclusions":exclusions,
            "warnings":["Camera motion is not athlete quality",
                        "Automatic context and incident cues remain hypotheses"]}


def studio_clip_metadata(workspace:Path, asset_id:str, start_ms:int, end_ms:int) -> dict:
    """Read-only, cached-only clip metadata, with explicit unknown coverage."""
    if not SHA_RE.fullmatch(asset_id):
        raise ValueError("Invalid asset ID")
    if (not isinstance(start_ms,int) or not isinstance(end_ms,int)
        or start_ms<0 or end_ms<=start_ms):
        raise ValueError("Invalid clip interval")
    with connect(workspace) as db:
        asset=db.execute("SELECT id,duration FROM assets WHERE id=?",(asset_id,)).fetchone()
        if asset is None:
            raise ValueError("Asset is not indexed")
        if end_ms>round(float(asset["duration"] or 0)*1000):
            raise ValueError("Clip exceeds indexed duration")
        cache=EdgeEvidence(db,workspace)
        segment={"sourceAssetId":asset_id,"startMs":start_ms,"endMs":end_ms}
        beginning=cache.snapshot(segment,"head")
        ending=cache.snapshot(segment,"tail")
    return {"schemaVersion":1,"version":EDGE_VERSION,"assetId":asset_id,
            "interval":{"startMs":start_ms,"endMs":end_ms},
            "head":{k:v for k,v in beginning.items() if not k.startswith("_")},
            "tail":{k:v for k,v in ending.items() if not k.startswith("_")},
            "status":"unverified_model_evidence",
            "note":"Query labels and similarities are hypotheses, not verified object identities or species"}


class SourceStream:
    """Read-only source resolver; first playback verifies entire original SHA-256."""
    def __init__(self,workspace: Path):
        self.workspace=workspace
        self._cache:dict[str,tuple[Path,tuple]]={}
        self._lock=threading.RLock()

    def open_asset(self,asset_id: str):
        if not SHA_RE.fullmatch(asset_id):
            raise ValueError("Invalid media ID")
        with self._lock:
            with connect(self.workspace) as db:
                asset=db.execute("SELECT * FROM assets WHERE id=?",(asset_id,)).fetchone()
                if asset is None:
                    raise KeyError("Unknown video")
                stored_path=Path(asset["stored_path"])
                # Full content check happens on the first stream per server
                # session; later requests validate stored inode and stat tuple.
                cached=self._cache.get(asset_id)
                if cached is None:
                    path=resolve_source(db,asset)
                    stat=path.stat()
                    fields=(stat.st_dev,stat.st_ino,stat.st_size,
                            stat.st_mtime_ns,stat.st_ctime_ns)
                    self._cache[asset_id]=(path,fields)
                else:
                    path,fields=cached
            fd=os.open(path,os.O_RDONLY | getattr(os,"O_NOFOLLOW",0))
            st=os.fstat(fd)
            now=(st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns)
            if now!=fields:
                os.close(fd)
                self._cache.pop(asset_id,None)
                raise OSError("Source media changed since verification; playback blocked")
            return fd,st.st_size


def make_handler(workspace:Path,token:str):
    """Handler factory allows isolated HTTP regression tests using ephemeral ports."""
    paths=files("adventure_intelligence").joinpath("studio_assets")
    streamer=SourceStream(workspace)

    class Handler(BaseHTTPRequestHandler):
        server_version="KlipStudio/1.0"
        sys_version=""
        protocol_version="HTTP/1.1"

        def log_message(self,fmt,*args):
            # Never log requested paths: video URL contains session credentials.
            print("[studio] request handled",flush=True)

        def _send(self,status:int,body:bytes=b"",content_type="application/json; charset=utf-8",
                  extra:dict|None=None):
            self.send_response(status)
            self.send_header("Content-Type",content_type)
            self.send_header("Content-Length",str(len(body)))
            self.send_header("Cache-Control","no-store")
            self.send_header("X-Content-Type-Options","nosniff")
            self.send_header("X-Frame-Options","DENY")
            self.send_header("Referrer-Policy","no-referrer")
            self.send_header("Content-Security-Policy",
                "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; media-src 'self'; connect-src 'self'; "
                "base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
            for k,v in (extra or {}).items():
                self.send_header(k,str(v))
            self.end_headers()
            if self.command!="HEAD" and body:
                self.wfile.write(body)

        def _json(self,status:int,value):
            self._send(status,json.dumps(value,allow_nan=False).encode())

        def _origin_safe(self) -> bool:
            host=self.headers.get("Host","").lower()
            if host not in {f"127.0.0.1:{self.server.server_port}",
                            f"localhost:{self.server.server_port}"}:
                return False
            origin=self.headers.get("Origin")
            return not origin or origin in {
                f"http://127.0.0.1:{self.server.server_port}",
                f"http://localhost:{self.server.server_port}",
            }

        def _authorized(self,q:dict) -> bool:
            supplied=self.headers.get("X-Klip-Token") or (q.get("token") or [""])[0]
            return secrets.compare_digest(supplied,token)

        def _send_video(self,asset_id):
            try:
                fd,size=streamer.open_asset(asset_id)
            except (OSError,KeyError,ValueError):
                self._json(404,{"error":"Video unavailable or changed since indexing"})
                return
            try:
                if size<=0:
                    self._send(416)
                    return
                head=self.headers.get("Range")
                start,end=0,size-1
                if head:
                    match=_RANGE_RE.fullmatch(head.strip())
                    if not match or not any(match.groups()):
                        self._send(416,extra={"Content-Range":f"bytes */{size}"})
                        return
                    a,b=match.groups()
                    if a:
                        start=int(a)
                        end=min(size-1,int(b)) if b else min(size-1,start+2*1024*1024-1)
                    else:
                        start=max(0,size-int(b))
                    if start>=size or end<start:
                        self._send(416,extra={"Content-Range":f"bytes */{size}"})
                        return
                length=end-start+1
                self.send_response(206 if head else 200)
                self.send_header("Content-Type","video/mp4")
                self.send_header("Content-Length",str(length))
                self.send_header("Accept-Ranges","bytes")
                if head:
                    self.send_header("Content-Range",f"bytes {start}-{end}/{size}")
                self.send_header("Cache-Control","no-store")
                self.send_header("X-Content-Type-Options","nosniff")
                self.send_header("Referrer-Policy","no-referrer")
                self.end_headers()
                if self.command=="HEAD":
                    return
                os.lseek(fd,start,os.SEEK_SET)
                left=length
                while left>0:
                    chunk=os.read(fd,min(256*1024,left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left-=len(chunk)
            except (BrokenPipeError,ConnectionResetError):
                pass
            finally:
                os.close(fd)

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            parsed=urlsplit(self.path)
            path=parsed.path
            q=parse_qs(parsed.query)
            if not self._origin_safe():
                self._json(403,{"error":"Local host required"})
                return
            if path=="/" or path=="/classic":
                # Focus is the default. Classic keeps the older analysis and
                # A/B comparison panels available without cluttering Review.
                name="focus.html" if path=="/" else "index.html"
                html=paths.joinpath(name).read_text(encoding="utf-8")
                html=html.replace("KLIP_SESSION_TOKEN",token)
                self._send(200,html.encode(),"text/html; charset=utf-8")
                return
            if path in _RESOURCE:
                name,mime=_RESOURCE[path]
                self._send(200,paths.joinpath(name).read_bytes(),mime)
                return
            if not self._authorized(q):
                self._json(403,{"error":"Unauthorized studio session"})
                return
            try:
                if path=="/api/overview":
                    self._json(200,studio_overview(workspace))
                elif path=="/api/sequence":
                    self._json(200,sequence_state(workspace))
                elif path=="/api/library":
                    self._json(200,library_index(workspace))
                elif path=="/api/clip-metadata":
                    aid=q.get("asset",[""])[0]
                    raw_start=q.get("startMs",[""])[0]
                    raw_end=q.get("endMs",[""])[0]
                    if not raw_start.isdecimal() or not raw_end.isdecimal() or len(raw_start)>10 or len(raw_end)>10:
                        raise ValueError("Expected integer millisecond boundaries")
                    self._json(200,studio_clip_metadata(workspace,aid,int(raw_start),int(raw_end)))
                elif path=="/api/queue":
                    raw_page=q.get("page",["0"])[0]
                    if not raw_page.isdigit() or len(raw_page)>6:
                        raise ValueError("Invalid queue page")
                    self._json(200,review_queue(workspace,
                        activity=q.get("activity",["all"])[0],page=int(raw_page)))
                elif path.startswith("/api/asset/"):
                    self._json(200,studio_asset(workspace,path.removeprefix("/api/asset/")))
                elif path=="/api/comparisons":
                    self._json(200,comparison_summary(workspace))
                elif path.startswith("/api/media/"):
                    self._send_video(path.removeprefix("/api/media/"))
                else:
                    self._json(404,{"error":"Not found"})
            except (ValueError,KeyError) as exc:
                self._json(400,{"error":str(exc)})
            except Exception:
                self._json(500,{"error":"Local studio error; see terminal"})

        def do_POST(self):
            parsed=urlsplit(self.path)
            if not self._origin_safe() or not self._authorized(parse_qs(parsed.query)):
                self._json(403,{"error":"Unauthorized studio write"})
                return
            if self.headers.get("Content-Type","").split(";")[0].strip()!="application/json":
                self._json(415,{"error":"JSON required"})
                return
            try:
                length=int(self.headers.get("Content-Length","0"))
            except ValueError:
                length=0
            if not 0<length<=8192:
                self._json(413,{"error":"Payload must be between 1 and 8192 bytes"})
                return
            try:
                body=json.loads(self.rfile.read(length))
                if not isinstance(body,dict):
                    raise ValueError("Expected a JSON object")
                if parsed.path=="/api/reviews":
                    with lock_workspace(workspace):
                        result=review_example(
                            workspace,
                            asset_id=body["assetId"],start_seconds=body["startSeconds"],
                            end_seconds=body["endSeconds"],label=body["label"],
                            decision=body["decision"],highlight=body.get("highlight","neutral"),
                            note=body.get("note",""))
                elif parsed.path=="/api/library/organize":
                    with lock_workspace(workspace):
                        result=organize_asset(workspace,asset_id=body["assetId"],
                            region=body.get("region"),place=body.get("place"),
                            collection=body.get("collection"),
                            activity=body.get("activity"),note=body.get("note"))
                elif parsed.path=="/api/sequence":
                    with lock_workspace(workspace):
                        result=save_sequence(workspace,entries=body["entries"],
                            expected_revision=body.get("expectedRevision"),
                            name=body.get("name","My highlights"))
                elif parsed.path=="/api/sequence/feedback":
                    with lock_workspace(workspace):
                        result=review_combination(workspace,
                            sequence_revision=body["sequenceRevision"],
                            left_example_id=body["leftExampleId"],
                            right_example_id=body["rightExampleId"],
                            verdict=body["verdict"],reason=body.get("reason","other"),
                            note=body.get("note",""))
                elif parsed.path=="/api/sequence/export":
                    with lock_workspace(workspace):
                        result=export_sequence(workspace,
                            expected_revision=body["expectedRevision"])
                elif parsed.path=="/api/compare":
                    with lock_workspace(workspace):
                        result=compare_clips(
                            workspace,
                            left_asset_id=body["leftAssetId"],left_start=body["leftStart"],
                            left_end=body["leftEnd"],right_asset_id=body["rightAssetId"],
                            right_start=body["rightStart"],right_end=body["rightEnd"],
                            preference=body["preference"],reason=body.get("reason","other"),
                            note=body.get("note",""))
                else:
                    self._json(404,{"error":"Unknown API route"})
                    return
                self._json(200,result)
            except (KeyError,ValueError,TypeError) as exc:
                self._json(400,{"error":str(exc)})
            except Exception:
                self._json(500,{"error":"Couldn't record review; check terminal"})

    return Handler


def serve_studio(workspace:Path,port:int=0,open_browser:bool=True):
    """Serve a private local UI, then block until Ctrl+C."""
    if not 0<=port<=65535:
        raise ValueError("Port must be 0..65535")
    workspace=Path(workspace).expanduser().resolve()
    with connect(workspace):
        pass
    token=secrets.token_urlsafe(32)
    server=ThreadingHTTPServer(("127.0.0.1",port),make_handler(workspace,token))
    server.daemon_threads=True
    url=f"http://127.0.0.1:{server.server_port}/"
    print(f"[klip] Local studio running at {url}",flush=True)
    print("[klip] Original video files are opened read-only; press Ctrl+C to stop.",flush=True)
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return {"stopped":True}
