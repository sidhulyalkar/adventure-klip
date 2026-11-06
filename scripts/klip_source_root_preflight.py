#!/usr/bin/env python3
"""Safe G1 source-root preflight for a Klip catalog.

The default inventory reads SQLite only. An explicitly selected healthy root
is enumerated for filename/size *hypotheses*; NO camera file contents are read.
Neither an original nor the catalog is modified. SHA verification and remapping
remain the responsibility of `klip index` on the trusted root.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import sys
from urllib.parse import quote

VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".m4v"})


def lexical(path: str | Path) -> Path:
    """Do not chase stale removable-source paths."""
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def location_class(stored: str, workspace: Path) -> str:
    if not isinstance(stored, str) or not stored.startswith("/"):
        return "invalid_catalog_path"
    path = lexical(stored)
    if path.is_relative_to(workspace / "originals"):
        return "managed_original_unverified_now"
    if path.is_relative_to(Path("/Volumes")):
        return "mounted_external_unverified"
    if path.is_relative_to(workspace):
        return "workspace_other_unverified"
    return "other_unverified"


def read_catalog(workspace: Path) -> list[dict]:
    catalog = workspace / "catalog.sqlite3"
    if not catalog.is_file():
        raise ValueError(f"Klip catalog not found: {catalog}")
    uri = "file:" + quote(str(catalog), safe="/") + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT id,sha256,stored_path,size_bytes,duration FROM assets ORDER BY id"
        ).fetchall()
    return [dict(row) for row in rows]


def trusted_root(raw: str | Path, workspace: Path, allow_mounted_ssd: bool) -> Path:
    """Reject broad, placeholder, workspace and unacknowledged mounted roots.

    This is only called for the user's separately selected, purportedly healthy
    root. Never call it on historical stored_path values from the catalog.
    """
    path = lexical(raw)
    home = lexical(Path.home())
    if (path in {Path("/"), Path("/Volumes"), home}
            or home.is_relative_to(path)
            or path == workspace or workspace.is_relative_to(path)
            or path.is_relative_to(workspace)):
        raise ValueError("Select a specific footage folder outside the Klip workspace and home root")
    if path.is_relative_to(Path("/Volumes")) and not allow_mounted_ssd:
        raise ValueError("A mounted SSD requires --allow-mounted-ssd; never select a stalled SDXC card")
    # Resolution and filesystem checks are allowed only after the lexical gate.
    real = path.resolve(strict=True)
    ws_real = workspace.resolve()
    if real in {Path("/"), Path("/Volumes"), home} or ws_real.is_relative_to(real) or real.is_relative_to(ws_real):
        raise ValueError("Root resolves to a broad or overlapping directory")
    if real.is_relative_to(Path("/Volumes")) and not allow_mounted_ssd:
        raise ValueError("Root resolves onto mounted storage; requires --allow-mounted-ssd")
    if not real.is_dir():
        raise ValueError(f"Healthy footage root is not a directory: {path}")
    return real


def candidates_under(root: Path):
    """Only stat entries in the explicitly selected root, without following links.

    Nested mountpoints are refused to avoid wandering into another mounted card.
    This is an inventory, not proof the bytes can be read or decoded.
    """
    device = os.lstat(root).st_dev
    for current, dirs, files in os.walk(root, followlinks=False):
        keep = []
        for name in sorted(dirs):
            item = Path(current) / name
            info = os.lstat(item)
            if stat.S_ISDIR(info.st_mode) and info.st_dev == device:
                keep.append(name)
        dirs[:] = keep
        for name in sorted(files):
            if name.startswith("._") or Path(name).suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            p = Path(current) / name
            info = os.lstat(p)
            if stat.S_ISREG(info.st_mode) and info.st_dev == device:
                yield p, info.st_size


def build_report(workspace: Path, root: Path | None = None) -> dict:
    rows = read_catalog(workspace)
    path_classes = Counter(location_class(r["stored_path"], workspace) for r in rows)
    catalog_bytes = sum(r["size_bytes"] for r in rows if isinstance(r["size_bytes"], int) and r["size_bytes"] > 0)
    report = {
        "version": "klip-source-root-preflight-v1",
        "createdUtc": datetime.now(timezone.utc).isoformat(),
        "catalogAssetCount": len(rows),
        "catalogSourceBytes": catalog_bytes,
        "catalogPathClasses": dict(sorted(path_classes.items())),
        "catalogOriginalsStatOrOpened": False,
        "sourceContentHashed": False,
        "sourceMediaDecoded": False,
        "catalogModified": False,
        "sourceRootExplicitlySelected": root is not None,
        "remapComplete": False,
        "scanReady": False,
    }
    if root is None:
        report["nextStep"] = "Select a known healthy LOCAL footage folder; then run preflight with --trusted-root"
        return report
    by_name_size = defaultdict(set)
    for row in rows:
        size = row["size_bytes"]
        if isinstance(size, int) and size > 0:
            by_name_size[(Path(row["stored_path"]).name.casefold(), size)].add(row["id"])
    matched_ids = set()
    potential = []
    filenames = 0
    total_bytes = 0
    ambiguous = 0
    for path, nbytes in candidates_under(root):
        filenames += 1
        total_bytes += nbytes
        ids = sorted(by_name_size.get((path.name.casefold(), nbytes), ()))
        if ids:
            matched_ids.update(ids)
            ambiguous += len(ids) > 1
            potential.append({"path": str(path), "sizeBytes": nbytes,
                              "possibleSourceIds": ids, "shaVerified": False})
    free_bytes = shutil.disk_usage(workspace).free
    report.update({
        "trustedRoot": str(root),
        "localVideoFilesEnumerated": filenames,
        "localVideoBytesEnumerated": total_bytes,
        "filenameSizeMatchingFiles": len(potential),
        "potentialCatalogSourceCount": len(matched_ids),
        "ambiguousFilenameSizeMatches": ambiguous,
        "matchedCandidatesNotVerified": potential,
        "workspaceFreeBytes": free_bytes,
        "catalogCopyWouldFitWorkspaceAtThisInstant": catalog_bytes <= free_bytes,
        "note": "Filename and size are only discovery clues; renamed files can match by SHA despite zero filename hits.",
        "nextStep": "If this is a healthy local source, run `klip --workspace WS index ROOT --dry-run` then `klip --workspace WS index ROOT`; inspect errors and re-audit into a NEW campaign directory.",
    })
    return report


def write_report(workspace: Path, output: str | None, report: dict) -> None:
    if output is None:
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    dest = lexical(output)
    reports = workspace / "reports"
    if not dest.is_relative_to(reports) or dest == reports:
        raise ValueError("Preflight output must be a new JSON file under WORKSPACE/reports")
    reports.mkdir(parents=True, exist_ok=True)
    # Parent path must not redirect outside the private workspace.
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.parent.resolve().is_relative_to(reports.resolve()):
        raise ValueError("Preflight output parent resolves outside workspace reports")
    data = (json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    with dest.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    print(json.dumps({"report": str(dest), "catalogAssets": report["catalogAssetCount"],
                      "shaVerified": False}, indent=2))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--trusted-root", type=Path,
                        help="An existing, healthy local footage folder (never a stalled card)")
    parser.add_argument("--allow-mounted-ssd", action="store_true",
                        help="Explicitly authorize inventory of an already-healthy mounted SSD")
    parser.add_argument("--output", help="Optional new JSON report within workspace/reports")
    args = parser.parse_args(argv)
    try:
        ws = lexical(args.workspace)
        root = (trusted_root(args.trusted_root, ws, args.allow_mounted_ssd)
                if args.trusted_root is not None else None)
        report = build_report(ws, root)
        write_report(ws, args.output, report)
        return 0
    except (OSError, ValueError, sqlite3.DatabaseError) as exc:
        print(f"SOURCE PREFLIGHT BLOCKED: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
