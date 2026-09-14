#!/usr/bin/env python3
"""Deadlines use local sockets/locks only, with isolated repository temp data."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import json
import socket
import threading
import time
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from test_spotifyctl import spotifyctl


class DeadlineTests(unittest.TestCase):
    def test_lock_contention_expires(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.lock'
            with spotifyctl.private_lock(path):
                start = time.monotonic()
                with self.assertRaises(spotifyctl.Failure) as raised:
                    with spotifyctl.private_lock(path, timeout=0.05):
                        self.fail('contended lock acquired')
                self.assertEqual(raised.exception.kind, 'storage')
                self.assertLess(time.monotonic() - start, 0.5)

    def test_total_helper_deadline_interrupts_a_stalled_operation(self):
        with patch.object(spotifyctl, 'COMMAND_TIMEOUT', 0.05, create=True), \
                patch.object(spotifyctl, 'load_config', return_value={'client_id': 'a' * 32}), \
                patch.object(spotifyctl.Client, 'dispatch', side_effect=lambda *_: time.sleep(0.3)) as dispatch, \
                redirect_stdout(StringIO()) as output:
            start = time.monotonic()
            self.assertEqual(spotifyctl.main(['next', '{}']), 1)
            self.assertLess(time.monotonic() - start, 0.25)
            self.assertEqual(dispatch.call_count, 1)
        self.assertEqual(json.loads(output.getvalue())['error']['kind'], 'network')

    def test_nested_deadline_cannot_extend_outer_budget_and_restores_timer(self):
        previous = spotifyctl.signal.getsignal(spotifyctl.signal.SIGALRM)
        start = time.monotonic()
        with self.assertRaises(spotifyctl.Failure):
            with spotifyctl.deadline(0.05):
                with spotifyctl.deadline(1):
                    time.sleep(0.3)
        self.assertLess(time.monotonic() - start, 0.25)
        self.assertEqual(spotifyctl.signal.getsignal(spotifyctl.signal.SIGALRM), previous)
        self.assertEqual(spotifyctl.signal.getitimer(spotifyctl.signal.ITIMER_REAL), (0, 0))

    def test_deadline_interrupts_slow_response_without_retrying_mutation(self):
        class SlowResponse:
            closed = False

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.closed = True

            def read(self, _limit):
                # Simulate a response that keeps delivering before its socket
                # timeout. The command deadline must still interrupt this read.
                for _ in range(100):
                    time.sleep(0.01)
                return b'acknowledgment'

        response = SlowResponse()
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(spotifyctl.os.environ, {'XDG_STATE_HOME': directory}), \
                patch.object(spotifyctl, 'COMMAND_TIMEOUT', 0.05), \
                patch.object(spotifyctl, 'load_config', return_value={'client_id': 'a' * 32}), \
                patch.object(spotifyctl.Client, 'access_token', return_value='access-value'), \
                patch.object(spotifyctl, 'urlopen', return_value=response) as http, \
                redirect_stdout(StringIO()) as output:
            start = time.monotonic()
            self.assertEqual(spotifyctl.main(['next', '{"device_id":"remote"}']), 1)
            self.assertLess(time.monotonic() - start, 0.25)
        http.assert_called_once()
        self.assertTrue(response.closed)
        self.assertEqual(json.loads(output.getvalue())['error']['kind'], 'network')
        self.assertNotIn('access-value', output.getvalue())

    def test_competing_login_rejects_without_opening_browser(self):
        with tempfile.TemporaryDirectory() as directory:
            client = spotifyctl.Client({'client_id': 'a' * 32}, Path(directory))
            with spotifyctl.private_lock(Path(directory) / 'login.lock'), \
                    patch.object(spotifyctl, 'HTTPServer') as server:
                start = time.monotonic()
                with self.assertRaises(spotifyctl.Failure) as raised:
                    client.login()
                self.assertEqual(raised.exception.kind, 'storage')
                self.assertLess(time.monotonic() - start, 0.5)
                server.assert_not_called()

    def run_callback(self, duplicate_host=False, broken_response=False,
                     response_method='send_response', transport_error=TimeoutError):
        original = spotifyctl.HTTPServer
        address = []
        threads = []
        def server(_address, handler):
            instance = original(('127.0.0.1', 0), handler)
            address.append(instance.server_address)
            return instance
        def browser(command, **_kwargs):
            state = parse_qs(urlsplit(command[1]).query)['state'][0]
            def request():
                with socket.create_connection(address[0], timeout=1) as peer:
                    headers = 'Host: 127.0.0.1:8888\r\n' * (2 if duplicate_host else 1)
                    peer.sendall(('GET /callback?code=test&state=' + state +
                                  ' HTTP/1.0\r\n' + headers + '\r\n').encode())
                    while peer.recv(4096):
                        pass
            thread = threading.Thread(target=request)
            thread.start()
            threads.append(thread)
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(spotifyctl, 'HTTPServer', server), \
                patch.object(spotifyctl.subprocess, 'run', browser):
            client = spotifyctl.Client({'client_id': 'a' * 32}, Path(directory))
            with patch.object(client, 'http', return_value={
                    'access_token':'new', 'refresh_token':'refresh', 'expires_in':3600,
                    'token_type':'Bearer'}) as exchange, patch.object(client, 'store_token') as store:
                try:
                    if broken_response:
                        # TimeoutError is swallowed by handle_one_request, unlike
                        # most handler exceptions. Prove validation has succeeded
                        # and the response mock was reached, not an unrelated failure.
                        with patch.object(spotifyctl, 'callback_code', wraps=spotifyctl.callback_code) as validate, \
                                patch.object(spotifyctl.BaseHTTPRequestHandler, response_method,
                                             autospec=True, side_effect=transport_error('private diagnostic')) as response, \
                                redirect_stderr(StringIO()) as diagnostics:
                            with self.assertRaises(spotifyctl.Failure) as raised:
                                client.login()
                        validate.assert_called_once()
                        self.assertEqual(spotifyctl.callback_code(*validate.call_args.args), 'test')
                        response.assert_called_once()
                        if response_method == 'send_response':
                            self.assertEqual(response.call_args.args[1], 200)
                        self.assertEqual(raised.exception.kind, 'auth')
                        self.assertNotIn('private diagnostic', str(raised.exception))
                        self.assertEqual(diagnostics.getvalue(), '')
                        self.assertFalse(client.blocked.exists())
                    elif duplicate_host:
                        with self.assertRaises(spotifyctl.Failure):
                            client.login()
                    else:
                        self.assertEqual(client.login(), {'authenticated':True})
                    self.assertEqual(exchange.call_count, 0 if broken_response or duplicate_host else 1)
                    self.assertEqual(store.call_count, exchange.call_count)
                finally:
                    for thread in threads:
                        thread.join(1)
                        self.assertFalse(thread.is_alive(), 'callback peer did not terminate')

    def test_callback_transport_failure_never_exchanges_code(self):
        self.run_callback(broken_response=True)

    def test_callback_response_failures_never_exchange_code(self):
        for method in ('send_response', 'end_headers'):
            for error in (TimeoutError, BrokenPipeError, ConnectionResetError):
                with self.subTest(method=method, error=error):
                    self.run_callback(broken_response=True, response_method=method, transport_error=error)

    def test_duplicate_host_rejects_without_exchange(self):
        self.run_callback(duplicate_host=True)

    def test_valid_callback_exchanges_exactly_once(self):
        self.run_callback()

    def test_slow_drip_callback_has_an_absolute_deadline(self):
        # Bind an ephemeral loopback port, never the real OAuth callback port.
        original = spotifyctl.HTTPServer
        peers = []
        done = threading.Event()

        def server(_address, handler):
            instance = original(('127.0.0.1', 0), handler)
            peers.append(instance.server_address)
            return instance

        def browser(*_args, **_kwargs):
            def drip():
                try:
                    with socket.create_connection(peers[0], timeout=1) as peer:
                        while not done.wait(0.01):
                            peer.sendall(b'G')
                except OSError:
                    pass
            thread = threading.Thread(target=drip)
            thread.start()
            peers.append(thread)

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(spotifyctl, 'HTTPServer', server), \
                patch.object(spotifyctl.subprocess, 'run', browser), \
                patch.object(spotifyctl, 'CALLBACK_TIMEOUT', 0.05, create=True):
            client = spotifyctl.Client({'client_id': 'a' * 32}, Path(directory))
            start = time.monotonic()
            try:
                with self.assertRaises(spotifyctl.Failure):
                    client.login()
                self.assertLess(time.monotonic() - start, 0.5)
            finally:
                done.set()
                if len(peers) > 1:
                    peers[1].join(1)


if __name__ == '__main__':
    unittest.main()
