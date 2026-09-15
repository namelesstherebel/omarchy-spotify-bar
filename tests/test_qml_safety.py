#!/usr/bin/env python3
"""Source-boundary checks for host controls whose implementation is external."""
import importlib.util
from pathlib import Path
import re
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class QmlSafetyTests(unittest.TestCase):
    def test_standalone_runners_do_not_execute_on_import(self):
        for name in ('test_search_style', 'test_qml_controls'):
            with self.subTest(runner=name), patch('subprocess.call') as run:
                spec = importlib.util.spec_from_file_location(name, ROOT / 'tests' / (name + '.py'))
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                run.assert_not_called()

    def test_qt_runners_clean_disposable_runtime_and_cache(self):
        for name in ('test_search_style', 'test_qml_controls'):
            for exit_code in (0, 1):
                with self.subTest(runner=name, exit_code=exit_code):
                    spec = importlib.util.spec_from_file_location(name, ROOT / 'tests' / (name + '.py'))
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    roots = []

                    def run(command, env):
                        directory = Path(command[-1]).parent
                        roots.append(directory)
                        for key in ('XDG_RUNTIME_DIR', 'XDG_CACHE_HOME'):
                            location = Path(env[key])
                            self.assertTrue(location.is_relative_to(directory))
                            self.assertTrue(location.is_dir())
                        self.assertEqual(Path(env['XDG_RUNTIME_DIR']).stat().st_mode & 0o777, 0o700)
                        # Fontconfig can produce symlinks, which plugin validate
                        # rejects even in ignored build directories.
                        (Path(env['XDG_CACHE_HOME']) / 'fontconfig-link').symlink_to(directory / 'absent')
                        return exit_code

                    with patch('subprocess.call', side_effect=run):
                        self.assertEqual(module.main(), exit_code)
                    self.assertEqual(len(roots), 1)
                    self.assertFalse(roots[0].exists())

    def test_client_id_warning_does_not_promise_secret_detection(self):
        panel = (ROOT / 'SpotifyPanel.qml').read_text()
        readme = (ROOT / 'README.md').read_text()
        backend = (ROOT / 'core/spotifyctl.py').read_text()
        for source in (panel, readme):
            self.assertIn('public data', source)
            self.assertIn('cannot distinguish', source)
            self.assertIn('Save Client ID', source)
            self.assertNotIn('never asks for or accepts a client secret', source)
            self.assertNotIn('does not request, accept, or store one', source)
            self.assertNotIn('reject secret-bearing configuration', source)
        self.assertIn('cannot distinguish', backend)
        self.assertNotIn('reject secret-bearing or unexpected config', backend)

    def test_all_text_nodes_explicitly_use_plain_text(self):
        for name in ('BarWidget.qml', 'SpotifyPanel.qml'):
            source = (ROOT / name).read_text()
            for match in re.finditer(r'\bText\s*\{', source):
                # textFormat must be the first property: easy to audit, no AutoText.
                self.assertRegex(source[match.end():], r'^\s*textFormat: Text.PlainText',
                                 f'{name}:{source[:match.start()].count(chr(10)) + 1}')

    def test_host_widget_does_not_render_external_text(self):
        source = (ROOT / 'BarWidget.qml').read_text()
        self.assertNotRegex(source, r'tooltipText:[\s\S]*?playback\.artist')
        self.assertNotIn('playback.title', source[source.index('id: opener'):source.index('            Text {')])


if __name__ == '__main__':
    unittest.main()
