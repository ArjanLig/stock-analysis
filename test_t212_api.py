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

_META = [
    {"ticker": "AAPL_US_EQ", "shortName": "AAPL", "currencyCode": "USD",
     "isin": "US0378331005"},
    {"ticker": "ASML_NL_EQ", "shortName": "ASML", "currencyCode": "EUR",
     "isin": "NL0010273215"},
]


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


class TestResolve(unittest.TestCase):
    def setUp(self):
        t212_api._INSTRUMENTS_CACHE = None  # reset module cache between tests

    @patch("t212_api._get")
    def test_resolves_us_and_non_us_codes(self, mock_get):
        mock_get.return_value = _META
        m = t212_api._resolve_instruments(_CREDS)
        self.assertEqual(m["AAPL_US_EQ"]["symbol"], "AAPL")
        self.assertEqual(m["AAPL_US_EQ"]["currency"], "USD")
        self.assertEqual(m["ASML_NL_EQ"]["symbol"], "ASML")
        self.assertEqual(m["ASML_NL_EQ"]["currency"], "EUR")
        self.assertEqual(m["ASML_NL_EQ"]["exchange"], "NL")

    @patch("t212_api._get")
    def test_metadata_fetched_once_and_cached(self, mock_get):
        mock_get.return_value = _META
        t212_api._resolve_instruments(_CREDS)
        t212_api._resolve_instruments(_CREDS)
        self.assertEqual(mock_get.call_count, 1)

    @patch("t212_api._get")
    def test_clean_falls_back_to_suffix_strip(self, mock_get):
        mock_get.return_value = _META
        info = t212_api._clean("TSLA_US_EQ", _CREDS)  # not in _META
        self.assertEqual(info["symbol"], "TSLA")
        self.assertEqual(info["exchange"], "US")


class TestPortfolio(unittest.TestCase):
    def setUp(self):
        t212_api._INSTRUMENTS_CACHE = None

    @patch("t212_api._get")
    def test_positions_normalise_to_cost_basis(self, mock_get):
        def _router(path, creds, **kw):
            if path == "/equity/metadata/instruments":
                return _META
            if path == "/equity/positions":
                return [
                    {"ticker": "AAPL_US_EQ", "quantity": 10,
                     "averagePrice": 150.0, "ppl": 200.0},
                    {"ticker": "ASML_NL_EQ", "quantity": 5,
                     "averagePrice": 600.0, "ppl": -50.0},
                ]
            if path == "/equity/account/info":
                return {"id": 42, "currencyCode": "EUR"}
            raise AssertionError(path)
        mock_get.side_effect = _router

        cb, acct = t212_api.fetch_portfolio_data(_CREDS)
        self.assertEqual(acct, "42")
        self.assertEqual(cb["AAPL"]["shares_held"], 10)
        self.assertEqual(cb["AAPL"]["cost_per_share"], 150.0)
        self.assertEqual(cb["AAPL"]["adjusted_cost"], 1500.0)
        self.assertEqual(cb["AAPL"]["total_pl"], 200.0)
        self.assertEqual(cb["AAPL"]["option_pl"], 0)
        self.assertEqual(cb["AAPL"]["trades"], [])
        self.assertEqual(cb["AAPL"]["currency"], "USD")
        self.assertEqual(cb["ASML"]["currency"], "EUR")
        self.assertEqual(cb["ASML"]["exchange"], "NL")
