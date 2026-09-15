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


def git_paths():
    """Keep each index stage and each reachable commit's full tree paths.

    rev-list --objects supplies only one name per object, not every use of a
    shared blob/tree. NUL-delimited tree entries preserve tabs and newlines.
    """
    for entry in git('ls-files', '--stage', '-z').split(b'\0'):
        if entry:
            info, path = entry.split(b'\t', 1)
            mode, identity, _stage = info.split()
            yield identity.decode(), path.decode(errors='surrogateescape'), mode != b'160000'
    for commit in git('rev-list', '--all').splitlines():
        for entry in git('ls-tree', '-r', '-z', commit.decode()).split(b'\0'):
            if entry:
                info, path = entry.split(b'\t', 1)
                _mode, kind, identity = info.split()
                yield identity.decode(), path.decode(errors='surrogateescape'), kind == b'blob'


def main():
    # Ensure the detector catches generated stand-ins without embedding secrets.
    assert suspicious(('ab' * 16).encode())
    assert suspicious(('BQ' + 'x' * 90).encode())
    assert suspicious(b'{"access_' + b'token":"unexpected-literal"}')
    inspected = {}
    count = 0
    for identity, path, is_blob in git_paths():
        count += 1
        forbidden = PurePosixPath(path).name in ('config.json', 'spotify.json') or path.endswith(
            ('.token', '.secret', '.login-required', '.rate-limit', '.lock'))
        if forbidden:
            raise SystemExit(f'Secret scan FAILED: {path} (value redacted)')
        if not is_blob:
            continue
        if identity not in inspected:
            data = git('cat-file', 'blob', identity)
            # Cache inspection only, never a path's permission to use fixtures.
            inspected[identity] = (suspicious(data), suspicious(data, True))
        if inspected[identity][int(path.startswith('tests/'))]:
            raise SystemExit(f'Secret scan FAILED: {path} (value redacted)')
    print(f'Secret scan: {count} index/history paths and {len(inspected)} unique blobs checked; no Client ID/token literals or runtime data found.')
    print('Known synthetic test fixtures allowed; no OCR or arbitrary-encoding detection.')


if __name__ == '__main__':
    main()
