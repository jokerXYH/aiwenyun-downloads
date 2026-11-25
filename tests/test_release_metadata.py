from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import APP_NAME, APP_VERSION


class ReleaseMetadataTests(unittest.TestCase):
    def test_readme_title_matches_app_version(self):
        text = (ROOT / 'README.md').read_text(encoding='utf-8')
        self.assertIn('# %s %s' % (APP_NAME, APP_VERSION), text)

    def test_macos_script_reads_version_from_config(self):
        text = (ROOT / 'build_macos.command').read_text(encoding='utf-8')
        self.assertIn('from config import APP_VERSION', text)
        self.assertIn(
            'plutil -replace CFBundleShortVersionString -string "$APP_VERSION"',
            text)
        self.assertIn(
            'plutil -replace CFBundleVersion -string "$APP_VERSION"', text)

    def test_release_workflow_default_matches_version(self):
        text = (ROOT / '.github' / 'workflows' /
                'build-release.yml').read_text(encoding='utf-8')
        self.assertIn("default: 'v%s'" % APP_VERSION, text)

    def test_release_rules_are_documented(self):
        text = (ROOT / 'docs' / 'RELEASE.md').read_text(encoding='utf-8')
        for needle in ('APP_VERSION', 'build-release.yml',
                       'mirror-aiwenyun.yml', 'build_macos.command',
                       'SHA-256'):
            self.assertIn(needle, text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
