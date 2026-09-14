#!/usr/bin/env python3
"""Exercise Spotify helper validation without credentials or network access."""
from contextlib import redirect_stdout
from io import BytesIO, StringIO
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

MODULE_PATH = Path(__file__).resolve().parents[1] / "core" / "spotifyctl.py"
spec = importlib.util.spec_from_file_location("oma_spotifyctl", MODULE_PATH)
spotifyctl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(spotifyctl)


class SpotifyCtlSecurityTests(unittest.TestCase):
    def test_callback_accepts_only_matching_single_code(self):
        self.assertEqual(spotifyctl.callback_code("/callback?code=abc&state=expected", "expected"), "abc")
        rejected = [
            "/other?code=abc&state=expected",
            "/callback?code=abc&state=wrong",
            "/callback?code=abc&code=def&state=expected",
            "/callback?code=&state=expected",
            "/callback?error=denied&state=expected",
            "//attacker/callback?code=abc&state=expected",
        ]
        for path in rejected:
            with self.subTest(path=path), self.assertRaises(spotifyctl.Failure):
                spotifyctl.callback_code(path, "expected")

    def test_config_rejects_client_secret_and_wrong_redirect(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spotify.json"
            base = {"client_id": "a" * 32, "redirect_uri": spotifyctl.CALLBACK,
                    "device_name": "Oma Spotify"}
            path.write_text(json.dumps({**base, "client_secret": "never"}))
            with self.assertRaises(spotifyctl.Failure):
                spotifyctl.load_config(path)
            path.write_text(json.dumps({**base, "redirect_uri": "http://localhost:8888/callback"}))
            with self.assertRaises(spotifyctl.Failure):
                spotifyctl.load_config(path)

    def test_configure_writes_only_public_values_to_private_plugin_path(self):
        with tempfile.TemporaryDirectory() as directory:
            config_home = Path(directory) / "config"
            state_home = Path(directory) / "state"
            old_config = os.environ.get("XDG_CONFIG_HOME")
            old_state = os.environ.get("XDG_STATE_HOME")
            os.environ["XDG_CONFIG_HOME"] = str(config_home)
            os.environ["XDG_STATE_HOME"] = str(state_home)
            try:
                with redirect_stdout(StringIO()):
                    self.assertEqual(spotifyctl.main(["configure", json.dumps({"client_id": "b" * 32})]), 0)
                    self.assertEqual(spotifyctl.main(["configure", json.dumps({
                        "client_id": "b" * 32, "client_secret": "never"
                    })]), 1)
            finally:
                if old_config is None:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                else:
                    os.environ["XDG_CONFIG_HOME"] = old_config
                if old_state is None:
                    os.environ.pop("XDG_STATE_HOME", None)
                else:
                    os.environ["XDG_STATE_HOME"] = old_state
            path = config_home / "oma-spotify" / "config.json"
            self.assertEqual(json.loads(path.read_text()), {
                "client_id": "b" * 32,
                "redirect_uri": spotifyctl.CALLBACK,
                "device_name": "Oma Spotify",
            })
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_api_rejects_non_spotify_or_traversal_endpoints_before_token_lookup(self):
        client = spotifyctl.Client({"client_id": "c" * 32}, Path("/unused"))
        for path in ("https://attacker.invalid", "/../me", "//me/player", "/me/player?next=x"):
            with self.subTest(path=path), self.assertRaises(spotifyctl.Failure) as raised:
                client.api("GET", path)
            self.assertEqual(raised.exception.kind, "input")

    def test_numeric_and_uri_inputs_are_not_coerced(self):
        for value in (True, "50", 101, -1):
            with self.subTest(value=value), self.assertRaises(spotifyctl.Failure):
                spotifyctl.bounded_int(value, 0, 100)
        self.assertEqual(spotifyctl.spotify_uri("spotify:track:AbC123", ("track",)), ("track", "AbC123"))
        for value in ("https://open.spotify.com/track/abc", "spotify:user:abc", "spotify:track:a/b"):
            with self.subTest(value=value), self.assertRaises(spotifyctl.Failure):
                spotifyctl.spotify_uri(value, ("track",))

    def test_secret_store_keeps_tokens_out_of_process_arguments(self):
        calls = []

        def run(command, **kwargs):
            calls.append((command, kwargs))
            return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        client = spotifyctl.Client({"client_id": "d" * 32}, Path("/unused"))
        token = {"access_token": "access-value", "refresh_token": "refresh-value", "expires_at": 1}
        with patch.object(spotifyctl.subprocess, "run", run):
            client.store_token(token)
        command, kwargs = calls[0]
        self.assertNotIn("access-value", command)
        self.assertNotIn("refresh-value", command)
        self.assertEqual(json.loads(kwargs["input"]), token)
        self.assertIn("blazeluminati.oma-spotify", command)

    def test_http_caps_responses_and_never_returns_raw_server_errors(self):
        class OversizedResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, _limit):
                return b"x" * (4 * 1024 * 1024 + 1)

        client = spotifyctl.Client({"client_id": "e" * 32}, Path("/unused"))
        with patch.object(spotifyctl, "urlopen", lambda *_args, **_kwargs: OversizedResponse()):
            with self.assertRaises(spotifyctl.Failure) as oversized:
                client.http("GET", spotifyctl.API + "/me")
        self.assertEqual(str(oversized.exception), "Spotify response was too large.")

        raw = "private upstream diagnostic"
        error = HTTPError(spotifyctl.API + "/me", 400, raw, {}, BytesIO(raw.encode()))
        with patch.object(spotifyctl, "urlopen", side_effect=error):
            with self.assertRaises(spotifyctl.Failure) as rejected:
                client.http("GET", spotifyctl.API + "/me")
        self.assertNotIn(raw, str(rejected.exception))


if __name__ == "__main__":
    unittest.main()
