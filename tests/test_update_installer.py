from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import update_installer
from update_installer import (UpdateInstallError, build_updater_script,
                              can_auto_update, cleanup_stale_update_files,
                              is_pe_executable, is_windows_exe_url,
                              parse_sha256_text, prepare_update, sha256_file)


def write_fake_pe(path: Path, size: int = 2048) -> Path:
    data = bytearray(size)
    data[0:2] = b'MZ'
    data[0x3c:0x40] = (0x80).to_bytes(4, 'little')
    data[0x80:0x84] = b'PE\0\0'
    path.write_bytes(data)
    return path


class ChecksumTests(unittest.TestCase):
    def test_parse_standard_sha256_line(self):
        digest = 'e627cab9c8e12c3570e09410ab759dab34c9c9563e6e664ead3ad67cf0a0a293'
        self.assertEqual(parse_sha256_text(digest + '  app.exe'), digest)

    def test_parse_uppercase_and_label(self):
        digest = 'A' * 64
        self.assertEqual(parse_sha256_text('SHA256 (app.exe) = ' + digest),
                         digest.lower())

    def test_parse_rejects_missing_digest(self):
        with self.assertRaises(UpdateInstallError):
            parse_sha256_text('no checksum here')

    def test_sha256_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'app.exe'
            path.write_bytes(b'hello')
            expected = hashlib.sha256(b'hello').hexdigest()
            self.assertEqual(sha256_file(path), expected)


class PeTests(unittest.TestCase):
    def test_accepts_pe_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_fake_pe(Path(tmp) / 'app.exe')
            self.assertTrue(is_pe_executable(path))

    def test_rejects_non_pe(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'app.exe'
            path.write_bytes(b'<html>not an exe</html>')
            self.assertFalse(is_pe_executable(path))

    def test_windows_exe_url(self):
        self.assertTrue(is_windows_exe_url(
            'https://cdn.example.com/app-windows-x64.exe?v=2.3.0'))
        self.assertFalse(is_windows_exe_url(
            'https://cdn.example.com/app-macos-arm64.zip'))
        self.assertFalse(is_windows_exe_url(
            'https://example.com/software/'))


class AutoUpdateTests(unittest.TestCase):
    def setUp(self):
        self.result = {
            'download_url': 'https://cdn.example.com/app.exe',
            'checksum_url': 'https://cdn.example.com/app.sha256',
        }

    def test_requires_frozen_windows_exe(self):
        self.assertTrue(can_auto_update(
            self.result, frozen=True, platform='win32'))
        self.assertFalse(can_auto_update(
            self.result, frozen=False, platform='win32'))
        self.assertFalse(can_auto_update(
            self.result, frozen=True, platform='darwin'))

    def test_requires_windows_exe_and_checksum(self):
        self.assertFalse(can_auto_update(
            {'download_url': 'https://cdn.example.com/app.zip',
             'checksum_url': 'https://cdn.example.com/app.sha256'},
            frozen=True, platform='win32'))
        self.assertFalse(can_auto_update(
            {'download_url': 'https://cdn.example.com/app.exe'},
            frozen=True, platform='win32'))


class PrepareUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = write_fake_pe(self.root / 'source.exe')
        self.digest = sha256_file(self.source)
        self.work = self.root / 'work'
        self.result = {
            'latest': '2.3.0',
            'download_url': 'https://cdn.example.com/app.exe',
            'checksum_url': 'https://cdn.example.com/app.sha256',
        }

    def tearDown(self):
        self.temp.cleanup()

    def _patch(self, digest=None, payload=None):
        def fake_download(url, dest, progress=None, timeout=30.0,
                          max_bytes=0):
            dest = Path(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if payload is None:
                shutil.copyfile(self.source, dest)
            else:
                dest.write_bytes(payload)
            if progress is not None:
                progress(dest.stat().st_size, dest.stat().st_size)
            return dest

        return (
            mock.patch.object(update_installer, 'download_file',
                              side_effect=fake_download),
            mock.patch.object(update_installer, 'fetch_sha256',
                              return_value=digest or self.digest),
        )

    def test_downloads_and_verifies_package(self):
        download, checksum = self._patch()
        with download, checksum:
            package = prepare_update(self.result, work_dir=self.work)
        self.assertTrue(package.is_file())
        self.assertEqual(sha256_file(package), self.digest)

    def test_rejects_non_exe_url(self):
        self.result['download_url'] = 'https://cdn.example.com/app.zip'
        with self.assertRaises(UpdateInstallError):
            prepare_update(self.result, work_dir=self.work)

    def test_requires_checksum(self):
        self.result.pop('checksum_url')
        with self.assertRaises(UpdateInstallError):
            prepare_update(self.result, work_dir=self.work)

    def test_checksum_mismatch_removes_package(self):
        download, checksum = self._patch(digest='0' * 64)
        with download, checksum:
            with self.assertRaises(UpdateInstallError):
                prepare_update(self.result, work_dir=self.work)
        self.assertFalse((self.work / 'update.exe').exists())

    def test_rejects_invalid_exe_payload(self):
        download, checksum = self._patch(
            digest=hashlib.sha256(b'not an exe').hexdigest(),
            payload=b'not an exe')
        with download, checksum:
            with self.assertRaises(UpdateInstallError):
                prepare_update(self.result, work_dir=self.work)


class UpdaterScriptTests(unittest.TestCase):
    def test_script_verifies_version_and_deletes_old_copy(self):
        script = build_updater_script(
            Path(r'C:\Apps\aiwenyun.exe'),
            Path(r'C:\Temp\update.exe'),
            pid=1234,
            parent_pid=5678,
            expected_version='2.3.0',
            log_path=Path(r'C:\Temp\update.log'),
        )
        self.assertIn('--version-file', script)
        self.assertIn('WaitForExit', script)
        self.assertIn('Move-Item -LiteralPath $target', script)
        self.assertIn('Remove-Item -LiteralPath $old', script)
        self.assertNotIn('.bak', script)

    def test_script_escapes_single_quotes(self):
        script = build_updater_script(
            Path("C:/Apps/O'Brien/aiwenyun.exe"),
            Path("C:/Temp/O'Brien.exe"),
            pid=1,
            expected_version="2.3.0",
        )
        self.assertIn("O''Brien", script)


class CleanupTests(unittest.TestCase):
    def test_removes_stale_old_file_when_target_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'aiwenyun.exe'
            target.write_bytes(b'MZ')
            old = Path(tmp) / 'aiwenyun.exe.old-123-1'
            old.write_bytes(b'old')
            stamp = time.time() - 60
            os.utime(old, (stamp, stamp))
            removed = cleanup_stale_update_files(
                target=target, max_age_seconds=0)
            self.assertIn(old, removed)
            self.assertFalse(old.exists())
            self.assertTrue(target.exists())

    def test_keeps_old_file_when_target_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'aiwenyun.exe'
            old = Path(tmp) / 'aiwenyun.exe.old-123-1'
            old.write_bytes(b'old')
            cleanup_stale_update_files(target=target, max_age_seconds=0)
            self.assertTrue(old.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
