"""The `adventure` command. Local paths only; no uploads or remote model calls."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import time
from .workspace import init, root_at
from .catalog import connect
from .locking import lock_workspace
from .ingest import ingest, looks_like_dji_card
from .analyze import analyze
from .curation import import_labels, bind_event, append_review_label
from .export import export_manifest
from .reels import RECIPES, plan_reel, render_reel
from .report import write_report
from .sessions import session_candidates
from .indexing import index_folder
from .timeline import generate_timelines
from .semantic import generate_semantics
from .storyboard import plan_storyboard
from .process import process_folder
from .run_audit import audit_log
from .nim_judges import evaluate_manifest, DEFAULT_ENDPOINT
from .nim_benchmark import prepare_benchmark
from .history import event_history
from .safety import protect_source_output
from .progress import Progress
from .acoustics import analyze_audio
from .submersion import continuity_report
from .diagnostics import write_diagnostics
from .studio import serve_studio
from .examples import review_example, review_inventory
from .review_evaluation import review_quality_report
from .editorial_quality import editorial_candidates
from .scene_atlas import enrich_candidates
from .reels import RECIPES


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
    diagnosis=sub.add_parser('diagnose',help='Create local activity/incident review JSON and timed VTT overlays')
    diagnosis.add_argument('--asset',help='Exact catalog SHA-256 asset ID; omit for all videos')
    proc=sub.add_parser("analyze",help="Compute low-resolution motion proposals (baseline or optical flow)")
    proc.add_argument("--limit",type=int)
    proc.add_argument("--force",action="store_true")
    proc.add_argument("--detector",default="baseline",help="Detector name: baseline, flow (requires [vision]), or installed plugin")
    sub.add_parser("status",help="Summarize catalog, episodes and pipeline status")
    prep=sub.add_parser("prepare-nim-benchmark",
                        help="Plan seeded candidate+random intervals; optional LOCAL frame extraction")
    prep.add_argument("--output-dir",type=Path,required=True,
                      help="New directory within the Klip workspace")
    prep.add_argument("--samples",type=int,default=12)
    prep.add_argument("--seed",type=int,default=20261031)
    prep.add_argument("--window-seconds",type=int,default=14)
    prep.add_argument("--decode-frames",action="store_true",
                      help="Explicitly seek and extract 6 still frames per clip locally")
    jury=sub.add_parser("judge-nim",help="Opt-in external VLM jury over PRE-EXTRACTED frames")
    jury.add_argument("manifest",type=Path,help="Explicit JSON list of candidate and random clips")
    jury.add_argument("--frames-dir",type=Path,required=True,help="Root of approved JPEG/PNG frames")
    jury.add_argument("--model",action="append",required=True,help="Repeat with 2-4 image-capable NIM models")
    jury.add_argument("--allow-external-upload",action="store_true",
                      help="Explicitly transmit selected frames to a third-party endpoint")
    jury.add_argument("--max-calls",type=int,default=12)
    jury.add_argument("--endpoint",default=DEFAULT_ENDPOINT)
    jury.add_argument("--output",type=Path,help="Write JSON result; fails if destination exists")
    audit=sub.add_parser("audit-log",help="Summarize a completed or ongoing run log without reading media")
    audit.add_argument("log",type=Path,help="Log file produced by --log-file")
    sessions=sub.add_parser("sessions",help="Suggest cross-file sessions for review (never auto-stitch)")
    sessions.add_argument("--gap-minutes",type=float,default=15.0)
    studio=sub.add_parser('studio',help='Open local, private video review studio')
    studio.add_argument('--port',type=int,default=0,help='Loopback-only port; 0 chooses a free port')
    studio.add_argument('--no-browser',action='store_true',help='Print URL without opening a browser')
    review=sub.add_parser("review",help="Create a local HTML report for video/event review")
    review.add_argument("--output",type=Path)
    mark=sub.add_parser('mark',help='Append one human-reviewed activity/incident label')
    mark.add_argument('asset',help='Complete content SHA-256 ID')
    mark.add_argument('--kind',required=True,help='ski_lift, slow_flat_skiing, crash, fall, near_miss, etc.')
    mark.add_argument('--start',type=float,required=True,help='Start in source-video seconds')
    mark.add_argument('--end',type=float,required=True,help='End in source-video seconds')
    mark.add_argument('--note',default='',help='Short factual reviewer annotation')
    examples=sub.add_parser('examples',help='Versioned reviewed clip examples and audit')
    ex_sub=examples.add_subparsers(dest='example_action',required=True)
    add_ex=ex_sub.add_parser('add',help='Review a source-video interval')
    add_ex.add_argument('asset',help='Full source SHA-256')
    add_ex.add_argument('--start',type=float,required=True)
    add_ex.add_argument('--end',type=float,required=True)
    add_ex.add_argument('--label',required=True)
    add_ex.add_argument('--decision',required=True,
                        choices=['confirmed','rejected','uncertain'])
    add_ex.add_argument('--highlight',choices=['keep','skip','neutral'],default='neutral')
    add_ex.add_argument('--note',default='')
    list_ex=ex_sub.add_parser('audit',help='List latest reviews, revisions and conflicts')
    list_ex.add_argument('--asset')
    ex_sub.add_parser('evaluate',help='Audit scene predictions against held-out reviewed labels')
    scout=sub.add_parser('scout',help='Rank cached visual review and audio triage opportunities')
    scout.add_argument('--recipe',choices=list(RECIPES),default='best-sequences')
    scout.add_argument('--provider',choices=['siglip2','histogram','stack'],
                       default='siglip2')
    scout.add_argument('--top',type=int,default=20)
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
    plan.add_argument('--diversity',choices=['none','visual'],default='none')
    plan.add_argument('--provider',choices=['siglip2','histogram','stack'],
                      default='siglip2')
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
        if args.command=="prepare-nim-benchmark":
            _print(prepare_benchmark(root, output_dir=args.output_dir,
                  sample_count=args.samples,seed=args.seed,
                  window_ms=args.window_seconds*1000,
                  decode_frames=args.decode_frames))
            return 0
        if args.command=="judge-nim":
            manifest=json.loads(args.manifest.expanduser().read_text(encoding="utf-8"))
            report=evaluate_manifest(
                manifest,frames_dir=args.frames_dir,models=args.model,
                allow_external_upload=args.allow_external_upload,
                endpoint=args.endpoint, max_calls=args.max_calls,
                api_key=(os.environ.get("NVIDIA_API_KEY","")
                         if args.allow_external_upload else ""))
            if args.output:
                destination=args.output.expanduser().resolve()
                if destination.exists() or destination==args.manifest.expanduser().resolve():
                    raise ValueError("Judge report output already exists or overlaps the manifest")
                destination.parent.mkdir(parents=True,exist_ok=True)
                with destination.open("x",encoding="utf-8") as f:
                    f.write(json.dumps(report,indent=2,allow_nan=False)+"\n")
            _print(report)
            return 0
        if args.command=="audit-log":
            _print(audit_log(args.log))
            return 0
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
        elif args.command=='diagnose':
            with lock_workspace(root):
                _print(write_diagnostics(root,asset_id=args.asset))
        elif args.command=="analyze":
            with lock_workspace(root):
                result=analyze(root,args.limit,args.force,args.detector)
                _print(result)
                if result["failed"]:
                    return 1
        elif args.command=='mark':
            with lock_workspace(root):
                _print(append_review_label(root,args.asset,args.kind,args.start,
                                           args.end,note=args.note))
        elif args.command=='examples':
            if args.example_action=='add':
                with lock_workspace(root):
                    _print(review_example(root,asset_id=args.asset,
                        start_seconds=args.start,end_seconds=args.end,
                        label=args.label,decision=args.decision,
                        highlight=args.highlight,note=args.note))
            elif args.example_action=='audit':
                _print(review_inventory(root,asset_id=args.asset))
            else:
                _print(review_quality_report(root))
        elif args.command=='scout':
            if args.top<1 or args.top>200:
                raise ValueError('--top must be 1..200')
            with connect(root) as db:
                rule=RECIPES[args.recipe]
                assets={r['id']:r['activity'] for r in db.execute(
                    'SELECT id,activity FROM assets')}
                rows=[dict(r) for r in db.execute('SELECT * FROM episodes')
                      if r['kind'] in rule['kinds'] and
                      (rule['activity'] is None or
                       assets.get(r['asset_id'])==rule['activity'])]
                candidates,_=editorial_candidates(db,root,rows,policy='suggest')
                enriched,summary=enrich_candidates(db,root,candidates,
                                                     provider=args.provider)
            enriched.sort(key=lambda c:(
                -(c['score']+.05*bool(c['audioTriage'].get('suggestVisualInspection'))),
                c['sourceAssetId'],c['startMs']))
            _print({'schemaVersion':1,'summary':summary,
                    'count':len(enriched),
                    'priorityWindows':[{
                        k:v for k,v in c.items() if not k.startswith('_')}
                        for c in enriched[:args.top]],
                    'warning':'Review priorities, not classifications; no source video modified'})
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
        elif args.command=='studio':
            serve_studio(root,port=args.port,open_browser=not args.no_browser)
        elif args.command=="review":
            _print(write_report(root,args.output or root/"exports"/"review.html"))
        elif args.command=="plan":
            doc=plan_storyboard(root,args.recipe,args.seconds,args.style,
                                ranking=args.ranking,context_policy=args.context_policy,
                                diversity=args.diversity,provider=args.provider)
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
