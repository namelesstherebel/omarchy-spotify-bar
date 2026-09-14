#!/usr/bin/env python3
"""Oma Spotify JSON CLI. Tokens stay in Secret Service, never in its output.

Usage: spotifyctl.py COMMAND [JSON_OBJECT]. All failures produce a safe JSON
error and exit 1. OAuth opens the user's browser and waits at most 180 seconds.
Only fixed Spotify HTTPS endpoints are reachable; mutations are never retried.
"""
import base64
from contextlib import contextmanager
import fcntl
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

CALLBACK = 'http://127.0.0.1:8888/callback'
API = 'https://api.spotify.com/v1'
TOKEN_URL = 'https://accounts.spotify.com/api/token'
SCOPES = ('user-read-playback-state user-modify-playback-state '
          'user-read-currently-playing user-read-private user-library-read '
          'user-library-modify playlist-read-private playlist-read-collaborative '
          'user-read-recently-played')


class Failure(Exception):
    """An explicitly safe error; never construct its message from server text."""

    def __init__(self, kind, message, retry_after=0):
        super().__init__(message)
        self.kind = kind
        self.retry_after = retry_after

    def envelope(self):
        """Return the only error shape allowed across the QML boundary."""
        return {'ok': False, 'error': {'kind': self.kind, 'message': str(self),
                                      'retry_after': self.retry_after}}


class NoRedirect(HTTPRedirectHandler):
    """Do not forward bearer tokens or token exchange bodies to redirects."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Make every redirect a normal HTTP failure, without a second request."""
        return None


urlopen = build_opener(NoRedirect()).open


def private_directory(path):
    """Require a real owner-private directory for locks and non-secret markers."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise Failure('storage', 'Spotify state directory is not owned by this user.')
    path.chmod(0o700)


@contextmanager
def private_lock(path):
    """Serialize token read/refresh/write across CLI processes using a 0600 lock."""
    private_directory(path.parent)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise Failure('storage', 'Spotify lock is not a user-owned regular file.')
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def challenge(verifier):
    """Derive the RFC 7636 S256 challenge without exposing the verifier."""
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).decode().rstrip('=')


def callback_code(path, state):
    """Accept exactly one code and matching state on the loopback callback path."""
    parsed = urlsplit(path)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (parsed.scheme or parsed.netloc or parsed.path != '/callback' or parsed.fragment
            or query.get('state') != [state] or len(query.get('code', [])) != 1
            or not query['code'][0] or 'error' in query):
        raise Failure('auth', 'Spotify login was cancelled or the callback was invalid. Try login again.')
    return query['code'][0]


def valid_token(data, previous=None):
    """Validate an exchange before replacing credentials; retain rotated refresh data."""
    if not isinstance(data, dict):
        raise Failure('auth', 'Spotify returned an invalid login response.')
    access = data.get('access_token')
    refresh = data.get('refresh_token', (previous or {}).get('refresh_token'))
    expires = data.get('expires_in')
    if (not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh
            or not isinstance(data.get('token_type'), str) or data['token_type'].lower() != 'bearer'
            or type(expires) not in (int, float) or not math.isfinite(expires) or expires <= 0):
        raise Failure('auth', 'Spotify returned an invalid login response.')
    return {'access_token': access, 'refresh_token': refresh, 'expires_at': time.time() + expires}


def bounded_int(value, low, high):
    """Reject invalid numeric API inputs rather than silently changing actions."""
    if type(value) is not int or not low <= value <= high:
        raise Failure('input', 'Spotify action has an invalid numeric value.')
    return value


def spotify_uri(value, kinds=('track', 'album', 'artist', 'playlist', 'episode')):
    """Validate Spotify URI components before constructing a path or action body."""
    match = re.fullmatch(r'spotify:([a-z]+):([A-Za-z0-9]+)', str(value))
    if not match or match[1] not in kinds:
        raise Failure('input', 'This Spotify item cannot be used for that action.')
    return match[1], match[2]


class Client:
    """Own account-scoped storage and a bounded standard-library Spotify client."""

    def __init__(self, config, state_dir):
        self.config = config
        self.state_dir = state_dir
        self.account_key = hashlib.sha256(config['client_id'].encode()).hexdigest()[:24]
        self.blocked = state_dir / (self.account_key + '.login-required')
        self.lock_path = state_dir / (self.account_key + '.lock')
        self.rate_path = state_dir / (self.account_key + '.rate-limit')

    def secret(self, operation, token=None):
        """Call Secret Service with secrets on stdin; discard all command diagnostics."""
        command = ['secret-tool', operation]
        if operation == 'store':
            command += ['--label=Oma Spotify OAuth']
        command += ['application', 'blazeluminati.oma-spotify', 'client-id', self.config['client_id']]
        try:
            result = subprocess.run(command, input=json.dumps(token) if token else None,
                                    capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            raise Failure('storage', 'Unlock your Secret Service keyring and install secret-tool.') from None
        if operation == 'lookup' and result.returncode == 1 and not result.stdout and not result.stderr:
            return None
        if result.returncode != 0:
            raise Failure('storage', 'Spotify credentials could not be read or saved. Unlock your keyring.')
        if operation == 'lookup':
            try:
                data = json.loads(result.stdout)
                if (not isinstance(data, dict) or not isinstance(data.get('access_token'), str)
                        or not isinstance(data.get('refresh_token'), str)
                        or type(data.get('expires_at')) not in (int, float)):
                    raise ValueError()
                return data
            except (ValueError, TypeError):
                raise Failure('storage', 'Saved Spotify credentials are invalid. Login again.') from None
        return None

    def read_token(self):
        """Look up this app's token without returning it to a caller outside the helper."""
        return self.secret('lookup')

    def store_token(self, token):
        """Replace this app's token only after validating the exchange response."""
        self.secret('store', token)

    def require_login(self):
        """Persist a non-secret retry stop while preserving saved diagnostic tokens."""
        private_directory(self.state_dir)
        self.blocked.touch(mode=0o600)

    def access_token(self):
        """Re-read under flock so concurrent expired-token calls refresh only once."""
        with private_lock(self.lock_path):
            if self.blocked.exists():
                raise Failure('auth', 'Spotify login expired. Login again to continue.')
            token = self.read_token()
            if not token:
                raise Failure('auth', 'Connect your Spotify account to continue.')
            if token['expires_at'] > time.time() + 60:
                return token['access_token']
            try:
                data = self.http('POST', TOKEN_URL, form={
                    'grant_type': 'refresh_token', 'refresh_token': token['refresh_token'],
                    'client_id': self.config['client_id']})
                token = valid_token(data, token)
                self.store_token(token)
            except Failure:
                self.require_login()
                raise Failure('auth', 'Spotify token refresh failed. Login again; saved credentials were kept.') from None
            return token['access_token']

    def http(self, method, url, query=None, body=None, form=None, token=None):
        """Send once with a timeout and response cap; never expose raw error bodies."""
        if not (url == TOKEN_URL or url.startswith(API + '/')):
            raise Failure('input', 'Spotify endpoint is not allowed.')
        if query:
            url += '?' + urlencode(query)
        headers = {'Accept': 'application/json'}
        payload = None
        if form is not None:
            payload = urlencode(form).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        elif body is not None:
            payload = json.dumps(body).encode()
            headers['Content-Type'] = 'application/json'
        if token:
            headers['Authorization'] = 'Bearer ' + token
        try:
            with urlopen(Request(url, data=payload, method=method, headers=headers), timeout=12) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise Failure('network', 'Spotify response was too large.')
                # Player mutations may acknowledge HTTP 200 with an opaque command
                # ID instead of JSON. The accepted status is the acknowledgment;
                # callers refresh state to observe the eventual playback change.
                if method != 'GET' and url.startswith(API + '/'):
                    return None
                return json.loads(raw) if raw else None
        except HTTPError as error:
            code = error.code
            error.close()
            if code == 429:
                try:
                    retry = max(1, int(error.headers.get('Retry-After', '30')))
                except (TypeError, ValueError):
                    retry = 30
                raise Failure('rate_limit', 'Spotify is rate limiting requests. Please wait.', retry) from None
            if code == 401:
                raise Failure('auth', 'Spotify rejected authorization. Login again.') from None
            if code == 404:
                raise Failure('no_device', 'No available playback device or item. Choose a device and refresh.') from None
            if code >= 500:
                raise Failure('network', 'Spotify is temporarily unavailable. Refresh when ready.') from None
            if code == 403:
                raise Failure('rejected', 'Spotify denied this action. Check Premium, app access and device restrictions.') from None
            raise Failure('rejected', 'Spotify rejected this request. Refresh and try a supported action.') from None
        except (URLError, TimeoutError, OSError):
            raise Failure('network', 'Spotify could not be reached. Last playback state may be stale.') from None
        except (ValueError, UnicodeError):
            raise Failure('network', 'Spotify returned an unreadable response. Refresh when ready.') from None

    def api(self, method, path, query=None, body=None):
        """Authorize a fixed relative endpoint and persist retry stops across processes."""
        if not re.fullmatch(r'/[A-Za-z0-9/_-]+', path) or '..' in path or path.startswith('//'):
            raise Failure('input', 'Spotify endpoint is not allowed.')
        if self.rate_path.exists():
            try:
                remaining = math.ceil(float(self.rate_path.read_text()) - time.time())
            except ValueError:
                remaining = 0
            if remaining > 0:
                raise Failure('rate_limit', 'Spotify is rate limiting requests. Please wait.', remaining)
        token = self.access_token()
        try:
            data = self.http(method, API + path, query, body, token=token)
            if data is not None and not isinstance(data, dict):
                raise Failure('network', 'Spotify returned an unexpected response. Refresh when ready.')
            return data
        except Failure as error:
            if error.kind == 'auth':
                self.require_login()
            elif error.kind == 'rate_limit':
                private_directory(self.state_dir)
                self.rate_path.write_text(str(time.time() + error.retry_after))
            raise

    def login(self):
        """Bind loopback before opening OAuth; accept one callback, then close the socket.

        The callback server logs nothing. Invalid callbacks terminate this attempt,
        preventing state guessing at the cost of requiring another login after a
        stray request. An independent login lock prevents competing listeners.
        """
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        result = {}

        class CallbackHandler(BaseHTTPRequestHandler):
            """Consume one callback without leaking its path into server logs."""

            def do_GET(handler):
                """Validate before accepting a code; send only fixed browser text."""
                try:
                    if handler.headers.get('Host') != '127.0.0.1:8888':
                        raise Failure('auth', 'Invalid Spotify callback host.')
                    result['code'] = callback_code(handler.path, state)
                    status, text = 200, 'Spotify callback received. Return to Oma Spotify for the login result.'
                except Failure as error:
                    result['error'] = error
                    status, text = 400, 'Spotify login was not accepted. Start login again in Oma Spotify.'
                handler.send_response(status)
                handler.send_header('Content-Type', 'text/plain; charset=utf-8')
                handler.send_header('Cache-Control', 'no-store')
                handler.send_header('Content-Security-Policy', "default-src 'none'")
                handler.end_headers()
                handler.wfile.write(text.encode())

            def log_message(handler, format, *args):
                """Suppress BaseHTTPRequestHandler's credential-bearing request log."""
                pass

        with private_lock(self.state_dir / 'login.lock'):
            try:
                with HTTPServer(('127.0.0.1', 8888), CallbackHandler) as server:
                    server.timeout = 180
                    server.socket.settimeout(180)
                    # Bound accepted-client reads too, not only accept().
                    original_get_request = server.get_request

                    def get_request():
                        """Prevent an idle loopback peer from holding login indefinitely."""
                        connection, address = original_get_request()
                        connection.settimeout(5)
                        return connection, address

                    server.get_request = get_request
                    url = 'https://accounts.spotify.com/authorize?' + urlencode({
                        'client_id': self.config['client_id'], 'response_type': 'code',
                        'redirect_uri': CALLBACK, 'scope': SCOPES, 'state': state,
                        'code_challenge_method': 'S256', 'code_challenge': challenge(verifier)})
                    subprocess.run(['xdg-open', url], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=10, check=True)
                    server.handle_request()
            except (OSError, subprocess.SubprocessError):
                raise Failure('setup', 'Cannot start browser login. Check the browser and that port 8888 is free.') from None
            if 'error' in result:
                raise result['error']
            if 'code' not in result:
                raise Failure('auth', 'Spotify login timed out. Start login again.')
            with private_lock(self.lock_path):
                token = valid_token(self.http('POST', TOKEN_URL, form={
                    'grant_type': 'authorization_code', 'client_id': self.config['client_id'],
                    'code': result['code'], 'redirect_uri': CALLBACK, 'code_verifier': verifier}))
                self.store_token(token)
                self.blocked.unlink(missing_ok=True)
                self.rate_path.unlink(missing_ok=True)
        return {'authenticated': True}

    def status(self):
        """Probe service and authorization without refreshing tokens or calling Spotify."""
        local = False
        if shutil.which('spotifyd'):
            try:
                local = subprocess.run(['systemctl', '--user', 'is-active', '--quiet', 'spotifyd.service'],
                                       capture_output=True, timeout=3).returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                pass
        token = self.read_token()
        return {'configured': True, 'authenticated': bool(token) and not self.blocked.exists(),
                'local_available': local, 'device_name': self.config['device_name'],
                'local_message': '' if local else 'Local audio unavailable. Install and start spotifyd.service, or choose another Connect device.'}

    def dispatch(self, command, args):
        """Map validated CLI actions to current Web API endpoints, one request per call."""
        if command == 'status':
            return self.status()
        if command == 'login':
            return self.login()
        query, body = {}, None
        reads = {'playback': '/me/player', 'devices': '/me/player/devices', 'queue': '/me/player/queue'}
        if command in reads:
            return self.api('GET', reads[command], query, body)
        if command in ('library', 'browse', 'search'):
            offset = bounded_int(args.get('offset', 0), 0, 100000)
            query = {'limit': 20, 'offset': offset}
            if command == 'library':
                paths = {'tracks': '/me/tracks', 'albums': '/me/albums', 'playlists': '/me/playlists',
                         'recent': '/me/player/recently-played'}
                kind = args.get('kind')
                if kind not in paths:
                    raise Failure('input', 'Choose a supported library view.')
                path = paths[kind]
                if kind == 'recent':
                    query = {'limit': 20}
                    if args.get('before'):
                        if not re.fullmatch(r'[0-9]{1,16}', str(args['before'])):
                            raise Failure('input', 'Invalid recent-history cursor.')
                        query['before'] = str(args['before'])
            elif command == 'browse':
                kind, identity = spotify_uri(args.get('uri'), ('album', 'artist', 'playlist'))
                path = {'album': '/albums/' + identity + '/tracks',
                        'artist': '/artists/' + identity + '/albums',
                        'playlist': '/playlists/' + identity + '/items'}[kind]
            else:
                text = args.get('query', '')
                kind = args.get('kind', 'track')
                if not isinstance(text, str) or not text.strip() or len(text) > 500 or kind not in ('track', 'album', 'artist', 'playlist'):
                    raise Failure('input', 'Enter a search and choose a supported result type.')
                query = {'q': text.strip(), 'type': kind, 'limit': 10, 'offset': offset}
                path = '/search'
            return self.api('GET', path, query, body)
        if command == 'save':
            spotify_uri(args.get('uri'), ('track', 'album'))
            return self.api('PUT', '/me/library', {'uris': args['uri']}, None)
        actions = ('transfer', 'play', 'pause', 'next', 'previous', 'seek', 'shuffle', 'repeat', 'volume', 'enqueue')
        if command not in actions:
            raise Failure('input', 'Unknown Spotify command.')
        device = args.get('device_id')
        if not isinstance(device, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', device):
            raise Failure('no_device', 'Choose a Spotify Connect device before using playback controls.')
        if command == 'transfer':
            return self.api('PUT', '/me/player', {}, {'device_ids': [device], 'play': True})
        query = {'device_id': device}
        method = 'POST' if command in ('next', 'previous', 'enqueue') else 'PUT'
        path = '/me/player/' + ('queue' if command == 'enqueue' else command)
        if command == 'play' and args.get('uri'):
            kind, _ = spotify_uri(args['uri'])
            body = {'uris': [args['uri']]} if kind in ('track', 'episode') else {'context_uri': args['uri']}
        elif command == 'enqueue':
            spotify_uri(args.get('uri'), ('track', 'episode'))
            query['uri'] = args['uri']
        elif command == 'seek':
            query['position_ms'] = bounded_int(args.get('value'), 0, 86400000)
        elif command == 'volume':
            query['volume_percent'] = bounded_int(args.get('value'), 0, 100)
        elif command == 'shuffle':
            if type(args.get('value')) is not bool:
                raise Failure('input', 'Shuffle must be on or off.')
            query['state'] = 'true' if args['value'] else 'false'
        elif command == 'repeat':
            if args.get('value') not in ('off', 'context', 'track'):
                raise Failure('input', 'Choose repeat off, context or track.')
            query['state'] = args['value']
        return self.api(method, path, query, body)


def load_config(path):
    """Read only public app settings; reject secret-bearing or unexpected config."""
    try:
        config = json.loads(path.read_text())
    except (OSError, ValueError):
        raise Failure('setup', 'Set your Spotify Client ID in the setup view. No client secret is needed.') from None
    if (not isinstance(config, dict) or set(config) - {'client_id', 'redirect_uri', 'device_name'}
            or not re.fullmatch(r'[A-Fa-f0-9]{32}', str(config.get('client_id', '')))
            or config.get('redirect_uri') != CALLBACK
            or not isinstance(config.get('device_name', 'Oma Spotify'), str)
            or not 1 <= len(config.get('device_name', 'Oma Spotify')) <= 100):
        raise Failure('setup', 'Spotify configuration is invalid. Use a Client ID and the loopback redirect, not a secret.')
    config.setdefault('device_name', 'Oma Spotify')
    return config


def main(argv=None):
    """Dispatch CLI arguments and print one bounded JSON result, without tracebacks."""
    argv = sys.argv[1:] if argv is None else argv
    try:
        if not 1 <= len(argv) <= 2:
            raise Failure('input', 'Usage: spotifyctl.py COMMAND [JSON_OBJECT]')
        args = json.loads(argv[1]) if len(argv) == 2 else {}
        if not isinstance(args, dict):
            raise Failure('input', 'Spotify arguments must be a JSON object.')
        config_path = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'oma-spotify/config.json'
        state_dir = Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state'))) / 'oma-spotify'
        if argv[0] == 'configure':
            identity = args.get('client_id', '')
            if (set(args) != {'client_id'} or not isinstance(identity, str)
                    or not re.fullmatch(r'[A-Fa-f0-9]{32}', identity)):
                raise Failure('input', 'Enter the 32-character Spotify Client ID, not a client secret.')
            private_directory(config_path.parent)
            temporary = config_path.with_name('spotify.json.' + secrets.token_hex(8))
            try:
                with temporary.open('x') as stream:
                    os.chmod(temporary, 0o600)
                    json.dump({'client_id': identity, 'redirect_uri': CALLBACK, 'device_name': 'Oma Spotify'}, stream)
                os.replace(temporary, config_path)
            finally:
                temporary.unlink(missing_ok=True)
            data = {'configured': True}
        else:
            data = Client(load_config(config_path), state_dir).dispatch(argv[0], args)
        print(json.dumps({'ok': True, 'data': data}, separators=(',', ':')))
        return 0
    except Failure as error:
        print(json.dumps(error.envelope(), separators=(',', ':')))
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        print(json.dumps(Failure('setup', 'Spotify could not complete this request. Check setup and try again.').envelope()))
    return 1


if __name__ == '__main__':
    sys.exit(main())
