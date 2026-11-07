"""Synthetic safety qualification for the reconnectable macOS SSD mount guard."""
import importlib.util
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

import pytest

MODULE = Path(__file__).resolve().parents[1] / "scripts" / "klip_ssd_mount_guard.py"
spec = importlib.util.spec_from_file_location("klip_ssd_mount_guard", MODULE)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def identity(uuid="vol-abc", root="/Volumes/ActionSSD/Raw", device="disk4s1"):
    return {
        "version": "klip-external-ssd-identity-v1",
        "volumeUuid": uuid,
        "footageRelativePath": "Raw",
        "footageRoot": root,
        "diskutilDeviceAtCapture": device,
    }


def test_same_volume_after_reinsertion_can_have_changed_disk_identifier():
    saved = identity(device="disk4s1")
    now = identity(device="disk8s1")
    result = guard.verify(now, saved)
    assert result["status"] == "same_external_volume_verified"
    assert result["rawVideoContentInspected"] is False


@pytest.mark.parametrize("current", [
    identity(uuid="vol-substitute"),
    identity(root="/Volumes/ActionSSD 1/Raw"),
    dict(identity(), footageRelativePath="Other")
])
def test_substitute_or_moved_volume_is_rejected(current):
    with pytest.raises(ValueError, match="mismatch|reindex"):
        guard.verify(current, identity())


def test_unmounted_volume_fails_before_diskutil():
    with patch.object(guard.os.path, "ismount", return_value=False):
        with patch.object(guard, "get_disk_info") as disk:
            with pytest.raises(ValueError, match="not currently mounted"):
                guard.volume_and_subpath(Path("/Volumes/MissingSSD/Raw"))
            disk.assert_not_called()


def test_identity_enroll_verify_is_exclusive_and_catalog_free(tmp_path, capsys):
    ws = tmp_path / "workspace"
    reports = ws / "reports"
    reports.mkdir(parents=True)
    target = reports / "ssd-identity.json"
    root = Path("/Volumes/ActionSSD/Raw")
    with patch.object(guard, "mounted_identity", side_effect=lambda path: identity()):
        assert guard.main(["enroll", "--workspace", str(ws),
                           "--root", str(root), "--identity", str(target)]) == 0
        assert json.loads(target.read_text())["volumeUuid"] == "vol-abc"
        assert guard.main(["verify", "--workspace", str(ws),
                           "--root", str(root), "--identity", str(target)]) == 0
        assert guard.main(["enroll", "--workspace", str(ws),
                           "--root", str(root), "--identity", str(target)]) == 3
    assert not (ws / "catalog.sqlite3").exists()
    assert "same_external_volume_verified" in capsys.readouterr().out


def test_identity_output_symlink_not_accepted(tmp_path):
    ws = tmp_path / "workspace"
    reports = ws / "reports"
    reports.mkdir(parents=True)
    external = tmp_path / "outside.json"
    external.write_text("original")
    link = reports / "ssd-identity.json"
    link.symlink_to(external)
    with pytest.raises(ValueError, match="symlink"):
        guard.safe_identity_file(ws, link)
    assert external.read_text() == "original"


def test_diskutil_metadata_is_not_assumed_to_prove_health():
    with patch.object(guard, "volume_and_subpath",
                      return_value=(Path("/Volumes/ActionSSD"), Path("Raw"))):
        with patch.object(guard, "get_disk_info",
                          return_value={"VolumeUUID":"VOL-ABC",
                                        "DeviceIdentifier":"disk8s1",
                                        "MountPoint":"/Volumes/ActionSSD",
                                        "Mounted":True}):
            result = guard.mounted_identity(Path("/Volumes/ActionSSD/Raw"))
    assert result["volumeUuid"] == "vol-abc"
    assert result["driveHealthy"] is None
    assert result["rawVideoContentInspected"] is False
