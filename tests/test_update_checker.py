from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import UPDATE_URL
from update_checker import (UpdateError, check_for_update, is_newer,
                            parse_update_payload)


class PayloadTests(unittest.TestCase):
    def test_json_common_fields(self):
        data = parse_update_payload(json.dumps({
            'version': 'v2.3.0',
            'url': '/downloads/app.exe',
            'notes': '修复更新入口',
        }), base_url='https://example.com/update/latest.json')
        self.assertEqual(data['version'], '2.3.0')
        self.assertEqual(data['download_url'],
                         'https://example.com/downloads/app.exe')
        self.assertEqual(data['notes'], '修复更新入口')

    def test_nested_fields(self):
        data = parse_update_payload(json.dumps({
            'data': {'versionName': '2.3.1', 'downloadUrl':
                     'https://cdn.example.com/app.zip'},
        }))
        self.assertEqual(data['version'], '2.3.1')
        self.assertEqual(data['download_url'],
                         'https://cdn.example.com/app.zip')

    def test_plain_version_text(self):
        data = parse_update_payload('v2.4.1')
        self.assertEqual(data['version'], '2.4.1')

    def test_missing_version(self):
        with self.assertRaises(UpdateError):
            parse_update_payload('{"message": "ok"}')

    def test_json_checksum_fields(self):
        data = parse_update_payload(json.dumps({
            'version': '2.3.0',
            'url': 'https://cdn.example.com/app.exe',
            'sha256_url': 'https://cdn.example.com/app.sha256',
            'sha256': 'a' * 64,
        }))
        self.assertEqual(data['checksum_url'],
                         'https://cdn.example.com/app.sha256')
        self.assertEqual(data['sha256'], 'a' * 64)


class HtmlPayloadTests(unittest.TestCase):
    HTML = '''
        <html><body>
        <span class="version-badge">v2.3.0</span>
        <a href="https://cdn.example.com/win/app-windows-x64.exe?v=2.3.0">
          Windows
        </a>
        <a href="https://cdn.example.com/win/app-windows-x64.sha256?v=2.3.0">
          SHA-256
        </a>
        <a href="https://cdn.example.com/mac/app-macos-arm64.zip">macOS</a>
        </body></html>
    '''

    def test_html_picks_windows_exe_and_checksum(self):
        data = parse_update_payload(
            self.HTML, base_url='https://example.com/software/')
        self.assertEqual(data['version'], '2.3.0')
        self.assertEqual(
            data['download_url'],
            'https://cdn.example.com/win/app-windows-x64.exe?v=2.3.0')
        self.assertEqual(
            data['checksum_url'],
            'https://cdn.example.com/win/app-windows-x64.sha256?v=2.3.0')

    def test_html_without_exe_does_not_invent_package(self):
        data = parse_update_payload(
            '<span class="version-badge">v2.3.0</span>'
            '<a href="/mac/app.zip">macOS</a>')
        self.assertEqual(data['download_url'], '')
        self.assertEqual(data['checksum_url'], '')

    def test_html_multiple_unmarked_exes_does_not_guess(self):
        data = parse_update_payload(
            '<span class="version-badge">v2.3.0</span>'
            '<a href="/a.exe">A</a><a href="/b.exe">B</a>')
        self.assertEqual(data['download_url'], '')

    def test_check_exposes_package_and_checksum(self):
        result = check_for_update('2.2.1',
                                  fetcher=lambda _url: self.HTML)
        self.assertEqual(result['status'], 'update')
        self.assertEqual(
            result['download_url'],
            'https://cdn.example.com/win/app-windows-x64.exe?v=2.3.0')
        self.assertEqual(
            result['checksum_url'],
            'https://cdn.example.com/win/app-windows-x64.sha256?v=2.3.0')


class VersionTests(unittest.TestCase):
    def test_compare(self):
        self.assertTrue(is_newer('2.3.0', '2.2.9'))
        self.assertFalse(is_newer('2.2.1', '2.2.1'))
        self.assertFalse(is_newer('2.1.9', '2.2.1'))
        self.assertFalse(is_newer('2.2', '2.2.1'))


class FixedUrlTests(unittest.TestCase):
    def test_check_uses_fixed_download_page(self):
        calls = []

        def fetcher(url):
            calls.append(url)
            return '{"version": "2.3.0", "url": "/app.exe"}'

        result = check_for_update('2.2.1', fetcher=fetcher)
        self.assertEqual(calls, [UPDATE_URL])
        self.assertEqual(result['status'], 'update')
        self.assertEqual(result['latest'], '2.3.0')
        self.assertEqual(result['download_url'], 'https://jokerx.top/app.exe')

    def test_no_update(self):
        result = check_for_update(
            '2.3.0', fetcher=lambda _url: '{"version": "2.3.0"}')
        self.assertEqual(result['status'], 'latest')


if __name__ == '__main__':
    unittest.main(verbosity=2)
