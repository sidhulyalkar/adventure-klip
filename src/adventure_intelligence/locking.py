"""Single writer across launchd/manual commands; POSIX flock (macOS/Linux)."""
from contextlib import contextmanager
from pathlib import Path
import fcntl

@contextmanager
def lock_workspace(root: Path):
    root.mkdir(parents=True,exist_ok=True)
    with (root/".adventure.lock").open("a+") as handle:
        fcntl.flock(handle.fileno(),fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
