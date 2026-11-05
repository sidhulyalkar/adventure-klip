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
from .nim_calibration import calibrate_workspace
from .history import event_history
from .safety import protect_source_output
from .progress import Progress
from .adaptive_sampling import build_plan, validate_plan, source_preflight
from .ecology_context import build_ecology_context
from .ecology_import import import_export
from .ecology_encounters import build_encounter_report
from .ecology_crops import create_crop_packet
from .activity_intelligence import run_activity_scan
from .activity_apply import apply_activity_proposals
from .activity_refinement import refine_activities
from .activity_confirm import confirm_activity
from .segment_workflow import scan_segments
from .semantic_recovery import recover_semantic_frames
from .event_context import build_event_context
from .moment_ranking import create_rank_report
from .archive_pilot import run_archive_pilot
from .archive_status import inspect_archive_campaign, write_archive_status
from .archive_review import seed_archive_reviews
from .archive_local_triage import run_local_triage
from .archive_temporal_look import inspect_temporal_candidates
from .judge_audit import compare_judge_policies, save_judge_audit
from .archive_editorial_audit import audit_editorial_evidence, write_editorial_audit
from .archive_value_study import save_value_study
from .social_action_lens import save_training, save_feature_template, save_comparison
from .social_corpus_onboarding import init_corpus, preflight_corpus
from .social_temporal_evidence import save_temporal_evidence
from .social_blind_pairs import write_pair_study, write_pair_results
from .social_scene_stories import save_scene_stories
from .social_workflow_status import inspect_story_workflow
from .social_story_counter_audit import save_counter_audit
from .benchmark_feedback import write_benchmark_feedback,import_human_reviews
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
    process.add_argument('--semantic-backend',choices=['histogram','siglip2','owlv2','ecology-owlv2','context','stack'])
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
    archive_import=sub.add_parser("archive-review-import",
        help="Atomically import offline benchmark ratings into append-only private review revisions")
    archive_import.add_argument("--assignments",type=Path,required=True)
    archive_import.add_argument("--reviews",type=Path,required=True,
        help="Local JSON exported by the offline benchmark review gallery")
    archive_results=sub.add_parser("archive-review-results",
        help="Evaluate saved human archive reviews without reading source videos")
    archive_results.add_argument("--assignments",required=True,type=Path,
        help="Versioned archive review assignments.json")
    archive_results.add_argument("--output-dir",required=True,type=Path,
        help="New private evaluation report folder")
    social_doctor=sub.add_parser("social-lens-workflow-status",
        help="Read-only diagnosis of personal feature, temporal and blind pair artifacts; no models or media")
    social_doctor.add_argument("--features",type=Path,required=True,
        help="Expected personal feature-template.json")
    social_doctor.add_argument("--temporal",type=Path,required=True,
        help="Expected temporal-evidence.json")
    social_doctor.add_argument("--pairs",type=Path,
        help="Optional exact pairs.json for pair-targeted scene story evaluation")
    social_doctor.add_argument("--study",type=Path,
        help="Optional archive-value-study study.json for exact next-step command guidance")
    social_stories=sub.add_parser("social-lens-stories",
        help="Independently reconstruct local scene narratives from verified stills and temporal caches")
    social_stories.add_argument("--features",type=Path,required=True,
        help="Exact private personal feature-template used for temporal analysis")
    social_stories.add_argument("--temporal",type=Path,required=True,
        help="Completed matching social-lens-temporal --run report")
    social_stories.add_argument("--pairs",type=Path,
        help="Optional frozen blind pairs.json: target their clips without showing model scores")
    social_stories.add_argument("--model",default="gemma3:4b",
        help="Installed image-capable local Ollama model name")
    social_stories.add_argument("--limit",type=int,default=8,
        help="Maximum scene stories per batch (1..24); run bounded pilot first")
    social_stories.add_argument("--offset",type=int,default=0,
        help="Deterministic start index for later bounded batches; first batch is 0")
    social_stories.add_argument("--run",action="store_true",
        help="Opt in to local Ollama inference over 7 verified preview frames; default is dry run")
    social_stories.add_argument("--output-dir",type=Path,required=True,
        help="NEW private reports/ or exports/ directory")
    social_story_audit=sub.add_parser("social-lens-story-counter-audit",
        help="Reveal scene-story counter-evidence only after A/B human votes are recorded")
    social_story_audit.add_argument("--pairs",type=Path,required=True)
    social_story_audit.add_argument("--reviews",type=Path,required=True,
        help="Previously exported A/B preference review JSON")
    social_story_audit.add_argument("--stories",type=Path,required=True,
        help="First narrative stories.json from local inference")
    social_story_audit.add_argument("--additional-stories",type=Path,action="append",
        default=[],help="Additional matching stories.json batches; repeat for each new offset")
    social_story_audit.add_argument("--output-dir",type=Path,required=True)
    social_pairs=sub.add_parser("social-lens-pair-review",
        help="Build a locally playable, score-blinded A/B choice study from measured verified candidates")
    social_pairs.add_argument("--study",required=True,type=Path,
        help="Existing archive-value-study study.json")
    social_pairs.add_argument("--features",required=True,type=Path,
        help="Exact personal feature-template used for temporal extraction")
    social_pairs.add_argument("--temporal",required=True,type=Path,
        help="Completed temporal-evidence.json from a --run analysis")
    social_pairs.add_argument("--output-dir",required=True,type=Path,
        help="NEW reports/ or exports/ folder")
    social_pairs.add_argument("--max-pairs",type=int,default=20,
        help="Maximum same-sport, different-source blind comparisons (1..40)")
    social_pairs.add_argument("--seed",type=int,default=104,
        help="Deterministic candidate and A/B randomization")
    social_pair_results=sub.add_parser("social-lens-pair-results",
        help="Evaluate downloaded private A/B preference votes without changing models or source media")
    social_pair_results.add_argument("--manifest",required=True,type=Path,
        help="Frozen pairs.json in the private pair-review report directory")
    social_pair_results.add_argument("--reviews",required=True,type=Path,
        help="Exported review JSON from the browser; may reside in Downloads")
    social_pair_results.add_argument("--output-dir",required=True,type=Path,
        help="NEW reports/ or exports/ folder")
    social_temporal=sub.add_parser("social-lens-temporal",
        help="Plan or run bounded raw-video temporal measurements from verified Studio H264 previews")
    social_temporal.add_argument("--features",type=Path,required=True,
        help="Existing social-lens-feature-template file, NOT external social video corpus")
    social_temporal.add_argument("--output-dir",type=Path,required=True,
        help="NEW reports/ or exports/ directory")
    social_temporal.add_argument("--limit",type=int,default=40,
        help="Maximum inspected candidate intervals (1..300)")
    social_temporal.add_argument("--run",action="store_true",
        help="Opt in to local FFmpeg decoding of existing verified H264 previews")
    social_temporal.add_argument("--prepare-previews",action="store_true",
        help="With --run only, checksum-verify and read original video to create absent H264 previews")
    social_init=sub.add_parser("social-lens-corpus-init",
        help="Create an EMPTY permissioned external social-reference corpus, never fake examples")
    social_init.add_argument("--output",type=Path,required=True,
        help="NEW .json file under workspace reports/ or exports/")
    social_preflight=sub.add_parser("social-lens-preflight",
        help="Audit external corpus existence, rights, observation completeness and matched pairs")
    social_preflight.add_argument("--corpus",type=Path,required=True,
        help="Local permissioned external social-video training JSON, NOT a Klip feature template")
    social_preflight.add_argument("--seed",type=int,default=23)
    social_train=sub.add_parser("social-lens-train",
        help="Train rights-gated local social action ranking head from permitted feature corpus")
    social_train.add_argument("--corpus",type=Path,required=True,
        help="Local JSON with documented creator/license permission and observed features")
    social_train.add_argument("--output-dir",type=Path,required=True,
        help="NEW local reports/ or exports/ folder")
    social_train.add_argument("--seed",type=int,default=23)
    social_template=sub.add_parser("social-lens-feature-template",
        help="Make a null-only common-rubric feature template for Klip candidates")
    social_template.add_argument("--study",type=Path,required=True,
        help="Archive-value-study study.json report")
    social_template.add_argument("--output-dir",type=Path,required=True)
    social_template.add_argument("--limit",type=int,default=300,
        help="Number of raw Klip candidates to request review for (1..300); fewer for a first pilot")
    social_compare=sub.add_parser("social-lens-compare",
        help="Compare frozen social head and Klip scores on exact same reviewed feature-complete moments")
    social_compare.add_argument("--model",type=Path,required=True)
    social_compare.add_argument("--study",type=Path,required=True)
    social_compare.add_argument("--features",type=Path,required=True)
    social_compare.add_argument("--output-dir",type=Path,required=True)
    social_compare.add_argument("--top-k",type=int,default=20)
    value_study=sub.add_parser("archive-value-study",
        help="Create a cache-only archive-wide candidate and blinded-control human study")
    value_study.add_argument("--output-dir",required=True,type=Path,
        help="NEW private reports/ or exports/ directory")
    value_study.add_argument("--max-assets",type=int,default=0,
        help="Metadata-ranked assets to analyze, 0 uses all accessible recordings")
    value_study.add_argument("--review-limit",type=int,default=96,
        help="Maximum blind candidate/control intervals for human review (1..500)")
    value_study.add_argument("--seed",type=int,default=104,
        help="Deterministic asset and control sampling seed")
    value_study.add_argument("--max-reel-seconds",type=int,default=900,
        help="Per-sport shortlist duration budget, not an export request")
    archive_audit=sub.add_parser("archive-judge-audit",
        help="Compare saved legacy vs adaptive-v2 Ollama suggestions with human decisions, cache only")
    archive_audit.add_argument("--assignments",required=True,type=Path)
    archive_audit.add_argument("--model",required=True)
    archive_audit.add_argument("--output-dir",type=Path,
        help="Optional new reports/ or exports/ folder for offline JSON+HTML audit")
    editorial_audit=sub.add_parser("archive-editorial-audit",
        help="Cache-only visual/relative-action quality audit; advisory deletion-review flags only")
    editorial_audit.add_argument("--assignments",required=True,type=Path)
    editorial_audit.add_argument("--model",default="gemma3:4b",
        help="Previously cached adaptive-v2 visual model identifier; never calls the model")
    editorial_audit.add_argument("--output-dir",type=Path,
        help="Optional NEW reports/ or exports/ directory for JSON and HTML")
    temporal=sub.add_parser("archive-temporal-look",
        help="Budgeted seven-frame second look at locally verified short previews; no original video reads")
    temporal.add_argument("--assignments",required=True,type=Path)
    temporal.add_argument("--model",required=True)
    temporal.add_argument("--limit",type=int,default=4)
    temporal.add_argument("--only-review-id",
        help="Investigate precisely one eligible frozen review interval")
    temporal.add_argument("--exclude-review-id",action="append",default=[],
        help="Omit this frozen ID from this run only; repeat to advance past failed items")
    temporal.add_argument("--run",action="store_true",
        help="Opt in to local Ollama inference using seven stills from verified cached H264 previews")
    archive_triage=sub.add_parser("archive-local-triage",
        help="Optionally judge a bounded balanced set of frozen review intervals with an installed localhost vision model")
    archive_triage.add_argument("--assignments",required=True,type=Path)
    archive_triage.add_argument("--model",required=True,
        help="An explicitly installed local Ollama image-capable model")
    archive_triage.add_argument("--limit",type=int,default=8)
    archive_triage.add_argument("--only-review-id",
        help="Retry one exact unreviewed assignment; skip all other cached or completed intervals")
    archive_triage.add_argument("--exclude-review-id",action="append",default=[],
        help="Omit this frozen ID from this run only; not a label or permanent skip")
    archive_triage.add_argument("--judge-policy",choices=["legacy","adaptive-v2"],
        default="legacy",help="Opt-in adaptive evidence-rubric v2, separate from running legacy inference")
    archive_triage.add_argument("--run",action="store_true",
        help="Explicitly verify sources, prepare small H.264 previews and call Ollama on 127.0.0.1 only")
    archive_review=sub.add_parser("archive-review",
        help="Create a balanced source-grounded candidate and control review benchmark from frozen batches")
    archive_review.add_argument("--campaign",type=Path,
        help="Frozen campaign root; default reports/archive-pilot-v1")
    archive_review.add_argument("--output-dir",required=True,type=Path,
        help="New private reports/ or exports/ folder; never overwrites earlier reviews")
    archive_review.add_argument("--max-assets",type=int,default=32)
    archive_review.add_argument("--window-seconds",type=int,default=14)
    archive_status=sub.add_parser("archive-status",
        help="Read-only diagnosis of frozen archive batches, frame gaps and recoverable checkpoints")
    archive_status.add_argument("--campaign",type=Path,
        help="Frozen campaign folder (default: workspace reports/archive-pilot-v1)")
    archive_status.add_argument("--provider",choices=["histogram","siglip2","owlv2",
        "ecology-owlv2","context"],default="histogram")
    archive_status.add_argument("--output-dir",type=Path,
        help="Optional new reports/ or exports/ directory for offline HTML and JSON")
    archive=sub.add_parser("archive-pilot",
        help="Automatically inventory, select and plan a frozen stratified archive batch; --run executes cheap histogram inference")
    archive.add_argument("--legacy",type=Path,
        help="Optional read-only comparison with a second Klip workspace; never merges databases")
    archive.add_argument("--batch",type=int,default=1,
        help="Frozen batch number, starting at 1; rerun the same number to resume")
    archive.add_argument("--batch-size",type=int,default=8,
        help="Videos per pilot batch (1..16)")
    archive.add_argument("--batch-count",type=int,default=1,
        help="Run or plan several consecutive frozen batches (1..8); stops on first error")
    archive.add_argument("--coverage-seconds",type=float,default=30)
    archive.add_argument("--max-frames",type=int,default=24)
    archive.add_argument("--output-dir",type=Path,
        help="Optional NEW batch folder under workspace reports/ or exports/")
    archive.add_argument("--run",action="store_true",
        help="Explicitly run inexpensive histogram semantics for the frozen batch")
    adaptive=sub.add_parser("adaptive-plan",
        help="Plan budgeted POV sampling from cached evidence without decoding")
    adaptive.add_argument("--output",type=Path,required=True,
        help="New JSON plan inside workspace exports/ or reports/")
    adaptive.add_argument("--limit",type=int,default=8,
        help="Pilot size; use --limit 0 only if you explicitly want the full library")
    adaptive.add_argument("--activity",choices=["all","unknown","ski","bike","snorkeling"],default="all")
    adaptive.add_argument("--asset",action="append",
        help="Optional exact source SHA-256 to plan; repeat for up to 16 verified assets")
    adaptive.add_argument("--coverage-seconds",type=float,default=45)
    adaptive.add_argument("--interest-seconds",type=float,default=2)
    adaptive.add_argument("--max-frames",type=int,default=128)
    adaptive_run=sub.add_parser("adaptive-scenes",
        help="Run opt-in semantic inference for a verified adaptive plan")
    adaptive_run.add_argument("--plan",type=Path,required=True)
    adaptive_run.add_argument("--backend",choices=["histogram","siglip2","owlv2","ecology-owlv2","context","stack"],
                              default="histogram")
    adaptive_run.add_argument("--decode-mode",choices=["single","batched"],default="single",
        help="Opt-in grouped FFmpeg seeks for nearby frames; falls back to single seeks")
    activity_scan=sub.add_parser("activity-scan",
        help="Sparse, cached-first activity proposals for mixed unknown POV recordings")
    activity_scan.add_argument("--output-dir",required=True,type=Path,
        help="Private resumable report directory inside workspace reports/ or exports/")
    activity_scan.add_argument("--frames",type=int,default=5,
        help="5..9 spatially distributed video samples")
    activity_scan.add_argument("--limit",type=int,default=0,
        help="Limit newly scanned records; 0 means all catalog assets")
    activity_scan.add_argument("--allow-model-download",action="store_true",
        help="Explicit permission to download SigLIP2 model weights if not cached")
    rank_moments=sub.add_parser("rank-moments",
        help="Explainable multi-objective moment triage from cached event manifest or reviewed features")
    rank_moments.add_argument("--input",required=True,type=Path)
    rank_moments.add_argument("--output-dir",required=True,type=Path)
    rank_moments.add_argument("--reviews",type=Path,
        help="Optional curated JSON overrides; only human/validated evidence, no species guesses")
    rank_moments.add_argument("--limit",type=int,default=20)
    rank_moments.add_argument("--unbalanced",action="store_true",
        help="Disable round-robin sport exposure; preliminary scores are uncalibrated")
    semantic_recover=sub.add_parser("semantic-recover",
        help="Recover checksum-verified partial marine frame evidence from a failed adaptive job")
    semantic_recover.add_argument("--asset",required=True)
    semantic_recover.add_argument("--plan",required=True,type=Path)
    semantic_recover.add_argument("--provider",choices=["histogram","ecology-owlv2","owlv2","siglip2","context"],
                                  default="ecology-owlv2")
    semantic_recover.add_argument("--max-missing",type=int,default=2)
    semantic_recover.add_argument("--min-fraction",type=float,default=.80)
    event_context=sub.add_parser("event-context",
        help="Create bounded before/during/after semantic embeddings from verified marine boxes")
    event_context.add_argument("--asset",required=True)
    event_context.add_argument("--output-dir",required=True,type=Path)
    event_context.add_argument("--max-events",type=int,default=4)
    event_context.add_argument("--allow-model-download",action="store_true")
    segments=sub.add_parser("segments",
        help="Sparse sport-specific scene segments, boundary investigation and review gallery")
    segments.add_argument("--asset",required=True,
        help="Exact indexed SHA-256 of one source video")
    segments.add_argument("--activity",choices=["auto","snorkeling","ski","bike"],default="auto",
        help="Catalog activity by default; explicit activity is an unverified hypothesis for unknown videos")
    segments.add_argument("--output-dir",type=Path,required=True,
        help="New private directory in workspace reports/ or exports/")
    segments.add_argument("--step-seconds",type=int,default=15)
    segments.add_argument("--max-frames",type=int,default=64)
    segments.add_argument("--refine-frames",type=int,default=16)
    segments.add_argument("--allow-model-download",action="store_true")
    activity_refine=sub.add_parser("activity-refine",
        help="Targeted sandy-seabed and surface-water rescoring; NEVER changes catalog labels")
    activity_refine.add_argument("--input-dir",type=Path,required=True,
        help="Existing completed first-pass activity report directory")
    activity_refine.add_argument("--output-dir",type=Path,required=True,
        help="Separate resumable report directory in workspace reports/ or exports/")
    activity_refine.add_argument("--mode",choices=["sampled","thumbnail"],default="sampled",
        help="Resample source frames or use only the cached single preview thumbnail")
    activity_refine.add_argument("--frames",type=int,default=5)
    activity_refine.add_argument("--limit",type=int,default=0)
    activity_refine.add_argument("--include-snorkeling-ambiguous",action="store_true")
    activity_refine.add_argument("--include-other-ambiguous",action="store_true")
    activity_refine.add_argument("--exclude-failed",action="store_true")
    activity_refine.add_argument("--allow-model-download",action="store_true")
    activity_confirm=sub.add_parser("activity-confirm",
        help="Explicit SHA-verified human confirmation of a single activity label")
    activity_confirm.add_argument("--asset",required=True)
    activity_confirm.add_argument("--activity",required=True,choices=["snorkeling","ski","bike"])
    activity_confirm.add_argument("--reviewed",action="store_true",
        help="Affirm you visually checked the source recording")
    activity_confirm.add_argument("--apply",action="store_true",
        help="Write one confirmed label after a SQLite backup")
    activity_apply=sub.add_parser("activity-apply",
        help="Preview or explicitly promote a few high-consensus activity proposals")
    activity_apply.add_argument("--report-dir",required=True,type=Path)
    activity_apply.add_argument("--activity",choices=["snorkeling","ski","bike"],
                                default="snorkeling")
    activity_apply.add_argument("--max-assets",type=int,default=2)
    activity_apply.add_argument("--minimum-margin",type=float,default=.065)
    activity_apply.add_argument("--minimum-votes",type=int,default=5)
    activity_apply.add_argument("--apply",action="store_true",
        help="Explicitly modify up to max-assets catalog labels with backup")
    ecology_import=sub.add_parser("ecology-import",
        help="Normalize a downloaded OBIS/GBIF occurrence JSON locally (no network)")
    ecology_import.add_argument("--provider",choices=["obis","gbif"],required=True)
    ecology_import.add_argument("--input",type=Path,required=True)
    ecology_import.add_argument("--retrieved-at",required=True)
    ecology_import.add_argument("--citation",required=True)
    ecology_import.add_argument("--license",required=True)
    ecology_import.add_argument("--group-map",type=Path,
        help="Optional explicit taxonId-to-biological-group JSON mapping")
    ecology_import.add_argument("--output",type=Path,required=True)
    ecology=sub.add_parser("ecology-context",
        help="Create a local, unverified marine taxa shortlist from a frozen occurrence snapshot")
    ecology.add_argument("--asset",required=True,
                         help="Exact indexed SHA-256 asset ID (snorkeling)")
    ecology.add_argument("--site",type=Path,required=True,
                         help="Explicit local site JSON; never uploaded")
    ecology.add_argument("--snapshot",type=Path,required=True,
                         help="Frozen normalized occurrence JSON (OBIS/GBIF exports)")
    ecology.add_argument("--radius-km",type=float,default=30)
    ecology.add_argument("--top",type=int,default=30)
    ecology.add_argument("--taxonomy",type=Path,
        help="Optional private, cited, local-only taxon/synonym crosswalk")
    ecology.add_argument("--with-encounters",action="store_true",
        help="Include unverified cached object-track review suggestions")
    encounters=sub.add_parser("ecology-encounters",
        help="Generate bounded marine encounter proposals from cached object boxes")
    encounters.add_argument("--asset",required=True,help="Existing snorkeling SHA-256 asset")
    encounters.add_argument("--max-results",type=int,default=40)
    encounters.add_argument("--max-gap-ms",type=int,default=5000)
    encounters.add_argument("--output",type=Path,required=True)
    crops=sub.add_parser("ecology-crops",
        help="Opt-in re-decode a few exact PTS-backed object boxes into private crop evidence")
    crops.add_argument("--asset",required=True)
    crops.add_argument("--output-dir",type=Path,required=True,
        help="NEW private folder under workspace reports/ or exports/")
    crops.add_argument("--max-events",type=int,default=6)
    crops.add_argument("--crops-per-event",type=int,default=3)
    crops.add_argument("--width",type=int,choices=[768,1536,2048],default=1536)
    crops.add_argument("--margin",type=float,default=.4)
    ecology.add_argument("--with-cached-siglip2",action="store_true",
        help="Optional local-only text retrieval over matching cached image embeddings")
    ecology.add_argument("--output",type=Path,required=True,
                         help="NEW workspace exports/ or reports/ JSON")
    scene=sub.add_parser("scenes",help="Extract optional sparse image embeddings / detections")
    scene.add_argument("--backend",choices=["histogram","siglip2","owlv2","ecology-owlv2","context","stack"],default="histogram")
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
    cal=sub.add_parser("calibrate-nim",help="Compare jury scores to explicit local human reviews")
    cal.add_argument("manifest",type=Path)
    cal.add_argument("jury_report",type=Path)
    audit=sub.add_parser("audit-log",help="Summarize a completed or ongoing run log without reading media")
    audit.add_argument("log",type=Path,help="Log file produced by --log-file")
    sessions=sub.add_parser("sessions",help="Suggest cross-file sessions for review (never auto-stitch)")
    sessions.add_argument("--gap-minutes",type=float,default=15.0)
    studio=sub.add_parser('studio',help='Open local, private video review studio')
    studio.add_argument('--port',type=int,default=0,help='Loopback-only port; 0 chooses a free port')
    studio.add_argument('--no-browser',action='store_true',help='Print URL without opening a browser')
    studio.add_argument('--benchmark',type=Path,
        help='Launch directly into source-backed archive review using assignments.json')
    studio.add_argument('--local-judge-model',
        help='Explicit opt-in: use an already installed Ollama vision model on localhost only')
    studio.add_argument('--judge-policy',choices=['legacy','adaptive-v2'],
        default='legacy',help='Use separate context-aware v2 rubric/cache; legacy remains unchanged')
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
    render.add_argument("--transition",choices=["cut","fade","smart"],default="cut",
                        help="smart uses explicit per-join transition suggestions")
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
        if args.command=="calibrate-nim":
            _print(calibrate_workspace(root,
                json.loads(args.manifest.expanduser().read_text(encoding="utf-8")),
                json.loads(args.jury_report.expanduser().read_text(encoding="utf-8"))))
            return 0
        if args.command=="prepare-nim-benchmark":
            _print(prepare_benchmark(root, output_dir=args.output_dir,
                  sample_count=args.samples,seed=args.seed,
                  window_ms=args.window_seconds*1000,
                  decode_frames=args.decode_frames))
            return 0
        if args.command=="judge-nim":
            manifest_path=args.manifest.expanduser().resolve()
            manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
            destination=args.output.expanduser().resolve() if args.output else None
            approved_dir=manifest_path.parent
            if destination is not None and (
                    destination.exists() or not destination.is_relative_to(approved_dir)
                    or destination==manifest_path):
                raise ValueError("Judge report must be a new file inside the manifest directory")
            checkpoint=approved_dir/"jury-checkpoint.jsonl" if args.allow_external_upload else None
            if destination==checkpoint:
                raise ValueError("Report output cannot overwrite the judge checkpoint")
            report=evaluate_manifest(
                manifest,frames_dir=args.frames_dir,models=args.model,
                allow_external_upload=args.allow_external_upload,
                endpoint=args.endpoint, max_calls=args.max_calls,
                checkpoint_file=checkpoint,
                api_key=(os.environ.get("NVIDIA_API_KEY","")
                         if args.allow_external_upload else ""))
            if destination is not None:
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
        elif args.command=="archive-review-import":
            with lock_workspace(root):
                _print(import_human_reviews(root,args.assignments,args.reviews))
        elif args.command=="archive-review-results":
            _print(write_benchmark_feedback(root,args.assignments,args.output_dir))
        elif args.command=="social-lens-workflow-status":
            _print(inspect_story_workflow(root,args.features,args.temporal,
                pairs_file=args.pairs,study_file=args.study))
        elif args.command=="social-lens-stories":
            _print(save_scene_stories(root,args.features,args.temporal,args.output_dir,
                model=args.model,limit=args.limit,offset=args.offset,
                run=args.run,pairs_file=args.pairs))
        elif args.command=="social-lens-story-counter-audit":
            _print(save_counter_audit(root,args.pairs,args.reviews,args.stories,
                args.output_dir,additional_stories=tuple(args.additional_stories)))
        elif args.command=="social-lens-pair-review":
            _print(write_pair_study(root,args.study,args.features,
                args.temporal,args.output_dir,max_pairs=args.max_pairs,
                seed=args.seed))
        elif args.command=="social-lens-pair-results":
            _print(write_pair_results(root,args.manifest,args.reviews,
                args.output_dir))
        elif args.command=="social-lens-temporal":
            _print(save_temporal_evidence(root,args.features,args.output_dir,
                limit=args.limit,run=args.run,
                prepare_previews=args.prepare_previews))
        elif args.command=="social-lens-corpus-init":
            _print(init_corpus(root,args.output))
        elif args.command=="social-lens-preflight":
            _print(preflight_corpus(args.corpus,seed=args.seed))
        elif args.command=="social-lens-train":
            _print(save_training(root,args.corpus,args.output_dir,seed=args.seed))
        elif args.command=="social-lens-feature-template":
            _print(save_feature_template(root,args.study,args.output_dir,
                limit=args.limit))
        elif args.command=="social-lens-compare":
            _print(save_comparison(root,args.model,args.study,args.features,
                args.output_dir,top_k=args.top_k))
        elif args.command=="archive-value-study":
            _print(save_value_study(root,args.output_dir,seed=args.seed,
                max_assets=args.max_assets,review_limit=args.review_limit,
                max_reel_seconds=args.max_reel_seconds))
        elif args.command=="archive-judge-audit":
            if args.output_dir is None:
                _print(compare_judge_policies(root,args.assignments,model=args.model))
            else:
                _print(save_judge_audit(root,args.assignments,
                    model=args.model,output_dir=args.output_dir))
        elif args.command=="archive-editorial-audit":
            if args.output_dir is None:
                _print(audit_editorial_evidence(root,args.assignments,model=args.model))
            else:
                _print(write_editorial_audit(root,args.assignments,
                    args.output_dir,model=args.model))
        elif args.command=="archive-temporal-look":
            report=inspect_temporal_candidates(root,args.assignments,
                model=args.model,limit=args.limit,
                only_review_id=args.only_review_id,
                exclude_review_ids=tuple(args.exclude_review_id),run=args.run)
            _print(report)
            if report.get("status")=="partial_failure":
                return 1
        elif args.command=="archive-local-triage":
            result=run_local_triage(root,args.assignments,
                model=args.model,limit=args.limit,run=args.run,
                policy=args.judge_policy,only_review_id=args.only_review_id,
                exclude_review_ids=tuple(args.exclude_review_id))
            _print(result)
            if result.get("status")=="partial_failure":
                return 1
        elif args.command=="archive-review":
            campaign=args.campaign or root/"reports"/"archive-pilot-v1"
            _print(seed_archive_reviews(root,campaign,args.output_dir,
                max_assets=args.max_assets,window_seconds=args.window_seconds))
        elif args.command=="archive-status":
            campaign=args.campaign or root/"reports"/"archive-pilot-v1"
            report=inspect_archive_campaign(root,campaign,provider=args.provider)
            if args.output_dir is not None:
                report["saved"]=write_archive_status(root,report,args.output_dir)
            _print(report)
        elif args.command=="archive-pilot":
            if not 1<=args.batch_count<=8:
                raise ValueError("--batch-count must be between 1 and 8")
            if args.batch_count>1 and args.output_dir is not None:
                raise ValueError("--output-dir with multiple batches is ambiguous; use the default frozen campaign")
            results=[]
            for n in range(args.batch,args.batch+args.batch_count):
                report=run_archive_pilot(root,legacy=args.legacy,
                    batch=n,batch_size=args.batch_size,
                    step_seconds=args.coverage_seconds,
                    max_frames=args.max_frames,output_dir=args.output_dir,
                    run=args.run)
                results.append(report)
                if report["status"] in {"partial_failure","no_more_eligible_assets","no_eligible_assets"}:
                    break
            if len(results)==1:
                _print(results[0])
            else:
                _print({"version":"archive-pilot-orchestrator-v1",
                        "status":("partial_failure"
                                  if any(x["status"]=="partial_failure" for x in results)
                                  else "completed" if args.run else "planned"),
                        "batches":results,
                        "nextBatch":results[-1]["batch"]+1
                        if results[-1].get("batch") is not None
                        and results[-1]["status"] not in {"no_more_eligible_assets"}
                        else None})
            if any(x["status"]=="partial_failure" for x in results):
                return 1
        elif args.command=="adaptive-plan":
            destination=args.output.expanduser().resolve()
            if not destination.is_relative_to(root) or destination.exists():
                raise ValueError("Plan must be a NEW file inside the workspace")
            if destination.relative_to(root).parts[0] not in {"exports","reports"}:
                raise ValueError("Adaptive plans belong in workspace exports/ or reports/")
            pilot_limit=None if args.limit==0 else args.limit
            plan=build_plan(root,coverage_seconds=args.coverage_seconds,
                interest_seconds=args.interest_seconds,max_frames=args.max_frames,
                limit=pilot_limit,activity=args.activity,
                asset_ids=args.asset)
            if not plan["assets"]:
                raise ValueError("No valid-duration assets matched the activity")
            with connect(root) as db:
                protect_source_output(db,destination)
            destination.parent.mkdir(parents=True,exist_ok=True)
            with destination.open("x",encoding="utf-8") as handle:
                handle.write(json.dumps(plan,sort_keys=True,indent=2,allow_nan=False)+"\n")
            _print({"plan":str(destination),"planId":plan["planId"],
                    "totals":plan["totals"],"limitations":plan["limitations"]})
        elif args.command=="adaptive-scenes":
            plan=json.loads(args.plan.expanduser().read_text(encoding="utf-8"))
            schedule=validate_plan(plan)
            with connect(root) as db:
                catalog={row["id"]:int((row["duration"] or 0)*1000)
                         for row in db.execute("SELECT id,duration FROM assets")}
            if any(catalog.get(a["assetId"])!=a["durationMs"] for a in plan["assets"]):
                raise ValueError("Adaptive plan durations differ from the current workspace")
            preflight=source_preflight(root,set(schedule))
            if preflight["unavailable"]:
                raise ValueError("Adaptive source preflight failed for "
                                 f"{len(preflight['unavailable'])} asset(s); "
                                 "reconnect the external SSD and restore its original mount path")
            with lock_workspace(root):
                result=generate_semantics(root,backend=args.backend,
                    asset_ids=set(schedule),sample_schedule=schedule,
                    schedule_id=plan["planId"],progress=Progress(),
                     decode_mode=args.decode_mode)
            _print(result)
            if result["errors"]:
                return 1
        elif args.command=="activity-scan":
            with lock_workspace(root):
                _print(run_activity_scan(root,args.output_dir,
                    frames=args.frames,limit=args.limit,
                    allow_model_download=args.allow_model_download))
        elif args.command=="semantic-recover":
            with lock_workspace(root):
                _print(recover_semantic_frames(root,args.asset,args.plan,
                    provider=args.provider,max_missing=args.max_missing,
                    min_fraction=args.min_fraction))
        elif args.command=="rank-moments":
            with lock_workspace(root):
                _print(create_rank_report(root,args.input,args.output_dir,
                    review_file=args.reviews,limit=args.limit,
                    balance_sports=not args.unbalanced))
        elif args.command=="event-context":
            with lock_workspace(root):
                _print(build_event_context(root,args.asset,args.output_dir,
                    max_events=args.max_events,
                    allow_model_download=args.allow_model_download))
        elif args.command=="segments":
            with lock_workspace(root):
                _print(scan_segments(root,args.asset,args.output_dir,
                    activity=args.activity,step_seconds=args.step_seconds,
                    max_frames=args.max_frames,refine_frames=args.refine_frames,
                    allow_model_download=args.allow_model_download))
        elif args.command=="activity-refine":
            with lock_workspace(root):
                _print(refine_activities(root,args.input_dir,args.output_dir,
                    mode=args.mode,frames=args.frames,limit=args.limit,
                    include_snorkeling_ambiguous=args.include_snorkeling_ambiguous,
                    include_other_ambiguous=args.include_other_ambiguous,
                    include_failed=not args.exclude_failed,
                    allow_model_download=args.allow_model_download))
        elif args.command=="activity-confirm":
            _print(confirm_activity(root,args.asset,args.activity,
                reviewed=args.reviewed,apply=args.apply))
        elif args.command=="activity-apply":
            with lock_workspace(root):
                _print(apply_activity_proposals(root,args.report_dir,
                    activity=args.activity,max_assets=args.max_assets,
                    minimum_margin=args.minimum_margin,
                    minimum_votes=args.minimum_votes,apply=args.apply))
        elif args.command=="ecology-import":
            destination=args.output.expanduser().resolve()
            if not destination.is_relative_to(root) or destination.exists():
                raise ValueError("Ecology snapshot must be a NEW file inside the workspace")
            if destination.relative_to(root).parts[0] not in {"exports","reports"}:
                raise ValueError("Ecology snapshots must be in exports/ or reports/")
            with connect(root) as db:
                protect_source_output(db,destination)
            _print(import_export(args.input.expanduser(),args.provider,destination,
                retrieved_at=args.retrieved_at,citation=args.citation,
                license=args.license,
                group_map_path=args.group_map.expanduser() if args.group_map else None))
        elif args.command=="ecology-context":
            destination=args.output.expanduser().resolve()
            if not destination.is_relative_to(root) or destination.exists():
                raise ValueError("Ecology report must be a NEW file inside the workspace")
            if destination.relative_to(root).parts[0] not in {"exports","reports"}:
                raise ValueError("Ecology report must be in exports/ or reports/")
            with connect(root) as db:
                protect_source_output(db,destination)
            report=build_ecology_context(root,args.asset,args.site.expanduser(),
                args.snapshot.expanduser(),radius_km=args.radius_km,top=args.top,
                with_cached_siglip2=args.with_cached_siglip2,
                taxonomy_file=args.taxonomy.expanduser() if args.taxonomy else None,
                with_encounters=args.with_encounters)
            destination.parent.mkdir(parents=True,exist_ok=True)
            with destination.open("x",encoding="utf-8") as handle:
                handle.write(json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+"\n")
            _print({"report":str(destination),"assetId":report["assetId"],
                    "status":report["status"],"candidateCount":len(report["candidates"]),
                    "snapshotRecordCount":report.get("inputRecordCount",0),
                    "visualProposalGroups":sorted(report["broadVisualProposals"]),
                    "limitations":report["limitations"]})
        elif args.command=="ecology-encounters":
            destination=args.output.expanduser().resolve()
            if not destination.is_relative_to(root) or destination.exists():
                raise ValueError("Encounter report must be a NEW file inside workspace")
            if destination.relative_to(root).parts[0] not in {"exports","reports"}:
                raise ValueError("Encounter report must live in exports/ or reports/")
            with connect(root) as db:
                protect_source_output(db,destination)
            report=build_encounter_report(root,args.asset,
                max_results=args.max_results,max_gap_ms=args.max_gap_ms)
            destination.parent.mkdir(parents=True,exist_ok=True)
            with destination.open("x",encoding="utf-8") as handle:
                handle.write(json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+"\n")
            _print({"report":str(destination),"assetId":report["assetId"],
                "status":report["status"],"windowCount":len(report["candidateWindows"]),
                "audit":report["audit"]})
        elif args.command=="ecology-crops":
            with lock_workspace(root):
                _print(create_crop_packet(root,args.asset,args.output_dir,
                    max_events=args.max_events,crops_per_event=args.crops_per_event,
                    width=args.width,margin=args.margin))
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
            serve_studio(root,port=args.port,open_browser=not args.no_browser,
                         benchmark_file=args.benchmark,
                         local_judge_model=args.local_judge_model,
                         judge_policy=args.judge_policy)
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
