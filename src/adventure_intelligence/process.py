"""Sequential, restartable folder processing using the workspace writer lock."""
from .analyze import analyze
from .indexing import index_folder
from .locking import lock_workspace
from .timeline import generate_timelines


def process_folder(source, workspace, *, activity='unknown', detector='baseline',
                   allow_removable=False):
    # Validate before taking the lock: never create a workspace inside the source.
    index_folder(source, workspace, activity=activity, dry_run=True,
                 allow_removable=allow_removable)
    with lock_workspace(workspace):
        indexed = index_folder(source, workspace, activity=activity,
                               allow_removable=allow_removable)
        asset_ids = set(indexed['asset_ids'])
        analysis = analyze(workspace, detector_name=detector, asset_ids=asset_ids)
        timelines = generate_timelines(workspace, asset_ids=asset_ids,
            source='optical-flow' if detector=='flow' else 'pixel-change')
    ok = not (indexed['errors'] or analysis['failed'] or timelines['failed'] or timelines['missing'])
    return {'status': 'completed' if ok else 'partial_failure', 'index': indexed,
            'analysis': analysis, 'timelines': timelines}
