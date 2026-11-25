from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import token_windows
except (ImportError, AttributeError):
    token_windows = None
from config import CLIENT_VERSION, CLIENT_VERSION_FULL
from login import _DEVICE_ID_RE, _stable_device_id
from token_model import TokenScanError


class ClientVersionTests(unittest.TestCase):
    def test_version_values_are_centralized(self):
        self.assertTrue(CLIENT_VERSION)
        self.assertTrue(CLIENT_VERSION_FULL)
        from login import CLIENT_VERSION as login_version
        from login import CLIENT_VERSION_FULL as login_version_full
        self.assertEqual(login_version, CLIENT_VERSION)
        self.assertEqual(login_version_full, CLIENT_VERSION_FULL)


class StableDeviceIdTests(unittest.TestCase):
    def test_fallback_id_is_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'device-id.txt'
            first = _stable_device_id(path)
            second = _stable_device_id(path)
            self.assertEqual(first, second)
            self.assertRegex(first, _DEVICE_ID_RE.pattern.decode('ascii'))
            self.assertEqual(path.read_text(encoding='ascii'), first)


class TokenScanDiagnosticsTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'win32' and token_windows is not None,
                         'Windows 内存扫描诊断只在 Windows 测试')
    def test_marker_hint_when_format_changed(self):
        def fake_scan(pid, deadline, stats=None):
            if stats is not None:
                stats.marker_hits = 4
                stats.processes = 1
            return []

        with patch.object(token_windows, 'list_client_pids',
                          return_value=[1234]), \
                patch.object(token_windows, '_scan_pid', side_effect=fake_scan):
            with self.assertRaises(TokenScanError) as context:
                token_windows.scan_tokens(timeout=1)
        self.assertIn('token 标记', str(context.exception))


if __name__ == '__main__':
    unittest.main(verbosity=2)
