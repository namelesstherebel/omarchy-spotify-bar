#!/usr/bin/env python3
"""Dependency-free, redacted scan of Git's index and all reachable history.

This is a literal/pattern scan, not OCR or proof against arbitrary encodings.
Only named synthetic fixtures in tests are allowed; runtime data is forbidden.
"""
from pathlib import PurePosixPath
import re
import subprocess


def git(*args):
    return subprocess.check_output(['git', *args])


def suspicious(data, test_file=False):
    text = data.decode('utf-8', errors='replace')
    if re.search(r'(?<![a-fA-F0-9])[a-fA-F0-9]{32}(?![a-fA-F0-9])', text):
        return True
    if re.search(r'\bBQ[A-Za-z0-9_-]{30,}|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', text):
        return True
    fixtures = {'a', 'b', 'c', 'd', 'e', 'old', 'new', 'refresh', 'never', 'access-value', 'refresh-value'}
    for match in re.finditer(r'''["'](?:client_id|client_secret|access_token|refresh_token)["']\s*[:=]\s*["']([^"']+)["']''', text):
        if not test_file or match[1] not in fixtures:
            return True
    return False


def main():
    # Ensure the detector catches generated stand-ins without embedding secrets.
    assert suspicious(('ab' * 16).encode())
    assert suspicious(('BQ' + 'x' * 90).encode())
    assert suspicious(b'{"access_' + b'token":"unexpected-literal"}')
    objects = {}
    for entry in git('ls-files', '--stage', '-z').split(b'\0'):
        if entry:
            info, path = entry.split(b'\t', 1)
            objects[info.split()[1].decode()] = path.decode()
    for entry in git('rev-list', '--objects', '--all').decode().splitlines():
        parts = entry.split(' ', 1)
        if len(parts) == 2:
            objects.setdefault(parts[0], parts[1])
    count = 0
    for identity, path in objects.items():
        if git('cat-file', '-t', identity).strip() != b'blob':
            continue
        count += 1
        forbidden = PurePosixPath(path).name in ('config.json', 'spotify.json') or path.endswith(
            ('.token', '.secret', '.login-required', '.rate-limit', '.lock'))
        if forbidden or suspicious(git('cat-file', 'blob', identity), path.startswith('tests/')):
            raise SystemExit(f'Secret scan FAILED: {path} (value redacted)')
    print(f'Secret scan: {count} index/history blobs checked; no Client ID/token literals or runtime data found.')
    print('Known synthetic test fixtures allowed; no OCR or arbitrary-encoding detection.')


if __name__ == '__main__':
    main()
