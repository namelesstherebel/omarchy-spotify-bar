#!/usr/bin/env python3
"""Real disposable Git indexes/trees; generated fixtures, never credentials."""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import scan_secrets


class SecretScanTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='oma-scan-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.git('init', '-q', '--template=')

    def git(self, *args):
        return subprocess.check_output(['git', '-c', 'user.name=Synthetic Test',
            '-c', 'user.email=test@example.invalid', '-c', 'commit.gpgsign=false',
            '-c', 'core.hooksPath=/dev/null', *args], cwd=self.root,
            env={**os.environ, 'GIT_CONFIG_GLOBAL':'/dev/null', 'GIT_CONFIG_NOSYSTEM':'1'},
            stderr=subprocess.PIPE)

    def put(self, path, data):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        self.git('add', '--', path)

    def scan(self):
        with patch.object(scan_secrets, 'git', side_effect=self.git), redirect_stdout(StringIO()):
            scan_secrets.main()

    def test_index_each_path_requires_its_own_fixture_exemption(self):
        self.git('commit', '--allow-empty', '-qm', 'base')
        data = b'{"access_' + b'token":"new"}'
        self.put('a-production.py', data)
        self.put('tests/z-fixture.py', data)  # Same blob must not authorize production.
        with self.assertRaisesRegex(SystemExit, 'a-production.py'):
            self.scan()

    def test_index_runtime_path_cannot_hide_behind_identical_safe_blob(self):
        self.git('commit', '--allow-empty', '-qm', 'base')
        self.put('config.json', b'innocent content\n')
        self.put('tests/z-fixture.py', b'innocent content\n')
        with self.assertRaisesRegex(SystemExit, 'config.json'):
            self.scan()

    def test_every_historical_tree_path_is_checked_even_if_index_allows_blob(self):
        for forbidden, data in [('production.py', b'{"access_' + b'token":"new"}'),
                                ('old/config.json', b'innocent content\n'),
                                ('old/tab\tand\nnewline.token', b'innocent content\n')]:
            with self.subTest(path=forbidden):
                self.root = Path(self.directory.name) / str(len(forbidden))
                self.root.mkdir()
                self.git('init', '-q', '--template=')
                self.put(forbidden, data)
                self.git('commit', '-qm', 'historical path')
                self.git('rm', '--', forbidden)
                self.put('tests/fixture.py', data)
                self.git('commit', '-qm', 'fixture now only')
                with self.assertRaises(SystemExit) as raised:
                    self.scan()
                self.assertIn(forbidden, str(raised.exception))

    def test_fixture_duplicates_are_allowed_and_content_inspection_is_deduped(self):
        data = b'{"access_' + b'token":"new"}'
        self.put('tests/a.py', data)
        self.put('tests/b.py', data)
        self.git('commit', '-qm', 'two allowed paths')
        with patch.object(scan_secrets, 'git', side_effect=self.git) as calls, redirect_stdout(StringIO()):
            scan_secrets.main()
        reads = [call for call in calls.call_args_list if call.args[:2] == ('cat-file', 'blob')]
        self.assertEqual(len(reads), 1, 'read content once, but authorize each path independently')


if __name__ == '__main__':
    unittest.main()
