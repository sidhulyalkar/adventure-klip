"""Read-only scan-pass receipt numbering tests."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import pytest

MODULE=Path(__file__).resolve().parents[1] / "scripts" / "klip_scan_pass_index.py"
spec=importlib.util.spec_from_file_location("klip_scan_pass_index", MODULE)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_empty_campaign_starts_at_one(tmp_path):
    assert mod.next_pass_index(tmp_path) == 1


def test_existing_partial_and_complete_passes_advance_without_overwrite(tmp_path):
    (tmp_path / "scan-pass-001.json").write_text("{}")
    (tmp_path / "scan-pass-002.stderr.log").write_text("in progress")
    (tmp_path / "scan-pass-002.json").write_text("{}")
    (tmp_path / "scan-pass-010.stderr.log").write_text("error")
    (tmp_path / "scan-pass-other.txt").write_text("unrelated")
    assert mod.next_pass_index(tmp_path) == 11
    assert (tmp_path / "scan-pass-001.json").read_text() == "{}"


def test_receipt_symlink_fails_closed(tmp_path):
    original=tmp_path / "real.log"
    original.write_text("evidence")
    (tmp_path / "scan-pass-001.stderr.log").symlink_to(original)
    with pytest.raises(ValueError, match="symlink"):
        mod.next_pass_index(tmp_path)


def test_missing_and_symlinked_report_root_fail(tmp_path):
    with pytest.raises(ValueError, match="missing"):
        mod.next_pass_index(tmp_path / "does-not-exist")
    root=tmp_path / "results"
    root.mkdir()
    symlink=tmp_path / "link"
    symlink.symlink_to(root)
    with pytest.raises(ValueError, match="symlink"):
        mod.next_pass_index(symlink)
