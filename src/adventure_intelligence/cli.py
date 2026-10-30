"""The `adventure` command. Local paths only; no uploads or remote model calls."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import time
from .workspace import init, root_at
from .catalog import connect
from .locking import lock_workspace
from .ingest import ingest, looks_like_dji_card
from .analyze import analyze
from .curation import import_labels, bind_event
from .export import export_manifest
from .reels import RECIPES, plan_reel, render_reel
from .report import write_report
from .sessions import session_candidates
from .indexing import index_folder
from .timeline import generate_timelines
from .semantic import generate_semantics
from .storyboard import plan_storyboard
from .process import process_folder
from .history import event_history
from .safety import protect_source_output
from .progress import Progress
from .acoustics import analyze_audio
from .submersion import continuity_report


def _print(data):
    print(json.dumps(data,indent=2,default=str))


def watch_once(root: Path, volumes: Path, activity: str, process: bool) -> dict:
    result={"recognized":[],"imports":[]}
    if not volumes.is_dir():
        return result
    for volume in sorted(volumes.iterdir()):
        if volume.is_symlink() or not volume.is_dir() or not looks_like_dji_card(volume):
            continue
        result["recognized"].append(str(volume))
        result["imports"].append(ingest(volume/"DCIM",root,activity=activity))
    if process and result["recognized"]:
        result["analysis"]=analyze(root)
    return result


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(prog="klip",description=__doc__)
    parser.add_argument("--workspace",default=None,help="Default: ~/AdventureAI or ADVENTURE_HOME")
    sub=parser.add_subparsers(dest="command",required=True)
    sub.add_parser("init",help="Create a local workspace and SQLite catalog")
    process=sub.add_parser('process',help='Index a stable folder, resume analysis, and build timelines')
    process.add_argument('source',type=Path)
    process.add_argument('--activity',choices=['unknown','ski','bike','snorkeling'],default='unknown')
    process.add_argument('--detector',choices=['baseline','flow'],default='baseline')
    process.add_argument('--allow-removable',action='store_true')
    process.add_argument('--semantic-backend',choices=['histogram','siglip2','owlv2','context','stack'])
    process.add_argument('--every',type=float,default=4.0)
    process.add_argument('--audio-evidence',action='store_true',help='Extract audio-change evidence without inferring breathing')
    process.add_argument('--no-progress',action='store_true',help='Hide live progress messages on stderr')
    process.add_argument('--log-file',type=Path,help='Also append progress messages to this file')
    history=sub.add_parser('history',help='Inspect immutable event revisions and review decisions')
    history.add_argument('asset_id')
    imp=sub.add_parser("ingest",help="Copy and verify camera files without modifying the card")
    imp.add_argument("source",type=Path)
    imp.add_argument("--activity",choices=["unknown","ski","bike","snorkeling"],default="unknown")
    imp.add_argument("--dry-run",action="store_true")
    idx=sub.add_parser("index",help="Zero-copy, read-only index of a stable local footage folder")
    idx.add_argument("source",type=Path)
    idx.add_argument("--activity",choices=["unknown","ski","bike","snorkeling"],default="unknown")
    idx.add_argument("--dry-run",action="store_true")
    idx.add_argument("--allow-removable",action="store_true")
    timeline=sub.add_parser("timeline",help="Produce calibrated per-video event timelines")
    timeline.add_argument("--source",choices=["pixel-change","optical-flow"],default="pixel-change")
    timeline.add_argument("--bin-ms",type=int,default=2000)
    scene=sub.add_parser("scenes",help="Extract optional sparse image embeddings / detections")
    scene.add_argument("--backend",choices=["histogram","siglip2","owlv2","context","stack"],default="histogram")
    scene.add_argument("--every",type=float,default=4.0)
    scene.add_argument("--limit",type=int)
    scene.add_argument("--pose-model",type=Path)
    audio=sub.add_parser('acoustics',help='Extract acoustic-change evidence from indexed videos')
    audio.add_argument('--limit',type=int)
    sub.add_parser('continuity',help='Review proposed continuous underwater filming intervals')
    proc=sub.add_parser("analyze",help="Compute low-resolution motion proposals (baseline or optical flow)")
    proc.add_argument("--limit",type=int)
    proc.add_argument("--force",action="store_true")
    proc.add_argument("--detector",default="baseline",help="Detector name: baseline, flow (requires [vision]), or installed plugin")
    sub.add_parser("status",help="Summarize catalog, episodes and pipeline status")
    sessions=sub.add_parser("sessions",help="Suggest cross-file sessions for review (never auto-stitch)")
    sessions.add_argument("--gap-minutes",type=float,default=15.0)
    review=sub.add_parser("review",help="Create a local HTML report for video/event review")
    review.add_argument("--output",type=Path)
    lab=sub.add_parser("labels",help="Replace manual labels for one asset from JSON")
    lab.add_argument("file",type=Path)
    binding=sub.add_parser("bind",help="Propose or manually approve a WorldLink association")
    binding.add_argument("event_id")
    binding.add_argument("world_entity_id")
    binding.add_argument("--match-type",choices=["exact","behavior","habitat","thematic"],default="exact")
    binding.add_argument("--approve",action="store_true")
    plan=sub.add_parser("plan",help="Generate a non-overlapping episode highlight plan")
    plan.add_argument("--recipe",choices=list(RECIPES),default="best-sequences")
    plan.add_argument("--seconds",type=int,default=75)
    plan.add_argument("--output",type=Path)
    plan.add_argument("--style",choices=["chronological","crescendo"],default="chronological")
    plan.add_argument('--ranking',choices=['legacy','context'],default='legacy')
    plan.add_argument('--context-policy',choices=['suggest','automatic'],default='suggest')
    render=sub.add_parser("render",help="Render a reviewable MP4 from an exported edit plan")
    render.add_argument("plan",type=Path)
    render.add_argument("--output",type=Path,required=True)
    render.add_argument("--height",type=int,default=720,choices=[480,720,1080])
    render.add_argument("--transition",choices=["cut","fade"],default="cut")
    render.add_argument("--fade-seconds",type=float,default=0.35)
    exp=sub.add_parser("export",help="Export approved, URL-resolved WorldLink bindings")
    exp.add_argument("--output",type=Path,required=True)
    exp.add_argument("--media-map",type=Path)
    watch=sub.add_parser("watch",help="Discover mounted DJI cards in /Volumes")
    watch.add_argument("--volumes",type=Path,default=Path("/Volumes"))
    watch.add_argument("--activity",choices=["unknown","ski","bike","snorkeling"],default="unknown")
    watch.add_argument("--process",action="store_true",help="Run lightweight baseline analysis after import")
    watch.add_argument("--interval",type=int,default=0,help="Poll seconds; default one scan and exit")
    args=parser.parse_args(argv)
    root=root_at(args.workspace)
    try:
        if args.command=="init":
            init(root)
            with connect(root):
                pass
            _print({"workspace":str(root),"state":"initialized"})
        elif args.command=='process':
            if args.log_file is not None:
                if args.no_progress:
                    raise ValueError('--log-file cannot be combined with --no-progress')
                source=args.source.expanduser().resolve()
                log_path=args.log_file.expanduser().resolve()
                if log_path.is_relative_to(source):
                    raise ValueError('Log file cannot be inside the input media folder')
                with connect(root) as db:
                    protect_source_output(db, args.log_file)
            progress=None if args.no_progress else Progress(log_file=args.log_file)
            result=process_folder(args.source,root,activity=args.activity,
                                  detector=args.detector,allow_removable=args.allow_removable,
                                  semantic_backend=args.semantic_backend,sample_seconds=args.every,
                                  progress=progress,audio_evidence=args.audio_evidence)
            _print(result)
            return 0 if result['status']=='completed' else 1
        elif args.command=="ingest":
            if args.dry_run:
                _print(ingest(args.source,root,activity=args.activity,dry_run=True))
            else:
                with lock_workspace(root):
                    _print(ingest(args.source,root,activity=args.activity))
        elif args.command=="index":
            with lock_workspace(root):
                _print(index_folder(args.source,root,activity=args.activity,
                                    dry_run=args.dry_run,allow_removable=args.allow_removable))
        elif args.command=="timeline":
            with lock_workspace(root):
                _print(generate_timelines(root,source=args.source,bin_ms=args.bin_ms))
        elif args.command=='history':
            _print(event_history(root,args.asset_id))
        elif args.command=="scenes":
            with lock_workspace(root):
                result=generate_semantics(root,backend=args.backend,sample_seconds=args.every,
                                          limit=args.limit,pose_model=args.pose_model)
                _print(result)
                if result['errors']:
                    return 1
        elif args.command=='acoustics':
            with lock_workspace(root):
                output=analyze_audio(root,limit=args.limit,progress=Progress())
                _print(output)
                if output['failed']:
                    return 1
        elif args.command=='continuity':
            _print(continuity_report(root))
        elif args.command=="analyze":
            with lock_workspace(root):
                result=analyze(root,args.limit,args.force,args.detector)
                _print(result)
                if result["failed"]:
                    return 1
        elif args.command=="labels":
            with lock_workspace(root):
                _print(import_labels(root,args.file))
        elif args.command=="bind":
            with lock_workspace(root):
                _print(bind_event(root,args.event_id,args.world_entity_id,args.match_type,args.approve))
        elif args.command=="status":
            with connect(root) as db:
                _print({table:db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                        for table in ("assets","imports","events","episodes","bindings","analyses")})
                assets=[dict(x) for x in db.execute("SELECT id,activity,duration FROM assets ORDER BY created_at LIMIT 30")]
                _print({"assets":assets})
        elif args.command=="sessions":
            _print(session_candidates(root,args.gap_minutes))
        elif args.command=="review":
            _print(write_report(root,args.output or root/"exports"/"review.html"))
        elif args.command=="plan":
            doc=plan_storyboard(root,args.recipe,args.seconds,args.style,
                                ranking=args.ranking,context_policy=args.context_policy)
            if args.output:
                with connect(root) as db:
                    protect_source_output(db, args.output)
                args.output.parent.mkdir(parents=True,exist_ok=True)
                args.output.write_text(json.dumps(doc,indent=2)+"\n")
            _print(doc)
        elif args.command=="render":
            _print(render_reel(root,json.loads(args.plan.read_text()),args.output,args.height,
                               args.transition,args.fade_seconds))
        elif args.command=="export":
            _print(export_manifest(root,args.output,args.media_map))
        elif args.command=="watch":
            if args.interval<0:
                raise ValueError("interval must be nonnegative")
            while True:
                with lock_workspace(root):
                    _print(watch_once(root,args.volumes,args.activity,args.process))
                if not args.interval:
                    break
                time.sleep(args.interval)
        return 0
    except (ValueError,RuntimeError,OSError,KeyError,json.JSONDecodeError) as exc:
        print(f"error: {exc}",file=sys.stderr)
        return 2

if __name__=="__main__":
    raise SystemExit(main())
