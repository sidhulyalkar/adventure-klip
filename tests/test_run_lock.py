from __future__ import annotations

import subprocess
import sys
import textwrap
import time

import pytest

from adventure_intelligence import run_lock


def test_second_process_is_refused_or_waits_and_nesting_is_reentrant(tmp_path):
    holder = subprocess.Popen([sys.executable, "-c", textwrap.dedent(f"""
        import time
        from pathlib import Path
        from adventure_intelligence import run_lock
        with run_lock.exclusive(folder=Path({str(tmp_path)!r})):
            print("held", flush=True)
            time.sleep(1.5)
    """)], stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "held"
    with pytest.raises(RuntimeError, match="Another model run holds ollama.lock"):
        with run_lock.exclusive(folder=tmp_path, wait=False):
            pass
    began = time.monotonic()
    messages = []
    with run_lock.exclusive(folder=tmp_path, poll_seconds=0.1, log=messages.append):
        assert time.monotonic() - began > 0.5            # waited for the holder to finish
        with run_lock.exclusive(folder=tmp_path, wait=False):   # nested: no deadlock
            pass
    assert messages and messages[0].startswith("waiting for the model lock (pid")
    holder.wait(timeout=10)
    with run_lock.exclusive(folder=tmp_path, wait=False):  # free again
        pass
