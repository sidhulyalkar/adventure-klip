"""Standalone regression tests for the source-root recovery preflight."""
import importlib.util
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / 'scripts' / 'klip_source_root_preflight.py'
spec = importlib.util.spec_from_file_location('klip_source_root_preflight', MODULE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class SourceRootPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.ws = base / 'workspace'
        self.ws.mkdir()
        self.root = base / 'healthy-videos'
        self.root.mkdir()
        with closing(sqlite3.connect(self.ws / 'catalog.sqlite3')) as db:
            db.execute('CREATE TABLE assets (id TEXT, sha256 TEXT, stored_path TEXT, size_bytes INT, duration REAL)')
            db.execute('INSERT INTO assets VALUES (?,?,?,?,?)', ('a' * 64, 'a' * 64,
                       '/Volumes/UNAVAILABLE/DCIM/DJI_01.MP4', 5, 20.0))
            db.execute('INSERT INTO assets VALUES (?,?,?,?,?)', ('b' * 64, 'b' * 64,
                       '/Volumes/UNAVAILABLE/DCIM/DJI_02.MP4', 10, 30.0))
            db.commit()

    def test_metadata_only_does_not_stat_missing_catalog_originals(self):
        real_lstat = os.lstat
        def safe_lstat(path, *args, **kwargs):
            if str(path).startswith('/Volumes/UNAVAILABLE'):
                raise AssertionError('Untrusted removable media accessed')
            return real_lstat(path, *args, **kwargs)
        with patch.object(os, 'lstat', side_effect=safe_lstat):
            report = mod.build_report(self.ws)
        self.assertEqual(report['catalogAssetCount'], 2)
        self.assertEqual(report['catalogPathClasses'], {'mounted_external_unverified': 2})
        self.assertFalse(report['sourceRootExplicitlySelected'])
        self.assertFalse(report['scanReady'])

    def test_trusted_root_is_stat_only_and_candidates_are_not_verified(self):
        (self.root / 'DJI_01.MP4').write_bytes(b'12345')
        (self.root / 'UNRELATED.mp4').write_bytes(b'1234')
        (self.root / 'DJI_02.MP4').symlink_to(self.root / 'DJI_01.MP4')
        root = mod.trusted_root(self.root, self.ws, False)
        report = mod.build_report(self.ws, root)
        self.assertEqual(report['localVideoFilesEnumerated'], 2)
        self.assertEqual(report['potentialCatalogSourceCount'], 1)
        self.assertEqual(report['filenameSizeMatchingFiles'], 1)
        self.assertEqual(report['matchedCandidatesNotVerified'][0]['possibleSourceIds'], ['a' * 64])
        self.assertFalse(report['sourceContentHashed'])
        self.assertFalse(report['scanReady'])

    def test_disallowed_roots_rejected_before_source_access(self):
        for root in [Path('/'), Path.home(), self.ws, Path('/Volumes/ProbablyBroken')]:
            with self.subTest(root=root):
                with self.assertRaises((ValueError, OSError)):
                    mod.trusted_root(root, self.ws, False)

    def test_report_is_new_file_and_inside_workspace_reports(self):
        report = mod.build_report(self.ws)
        out = self.ws / 'reports' / 'preflight.json'
        mod.write_report(self.ws, str(out), report)
        self.assertEqual(json.loads(out.read_text())['catalogAssetCount'], 2)
        with self.assertRaises(FileExistsError):
            mod.write_report(self.ws, str(out), report)
        with self.assertRaises(ValueError):
            mod.write_report(self.ws, str(self.root / 'escape.json'), report)


    def test_existing_ssd_catalog_paths_reconcile_without_hashing(self):
        known = self.root / 'DJI_01.MP4'
        changed = self.root / 'DJI_02.MP4'
        unknown = self.root / 'NEW.MP4'
        known.write_bytes(b'12345')
        changed.write_bytes(b'xyz')
        unknown.write_bytes(b'other')
        with closing(sqlite3.connect(self.ws / 'catalog.sqlite3')) as db:
            db.execute('UPDATE assets SET stored_path=? WHERE id=?',
                       (str(known), 'a' * 64))
            db.execute('UPDATE assets SET stored_path=? WHERE id=?',
                       (str(changed), 'b' * 64))
            db.commit()
        report = mod.build_report(self.ws, self.root)
        self.assertEqual(report['catalogPathsInsideTrustedRoot'], 2)
        self.assertEqual(report['catalogSamePathSameSizeUnverified'], 1)
        self.assertEqual(report['catalogSamePathDifferentSize'], 1)
        self.assertEqual(report['catalogPathNotEnumerated'], 0)
        self.assertEqual(report['localPathsNotInCatalogAtSamePath'], 1)
        self.assertEqual(report['localPathsNotInCatalogSample'], [str(unknown)])
        self.assertFalse(report['sourceContentHashed'])
        self.assertFalse(report['scanReady'])

    def test_missing_catalog_path_is_not_treated_as_matching_unknown_size(self):
        with closing(sqlite3.connect(self.ws / 'catalog.sqlite3')) as db:
            db.execute('UPDATE assets SET stored_path=?,size_bytes=? WHERE id=?',
                       (str(self.root / 'MISSING.MP4'), None, 'a' * 64))
            db.commit()
        report = mod.build_report(self.ws, self.root)
        self.assertEqual(report['catalogSamePathSameSizeUnverified'], 0)
        self.assertEqual(report['catalogPathNotEnumerated'], 1)


if __name__ == '__main__':
    unittest.main()
