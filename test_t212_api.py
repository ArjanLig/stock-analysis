"""
Unit tests for t212_api.py — the read-only Trading 212 broker client.

All HTTP is mocked; tests run without network access or real credentials.
"""

import base64
import unittest
from unittest.mock import MagicMock, patch

import requests

import t212_api


_CREDS = {"t212_api_key": "KEY123", "t212_api_secret": "SECRET456"}


class TestAuthHeader(unittest.TestCase):
    def test_auth_header_is_basic_base64_key_colon_secret(self):
        header = t212_api._auth_header(_CREDS)
        expected = base64.b64encode(b"KEY123:SECRET456").decode()
        self.assertEqual(header["Authorization"], f"Basic {expected}")


class TestGet(unittest.TestCase):
    def _resp(self, status=200, json_data=None, headers=None):
        r = MagicMock()
        r.status_code = status
        r.json.return_value = json_data if json_data is not None else {}
        r.headers = headers or {}
        r.raise_for_status.side_effect = (
            None if status < 400 else requests.HTTPError(f"{status}")
        )
        return r

    @patch("t212_api.time.sleep", return_value=None)
    @patch("t212_api.requests.get")
    def test_get_sends_auth_and_returns_json(self, mock_get, _sleep):
        mock_get.return_value = self._resp(200, {"ok": True})
        out = t212_api._get("/equity/positions", _CREDS)
        self.assertEqual(out, {"ok": True})
        _, kwargs = mock_get.call_args
        self.assertIn("Authorization", kwargs["headers"])

    @patch("t212_api.time.sleep", return_value=None)
    @patch("t212_api.requests.get")
    def test_get_retries_on_429_then_succeeds(self, mock_get, _sleep):
        mock_get.side_effect = [
            self._resp(429, headers={"Retry-After": "1"}),
            self._resp(200, {"ok": 1}),
        ]
        out = t212_api._get("/equity/account/cash", _CREDS, max_retries=3)
        self.assertEqual(out, {"ok": 1})
        self.assertEqual(mock_get.call_count, 2)

    @patch("t212_api.time.sleep", return_value=None)
    @patch("t212_api.requests.get")
    def test_get_raises_after_exhausting_retries(self, mock_get, _sleep):
        mock_get.return_value = self._resp(429, headers={"Retry-After": "1"})
        with self.assertRaises(requests.HTTPError):
            t212_api._get("/equity/positions", _CREDS, max_retries=2)
