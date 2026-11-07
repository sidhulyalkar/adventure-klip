#!/usr/bin/env python3
"""Pin an explicitly selected macOS external video volume across reconnects.

Reads macOS mount metadata via diskutil only; no video bytes, copies, hashes or
workspace catalog mutations. Requires a stable VolumeUUID or DiskUUID; mount
name or device number alone is never accepted as evidence of disk identity.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys


def lexical(value: str | Path) -> Path:
    return Path(os.path.abspath(os.path.expanduser(str(value))))


def volume_and_subpath(root: Path) -> tuple[Path, Path]:
    root = lexical(root)
    try:
        relative = root.relative_to("/Volumes")
    except ValueError as exc:
        raise ValueError("SSD guard requires a selected /Volumes/<name> footage root") from exc
    if not relative.parts:
        raise ValueError("Select one specific SSD volume, not /Volumes")
    if relative.parts[0] in (".", ".."):
        raise ValueError("Invalid SSD volume path")
    volume = Path("/Volumes") / relative.parts[0]
    # ismount makes the selected mountpoint fail closed if macOS has unmounted
    # the disk and left an empty directory at its former path.
    if not os.path.ismount(volume):
        raise ValueError(f"SSD is not currently mounted at {volume}")
    if not root.is_dir():
        raise ValueError(f"Selected video folder is missing: {root}")
    real = root.resolve(strict=True)
    if not real.is_relative_to(volume.resolve(strict=True)):
        raise ValueError("Video root resolves outside its selected SSD mount")
    return volume, relative.relative_to(relative.parts[0])


def get_disk_info(volume: Path) -> dict:
    try:
        run = subprocess.run(
            ["diskutil", "info", "-plist", str(volume)],
            capture_output=True, check=True, timeout=12)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"Cannot verify SSD mount using diskutil: {exc}") from exc
    try:
        info = plistlib.loads(run.stdout)
    except (ValueError, plistlib.InvalidFileException) as exc:
        raise ValueError("diskutil did not return a valid volume plist") from exc
    if not isinstance(info, dict):
        raise ValueError("Invalid diskutil volume metadata")
    return info


def mounted_identity(root: Path) -> dict:
    volume, relative = volume_and_subpath(root)
    info = get_disk_info(volume)
    uuid = info.get("VolumeUUID") or info.get("DiskUUID")
    if not isinstance(uuid, str) or not uuid.strip():
        raise ValueError("No stable VolumeUUID/DiskUUID reported; do not rely on the volume name alone")
    returned_mount = info.get("MountPoint")
    if returned_mount and lexical(returned_mount) != volume:
        raise ValueError("diskutil mount point does not match the selected volume")
    if info.get("Mounted") is False:
        raise ValueError("diskutil reports the volume is not mounted")
    return {
        "version": "klip-external-ssd-identity-v1",
        "volumeUuid": uuid.strip().lower(),
        "volumeName": volume.name,
        "mountPoint": str(volume),
        "footageRelativePath": str(relative),
        "footageRoot": str(lexical(root)),
        "diskutilDeviceAtCapture": str(info.get("DeviceIdentifier", "")),
        "volumeFileSystem": str(info.get("FilesystemType", info.get("FileSystemName", ""))),
        "driveHealthy": None,
        "rawVideoContentInspected": False,
        "note": "Mount identity confirms the named volume, not SSD health or every video's integrity.",
    }


def verify(current: dict, frozen: dict) -> dict:
    required = ("version", "volumeUuid", "footageRelativePath")
    if any(not frozen.get(k) for k in required):
        raise ValueError("Incomplete frozen SSD identity file")
    if frozen["version"] != "klip-external-ssd-identity-v1":
        raise ValueError("Unsupported SSD identity format")
    # The same device identifier is not required; macOS assigns a new diskN
    # after a disconnect. Stable volume identity is the contract.
    for key in ("volumeUuid", "footageRelativePath"):
        if current[key] != frozen[key]:
            raise ValueError(f"SSD identity mismatch ({key}); do not scan a substituted drive")
    # Avoid accepting unexpected remount paths without reindexing and a new
    # frozen campaign because the catalog stores absolute source paths.
    if current["footageRoot"] != frozen.get("footageRoot"):
        raise ValueError("SSD mounted at a new path; reindex and freeze a new campaign")
    return {
        "status": "same_external_volume_verified",
        "volumeUuid": current["volumeUuid"],
        "footageRoot": current["footageRoot"],
        "rawVideoContentInspected": False,
        "driveHealthy": None,
    }


def safe_identity_file(workspace: Path, target: Path) -> Path:
    workspace = lexical(workspace)
    target = lexical(target)
    reports = workspace / "reports"
    if target.suffix.lower() != ".json" or not target.is_relative_to(reports):
        raise ValueError("SSD identity file must be a .json file under WORKSPACE/reports")
    if target.is_symlink():
        raise ValueError("SSD identity must not be a symlink")
    if not reports.is_dir() or not target.parent.is_dir():
        raise ValueError("Create the workspace reports directory first")
    if not target.parent.resolve().is_relative_to(reports.resolve()):
        raise ValueError("SSD identity parent resolves outside workspace reports")
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("enroll", "verify"))
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--identity", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        root = lexical(args.root)
        workspace = lexical(args.workspace)
        if root.is_relative_to(workspace) or workspace.is_relative_to(root):
            raise ValueError("SSD footage root and workspace must remain separate")
        target = safe_identity_file(workspace, args.identity)
        current = mounted_identity(root)
        if args.mode == "enroll":
            payload = dict(current, enrolledAtUtc=datetime.now(timezone.utc).isoformat())
            # xb fails safely when an identity already exists; never overwrite.
            with target.open("x", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            result = {"status": "volume_identity_enrolled", "identity": str(target),
                      "volumeUuid": current["volumeUuid"], "driveHealthy": None}
        else:
            if not target.is_file():
                raise ValueError("SSD identity file missing; run enroll first")
            with target.open(encoding="utf-8") as handle:
                frozen = json.load(handle)
            result = verify(current, frozen)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"SSD IDENTITY BLOCKED: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
